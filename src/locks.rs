//! Per-profile directory locks (mkdir-based) so concurrent jobs sharing a
//! Chromium user_data_dir cannot corrupt each other.

use anyhow::{bail, Result};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use crate::state;

pub struct ProfileLock {
    path: PathBuf,
}

impl ProfileLock {
    /// Block until the exclusive lock for `profile` is acquired (or timeout).
    pub async fn acquire(root: &Path, profile: &str, timeout: Duration) -> Result<Self> {
        let dir = state::locks_dir(root);
        fs::create_dir_all(&dir)?;
        let path = state::profile_lock_dir(root, profile);
        let deadline = Instant::now() + timeout;

        loop {
            match try_acquire(&path) {
                Ok(lock) => return Ok(lock),
                Err(_) => {
                    // Stale? if pid file points to dead process, reclaim
                    if is_stale(&path) {
                        let _ = fs::remove_dir_all(&path);
                        continue;
                    }
                    if Instant::now() >= deadline {
                        bail!(
                            "timeout waiting for profile lock '{profile}' ({})",
                            path.display()
                        );
                    }
                    tokio::time::sleep(Duration::from_millis(100)).await;
                }
            }
        }
    }
}

impl Drop for ProfileLock {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}

fn try_acquire(path: &Path) -> Result<ProfileLock> {
    fs::create_dir(path)?;
    let _ = fs::write(path.join("pid"), std::process::id().to_string());
    Ok(ProfileLock {
        path: path.to_path_buf(),
    })
}

fn is_stale(path: &Path) -> bool {
    let pid_path = path.join("pid");
    let Ok(text) = fs::read_to_string(&pid_path) else {
        // no pid → treat as stale if lock dir older logic: if empty-ish, reclaim
        return true;
    };
    let Ok(pid) = text.trim().parse::<i32>() else {
        return true;
    };
    // signal 0: check if process exists
    let alive = libc_kill(pid, 0) == 0;
    !alive
}

/// Avoid depending on the `libc` crate: use `kill -0` via /proc on Linux.
fn libc_kill(pid: i32, _sig: i32) -> i32 {
    if pid <= 0 {
        return -1;
    }
    if Path::new(&format!("/proc/{pid}")).exists() {
        0
    } else {
        -1
    }
}
