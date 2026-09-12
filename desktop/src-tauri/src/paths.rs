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
    let canon = validate_existing_path(raw, "CLOAKCLI_BIN", false)?;
    if !is_executable_file(&canon) {
        return Err(format!(
            "CLOAKCLI_BIN is not executable: {}",
            canon.display()
        ));
    }
    Ok(canon)
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
        // Trusted local override, but still absolute + existing + executable
        // after canonicalize. Relative values are rejected.
        return validate_bin(&value);
    }

    for candidate in sidecar_candidates(current_exe) {
        if let Some(ok) = accept_executable(&candidate) {
            return Ok(ok);
        }
    }

    for ancestor in current_exe.ancestors().take(8) {
        for profile in ["debug", "release"] {
            for name in BIN_NAMES {
                let candidate = ancestor.join("target").join(profile).join(name);
                if let Some(ok) = accept_executable(&candidate) {
                    return Ok(ok);
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

/// Sidecar locations next to the desktop executable.
///
/// macOS `.app` layout:
/// - `Foo.app/Contents/MacOS/cloakcli`
/// - `Foo.app/Contents/Resources/cloakcli`
/// - `Foo.app/cloakcli`
/// - directory *beside* the `.app` (`…/cloakcli`) — one more parent than
///   `Contents` so a sibling binary of the bundle is found.
pub(crate) fn sidecar_candidates(current_exe: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    let Some(dir) = current_exe.parent() else {
        return out;
    };
    for name in BIN_NAMES {
        out.push(dir.join(name));
    }
    if dir.ends_with("MacOS") {
        if let Some(contents) = dir.parent() {
            let resources = contents.join("Resources");
            for name in BIN_NAMES {
                out.push(resources.join(name));
            }
            if let Some(app_bundle) = contents.parent() {
                for name in BIN_NAMES {
                    out.push(app_bundle.join(name));
                }
                if let Some(beside_app) = app_bundle.parent() {
                    for name in BIN_NAMES {
                        out.push(beside_app.join(name));
                    }
                }
            }
        }
    }
    out
}

fn is_executable_file(path: &Path) -> bool {
    if !path.is_file() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        path.metadata()
            .map(|m| m.permissions().mode() & 0o111 != 0)
            .unwrap_or(false)
    }
    #[cfg(not(unix))]
    {
        true
    }
}

/// Keep a candidate only when it is absolute, `canonicalize` succeeds, and
/// the resolved path is an executable file. Failed canonicalize is a drop,
/// not a fallback to the original path.
pub(crate) fn accept_executable(path: &Path) -> Option<PathBuf> {
    if !path.is_absolute() {
        return None;
    }
    let canon = path.canonicalize().ok()?;
    if !canon.is_absolute() {
        return None;
    }
    if !is_executable_file(&canon) {
        return None;
    }
    Some(canon)
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
        // Empty PATH component means "current directory" on Unix and Windows.
        // Relative entries also resolve against cwd. Reject both so we never
        // exec a namesake from the process working directory.
        if dir.as_os_str().is_empty() || !dir.is_absolute() {
            continue;
        }
        let candidate = dir.join(name);
        if let Some(ok) = accept_executable(&candidate) {
            return Some(ok);
        }
        if cfg!(windows) {
            let mut exe = candidate;
            exe.set_extension("exe");
            if let Some(ok) = accept_executable(&exe) {
                return Some(ok);
            }
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::sync::Mutex;

    static ENV_LOCK: Mutex<()> = Mutex::new(());

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

    #[cfg(unix)]
    #[test]
    fn rejects_non_executable_bin() {
        use std::os::unix::fs::PermissionsExt;
        let dir = temp_dir();
        let file = dir.join("cloakcli");
        fs::write(&file, b"#!/bin/sh\necho no\n").unwrap();
        fs::set_permissions(&file, fs::Permissions::from_mode(0o644)).unwrap();
        let err = validate_bin(file.to_str().unwrap()).unwrap_err();
        assert!(err.contains("not executable"), "{err}");
        assert!(accept_executable(&file).is_none());
        fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn accept_executable_drops_failed_canonicalize() {
        let missing = PathBuf::from("/definitely/not/a/cloakcli/bin/that/does/not/exist");
        assert!(accept_executable(&missing).is_none());
        assert!(accept_executable(Path::new("relative/cloakcli")).is_none());
        assert!(accept_executable(Path::new("")).is_none());
    }

    #[test]
    fn search_path_skips_empty_and_relative_entries() {
        let _guard = ENV_LOCK.lock().unwrap();
        let cwd = temp_dir();
        let abs = temp_dir();
        let name = "cloakcli-path-probe";
        let cwd_bin = cwd.join(name);
        let abs_bin = abs.join(name);
        fs::write(&cwd_bin, b"#!/bin/sh\n").unwrap();
        fs::write(&abs_bin, b"#!/bin/sh\n").unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&cwd_bin, fs::Permissions::from_mode(0o755)).unwrap();
            fs::set_permissions(&abs_bin, fs::Permissions::from_mode(0o755)).unwrap();
        }

        let old_cwd = env::current_dir().unwrap();
        let old_path = env::var_os("PATH");
        env::set_current_dir(&cwd).unwrap();

        env::set_var("PATH", ":.:./rel:relative");
        assert!(
            search_path(name).is_none(),
            "empty/relative PATH must not exec from cwd"
        );

        env::set_var("PATH", format!(":./rel:{}", abs.display()));
        let found = search_path(name).expect("absolute PATH entry should win");
        assert_eq!(found, abs_bin.canonicalize().unwrap());

        match old_path {
            Some(p) => env::set_var("PATH", p),
            None => env::remove_var("PATH"),
        }
        let _ = env::set_current_dir(old_cwd);
        fs::remove_dir_all(&cwd).ok();
        fs::remove_dir_all(&abs).ok();
    }

    #[test]
    fn macos_sidecar_walks_beside_app_bundle() {
        let exe = PathBuf::from("/Applications/CloakCLI.app/Contents/MacOS/cloakcli-desktop");
        let cands = sidecar_candidates(&exe);
        let as_str: Vec<String> = cands
            .iter()
            .map(|p| p.to_string_lossy().into_owned())
            .collect();
        assert!(
            as_str.iter().any(|p| p.ends_with("/Contents/MacOS/cloakcli")),
            "{as_str:?}"
        );
        assert!(
            as_str
                .iter()
                .any(|p| p.ends_with("/Contents/Resources/cloakcli")),
            "{as_str:?}"
        );
        assert!(
            as_str
                .iter()
                .any(|p| p == "/Applications/CloakCLI.app/cloakcli"),
            "{as_str:?}"
        );
        assert!(
            as_str.iter().any(|p| p == "/Applications/cloakcli"),
            "must walk one parent past the .app: {as_str:?}"
        );
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
