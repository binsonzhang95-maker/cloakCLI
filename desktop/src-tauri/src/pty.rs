//! Single-session PTY for the fixed `cloakcli tui` command.
//!
//! Cleanup is the same path for natural exit, `pty_stop`, spawn/reader/writer
//! failure, window close, and app exit. The session stays in the slot until
//! kill + wait finish.
//!
//! portable-pty `setsid`s the child, so the child's pid is the process-group
//! id. cloakcli's Python worker is spawned without `setsid`/`setpgid`, so it
//! stays in that group and is reaped by SIGTERM then SIGKILL of the group.
//!
//! Processes that *leave* the group (CloakBrowser/Chrome often daemonize) are
//! not covered by the group signal. Those are reclaimed from:
//! 1. a descendant/group pid snapshot taken while the session is alive, and
//! 2. `CLOAKCLI_HOME/data/worker.pid` when that pid appeared after this
//!    session started (pre-existing daemons are left alone).

use crate::env_inherit;
use portable_pty::{native_pty_system, CommandBuilder, MasterPty, PtySize};
use serde_json::json;
use std::collections::HashSet;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter};

const KILL_TIMEOUT: Duration = Duration::from_secs(2);

pub struct PtySession {
    writer: Mutex<Box<dyn Write + Send>>,
    master: Box<dyn MasterPty + Send>,
    child: Box<dyn portable_pty::Child + Send + Sync>,
    /// Unix process-group id. Equal to the child's pid after portable-pty `setsid`.
    pgid: Option<i32>,
    home: PathBuf,
    /// `data/worker.pid` at session start, if any. Unchanged pid = pre-existing.
    baseline_worker_pid: Option<u32>,
    seen_pids: HashSet<u32>,
}

pub type SharedPty = Arc<Mutex<Option<PtySession>>>;

pub fn is_running(slot: &SharedPty) -> bool {
    slot.lock().map(|g| g.is_some()).unwrap_or(false)
}

pub fn start(
    slot: &SharedPty,
    app: AppHandle,
    bin: &Path,
    home: &Path,
    cols: u16,
    rows: u16,
) -> Result<(), String> {
    {
        let mut guard = slot.lock().map_err(|e| e.to_string())?;
        if let Some(session) = guard.as_mut() {
            match session.child.try_wait() {
                Ok(None) => return Err("cloakcli tui is already running".into()),
                _ => {
                    // Stale slot (child already gone, or wait failed): reclaim
                    // the group before dropping the handle.
                    terminate_session(session);
                    *guard = None;
                }
            }
        }
    }

    let pty_system = native_pty_system();
    let pair = pty_system
        .openpty(PtySize {
            rows: rows.max(2),
            cols: cols.max(2),
            pixel_width: 0,
            pixel_height: 0,
        })
        .map_err(|e| format!("open pty: {e}"))?;

    let mut cmd = CommandBuilder::new(bin);
    cmd.arg("tui");
    cmd.env_clear();
    for (key, value) in env_inherit::inherited_env() {
        cmd.env(key, value);
    }
    cmd.env("CLOAKCLI_HOME", home.as_os_str());
    cmd.env("TERM", "xterm-256color");
    cmd.env("COLORTERM", "truecolor");

    let mut child = pair
        .slave
        .spawn_command(cmd)
        .map_err(|e| format!("spawn cloakcli tui: {e}"))?;
    drop(pair.slave);

    // After setsid in portable-pty pre_exec, the child is the session leader
    // so pgid == pid. Prefer that identity for group signals.
    let pid = child.process_id();
    let pgid = pid.map(|p| p as i32);
    let baseline_worker_pid = read_worker_pid(home);

    let reader = match pair.master.try_clone_reader() {
        Ok(r) => r,
        Err(e) => {
            reclaim_spawned(&mut child, pgid, home, baseline_worker_pid, pid);
            return Err(format!("pty reader: {e}"));
        }
    };
    let writer = match pair.master.take_writer() {
        Ok(w) => w,
        Err(e) => {
            reclaim_spawned(&mut child, pgid, home, baseline_worker_pid, pid);
            return Err(format!("pty writer: {e}"));
        }
    };

    let mut seen_pids = HashSet::new();
    if let Some(pid) = pid {
        seen_pids.insert(pid);
        seen_pids.extend(list_descendants(pid));
    }

    let session = PtySession {
        writer: Mutex::new(writer),
        master: pair.master,
        child,
        pgid,
        home: home.to_path_buf(),
        baseline_worker_pid,
        seen_pids,
    };

    match slot.lock() {
        Ok(mut guard) => {
            *guard = Some(session);
        }
        Err(e) => {
            let mut session = session;
            terminate_session(&mut session);
            return Err(e.to_string());
        }
    }

    let app_read = app.clone();
    let mut reader = reader;
    thread::spawn(move || {
        let mut buf = [0u8; 8192];
        let mut pending = Vec::new();
        loop {
            match reader.read(&mut buf) {
                Ok(0) => break,
                Ok(n) => {
                    let text = drain_utf8(&mut pending, &buf[..n]);
                    if !text.is_empty() {
                        let _ = app_read.emit("pty-data", text);
                    }
                }
                Err(_) => break,
            }
        }
        // Flush a replacement for a truncated sequence at EOF so we never
        // silently drop the last incomplete bytes.
        if !pending.is_empty() {
            let _ = app_read.emit("pty-data", "\u{FFFD}".repeat(pending.len().min(4)));
        }
    });

    let slot_wait = Arc::clone(slot);
    let app_wait = app.clone();
    thread::spawn(move || loop {
        thread::sleep(Duration::from_millis(50));
        let mut guard = match slot_wait.lock() {
            Ok(g) => g,
            Err(_) => break,
        };
        let Some(session) = guard.as_mut() else {
            break;
        };
        refresh_seen_pids(session);
        match session.child.try_wait() {
            Ok(Some(status)) => {
                // Child is reaped; still reclaim the rest of the group /
                // recorded workers before clearing the slot.
                terminate_session(session);
                *guard = None;
                drop(guard);
                let code = status.exit_code() as i32;
                let message = if status.success() {
                    "cloakcli tui exited".to_string()
                } else {
                    format!("cloakcli tui exited with status {code}")
                };
                emit_stopped(&app_wait, Some(code), &message, "exited");
                break;
            }
            Ok(None) => {}
            Err(_) => {
                terminate_session(session);
                *guard = None;
                drop(guard);
                emit_stopped(
                    &app_wait,
                    None,
                    "cloakcli tui wait failed",
                    "error",
                );
                break;
            }
        }
    });

    Ok(())
}

pub fn write_input(slot: &SharedPty, data: &str) -> Result<(), String> {
    let mut guard = slot.lock().map_err(|e| e.to_string())?;
    let session = guard.as_mut().ok_or("PTY is not running")?;
    let mut writer = session.writer.lock().map_err(|e| e.to_string())?;
    writer
        .write_all(data.as_bytes())
        .map_err(|e| format!("pty write: {e}"))?;
    writer.flush().ok();
    Ok(())
}

pub fn resize(slot: &SharedPty, cols: u16, rows: u16) -> Result<(), String> {
    let guard = slot.lock().map_err(|e| e.to_string())?;
    let session = guard.as_ref().ok_or("PTY is not running")?;
    session
        .master
        .resize(PtySize {
            rows: rows.max(2),
            cols: cols.max(2),
            pixel_width: 0,
            pixel_height: 0,
        })
        .map_err(|e| format!("pty resize: {e}"))
}

pub fn stop(slot: &SharedPty, app: Option<&AppHandle>) -> Result<(), String> {
    let mut guard = slot.lock().map_err(|e| e.to_string())?;
    let Some(session) = guard.as_mut() else {
        if let Some(app) = app {
            emit_stopped(app, None, "cloakcli tui is not running", "stopped");
        }
        return Ok(());
    };
    terminate_session(session);
    *guard = None;
    drop(guard);
    if let Some(app) = app {
        emit_stopped(app, None, "cloakcli tui stopped", "stopped");
    }
    Ok(())
}

fn emit_stopped(app: &AppHandle, code: Option<i32>, message: &str, reason: &str) {
    let payload = json!({
        "code": code,
        "message": message,
        "reason": reason,
        "running": false,
    });
    let _ = app.emit("pty-exit", &payload);
    let _ = app.emit("pty-status", &payload);
}

fn reclaim_spawned(
    child: &mut Box<dyn portable_pty::Child + Send + Sync>,
    pgid: Option<i32>,
    home: &Path,
    baseline_worker_pid: Option<u32>,
    pid: Option<u32>,
) {
    let mut extra = HashSet::new();
    if let Some(pid) = pid {
        extra.insert(pid);
        extra.extend(list_descendants(pid));
    }
    extra.extend(session_worker_pids(home, baseline_worker_pid));
    unix_or_kill_child(child, pgid, &extra, KILL_TIMEOUT);
}

fn terminate_session(session: &mut PtySession) {
    refresh_seen_pids(session);
    let mut extra = session.seen_pids.clone();
    extra.extend(session_worker_pids(
        &session.home,
        session.baseline_worker_pid,
    ));
    if let Some(pid) = session.child.process_id() {
        extra.insert(pid);
        extra.extend(list_descendants(pid));
    }

    let already_exited = matches!(session.child.try_wait(), Ok(Some(_)));
    if !already_exited {
        if let Ok(mut writer) = session.writer.lock() {
            let _ = writer.write_all(&[0x03]); // ETX / Ctrl-C
            let _ = writer.flush();
        }
    }

    unix_or_kill_child(&mut session.child, session.pgid, &extra, KILL_TIMEOUT);
}

fn unix_or_kill_child(
    child: &mut Box<dyn portable_pty::Child + Send + Sync>,
    pgid: Option<i32>,
    extra: &HashSet<u32>,
    timeout: Duration,
) {
    #[cfg(unix)]
    {
        reclaim_unix(child, pgid, extra, timeout);
    }
    #[cfg(not(unix))]
    {
        let _ = pgid;
        let _ = extra;
        let _ = timeout;
        if child.try_wait().ok().flatten().is_none() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

/// SIGTERM the process group, wait `timeout`, then SIGKILL the group and any
/// recorded pids that left the group. Always `wait` the direct child unless it
/// was already reaped, so it cannot stay a zombie.
#[cfg(unix)]
fn reclaim_unix(
    child: &mut Box<dyn portable_pty::Child + Send + Sync>,
    pgid: Option<i32>,
    extra: &HashSet<u32>,
    timeout: Duration,
) {
    let mut reaped = matches!(child.try_wait(), Ok(Some(_)));

    if let Some(pgid) = pgid {
        send_signal_group(pgid, libc::SIGTERM);
    }
    for pid in extra {
        send_signal_pid(*pid, libc::SIGTERM);
    }

    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if !reaped {
            if matches!(child.try_wait(), Ok(Some(_))) {
                reaped = true;
            }
        }
        let group_gone = pgid.map(|g| !group_alive(g)).unwrap_or(true);
        let extras_gone = extra.iter().all(|p| !pid_alive(*p));
        if reaped && group_gone && extras_gone {
            break;
        }
        thread::sleep(Duration::from_millis(50));
    }

    if let Some(pgid) = pgid {
        send_signal_group(pgid, libc::SIGKILL);
    }
    for pid in extra {
        send_signal_pid(*pid, libc::SIGKILL);
    }

    if !reaped {
        let _ = child.kill();
        let _ = child.wait();
    }

    if let Some(pgid) = pgid {
        reap_group_zombies(pgid);
    }
}

#[cfg(unix)]
fn send_signal_group(pgid: i32, sig: i32) {
    if pgid <= 1 {
        return;
    }
    unsafe {
        libc::kill(-pgid, sig);
    }
}

#[cfg(unix)]
fn send_signal_pid(pid: u32, sig: i32) {
    if !safe_to_signal(pid) {
        return;
    }
    unsafe {
        libc::kill(pid as i32, sig);
    }
}

#[cfg(unix)]
fn safe_to_signal(pid: u32) -> bool {
    pid > 1 && pid != std::process::id()
}

#[cfg(unix)]
fn pid_alive(pid: u32) -> bool {
    if !safe_to_signal(pid) && pid != std::process::id() {
        return false;
    }
    if pid == 0 {
        return false;
    }
    let rc = unsafe { libc::kill(pid as i32, 0) };
    if rc == 0 {
        return true;
    }
    matches!(
        std::io::Error::last_os_error().raw_os_error(),
        Some(libc::EPERM)
    )
}

#[cfg(unix)]
fn group_alive(pgid: i32) -> bool {
    if pgid <= 1 {
        return false;
    }
    let rc = unsafe { libc::kill(-pgid, 0) };
    if rc == 0 {
        return true;
    }
    matches!(
        std::io::Error::last_os_error().raw_os_error(),
        Some(libc::EPERM)
    )
}

/// Reap any remaining children in the group we are still parent of (WNOHANG).
#[cfg(unix)]
fn reap_group_zombies(pgid: i32) {
    if pgid <= 1 {
        return;
    }
    loop {
        let mut status = 0;
        let rc = unsafe { libc::waitpid(-pgid, &mut status, libc::WNOHANG) };
        if rc <= 0 {
            break;
        }
    }
}

fn refresh_seen_pids(session: &mut PtySession) {
    if let Some(pid) = session.child.process_id() {
        session.seen_pids.insert(pid);
        session.seen_pids.extend(list_descendants(pid));
    }
    if let Some(pgid) = session.pgid {
        session.seen_pids.extend(list_group(pgid as u32));
    }
    session
        .seen_pids
        .extend(session_worker_pids(&session.home, session.baseline_worker_pid));
}

fn read_worker_pid(home: &Path) -> Option<u32> {
    // Mirrors cloakcli `state::worker_pid_file` (`<home>/data/worker.pid`)
    // without depending on the CLI crate.
    let path = home.join("data").join("worker.pid");
    std::fs::read_to_string(path)
        .ok()?
        .trim()
        .parse()
        .ok()
}

fn session_worker_pids(home: &Path, baseline: Option<u32>) -> HashSet<u32> {
    let mut out = HashSet::new();
    let Some(pid) = read_worker_pid(home) else {
        return out;
    };
    if Some(pid) == baseline {
        return out;
    }
    #[cfg(unix)]
    {
        if !pid_alive(pid) {
            return out;
        }
    }
    out.insert(pid);
    out.extend(list_descendants(pid));
    out
}

#[cfg(target_os = "linux")]
fn list_descendants(root: u32) -> HashSet<u32> {
    let mut out = HashSet::new();
    let mut stack = vec![root];
    while let Some(pid) = stack.pop() {
        for child in children_of(pid) {
            if child > 1 && out.insert(child) {
                stack.push(child);
            }
        }
    }
    out
}

#[cfg(target_os = "linux")]
fn children_of(pid: u32) -> Vec<u32> {
    let path = format!("/proc/{pid}/task/{pid}/children");
    if let Ok(text) = std::fs::read_to_string(&path) {
        return text
            .split_whitespace()
            .filter_map(|s| s.parse().ok())
            .collect();
    }
    list_group_or_children_by_scan(None, Some(pid))
}

#[cfg(target_os = "linux")]
fn list_group(pgid: u32) -> HashSet<u32> {
    list_group_or_children_by_scan(Some(pgid), None)
        .into_iter()
        .collect()
}

/// Parse `/proc/*/stat` after the comm field: state ppid pgrp session ...
#[cfg(target_os = "linux")]
fn list_group_or_children_by_scan(pgid: Option<u32>, parent: Option<u32>) -> Vec<u32> {
    let mut out = Vec::new();
    let Ok(dir) = std::fs::read_dir("/proc") else {
        return out;
    };
    for entry in dir.flatten() {
        let pid: u32 = match entry.file_name().to_str().and_then(|s| s.parse().ok()) {
            Some(p) => p,
            None => continue,
        };
        let stat = match std::fs::read_to_string(entry.path().join("stat")) {
            Ok(s) => s,
            Err(_) => continue,
        };
        let Some(rest) = stat.rsplit_once(')').map(|(_, r)| r) else {
            continue;
        };
        let mut fields = rest.split_whitespace();
        let _state = fields.next();
        let ppid = fields.next().and_then(|s| s.parse::<u32>().ok());
        let pgrp = fields.next().and_then(|s| s.parse::<u32>().ok());
        let matches_group = pgid.is_some() && pgrp == pgid;
        let matches_parent = parent.is_some() && ppid == parent;
        if matches_group || matches_parent {
            out.push(pid);
        }
    }
    out
}

#[cfg(not(target_os = "linux"))]
fn list_descendants(_root: u32) -> HashSet<u32> {
    HashSet::new()
}

#[cfg(not(target_os = "linux"))]
fn list_group(_pgid: u32) -> HashSet<u32> {
    HashSet::new()
}

/// Decode `incoming` plus any leftover bytes from a previous read.
/// Incomplete UTF-8 at the chunk boundary is held back; invalid sequences
/// become U+FFFD. This is what keeps CJK / emoji from turning into
/// replacement characters when a multi-byte scalar is split across reads.
pub(crate) fn drain_utf8(pending: &mut Vec<u8>, incoming: &[u8]) -> String {
    pending.extend_from_slice(incoming);
    let mut out = String::new();
    loop {
        match std::str::from_utf8(pending) {
            Ok(s) => {
                out.push_str(s);
                pending.clear();
                break;
            }
            Err(e) => {
                let valid = e.valid_up_to();
                if valid > 0 {
                    let s = std::str::from_utf8(&pending[..valid])
                        .expect("valid_up_to is a UTF-8 boundary");
                    out.push_str(s);
                    pending.drain(..valid);
                    continue;
                }
                match e.error_len() {
                    Some(len) => {
                        out.push('\u{FFFD}');
                        let n = len.min(pending.len()).max(1);
                        pending.drain(..n);
                    }
                    None => break, // incomplete sequence at the end of the buffer
                }
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn utf8_cjk_split_across_chunks() {
        // "你好" = E4 BD A0 E5 A5 BD
        let bytes = "你好".as_bytes();
        assert_eq!(bytes.len(), 6);
        let mut pending = Vec::new();
        let first = drain_utf8(&mut pending, &bytes[..2]);
        assert_eq!(first, "");
        assert_eq!(pending.as_slice(), &bytes[..2]);
        let second = drain_utf8(&mut pending, &bytes[2..]);
        assert_eq!(second, "你好");
        assert!(pending.is_empty());
    }

    #[test]
    fn utf8_emoji_split_across_chunks() {
        // "😀" = F0 9F 98 80
        let bytes = "😀".as_bytes();
        assert_eq!(bytes.len(), 4);
        let mut pending = Vec::new();
        assert_eq!(drain_utf8(&mut pending, &bytes[..1]), "");
        assert_eq!(drain_utf8(&mut pending, &bytes[1..3]), "");
        assert_eq!(drain_utf8(&mut pending, &bytes[3..]), "😀");
        assert!(pending.is_empty());
    }

    #[test]
    fn utf8_mixed_ascii_and_split_cjk() {
        let mut pending = Vec::new();
        assert_eq!(drain_utf8(&mut pending, b"hi "), "hi ");
        let ni = "你".as_bytes();
        assert_eq!(drain_utf8(&mut pending, &ni[..1]), "");
        assert_eq!(drain_utf8(&mut pending, &ni[1..]), "你");
    }

    #[test]
    fn utf8_invalid_byte_is_replacement_not_held() {
        let mut pending = Vec::new();
        let out = drain_utf8(&mut pending, &[0x80, b'A']);
        assert!(out.contains('\u{FFFD}'));
        assert!(out.contains('A'));
        assert!(pending.is_empty());
    }

    #[cfg(unix)]
    #[test]
    fn process_group_sigkill_after_term_timeout() {
        use std::os::unix::process::CommandExt;
        use std::process::{Command, Stdio};

        let mut child = Command::new("sh")
            .args(["-c", "trap '' TERM; sleep 30"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .process_group(0)
            .spawn()
            .expect("spawn term-resistant sleep");
        let pid = child.id();
        let pgid = pid as i32;

        send_signal_group(pgid, libc::SIGTERM);
        thread::sleep(Duration::from_millis(150));
        assert!(
            pid_alive(pid),
            "child ignores SIGTERM and should still be alive before SIGKILL"
        );

        send_signal_group(pgid, libc::SIGKILL);
        let deadline = Instant::now() + Duration::from_secs(2);
        while Instant::now() < deadline {
            if !pid_alive(pid) {
                break;
            }
            thread::sleep(Duration::from_millis(20));
        }
        let _ = child.wait();
        assert!(!pid_alive(pid), "SIGKILL must reap the process group");
    }

    #[cfg(unix)]
    #[test]
    fn process_group_kills_grandchild_that_stays_in_group() {
        use std::os::unix::process::CommandExt;
        use std::process::{Command, Stdio};

        // Leader ignores TERM; grandchild stays in the same group and also
        // ignores TERM. Group SIGKILL must collect both.
        let mut child = Command::new("sh")
            .args([
                "-c",
                "trap '' TERM; sh -c \"trap '' TERM; sleep 30\" & wait",
            ])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .process_group(0)
            .spawn()
            .expect("spawn group");
        let pid = child.id();
        thread::sleep(Duration::from_millis(80));

        let extra: HashSet<u32> = list_descendants(pid);
        send_signal_group(pid as i32, libc::SIGTERM);
        thread::sleep(Duration::from_millis(120));
        send_signal_group(pid as i32, libc::SIGKILL);
        for p in extra.iter().chain(std::iter::once(&pid)) {
            send_signal_pid(*p, libc::SIGKILL);
        }
        let deadline = Instant::now() + Duration::from_secs(2);
        while Instant::now() < deadline {
            if !pid_alive(pid) && extra.iter().all(|p| !pid_alive(*p)) {
                break;
            }
            thread::sleep(Duration::from_millis(20));
        }
        let _ = child.wait();
        assert!(!pid_alive(pid), "leader must be gone");
        for p in extra {
            assert!(!pid_alive(p), "group member {p} must be gone");
        }
    }
}
