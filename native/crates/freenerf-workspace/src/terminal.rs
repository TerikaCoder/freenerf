//! Shell execution inside the workspace with a hard timeout.
//!
//! On Android this runs `/system/bin/sh` (toybox) inside the application
//! sandbox: it is a real shell, but a restricted one. It is not root and not a
//! full Linux userland. Termux is offered as an explicit, submit-only backend
//! by the Android UI, because Termux's RUN_COMMAND intent cannot stream command
//! output back to the caller.

use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

pub const MAX_OUTPUT: usize = 20_000;
pub const MAX_TIMEOUT_SECS: u64 = 600;

#[derive(Debug, Clone, PartialEq)]
pub struct ShellResult {
    pub output: String,
    pub exit_code: Option<i32>,
    pub timed_out: bool,
    pub elapsed: f64,
}

impl ShellResult {
    pub fn succeeded(&self) -> bool {
        self.exit_code == Some(0) && !self.timed_out
    }

    pub fn formatted(&self) -> String {
        let header = if self.timed_out {
            "Command timed out".to_string()
        } else if let Some(code) = self.exit_code {
            format!("Exit code: {code}")
        } else {
            "Command could not be run".to_string()
        };
        format!("{header} ({:.2}s)\n{}", self.elapsed, self.output)
            .trim_end()
            .to_string()
    }
}

/// Pluggable shell backend, so the Android layer can substitute a different
/// executor without changing the tool implementations.
pub trait ShellBackend: Send + Sync {
    fn run(&self, command: &str, cwd: &Path, timeout: Duration) -> ShellResult;
}

#[derive(Debug, Clone)]
pub struct Terminal {
    cwd: PathBuf,
    default_timeout: u64,
}

impl Terminal {
    pub fn new(cwd: impl AsRef<Path>, default_timeout: u64) -> Self {
        Self {
            cwd: cwd.as_ref().to_path_buf(),
            default_timeout,
        }
    }

    pub fn cwd(&self) -> &Path {
        &self.cwd
    }

    pub fn which(program: &str) -> Option<PathBuf> {
        let path = std::env::var_os("PATH")?;
        for directory in std::env::split_paths(&path) {
            let candidate = directory.join(program);
            if candidate.is_file() {
                return Some(candidate);
            }
        }
        None
    }

    fn shell() -> &'static str {
        if Path::new("/system/bin/sh").exists() {
            "/system/bin/sh"
        } else {
            "sh"
        }
    }
}

impl ShellBackend for Terminal {
    fn run(&self, command: &str, cwd: &Path, timeout: Duration) -> ShellResult {
        let command = command.trim();
        if command.is_empty() {
            return ShellResult {
                output: "No command provided".into(),
                exit_code: None,
                timed_out: false,
                elapsed: 0.0,
            };
        }

        let workdir = if cwd.as_os_str().is_empty() {
            self.cwd.clone()
        } else {
            cwd.to_path_buf()
        };

        if !workdir.is_dir() {
            return ShellResult {
                output: format!("Working directory does not exist: {}", workdir.display()),
                exit_code: None,
                timed_out: false,
                elapsed: 0.0,
            };
        }

        let timeout = timeout
            .as_secs_f64()
            .clamp(1.0, MAX_TIMEOUT_SECS as f64);

        let started = Instant::now();

        let mut builder = Command::new(Self::shell());
        builder
            .arg("-c")
            .arg(command)
            .current_dir(&workdir)
            .env("FREENERF", "1")
            .env("GIT_TERMINAL_PROMPT", "0")
            .env("GIT_PAGER", "cat")
            .env("PAGER", "cat")
            .env("TERM", "dumb")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        // Put the shell in its own process group so a timeout can terminate
        // grandchildren too. Otherwise a child such as `sleep` keeps the
        // stdout pipe open and the readers block past the deadline.
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            builder.process_group(0);
        }

        let mut process = match builder.spawn() {
            Ok(child) => child,
            Err(e) => {
                return ShellResult {
                    output: format!("Cannot start command: {e}"),
                    exit_code: None,
                    timed_out: false,
                    elapsed: started.elapsed().as_secs_f64(),
                }
            }
        };

        let mut stdout = process.stdout.take();
        let mut stderr = process.stderr.take();

        let stdout_reader = stdout.take().map(|mut pipe| {
            std::thread::spawn(move || {
                let mut buffer = String::new();
                let _ = pipe.read_to_string(&mut buffer);
                buffer
            })
        });
        let stderr_reader = stderr.take().map(|mut pipe| {
            std::thread::spawn(move || {
                let mut buffer = String::new();
                let _ = pipe.read_to_string(&mut buffer);
                buffer
            })
        });

        let deadline = started + Duration::from_secs_f64(timeout);
        let mut timed_out = false;

        loop {
            match process.try_wait() {
                Ok(Some(_)) => break,
                Ok(None) => {
                    if Instant::now() >= deadline {
                        timed_out = true;
                        kill_process_tree(&mut process);
                        let _ = process.wait();
                        break;
                    }
                    std::thread::sleep(Duration::from_millis(20));
                }
                Err(_) => break,
            }
        }

        let mut output = String::new();
        if let Some(reader) = stdout_reader {
            if let Ok(text) = reader.join() {
                output.push_str(&text);
            }
        }
        if let Some(reader) = stderr_reader {
            if let Ok(text) = reader.join() {
                output.push_str(&text);
            }
        }

        let elapsed = started.elapsed().as_secs_f64();
        let code = process.try_wait().ok().flatten().and_then(|s| s.code());

        let char_count = output.chars().count();
        if char_count > MAX_OUTPUT {
            let omitted = char_count - MAX_OUTPUT;
            let tail: String = output.chars().skip(omitted).collect();
            output = format!("[first {omitted} characters omitted]\n{tail}");
        }

        ShellResult {
            output,
            exit_code: if timed_out { None } else { code },
            timed_out,
            elapsed,
        }
    }
}

fn kill_process_tree(child: &mut std::process::Child) {
    #[cfg(unix)]
    unsafe {
        // Negative pid targets the whole process group created above.
        libc::killpg(child.id() as libc::pid_t, libc::SIGKILL);
    }
    let _ = child.kill();
}

impl Terminal {
    /// Convenience wrapper using the terminal's default timeout.
    pub fn run(&self, command: &str, timeout: Option<u64>) -> String {
        let seconds = timeout.unwrap_or(self.default_timeout);
        ShellBackend::run(
            self,
            command,
            &self.cwd,
            Duration::from_secs(seconds.max(1)),
        )
        .formatted()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn runs_a_command_in_the_workspace() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("hello.txt"), "hi").unwrap();
        let terminal = Terminal::new(dir.path(), 10);
        let output = terminal.run("cat hello.txt", None);
        assert!(output.contains("Exit code: 0"), "{output}");
        assert!(output.contains("hi"), "{output}");
    }

    #[test]
    fn enforces_timeouts() {
        let dir = tempfile::tempdir().unwrap();
        let terminal = Terminal::new(dir.path(), 1);
        let started = Instant::now();
        let output = terminal.run("sleep 5", Some(1));
        assert!(started.elapsed() < Duration::from_secs(4));
        assert!(output.contains("timed out"), "{output}");
    }

    #[test]
    fn preserves_nonzero_exit_status() {
        let dir = tempfile::tempdir().unwrap();
        let terminal = Terminal::new(dir.path(), 10);
        let result = ShellBackend::run(
            &terminal,
            "printf failure; exit 7",
            dir.path(),
            Duration::from_secs(10),
        );
        assert_eq!(result.exit_code, Some(7));
        assert!(!result.succeeded());
        assert!(result.formatted().contains("failure"));
    }

    #[test]
    fn reports_missing_directories() {
        let dir = tempfile::tempdir().unwrap();
        let terminal = Terminal::new(dir.path().join("missing"), 5);
        assert!(terminal.run("echo hi", None).contains("does not exist"));
    }
}
