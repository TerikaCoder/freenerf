//! Allow-listed git wrapper rooted at the workspace.

use std::path::{Path, PathBuf};
use std::process::Command;

use crate::terminal::Terminal;

const READ_ONLY: &[&str] = &[
    "status", "diff", "log", "show", "branch", "rev-parse", "ls-files", "blame", "describe",
    "remote", "stash", "tag",
];

const ALLOWED_EXTRA: &[&str] = &["add", "commit", "checkout", "restore", "config"];

const FORBIDDEN: &[&str] = &[
    "push",
    "reset",
    "clean",
    "filter-branch",
    "filter-repo",
    "gc",
    "reflog",
    "update-ref",
    "submodule",
    "clone",
    "worktree",
];

#[derive(Debug, Clone)]
pub struct Git {
    root: PathBuf,
}

impl Git {
    pub fn new(root: impl AsRef<Path>) -> Self {
        Self {
            root: root.as_ref().to_path_buf(),
        }
    }

    pub fn available() -> bool {
        Terminal::which("git").is_some() || Path::new("/system/bin/git").exists()
    }

    pub fn run(&self, command: &str, timeout: u64) -> String {
        let command = command.trim();
        let subcommand = subcommand_of(command);

        if subcommand.is_empty() {
            return "Provide a git subcommand, e.g. 'status --short'".into();
        }
        if FORBIDDEN.contains(&subcommand.as_str()) {
            return format!(
                "git {subcommand} is not available through this tool. Run it manually after review."
            );
        }
        if !READ_ONLY.contains(&subcommand.as_str())
            && !ALLOWED_EXTRA.contains(&subcommand.as_str())
        {
            let mut allowed: Vec<&str> = READ_ONLY.iter().chain(ALLOWED_EXTRA.iter()).copied().collect();
            allowed.sort_unstable();
            return format!("Unsupported git subcommand. Allowed: {}", allowed.join(", "));
        }

        let args = match split_args(command) {
            Some(args) => args,
            None => return "Invalid git command".into(),
        };

        let Some(git) = Terminal::which("git") else {
            return "git is not installed in this Android environment. Install it in Termux \
                    and use Termux integration, or use run_shell for other tools."
                .into();
        };

        let output = Command::new(git)
            .args(&args)
            .current_dir(&self.root)
            .env("GIT_PAGER", "cat")
            .env("PAGER", "cat")
            .env("GIT_TERMINAL_PROMPT", "0")
            .env("TERM", "dumb")
            .output();

        match output {
            Ok(result) => {
                let mut text = String::from_utf8_lossy(&result.stdout).to_string();
                text.push_str(&String::from_utf8_lossy(&result.stderr));
                let text = text.trim();
                let mut text = text.to_string();
                if text.chars().count() > 20_000 {
                    text = text.chars().take(20_000).collect::<String>() + "\n[truncated]";
                }
                let _ = timeout;
                format!(
                    "Exit code: {}\n{}",
                    result.status.code().unwrap_or(-1),
                    text
                )
                .trim_end()
                .to_string()
            }
            Err(e) => format!("Cannot run git: {e}"),
        }
    }
}

fn subcommand_of(command: &str) -> String {
    split_args(command)
        .unwrap_or_default()
        .into_iter()
        .find(|part| !part.starts_with('-'))
        .map(|part| part.to_ascii_lowercase())
        .unwrap_or_default()
}

fn split_args(command: &str) -> Option<Vec<String>> {
    let mut args = Vec::new();
    let mut current = String::new();
    let mut quote: Option<char> = None;
    let mut started = false;

    for ch in command.chars() {
        match quote {
            Some(q) if ch == q => {
                quote = None;
                started = true;
            }
            Some(_) => {
                current.push(ch);
                started = true;
            }
            None => match ch {
                '\'' | '"' => {
                    quote = Some(ch);
                    started = true;
                }
                c if c.is_whitespace() => {
                    if started || !current.is_empty() {
                        args.push(std::mem::take(&mut current));
                        started = false;
                    }
                }
                c => {
                    current.push(c);
                    started = true;
                }
            },
        }
    }

    if quote.is_some() {
        return None;
    }
    if started || !current.is_empty() {
        args.push(current);
    }

    Some(args)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_quoted_arguments() {
        assert_eq!(
            split_args("commit -m \"hello world\"").unwrap(),
            vec!["commit", "-m", "hello world"]
        );
        assert!(split_args("commit -m \"oops").is_none());
    }

    #[test]
    fn rejects_forbidden_and_unknown_subcommands() {
        let git = Git::new(".");
        assert!(git.run("push origin main", 30).contains("not available"));
        assert!(git.run("frobnicate", 30).contains("Unsupported"));
        assert!(git.run("", 30).contains("Provide a git subcommand"));
    }
}
