//! Subprocess adapter: `cloakcli teach chat --events` JSONL.
//!
//! The desktop crate does not path-dep on `cloakcli`. This module only
//! execs the resolved binary with a fixed argv, parses structured events,
//! and redacts free-text before it reaches the WebView.

use crate::env_inherit;
use crate::redact::redact_text;
use crate::teach_event;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use tauri::{AppHandle, Emitter};

const START_WAIT: Duration = Duration::from_secs(8);
const KILL_TIMEOUT: Duration = Duration::from_secs(2);

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TeachStartDto {
    pub running: bool,
    pub profile: String,
    pub spawn_browser: bool,
    pub session_id: Option<String>,
    pub pairing_id: Option<String>,
    pub pairing_code: Option<String>,
    pub hub_url: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TeachStatusDto {
    pub running: bool,
    pub profile: Option<String>,
    pub spawn_browser: bool,
    pub phase: Option<String>,
    pub hub: bool,
    pub worker: bool,
    pub extension: bool,
    pub busy: bool,
    pub last_request_id: Option<String>,
    pub error: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JobStartDto {
    pub wired: bool,
    pub via: String,
    pub hint: String,
}

pub struct TeachSession {
    child: Child,
    stdin: ChildStdin,
    pgid: Option<i32>,
    #[allow(dead_code)]
    home: PathBuf,
    profile: String,
    spawn_browser: bool,
    last_status: TeachStatusDto,
    last_session: Option<TeachStartDto>,
    #[allow(dead_code)]
    stderr_tail: Arc<Mutex<String>>,
}

pub type SharedTeach = Arc<Mutex<Option<TeachSession>>>;

pub fn default_spawn_browser() -> bool {
    env_nonempty("DISPLAY") || env_nonempty("WAYLAND_DISPLAY")
}

fn env_nonempty(name: &str) -> bool {
    std::env::var(name).map(|s| !s.is_empty()).unwrap_or(false)
}

/// Fixed argv for the cloakcli teach-chat events child. Never a shell.
pub fn argv(profile: &str, url: Option<&str>, spawn_browser: bool) -> Result<Vec<String>, String> {
    validate_profile_name(profile)?;
    let mut args = vec![
        "teach".into(),
        "chat".into(),
        "--events".into(),
        "--profile".into(),
        profile.to_string(),
    ];
    if let Some(u) = url.map(str::trim).filter(|s| !s.is_empty()) {
        validate_start_url(u)?;
        args.push("--url".into());
        args.push(u.to_string());
    }
    if !spawn_browser {
        args.push("--no-browser".into());
    }
    Ok(args)
}

pub fn validate_profile_name(name: &str) -> Result<(), String> {
    let ok = !name.is_empty()
        && name.len() <= 64
        && name.chars().enumerate().all(|(i, c)| {
            if i == 0 {
                c.is_ascii_alphanumeric()
            } else {
                c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-')
            }
        })
        && !name.contains("..")
        && !name.contains('/')
        && !name.contains('\\');
    if !ok {
        return Err(
            "Invalid profile name. Use 1-64 chars: letters, digits, . _ - (start alphanumeric)."
                .into(),
        );
    }
    Ok(())
}

pub fn validate_start_url(url: &str) -> Result<(), String> {
    let url = url.trim();
    if url.is_empty() {
        return Err("url is empty".into());
    }
    if url.len() > 2048 {
        return Err("url exceeds 2048 chars".into());
    }
    if url.contains('\0') || url.chars().any(|c| c.is_control()) {
        return Err("url contains invalid characters".into());
    }
    let lower = url.to_ascii_lowercase();
    if lower.starts_with("javascript:")
        || lower.starts_with("data:")
        || lower.starts_with("file:")
        || lower.starts_with("vbscript:")
    {
        return Err("url scheme rejected".into());
    }
    if !(lower.starts_with("http://") || lower.starts_with("https://")) {
        return Err("url must be http(s)".into());
    }
    Ok(())
}

pub fn redact_event(mut v: Value) -> Value {
    redact_value(&mut v);
    v
}

fn redact_value(v: &mut Value) {
    match v {
        Value::String(s) => *s = redact_text(s),
        Value::Array(arr) => {
            for item in arr {
                redact_value(item);
            }
        }
        Value::Object(map) => {
            for (k, item) in map.iter_mut() {
                if matches!(
                    k.as_str(),
                    "kind"
                        | "v"
                        | "role"
                        | "code"
                        | "phase"
                        | "state"
                        | "job_id"
                        | "session_id"
                        | "pairing_id"
                        | "pairing_code"
                        | "hub_url"
                        | "profile"
                        | "spawn_browser"
                        | "hub"
                        | "worker"
                        | "extension"
                        | "busy"
                        | "ok"
                        | "mode"
                        | "last_request_id"
                        | "running"
                        | "wired"
                        | "via"
                        | "seq"
                        | "done"
                        | "hub_resume"
                ) {
                    continue;
                }
                redact_value(item);
            }
        }
        _ => {}
    }
}

pub fn start(
    slot: &SharedTeach,
    app: AppHandle,
    bin: &Path,
    home: &Path,
    profile: &str,
    url: Option<&str>,
    spawn_browser: bool,
) -> Result<TeachStartDto, String> {
    {
        let mut guard = slot.lock().map_err(|e| e.to_string())?;
        if let Some(session) = guard.as_mut() {
            match session.child.try_wait() {
                Ok(None) => {
                    if session.profile == profile && session.spawn_browser == spawn_browser {
                        if let Some(dto) = session.last_session.clone() {
                            return Ok(dto);
                        }
                    }
                    terminate_session(session);
                    *guard = None;
                }
                _ => {
                    terminate_session(session);
                    *guard = None;
                }
            }
        }
    }

    let args = argv(profile, url, spawn_browser)?;
    let mut cmd = Command::new(bin);
    cmd.args(&args);
    cmd.env_clear();
    for (key, value) in env_inherit::inherited_env() {
        cmd.env(key, value);
    }
    cmd.env("CLOAKCLI_HOME", home.as_os_str());
    cmd.env("PYTHONUNBUFFERED", "1");
    cmd.stdin(Stdio::piped());
    cmd.stdout(Stdio::piped());
    cmd.stderr(Stdio::piped());
    cmd.current_dir(home);

    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        unsafe {
            cmd.pre_exec(|| {
                libc::setpgid(0, 0);
                Ok(())
            });
        }
    }

    let mut child = cmd
        .spawn()
        .map_err(|e| format!("spawn cloakcli teach chat: {e}"))?;
    let pid = child.id();
    let pgid = Some(pid as i32);
    let stdin = child.stdin.take().ok_or("teach stdin missing")?;
    let stdout = child.stdout.take().ok_or("teach stdout missing")?;
    let stderr = child.stderr.take().ok_or("teach stderr missing")?;

    let stderr_tail = Arc::new(Mutex::new(String::new()));
    let stderr_tail_r = stderr_tail.clone();
    thread::spawn(move || {
        let reader = BufReader::new(stderr);
        for line in reader.lines().map_while(Result::ok) {
            let red = redact_text(&line);
            if let Ok(mut g) = stderr_tail_r.lock() {
                let n = g.len();
                if n > 4000 {
                    g.drain(..n - 2000);
                }
                g.push_str(&red);
                g.push('\n');
            }
        }
    });

    let first = Arc::new(Mutex::new(None::<TeachStartDto>));
    let first_w = first.clone();
    let slot_r = slot.clone();
    let app_r = app.clone();
    thread::spawn(move || {
        let reader = BufReader::new(stdout);
        for line in reader.lines().map_while(Result::ok) {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            let raw = match teach_event::parse_event_line(line) {
                Ok(v) => v,
                Err(e) => {
                    let payload = teach_event::bad_event_payload(&redact_text(&e));
                    let _ = app_r.emit("teach_chat_event", &payload);
                    continue;
                }
            };
            let event = redact_event(raw);
            if event.get("kind").and_then(|k| k.as_str()) == Some("session") {
                let dto = TeachStartDto {
                    running: true,
                    profile: event
                        .get("profile")
                        .and_then(|p| p.as_str())
                        .unwrap_or("")
                        .to_string(),
                    spawn_browser: event
                        .get("spawn_browser")
                        .and_then(|b| b.as_bool())
                        .unwrap_or(false),
                    session_id: event
                        .get("session_id")
                        .and_then(|s| s.as_str())
                        .map(|s| s.to_string()),
                    pairing_id: event
                        .get("pairing_id")
                        .and_then(|s| s.as_str())
                        .map(|s| s.to_string()),
                    pairing_code: event
                        .get("pairing_code")
                        .and_then(|s| s.as_str())
                        .map(|s| s.to_string()),
                    hub_url: event
                        .get("hub_url")
                        .and_then(|s| s.as_str())
                        .map(|s| s.to_string()),
                };
                if let Ok(mut g) = first_w.lock() {
                    *g = Some(dto.clone());
                }
                if let Ok(mut slot) = slot_r.lock() {
                    if let Some(sess) = slot.as_mut() {
                        sess.last_session = Some(dto);
                    }
                }
            }
            if event.get("kind").and_then(|k| k.as_str()) == Some("status") {
                if let Ok(mut slot) = slot_r.lock() {
                    if let Some(sess) = slot.as_mut() {
                        sess.last_status = status_from_event(&event, &sess.last_status);
                    }
                }
            }
            let _ = app_r.emit("teach_chat_event", &event);
        }
        let closed = json!({
            "v": 1,
            "kind": "closed",
            "reason": "child_exit",
            "profile": "",
        });
        let _ = app_r.emit("teach_chat_event", &closed);
    });

    let deadline = Instant::now() + START_WAIT;
    loop {
        if let Ok(g) = first.lock() {
            if let Some(dto) = g.clone() {
                let status = TeachStatusDto {
                    running: true,
                    profile: Some(profile.to_string()),
                    spawn_browser,
                    phase: Some("chat".into()),
                    hub: true,
                    worker: false,
                    extension: false,
                    busy: false,
                    last_request_id: None,
                    error: None,
                };
                let mut guard = slot.lock().map_err(|e| e.to_string())?;
                *guard = Some(TeachSession {
                    child,
                    stdin,
                    pgid,
                    home: home.to_path_buf(),
                    profile: profile.to_string(),
                    spawn_browser,
                    last_status: status,
                    last_session: Some(dto.clone()),
                    stderr_tail,
                });
                return Ok(dto);
            }
        }
        match child.try_wait() {
            Ok(Some(st)) => {
                let err = stderr_tail
                    .lock()
                    .ok()
                    .map(|g| g.clone())
                    .filter(|s| !s.trim().is_empty())
                    .unwrap_or_else(|| format!("cloakcli teach chat exited {st}"));
                return Err(redact_text(&err));
            }
            Ok(None) => {}
            Err(e) => return Err(format!("teach wait: {e}")),
        }
        if Instant::now() > deadline {
            let _ = child.kill();
            let _ = child.wait();
            return Err("teach chat session did not emit a session event".into());
        }
        thread::sleep(Duration::from_millis(40));
    }
}

fn status_from_event(event: &Value, prev: &TeachStatusDto) -> TeachStatusDto {
    TeachStatusDto {
        running: true,
        profile: event
            .get("profile")
            .and_then(|p| p.as_str())
            .map(|s| s.to_string())
            .or_else(|| prev.profile.clone()),
        spawn_browser: prev.spawn_browser,
        phase: event
            .get("phase")
            .and_then(|p| p.as_str())
            .map(|s| s.to_string()),
        hub: event.get("hub").and_then(|b| b.as_bool()).unwrap_or(prev.hub),
        worker: event
            .get("worker")
            .and_then(|b| b.as_bool())
            .unwrap_or(prev.worker),
        extension: event
            .get("extension")
            .and_then(|b| b.as_bool())
            .unwrap_or(prev.extension),
        busy: event
            .get("busy")
            .and_then(|b| b.as_bool())
            .unwrap_or(prev.busy),
        last_request_id: event
            .get("last_request_id")
            .and_then(|s| s.as_str())
            .map(|s| s.to_string()),
        error: None,
    }
}

fn write_cmd(session: &mut TeachSession, body: &Value) -> Result<(), String> {
    let line = format!("{body}\n");
    session
        .stdin
        .write_all(line.as_bytes())
        .map_err(|e| format!("teach stdin: {e}"))?;
    session.stdin.flush().map_err(|e| format!("teach flush: {e}"))
}

pub fn send(
    slot: &SharedTeach,
    goal: &str,
    profile: Option<&str>,
    skill: Option<&str>,
) -> Result<(), String> {
    if goal.trim().is_empty() {
        return Err("goal is empty".into());
    }
    if goal.len() > 8000 {
        return Err("goal exceeds 8000 chars".into());
    }
    let mut guard = slot.lock().map_err(|e| e.to_string())?;
    let session = guard.as_mut().ok_or("teach chat is not running")?;
    if let Some(p) = profile {
        validate_profile_name(p)?;
        if p != session.profile {
            return Err(format!(
                "session profile is {}; restart Teach Chat to use {p}",
                session.profile
            ));
        }
    }
    if let Some(s) = skill {
        validate_profile_name(s).map_err(|_| "invalid skill name".to_string())?;
    }
    let mut body = json!({"cmd": "send", "goal": goal, "profile": session.profile});
    if let Some(s) = skill.filter(|s| !s.is_empty()) {
        body["skill"] = json!(s);
    }
    write_cmd(session, &body)
}

pub fn cancel(slot: &SharedTeach) -> Result<(), String> {
    let mut guard = slot.lock().map_err(|e| e.to_string())?;
    let session = guard.as_mut().ok_or("teach chat is not running")?;
    write_cmd(session, &json!({"cmd": "cancel"}))
}

pub fn confirm(slot: &SharedTeach, yes: bool) -> Result<(), String> {
    let mut guard = slot.lock().map_err(|e| e.to_string())?;
    let session = guard.as_mut().ok_or("teach chat is not running")?;
    write_cmd(session, &json!({"cmd": "confirm", "yes": yes}))
}

pub fn request_status(slot: &SharedTeach) -> Result<TeachStatusDto, String> {
    let mut guard = slot.lock().map_err(|e| e.to_string())?;
    let Some(session) = guard.as_mut() else {
        return Ok(TeachStatusDto {
            running: false,
            profile: None,
            spawn_browser: false,
            phase: None,
            hub: false,
            worker: false,
            extension: false,
            busy: false,
            last_request_id: None,
            error: None,
        });
    };
    match session.child.try_wait() {
        Ok(None) => {
            let _ = write_cmd(session, &json!({"cmd": "status"}));
            Ok(session.last_status.clone())
        }
        Ok(Some(_)) | Err(_) => {
            terminate_session(session);
            *guard = None;
            Ok(TeachStatusDto {
                running: false,
                profile: None,
                spawn_browser: false,
                phase: None,
                hub: false,
                worker: false,
                extension: false,
                busy: false,
                last_request_id: None,
                error: Some("teach chat exited".into()),
            })
        }
    }
}

pub fn stop(slot: &SharedTeach) -> Result<(), String> {
    let mut guard = slot.lock().map_err(|e| e.to_string())?;
    let Some(session) = guard.as_mut() else {
        return Ok(());
    };
    let _ = write_cmd(session, &json!({"cmd": "stop"}));
    terminate_session(session);
    *guard = None;
    Ok(())
}

pub fn job_start_dto(slot: &SharedTeach) -> JobStartDto {
    let running = slot
        .lock()
        .ok()
        .and_then(|g| g.as_ref().map(|_| true))
        .unwrap_or(false);
    if running {
        JobStartDto {
            wired: true,
            via: "teach_chat".into(),
            hint: "send a Teach Chat goal; progress events are teach_chat_event kind=job".into(),
        }
    } else {
        JobStartDto {
            wired: false,
            via: "none".into(),
            hint: "start Teach Chat first. Fleet master submit is not wired in desktop M2.".into(),
        }
    }
}

fn terminate_session(session: &mut TeachSession) {
    let pid = session.child.id();
    #[cfg(unix)]
    {
        if let Some(pgid) = session.pgid {
            unsafe {
                libc::killpg(pgid, libc::SIGTERM);
            }
        }
        unsafe {
            libc::kill(pid as i32, libc::SIGTERM);
        }
        let deadline = Instant::now() + KILL_TIMEOUT;
        loop {
            match session.child.try_wait() {
                Ok(Some(_)) => break,
                Ok(None) if Instant::now() < deadline => {
                    thread::sleep(Duration::from_millis(40));
                }
                _ => {
                    if let Some(pgid) = session.pgid {
                        unsafe {
                            libc::killpg(pgid, libc::SIGKILL);
                        }
                    }
                    let _ = session.child.kill();
                    let _ = session.child.wait();
                    break;
                }
            }
        }
    }
    #[cfg(not(unix))]
    {
        let _ = pid;
        let _ = session.child.kill();
        let _ = session.child.wait();
    }
}

/// Used by tests to prove we never wrap the child in a shell.
#[cfg(test)]
pub fn command_is_direct(args: &[String]) -> bool {
    !args.iter().any(|a| {
        a == "sh" || a == "-c" || a.contains("&&") || a.contains("|") || a.contains(";")
    }) && args.first().map(|s| s.as_str()) == Some("teach")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn argv_is_fixed_and_validates_profile() {
        let a = argv("demo", None, false).unwrap();
        assert_eq!(
            a,
            vec!["teach", "chat", "--events", "--profile", "demo", "--no-browser"]
        );
        assert!(command_is_direct(&a));
        assert!(argv("../etc", None, false).is_err());
        assert!(argv("demo; rm -rf /", None, false).is_err());
        let with_url = argv("demo", Some("https://example.com/x"), true).unwrap();
        assert!(with_url.contains(&"--url".into()));
        assert!(with_url.contains(&"https://example.com/x".into()));
        assert!(!with_url.iter().any(|s| s == "--no-browser"));
        assert!(command_is_direct(&with_url));
    }

    #[test]
    fn url_rejects_dangerous_schemes() {
        assert!(validate_start_url("javascript:alert(1)").is_err());
        assert!(validate_start_url("file:///etc/passwd").is_err());
        assert!(validate_start_url("data:text/html,hi").is_err());
        assert!(validate_start_url("https://example.com/login").is_ok());
        assert!(validate_start_url("http://127.0.0.1:9/").is_ok());
    }

    #[test]
    fn redact_event_strips_secrets_keeps_kind() {
        let v = redact_event(json!({
            "kind": "assistant",
            "text": "Authorization: Bearer sk-secretTEST99abc cookie=SESSIONID_SUPER_SECRET",
            "pairing_code": "K7Q2MX"
        }));
        let t = v["text"].as_str().unwrap();
        assert!(!t.contains("sk-secretTEST99abc"), "{t}");
        assert!(!t.contains("SESSIONID_SUPER_SECRET"), "{t}");
        assert_eq!(v["kind"], "assistant");
        assert_eq!(v["pairing_code"], "K7Q2MX");
    }

    #[test]
    fn job_start_honest_when_idle() {
        let slot: SharedTeach = Arc::new(Mutex::new(None));
        let dto = job_start_dto(&slot);
        assert!(!dto.wired);
        assert_eq!(dto.via, "none");
        assert!(dto.hint.contains("not wired") || dto.hint.contains("Teach Chat"));
    }

    #[test]
    fn child_event_schema_rejects_unknown() {
        assert!(teach_event::parse_event_line(r#"{"v":1,"kind":"shell"}"#).is_err());
        assert!(teach_event::parse_event_line("nope").is_err());
        let ok = teach_event::parse_event_line(
            r#"{"v":1,"kind":"assistant_delta","role":"assistant","text":"x","seq":1,"done":false}"#,
        )
        .unwrap();
        assert_eq!(ok["kind"], "assistant_delta");
    }
}
