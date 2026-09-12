//! Single-session PTY for the fixed `cloakcli tui` command.

use crate::env_inherit;
use portable_pty::{native_pty_system, CommandBuilder, MasterPty, PtySize};
use std::io::{Read, Write};
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter};

const KILL_TIMEOUT: Duration = Duration::from_secs(2);

pub struct PtySession {
    writer: Mutex<Box<dyn Write + Send>>,
    master: Box<dyn MasterPty + Send>,
    child: Box<dyn portable_pty::Child + Send + Sync>,
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
            if session
                .child
                .try_wait()
                .map_err(|e| e.to_string())?
                .is_none()
            {
                return Err("cloakcli tui is already running".into());
            }
            *guard = None;
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

    let child = pair
        .slave
        .spawn_command(cmd)
        .map_err(|e| format!("spawn cloakcli tui: {e}"))?;
    drop(pair.slave);

    let mut reader = pair
        .master
        .try_clone_reader()
        .map_err(|e| format!("pty reader: {e}"))?;
    let writer = pair
        .master
        .take_writer()
        .map_err(|e| format!("pty writer: {e}"))?;

    let session = PtySession {
        writer: Mutex::new(writer),
        master: pair.master,
        child,
    };

    {
        let mut guard = slot.lock().map_err(|e| e.to_string())?;
        *guard = Some(session);
    }

    let slot_read = Arc::clone(slot);
    let app_read = app.clone();
    thread::spawn(move || {
        let mut buf = [0u8; 8192];
        loop {
            match reader.read(&mut buf) {
                Ok(0) => break,
                Ok(n) => {
                    let text = String::from_utf8_lossy(&buf[..n]).into_owned();
                    let _ = app_read.emit("pty-data", text);
                }
                Err(_) => break,
            }
        }
        let _ = slot_read;
    });

    let slot_wait = Arc::clone(slot);
    thread::spawn(move || loop {
        thread::sleep(Duration::from_millis(50));
        let mut guard = match slot_wait.lock() {
            Ok(g) => g,
            Err(_) => break,
        };
        let Some(session) = guard.as_mut() else {
            break;
        };
        match session.child.try_wait() {
            Ok(Some(status)) => {
                *guard = None;
                drop(guard);
                let code = status.exit_code() as i32;
                let message = if status.success() {
                    "cloakcli tui exited".to_string()
                } else {
                    format!("cloakcli tui exited with status {code}")
                };
                let _ = app.emit(
                    "pty-exit",
                    serde_json::json!({ "code": code, "message": message }),
                );
                break;
            }
            Ok(None) => {}
            Err(_) => {
                *guard = None;
                drop(guard);
                let _ = app.emit(
                    "pty-exit",
                    serde_json::json!({
                        "code": null,
                        "message": "cloakcli tui wait failed"
                    }),
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

pub fn stop(slot: &SharedPty) -> Result<(), String> {
    let mut guard = slot.lock().map_err(|e| e.to_string())?;
    let Some(mut session) = guard.take() else {
        return Ok(());
    };
    drop(guard);
    terminate_session(&mut session);
    Ok(())
}

fn terminate_session(session: &mut PtySession) {
    if let Ok(Some(_)) = session.child.try_wait() {
        return;
    }

    if let Ok(mut writer) = session.writer.lock() {
        let _ = writer.write_all(&[0x03]); // ETX / Ctrl-C
        let _ = writer.flush();
    }

    if let Some(pid) = session.child.process_id() {
        send_term(pid);
    }

    let deadline = Instant::now() + KILL_TIMEOUT;
    while Instant::now() < deadline {
        if let Ok(Some(_)) = session.child.try_wait() {
            return;
        }
        thread::sleep(Duration::from_millis(50));
    }

    let _ = session.child.kill();
    let _ = session.child.wait();
}

fn send_term(pid: u32) {
    #[cfg(unix)]
    unsafe {
        let raw = pid as i32;
        libc::kill(raw, libc::SIGTERM);
        libc::kill(-raw, libc::SIGTERM);
    }
    #[cfg(windows)]
    {
        let _ = pid;
    }
}
