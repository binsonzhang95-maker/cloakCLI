use anyhow::{bail, Context, Result};
use std::env;
use std::path::{Path, PathBuf};

/// Resolve CloakCLI project root.
/// Priority: CLOAKCLI_HOME → walk up for Cargo.toml+skills/ → cwd if it looks right → error.
pub fn project_root() -> Result<PathBuf> {
    if let Ok(home) = env::var("CLOAKCLI_HOME") {
        let p = PathBuf::from(home);
        std::fs::create_dir_all(&p)?;
        return Ok(p);
    }

    let cwd = env::current_dir().context("current_dir")?;
    if let Some(found) = find_root(&cwd) {
        return Ok(found);
    }

    // Also try relative to the binary (dev convenience)
    if let Ok(exe) = env::current_exe() {
        if let Some(parent) = exe.parent() {
            // target/debug/cloakcli → climb
            for ancestor in parent.ancestors().take(5) {
                if looks_like_root(ancestor) {
                    return Ok(ancestor.to_path_buf());
                }
            }
        }
    }

    bail!(
        "Cannot find CloakCLI project root. Set CLOAKCLI_HOME or run from the repo \
         (needs Cargo.toml + skills/)."
    )
}

fn find_root(start: &Path) -> Option<PathBuf> {
    for p in start.ancestors() {
        if looks_like_root(p) {
            return Some(p.to_path_buf());
        }
    }
    None
}

fn looks_like_root(p: &Path) -> bool {
    (p.join("Cargo.toml").is_file() || p.join("python").join("pyproject.toml").is_file())
        && p.join("skills").is_dir()
}

pub fn profiles_dir(root: &Path) -> PathBuf {
    root.join("profiles")
}

pub fn skills_dir(root: &Path) -> PathBuf {
    root.join("skills")
}

pub fn data_dir(root: &Path) -> PathBuf {
    root.join("data")
}

pub fn profile_user_data_dir(root: &Path, name: &str) -> PathBuf {
    data_dir(root).join("profiles").join(name)
}

pub fn worker_sock(root: &Path) -> PathBuf {
    data_dir(root).join("worker.sock")
}

pub fn worker_pid_file(root: &Path) -> PathBuf {
    data_dir(root).join("worker.pid")
}

pub fn locks_dir(root: &Path) -> PathBuf {
    data_dir(root).join("locks")
}

pub fn profile_lock_dir(root: &Path, name: &str) -> PathBuf {
    locks_dir(root).join(format!("{name}.lock"))
}

pub fn default_headed() -> bool {
    match env::var("CLOAKCLI_HEADED") {
        Ok(v) => matches!(v.to_lowercase().as_str(), "1" | "true" | "yes" | "on"),
        Err(_) => {
            // macOS often headed for teach; Linux default headless
            cfg!(target_os = "macos")
        }
    }
}

/// Default IPC request timeout (seconds). Override with CLOAKCLI_IPC_TIMEOUT.
pub fn ipc_timeout_secs() -> u64 {
    env::var("CLOAKCLI_IPC_TIMEOUT")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(120)
}
