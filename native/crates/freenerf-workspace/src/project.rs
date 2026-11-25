//! Project inspection: languages, package manager, entry points and the
//! commands used by the `verify` tool.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde_json::{json, Value};
use walkdir::WalkDir;

const IGNORED_PARTS: &[&str] = &[
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".freenerf",
    "dist",
    "build",
    "target",
    ".next",
    ".gradle",
];

const SOURCE_LIMIT: usize = 4000;

#[derive(Debug, Clone)]
pub struct ProjectInspector {
    root: PathBuf,
}

impl ProjectInspector {
    pub fn new(root: impl AsRef<Path>) -> Self {
        Self {
            root: root.as_ref().to_path_buf(),
        }
    }

    fn exists(&self, name: &str) -> bool {
        self.root.join(name).exists()
    }

    fn read(&self, name: &str) -> String {
        std::fs::read_to_string(self.root.join(name)).unwrap_or_default()
    }

    fn language_of(path: &Path) -> &'static str {
        match path
            .extension()
            .and_then(|e| e.to_str())
            .unwrap_or("")
            .to_ascii_lowercase()
            .as_str()
        {
            "py" => "Python",
            "js" | "jsx" | "mjs" | "cjs" => "JavaScript",
            "ts" | "tsx" => "TypeScript",
            "go" => "Go",
            "rs" => "Rust",
            "java" => "Java",
            "kt" | "kts" => "Kotlin",
            "rb" => "Ruby",
            "php" => "PHP",
            "c" => "C",
            "h" | "cc" | "cpp" | "hpp" => "C/C++",
            "cs" => "C#",
            "sh" | "bash" => "Shell",
            "md" => "Markdown",
            _ => "Other",
        }
    }

    fn file_count(&self) -> (usize, BTreeMap<&'static str, usize>) {
        let mut counts: BTreeMap<&'static str, usize> = BTreeMap::new();
        let mut total = 0;
        for entry in WalkDir::new(&self.root)
            .follow_links(false)
            .into_iter()
            .filter_entry(|entry| {
                !entry.path().components().any(|component| {
                    let name = component.as_os_str().to_string_lossy().to_string();
                    IGNORED_PARTS.iter().any(|ignored| *ignored == name)
                })
            })
            .filter_map(|e| e.ok())
        {
            if !entry.file_type().is_file() {
                continue;
            }
            total += 1;
            *counts.entry(Self::language_of(entry.path())).or_insert(0) += 1;
            if total >= SOURCE_LIMIT {
                break;
            }
        }
        (total, counts)
    }

    fn package_manager(&self) -> &'static str {
        if self.exists("uv.lock") {
            "uv"
        } else if self.exists("poetry.lock") {
            "poetry"
        } else if self.exists("pdm.lock") {
            "pdm"
        } else if self.exists("pyproject.toml") {
            "pip/pyproject"
        } else if self.exists("package-lock.json") {
            "npm"
        } else if self.exists("pnpm-lock.yaml") {
            "pnpm"
        } else if self.exists("yarn.lock") {
            "yarn"
        } else if self.exists("bun.lockb") || self.exists("bun.lock") {
            "bun"
        } else if self.exists("Cargo.toml") {
            "cargo"
        } else if self.exists("go.mod") {
            "go modules"
        } else if self.exists("settings.gradle.kts") || self.exists("settings.gradle") {
            "gradle"
        } else {
            ""
        }
    }

    fn package_json(&self) -> Value {
        let text = self.read("package.json");
        serde_json::from_str(&text).unwrap_or(Value::Null)
    }

    fn python_has(&self, name: &str) -> bool {
        for filename in ["pyproject.toml", "requirements.txt", "setup.cfg"] {
            if self.exists(filename) && self.read(filename).to_lowercase().contains(name) {
                return true;
            }
        }
        false
    }

    pub fn test_command(&self) -> String {
        let package = self.package_json();
        let scripts = package.get("scripts").cloned().unwrap_or(Value::Null);
        if scripts.get("test").and_then(|v| v.as_str()).is_some() {
            return match self.package_manager() {
                "yarn" => "yarn test".into(),
                "pnpm" => "pnpm test".into(),
                "bun" => "bun test".into(),
                _ => "npm test".into(),
            };
        }

        if self.python_has("pytest") || self.root.join("tests").is_dir() {
            return "python -m pytest -q".into();
        }
        if self.exists("Cargo.toml") {
            return "cargo test --workspace".into();
        }
        if self.exists("go.mod") {
            return "go test ./...".into();
        }
        if self.exists("gradlew") {
            return "./gradlew test".into();
        }
        if self.exists("Makefile") && self.read("Makefile").contains("test:") {
            return "make test".into();
        }
        String::new()
    }

    pub fn lint_command(&self) -> String {
        let package = self.package_json();
        let scripts = package.get("scripts").cloned().unwrap_or(Value::Null);
        for name in ["lint", "typecheck", "check"] {
            if scripts.get(name).and_then(|v| v.as_str()).is_some() {
                return match self.package_manager() {
                    "yarn" => format!("yarn {name}"),
                    "pnpm" => format!("pnpm {name}"),
                    "bun" => format!("bun run {name}"),
                    _ => format!("npm run {name}"),
                };
            }
        }

        if self.python_has("ruff") {
            return "python -m ruff check .".into();
        }
        if self.python_has("flake8") {
            return "python -m flake8".into();
        }
        if self.exists("Cargo.toml") {
            return "cargo clippy".into();
        }
        if self.exists("go.mod") {
            return "go vet ./...".into();
        }
        if self.exists("gradlew") {
            return "./gradlew lint".into();
        }
        String::new()
    }

    pub fn entry_points(&self) -> Vec<String> {
        [
            "main.py",
            "app.py",
            "manage.py",
            "src/main.py",
            "index.js",
            "src/index.js",
            "src/index.ts",
            "src/main.ts",
            "src/main.rs",
            "main.go",
            "android/app/src/main/AndroidManifest.xml",
        ]
        .iter()
        .filter(|item| self.exists(item))
        .map(|item| (*item).to_string())
        .collect()
    }

    pub fn detect(&self) -> Value {
        let (files, counts) = self.file_count();
        let mut languages: Vec<(&'static str, usize)> = counts
            .into_iter()
            .filter(|(name, _)| *name != "Other")
            .collect();
        languages.sort_by(|a, b| b.1.cmp(&a.1));
        languages.truncate(6);

        json!({
            "root": self.root.to_string_lossy(),
            "files": files,
            "languages": languages
                .into_iter()
                .map(|(name, count)| json!({"name": name, "files": count}))
                .collect::<Vec<_>>(),
            "package_manager": self.package_manager(),
            "test_command": self.test_command(),
            "lint_command": self.lint_command(),
            "entry_points": self.entry_points(),
            "git": self.root.join(".git").exists(),
        })
    }

    pub fn summary(&self) -> String {
        let info = self.detect();
        let mut lines = vec![
            format!("Project root: {}", info["root"].as_str().unwrap_or("")),
            format!("Files scanned: {}", info["files"].as_u64().unwrap_or(0)),
        ];

        if let Some(languages) = info["languages"].as_array() {
            if !languages.is_empty() {
                let rendered = languages
                    .iter()
                    .map(|item| {
                        format!(
                            "{} ({})",
                            item["name"].as_str().unwrap_or(""),
                            item["files"].as_u64().unwrap_or(0)
                        )
                    })
                    .collect::<Vec<_>>()
                    .join(", ");
                lines.push(format!("Languages: {rendered}"));
            }
        }

        let field = |key: &str, fallback: &str| {
            info[key]
                .as_str()
                .filter(|value| !value.is_empty())
                .unwrap_or(fallback)
                .to_string()
        };
        lines.push(format!("Package manager: {}", field("package_manager", "unknown")));
        lines.push(format!("Test command: {}", field("test_command", "not detected")));
        lines.push(format!("Lint command: {}", field("lint_command", "not detected")));
        lines.push(format!(
            "Git repository: {}",
            if info["git"].as_bool().unwrap_or(false) { "yes" } else { "no" }
        ));

        if let Some(entries) = info["entry_points"].as_array() {
            if !entries.is_empty() {
                lines.push(format!(
                    "Entry points: {}",
                    entries
                        .iter()
                        .filter_map(|v| v.as_str())
                        .collect::<Vec<_>>()
                        .join(", ")
                ));
            }
        }

        lines.join("\n")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detects_python_projects() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("pyproject.toml"), "[project]\nname='x'").unwrap();
        std::fs::create_dir(dir.path().join("tests")).unwrap();
        std::fs::write(dir.path().join("src.py"), "print(1)").unwrap();
        let inspector = ProjectInspector::new(dir.path());
        assert_eq!(inspector.package_manager(), "pip/pyproject");
        assert_eq!(inspector.test_command(), "python -m pytest -q");
        assert!(inspector.summary().contains("Python (1)"));
    }

    #[test]
    fn detects_cargo_and_gradle() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("Cargo.toml"), "[workspace]").unwrap();
        let inspector = ProjectInspector::new(dir.path());
        assert_eq!(inspector.test_command(), "cargo test --workspace");
        assert_eq!(inspector.lint_command(), "cargo clippy");

        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("settings.gradle.kts"), "").unwrap();
        std::fs::write(dir.path().join("gradlew"), "").unwrap();
        let inspector = ProjectInspector::new(dir.path());
        assert_eq!(inspector.package_manager(), "gradle");
        assert_eq!(inspector.test_command(), "./gradlew test");
    }

    #[test]
    fn prefers_package_json_test_script() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(
            dir.path().join("package.json"),
            r#"{"scripts":{"test":"vitest","lint":"eslint ."}}"#,
        )
        .unwrap();
        std::fs::write(dir.path().join("package-lock.json"), "{}").unwrap();
        let inspector = ProjectInspector::new(dir.path());
        assert_eq!(inspector.test_command(), "npm test");
        assert_eq!(inspector.lint_command(), "npm run lint");
    }
}
