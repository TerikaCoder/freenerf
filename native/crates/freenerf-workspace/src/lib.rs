//! Workspace abstraction: every filesystem operation is rooted here and
//! nothing outside the workspace can be reached.

pub mod git;
pub mod project;
pub mod terminal;

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};

use chrono::Utc;
use freenerf_core::{Error, Result};
use walkdir::WalkDir;

pub use git::Git;
pub use project::ProjectInspector;
pub use terminal::{ShellBackend, Terminal};

pub const DATA_DIR_NAME: &str = ".freenerf";
pub const MAX_READ_BYTES: u64 = 300_000;
pub const MAX_LIST_ENTRIES: usize = 500;
pub const MAX_SEARCH_LINES: usize = 80;
pub const MAX_SEARCH_CHARS: usize = 12_000;

const SKIP_DIRECTORIES: &[&str] = &[
    "__pycache__",
    ".git",
    ".venv",
    "venv",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".freenerf",
    "target",
    "build",
];

#[derive(Debug, Clone)]
pub struct Workspace {
    root: PathBuf,
}

impl Workspace {
    pub fn open(path: impl AsRef<Path>, create: bool) -> Result<Self> {
        let candidate = path.as_ref();
        let absolute = if candidate.is_absolute() {
            candidate.to_path_buf()
        } else {
            std::env::current_dir()
                .map_err(|e| Error::workspace(format!("cannot resolve cwd: {e}")))?
                .join(candidate)
        };

        if !absolute.exists() {
            if !create {
                return Err(Error::workspace(format!(
                    "Workspace does not exist: {}",
                    absolute.display()
                )));
            }
            fs::create_dir_all(&absolute).map_err(|e| {
                Error::workspace(format!("Cannot create workspace {}: {e}", absolute.display()))
            })?;
        }

        let root = absolute.canonicalize().map_err(|e| {
            Error::workspace(format!("Cannot resolve workspace {}: {e}", absolute.display()))
        })?;

        if !root.is_dir() {
            return Err(Error::workspace(format!(
                "Workspace is not a directory: {}",
                root.display()
            )));
        }

        Ok(Self { root })
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn display_name(&self) -> String {
        self.root
            .file_name()
            .map(|n| n.to_string_lossy().to_string())
            .unwrap_or_else(|| self.root.display().to_string())
    }

    pub fn data_dir(&self) -> PathBuf {
        self.root.join(DATA_DIR_NAME)
    }

    pub fn sessions_db(&self) -> PathBuf {
        self.data_dir().join("sessions.db")
    }

    pub fn tasks_db(&self) -> PathBuf {
        self.data_dir().join("tasks.db")
    }

    pub fn settings_db(&self) -> PathBuf {
        self.data_dir().join("settings.db")
    }

    pub fn images_dir(&self) -> PathBuf {
        self.data_dir().join("images")
    }

    pub fn backups_dir(&self) -> PathBuf {
        self.data_dir().join("backups")
    }

    pub fn logs_dir(&self) -> PathBuf {
        self.data_dir().join("logs")
    }

    /// Create the workspace-local data layout and metadata file.
    pub fn ensure(&self) -> Result<()> {
        for directory in [
            self.data_dir(),
            self.images_dir(),
            self.backups_dir(),
            self.logs_dir(),
        ] {
            fs::create_dir_all(&directory).map_err(|e| {
                Error::workspace(format!(
                    "Cannot create workspace data directory {}: {e}",
                    directory.display()
                ))
            })?;
        }

        let metadata = self.data_dir().join("workspace.json");
        if !metadata.exists() {
            let payload = serde_json::json!({
                "name": self.display_name(),
                "root": self.root.to_string_lossy(),
                "created_at": Utc::now().to_rfc3339(),
            });
            let _ = fs::write(metadata, serde_json::to_string_pretty(&payload).unwrap_or_default());
        }
        Ok(())
    }

    pub fn summary(&self) -> serde_json::Value {
        serde_json::json!({
            "root": self.root.to_string_lossy(),
            "name": self.display_name(),
            "data_dir": self.data_dir().to_string_lossy(),
        })
    }

    // ------------------------------------------------------------------
    // Path safety
    // ------------------------------------------------------------------
    pub fn resolve(&self, path: &str, must_exist: bool) -> Result<PathBuf> {
        let raw = if path.trim().is_empty() { "." } else { path.trim() };
        let candidate = PathBuf::from(raw);
        let target = if candidate.is_absolute() {
            candidate
        } else {
            self.root.join(candidate)
        };

        // Resolve through the nearest existing ancestor so `..` escapes and
        // symlinks outside the workspace are both rejected.
        let resolved = resolve_lenient(&target)?;

        if !resolved.starts_with(&self.root) {
            return Err(Error::Permission(
                "Path outside project is not allowed".into(),
            ));
        }

        if must_exist && !resolved.exists() {
            return Err(Error::Workspace(format!("Path does not exist: {path}")));
        }

        Ok(resolved)
    }

    pub fn relative(&self, path: &Path) -> String {
        path.strip_prefix(&self.root)
            .map(|p| p.to_string_lossy().to_string())
            .unwrap_or_else(|_| path.to_string_lossy().to_string())
    }

    fn backup(&self, target: &Path) -> Option<PathBuf> {
        if !target.is_file() {
            return None;
        }
        if self.data_dir().is_ancestor_of(target) {
            return None;
        }

        let stamp = Utc::now().format("%Y%m%d_%H%M%S_%f").to_string();
        let relative = target.strip_prefix(&self.root).ok()?;
        let destination = self.backups_dir().join(stamp).join(relative);

        if let Some(parent) = destination.parent() {
            fs::create_dir_all(parent).ok()?;
        }
        fs::copy(target, &destination).ok()?;
        Some(destination)
    }

    // ------------------------------------------------------------------
    // Reads
    // ------------------------------------------------------------------
    pub fn list_files(&self, path: &str, recursive: bool) -> Result<String> {
        let target = self.resolve(path, false)?;

        if !target.exists() {
            return Ok("Path does not exist".into());
        }
        if target.is_file() {
            return Ok(format!("FILE {}", self.relative(&target)));
        }

        let mut entries: Vec<PathBuf> = Vec::new();
        let mut truncated = false;

        if recursive {
            for entry in WalkDir::new(&target)
                .follow_links(false)
                .into_iter()
                .filter_entry(|entry| !is_skipped(entry.path()))
                .filter_map(|e| e.ok())
            {
                if entry.path() == target {
                    continue;
                }
                if entry.file_type().is_dir() || entry.file_type().is_file() {
                    entries.push(entry.path().to_path_buf());
                    if entries.len() >= MAX_LIST_ENTRIES {
                        truncated = true;
                        break;
                    }
                }
            }
        } else if let Ok(read_dir) = fs::read_dir(&target) {
            for entry in read_dir.flatten() {
                if is_skipped(&entry.path()) {
                    continue;
                }
                entries.push(entry.path());
                if entries.len() >= MAX_LIST_ENTRIES {
                    truncated = true;
                    break;
                }
            }
        }

        entries.sort_by_key(|p| p.to_string_lossy().to_lowercase());

        if entries.is_empty() {
            return Ok("Directory is empty".into());
        }

        let mut lines: Vec<String> = entries
            .iter()
            .map(|item| {
                let prefix = if item.is_dir() { "DIR  " } else { "FILE " };
                format!("{prefix}{}", self.relative(item))
            })
            .collect();
        if truncated {
            lines.push(format!("[limited to {MAX_LIST_ENTRIES} entries]"));
        }

        Ok(lines.join("\n"))
    }

    pub fn read_file(&self, path: &str, offset: usize, limit: Option<usize>) -> Result<String> {
        let target = self.resolve(path, true)?;
        if !target.is_file() {
            return Ok("File does not exist".into());
        }

        let size = fs::metadata(&target).map(|m| m.len()).unwrap_or(0);
        if size > MAX_READ_BYTES {
            return Ok(format!(
                "File too large ({size} bytes). Use search or read a range."
            ));
        }

        let bytes = match fs::read(&target) {
            Ok(bytes) => bytes,
            Err(e) => return Ok(format!("Cannot read file: {e}")),
        };
        let content = match String::from_utf8(bytes) {
            Ok(text) => text,
            Err(_) => return Ok("File is binary or is not valid UTF-8 text".into()),
        };

        if offset > 0 || limit.is_some() {
            let lines: Vec<&str> = content.lines().collect();
            let start = offset.min(lines.len());
            let end = match limit {
                Some(limit) => (start + limit.max(1)).min(lines.len()),
                None => lines.len(),
            };
            return Ok(lines[start..end].join("\n"));
        }

        Ok(content)
    }

    pub fn file_info(&self, path: &str) -> Result<String> {
        let target = self.resolve(path, true)?;
        if !target.is_file() {
            return Ok("File does not exist".into());
        }
        let metadata = fs::metadata(&target).map_err(Error::Io)?;
        let line_count = fs::read_to_string(&target)
            .map(|text| text.lines().count().to_string())
            .unwrap_or_else(|_| "unknown".into());
        let modified = metadata
            .modified()
            .ok()
            .map(|time| chrono::DateTime::<Utc>::from(time).to_rfc3339())
            .unwrap_or_else(|| "unknown".into());

        Ok([
            format!("Path: {}", self.relative(&target)),
            format!("Size: {} bytes", metadata.len()),
            format!("Lines: {line_count}"),
            format!(
                "Suffix: {}",
                target
                    .extension()
                    .map(|e| format!(".{}", e.to_string_lossy()))
                    .unwrap_or_else(|| "(none)".into())
            ),
            format!("Modified: {modified}"),
        ]
        .join("\n"))
    }

    pub fn tree(&self, path: &str, depth: usize) -> Result<String> {
        let depth = depth.clamp(1, 6);
        let base = self.resolve(path, false)?;
        if !base.is_dir() {
            return Ok("Path is not a directory".into());
        }

        let mut lines = vec![format!("{}/", self.relative(&base))];
        let mut truncated = false;

        'walk: for entry in WalkDir::new(&base)
            .min_depth(1)
            .max_depth(depth)
            .follow_links(false)
            .into_iter()
            .filter_entry(|entry| !is_skipped(entry.path()))
            .filter_map(|e| e.ok())
        {
            let level = entry.depth();
            let name = entry.file_name().to_string_lossy();
            let suffix = if entry.file_type().is_dir() { "/" } else { "" };
            lines.push(format!("{}{name}{suffix}", "  ".repeat(level)));
            if lines.len() >= MAX_LIST_ENTRIES {
                truncated = true;
                break 'walk;
            }
        }

        if truncated {
            lines.push("[truncated]".into());
        }
        Ok(lines.join("\n"))
    }

    // ------------------------------------------------------------------
    // Writes
    // ------------------------------------------------------------------
    pub fn write_file(&self, path: &str, content: &str) -> Result<String> {
        let target = self.resolve(path, false)?;
        self.backup(&target);

        if let Some(parent) = target.parent() {
            fs::create_dir_all(parent)
                .map_err(|e| Error::Workspace(format!("Cannot write file: {e}")))?;
        }

        let temporary = target.with_file_name(format!(
            "{}.freenerf.tmp",
            target
                .file_name()
                .map(|n| n.to_string_lossy().to_string())
                .unwrap_or_else(|| "file".into())
        ));

        let write = (|| -> std::io::Result<()> {
            let mut handle = fs::File::create(&temporary)?;
            handle.write_all(content.as_bytes())?;
            handle.sync_all()?;
            fs::rename(&temporary, &target)?;
            Ok(())
        })();

        if let Err(e) = write {
            let _ = fs::remove_file(&temporary);
            return Err(Error::Workspace(format!("Cannot write file: {e}")));
        }

        Ok(format!(
            "Written: {} ({} characters)",
            self.relative(&target),
            content.chars().count()
        ))
    }

    pub fn edit_file(
        &self,
        path: &str,
        old: &str,
        new: &str,
        replace_all: bool,
    ) -> Result<String> {
        let target = self.resolve(path, true)?;
        if !target.is_file() {
            return Ok("File does not exist".into());
        }
        if old.is_empty() {
            return Ok("The text to replace must not be empty".into());
        }

        let content = match fs::read_to_string(&target) {
            Ok(text) => text,
            Err(e) => return Ok(format!("Cannot read file: {e}")),
        };

        let occurrences = content.matches(old).count();
        if occurrences == 0 {
            return Ok("Text not found; the file was not modified".into());
        }
        if occurrences > 1 && !replace_all {
            return Ok(format!(
                "Text occurs {occurrences} times. Provide a more specific snippet or set replace_all."
            ));
        }

        let updated = if replace_all {
            content.replace(old, new)
        } else {
            content.replacen(old, new, 1)
        };

        self.backup(&target);
        fs::write(&target, updated).map_err(|e| Error::Workspace(format!("Cannot write file: {e}")))?;

        Ok(format!(
            "Edited: {} ({} replacement(s))",
            self.relative(&target),
            if replace_all { occurrences } else { 1 }
        ))
    }

    pub fn delete_file(&self, path: &str) -> Result<String> {
        let target = self.resolve(path, true)?;

        if target == self.root {
            return Err(Error::Permission("Cannot delete the workspace root".into()));
        }

        let relative = target.strip_prefix(&self.root).unwrap_or(&target);
        if let Some(first) = relative.components().next() {
            let head = first.as_os_str().to_string_lossy();
            if head == ".git" || head == ".freenerf" {
                return Err(Error::Permission(format!(
                    "Cannot delete files inside {head}/; it is protected project metadata \
                     and recovery data"
                )));
            }
        }

        if target.is_dir() {
            let empty = fs::read_dir(&target)
                .map(|mut it| it.next().is_none())
                .unwrap_or(false);
            if !empty {
                return Ok("Directory is not empty; refusing to delete it".into());
            }
        }

        self.backup(&target);
        let result = if target.is_dir() {
            fs::remove_dir(&target)
        } else {
            fs::remove_file(&target)
        };
        result.map_err(|e| Error::Workspace(format!("Cannot delete: {e}")))?;

        Ok(format!("Deleted: {}", self.relative(&target)))
    }

    // ------------------------------------------------------------------
    // Search
    // ------------------------------------------------------------------
    /// Literal, case-insensitive workspace search.
    ///
    /// The Python version shells out to ripgrep, which is not guaranteed to
    /// exist in an Android app sandbox. This implementation yields the same
    /// `path:line:text` output using a bounded in-process walk.
    pub fn search(&self, query: &str, scope: &str) -> Result<String> {
        let query = query.trim();
        if query.is_empty() {
            return Ok("Query is empty".into());
        }
        if scope != "source" && scope != "project" {
            return Ok("Invalid scope. Use source or project".into());
        }

        let needle = query.to_lowercase();
        let roots: Vec<PathBuf> = if scope == "source" {
            let candidates = [
                "src",
                "tests",
                "README.md",
                "INSTRUCTIONS.md",
                "pyproject.toml",
                ".gitignore",
                "native",
                "android",
                "docs",
            ];
            let found: Vec<PathBuf> = candidates
                .iter()
                .map(|item| self.root.join(item))
                .filter(|path| path.exists())
                .collect();
            if found.is_empty() {
                return Ok("No source paths found".into());
            }
            found
        } else {
            vec![self.root.clone()]
        };

        let mut lines: Vec<String> = Vec::new();
        let mut total_chars = 0usize;

        'outer: for root in roots {
            let walker = if root.is_file() {
                WalkDir::new(&root).max_depth(0)
            } else {
                WalkDir::new(&root).follow_links(false).max_depth(20)
            };

            for entry in walker
                .into_iter()
                .filter_entry(|entry| !is_skipped(entry.path()))
                .filter_map(|e| e.ok())
            {
                if !entry.file_type().is_file() {
                    continue;
                }
                let path = entry.path();
                if is_binary_extension(path) {
                    continue;
                }
                let Ok(metadata) = entry.metadata() else {
                    continue;
                };
                if metadata.len() > 200 * 1024 {
                    continue;
                }
                let Ok(text) = fs::read_to_string(path) else {
                    continue;
                };
                for (number, line) in text.lines().enumerate() {
                    if !line.to_lowercase().contains(&needle) {
                        continue;
                    }
                    let rendered = format!(
                        "{}:{}:{}",
                        self.relative(path),
                        number + 1,
                        line.trim_end()
                    );
                    total_chars += rendered.len();
                    lines.push(rendered);
                    if lines.len() >= MAX_SEARCH_LINES || total_chars >= MAX_SEARCH_CHARS {
                        break 'outer;
                    }
                }
            }
        }

        if lines.is_empty() {
            return Ok("No results".into());
        }
        let mut output = lines.join("\n");
        if output.len() > MAX_SEARCH_CHARS {
            output.truncate(MAX_SEARCH_CHARS);
        }
        Ok(output)
    }
}

fn resolve_lenient(target: &Path) -> Result<PathBuf> {
    if let Ok(resolved) = target.canonicalize() {
        return Ok(resolved);
    }

    let mut missing: Vec<std::ffi::OsString> = Vec::new();
    let mut current = target.to_path_buf();

    loop {
        if let Ok(canonical) = current.canonicalize() {
            let mut result = canonical;
            for part in missing.iter().rev() {
                result.push(part);
            }
            return Ok(result);
        }
        match (current.file_name(), current.parent()) {
            (Some(name), Some(parent)) => {
                missing.push(name.to_os_string());
                current = parent.to_path_buf();
            }
            _ => {
                return Err(Error::Workspace(format!(
                    "Cannot resolve path: {}",
                    target.display()
                )))
            }
        }
    }
}

fn is_skipped(path: &Path) -> bool {
    path.components().any(|component| {
        let name = component.as_os_str().to_string_lossy().to_string();
        SKIP_DIRECTORIES.iter().any(|skip| *skip == name)
    })
}

fn is_binary_extension(path: &Path) -> bool {
    matches!(
        path.extension().and_then(|e| e.to_str()).unwrap_or(""),
        "png" | "jpg" | "jpeg" | "gif" | "webp" | "pdf" | "zip" | "gz" | "db" | "so" | "apk"
            | "jar" | "ttf" | "otf" | "mp3" | "mp4" | "class" | "dex" | "woff" | "woff2"
    )
}

trait IsAncestor {
    fn is_ancestor_of(&self, other: &Path) -> bool;
}

impl IsAncestor for Path {
    fn is_ancestor_of(&self, other: &Path) -> bool {
        other.starts_with(self) && other != self
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn workspace() -> (tempfile::TempDir, Workspace) {
        let dir = tempfile::tempdir().unwrap();
        let workspace = Workspace::open(dir.path(), true).unwrap();
        workspace.ensure().unwrap();
        (dir, workspace)
    }

    #[test]
    fn rejects_paths_outside_the_workspace() {
        let (_dir, workspace) = workspace();
        assert!(workspace.resolve("../outside", false).is_err());
        assert!(workspace.resolve("/etc/passwd", false).is_err());
        assert!(workspace.resolve("inside.txt", false).is_ok());
    }

    #[test]
    fn writes_backup_and_edits() {
        let (_dir, workspace) = workspace();
        workspace.write_file("src/a.txt", "hello world").unwrap();
        workspace.edit_file("src/a.txt", "world", "there", false).unwrap();
        assert_eq!(workspace.read_file("src/a.txt", 0, None).unwrap(), "hello there");

        let backups = fs::read_dir(workspace.backups_dir())
            .unwrap()
            .flatten()
            .count();
        assert_eq!(backups, 1, "one backup per modifying operation on existing files");
    }

    #[test]
    fn edit_requires_disambiguation() {
        let (_dir, workspace) = workspace();
        workspace.write_file("a.txt", "x x").unwrap();
        let message = workspace.edit_file("a.txt", "x", "y", false).unwrap();
        assert!(message.contains("occurs 2 times"));
        workspace.edit_file("a.txt", "x", "y", true).unwrap();
        assert_eq!(workspace.read_file("a.txt", 0, None).unwrap(), "y y");
    }

    #[test]
    fn read_offset_and_limit() {
        let (_dir, workspace) = workspace();
        workspace.write_file("a.txt", "one\ntwo\nthree\n").unwrap();
        assert_eq!(workspace.read_file("a.txt", 1, Some(1)).unwrap(), "two");
    }

    #[test]
    fn delete_protects_metadata_and_non_empty_dirs() {
        let (_dir, workspace) = workspace();
        workspace.ensure().unwrap();
        assert!(workspace.delete_file(".freenerf").is_err());
        workspace.write_file("dir/a.txt", "a").unwrap();
        assert!(workspace.delete_file("dir").unwrap().contains("not empty"));
        workspace.delete_file("dir/a.txt").unwrap();
        workspace.delete_file("dir").unwrap();
        assert!(!workspace.root().join("dir").exists());
    }

    #[test]
    fn search_is_literal_case_insensitive_and_skips_build_dirs() {
        let (_dir, workspace) = workspace();
        workspace.write_file("src/lib.rs", "fn Hello() {}\n").unwrap();
        workspace.write_file("node_modules/x.js", "hello\n").unwrap();
        let result = workspace.search("hello", "project").unwrap();
        assert!(result.contains("src/lib.rs:1:"));
        assert!(!result.contains("node_modules"));
        assert_eq!(workspace.search("missing", "source").unwrap(), "No results");
    }
}
