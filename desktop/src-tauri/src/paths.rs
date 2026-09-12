//! Resolve and validate `cloakcli` binary + `CLOAKCLI_HOME`.
//! Frontend cannot choose the command; it can only supply a home directory.

use serde::{Deserialize, Serialize};
use std::env;
use std::fs;
use std::path::{Component, Path, PathBuf};

const BIN_NAMES: &[&str] = if cfg!(windows) {
    &["cloakcli.exe", "cloakcli"]
} else {
    &["cloakcli"]
};

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct StoredConfig {
    pub cloakcli_home: Option<String>,
}

pub fn config_file(config_dir: &Path) -> PathBuf {
    config_dir.join("home.json")
}

pub fn load_stored_config(config_dir: &Path) -> StoredConfig {
    let path = config_file(config_dir);
    let Ok(bytes) = fs::read(&path) else {
        return StoredConfig::default();
    };
    serde_json::from_slice(&bytes).unwrap_or_default()
}

pub fn save_stored_home(config_dir: &Path, home: &Path) -> Result<(), String> {
    fs::create_dir_all(config_dir).map_err(|e| format!("cannot create config dir: {e}"))?;
    let mut cfg = load_stored_config(config_dir);
    cfg.cloakcli_home = Some(home.to_string_lossy().into_owned());
    let json = serde_json::to_vec_pretty(&cfg).map_err(|e| e.to_string())?;
    fs::write(config_file(config_dir), json).map_err(|e| format!("cannot write config: {e}"))
}

pub fn validate_home(raw: &str) -> Result<PathBuf, String> {
    validate_existing_path(raw, "CLOAKCLI_HOME", true)
}

pub fn validate_bin(raw: &str) -> Result<PathBuf, String> {
    validate_existing_path(raw, "CLOAKCLI_BIN", false)
}

fn validate_existing_path(raw: &str, label: &str, must_be_dir: bool) -> Result<PathBuf, String> {
    let raw = raw.trim();
    if raw.is_empty() {
        return Err(format!("{label} is empty"));
    }
    if raw.contains('\0') {
        return Err(format!("{label} contains invalid characters"));
    }
    let path = Path::new(raw);
    if !path.is_absolute() {
        return Err(format!("{label} must be an absolute path"));
    }
    for component in path.components() {
        match component {
            Component::ParentDir | Component::CurDir => {
                return Err(format!(
                    "{label} must not contain '.' or '..' segments (got {raw})"
                ));
            }
            _ => {}
        }
    }
    let canon = path.canonicalize().map_err(|e| {
        format!("{label} does not exist or cannot be resolved ({raw}): {e}")
    })?;
    if must_be_dir {
        if !canon.is_dir() {
            return Err(format!("{label} is not a directory: {}", canon.display()));
        }
    } else if !canon.is_file() {
        return Err(format!("{label} is not a file: {}", canon.display()));
    }
    Ok(canon)
}

pub fn resolve_home(
    stored: Option<&str>,
    current_exe: &Path,
) -> Result<PathBuf, String> {
    if let Ok(value) = env::var("CLOAKCLI_HOME") {
        return validate_home(&value);
    }
    if let Some(stored) = stored {
        if !stored.trim().is_empty() {
            return validate_home(stored);
        }
    }
    if let Some(root) = discover_repo_root(current_exe) {
        return Ok(root);
    }
    Err(
        "CLOAKCLI_HOME is not set. Provide an absolute existing directory \
         (env CLOAKCLI_HOME or the in-app field)."
            .into(),
    )
}

pub fn resolve_bin(current_exe: &Path) -> Result<PathBuf, String> {
    if let Ok(value) = env::var("CLOAKCLI_BIN") {
        return validate_bin(&value).map_err(|e| format!("{e}"));
    }

    if let Some(dir) = current_exe.parent() {
        for name in BIN_NAMES {
            let candidate = dir.join(name);
            if candidate.is_file() {
                return Ok(candidate.canonicalize().unwrap_or(candidate));
            }
        }
        // macOS app bundle: Contents/MacOS/<app> → Contents/MacOS/cloakcli already covered;
        // also look in Contents/Resources and next to the .app
        if dir.ends_with("MacOS") {
            if let Some(contents) = dir.parent() {
                let resources = contents.join("Resources");
                for name in BIN_NAMES {
                    let candidate = resources.join(name);
                    if candidate.is_file() {
                        return Ok(candidate.canonicalize().unwrap_or(candidate));
                    }
                }
                if let Some(app_parent) = contents.parent() {
                    for name in BIN_NAMES {
                        let candidate = app_parent.join(name);
                        if candidate.is_file() {
                            return Ok(candidate.canonicalize().unwrap_or(candidate));
                        }
                    }
                }
            }
        }
    }

    for ancestor in current_exe.ancestors().take(8) {
        for profile in ["debug", "release"] {
            for name in BIN_NAMES {
                let candidate = ancestor.join("target").join(profile).join(name);
                if candidate.is_file() {
                    return Ok(candidate.canonicalize().unwrap_or(candidate));
                }
            }
        }
    }

    if let Some(found) = search_path("cloakcli") {
        return Ok(found);
    }

    Err(
        "cloakcli binary not found. Set CLOAKCLI_BIN to an absolute path, \
         install cloakcli on PATH, or place it next to this app."
            .into(),
    )
}

fn discover_repo_root(current_exe: &Path) -> Option<PathBuf> {
    for ancestor in current_exe.ancestors().take(8) {
        if looks_like_root(ancestor) {
            return Some(ancestor.to_path_buf());
        }
    }
    None
}

fn looks_like_root(path: &Path) -> bool {
    (path.join("Cargo.toml").is_file() || path.join("python").join("pyproject.toml").is_file())
        && path.join("skills").is_dir()
}

fn search_path(name: &str) -> Option<PathBuf> {
    let path_var = env::var_os("PATH")?;
    for dir in env::split_paths(&path_var) {
        let mut candidate = dir.join(name);
        if candidate.is_file() {
            return Some(candidate.canonicalize().unwrap_or(candidate));
        }
        if cfg!(windows) {
            candidate.set_extension("exe");
            if candidate.is_file() {
                return Some(candidate.canonicalize().unwrap_or(candidate));
            }
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn temp_dir() -> PathBuf {
        let base = env::temp_dir().join(format!(
            "cloakcli-desktop-test-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&base).unwrap();
        base
    }

    #[test]
    fn rejects_empty_home() {
        let err = validate_home("").unwrap_err();
        assert!(err.contains("empty"), "{err}");
    }

    #[test]
    fn rejects_relative_home() {
        let err = validate_home("relative/path").unwrap_err();
        assert!(err.contains("absolute"), "{err}");
        let err = validate_home("./here").unwrap_err();
        assert!(err.contains("absolute") || err.contains("..") || err.contains("."), "{err}");
    }

    #[test]
    fn rejects_traversal_home() {
        let err = validate_home("/tmp/foo/../etc").unwrap_err();
        assert!(err.contains(".."), "{err}");
    }

    #[test]
    fn rejects_missing_home() {
        let err = validate_home("/definitely/not/a/cloakcli/home/dir").unwrap_err();
        assert!(err.contains("does not exist") || err.contains("cannot be resolved"), "{err}");
    }

    #[test]
    fn accepts_existing_absolute_dir() {
        let dir = temp_dir();
        let got = validate_home(dir.to_str().unwrap()).unwrap();
        assert_eq!(got, dir.canonicalize().unwrap());
        fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn rejects_file_as_home() {
        let dir = temp_dir();
        let file = dir.join("not-a-dir");
        fs::write(&file, b"x").unwrap();
        let err = validate_home(file.to_str().unwrap()).unwrap_err();
        assert!(err.contains("not a directory"), "{err}");
        fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn rejects_relative_bin() {
        let err = validate_bin("cloakcli").unwrap_err();
        assert!(err.contains("absolute"), "{err}");
    }

    #[test]
    fn stored_config_roundtrip() {
        let dir = temp_dir();
        save_stored_home(&dir, Path::new("/usr")).unwrap();
        let cfg = load_stored_config(&dir);
        assert_eq!(cfg.cloakcli_home.as_deref(), Some("/usr"));
        fs::remove_dir_all(&dir).ok();
    }
}
