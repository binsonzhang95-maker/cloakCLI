//! Read-only catalog of CloakCLI on-disk state for the desktop Web UI.
//!
//! The desktop crate stays independent of the CLI (no path-dep on `cloakcli`,
//! so Tauri never pulls ratatui/clap/tokio into this binary). These readers
//! follow the same layout as the CLI (`profiles/`, `skills/`, `data/`) and
//! return structured DTOs — they do not scrape TUI/CLI text and do not exec
//! a shell. Proxy userinfo is redacted; cookie values are never copied into
//! DTOs, events, or error strings. Free-text fields (`notes`, `description`,
//! `on_stall`, error/detail strings) run through the same token / Authorization
//! / cookie redaction before they reach the UI.

use crate::redact::redact_text;
use serde::Serialize;
use serde_json::Value;
use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

pub use crate::redact::redact_proxy;

const DESKTOP_VERSION: &str = env!("CARGO_PKG_VERSION");

#[derive(Debug, Clone, Serialize)]
pub struct ProfileDto {
    pub name: String,
    /// Proxy URL with userinfo replaced by `***:***`. Never the raw value.
    pub proxy: Option<String>,
    pub notes: Option<String>,
    pub created_at: String,
    pub cookie_present: bool,
    pub cookie_count: usize,
    pub cookie_valid: usize,
    pub cookie_expired: usize,
}

#[derive(Debug, Clone, Serialize)]
pub struct SkillDto {
    pub name: String,
    pub description: String,
    pub schema_version: u32,
    pub step_count: usize,
    pub param_count: usize,
    pub on_stall: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct SkillListDto {
    pub skills: Vec<SkillDto>,
    pub invalid: Vec<InvalidSkillDto>,
}

#[derive(Debug, Clone, Serialize)]
pub struct InvalidSkillDto {
    pub path: String,
    pub error: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct ComponentStatus {
    /// `running` | `stopped` | `unknown`
    pub state: String,
    pub detail: String,
    pub pid: Option<u32>,
    /// How this was determined (`pid+sock`, `files`, `absent`).
    pub source: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct JobStub {
    pub job_id: String,
    pub skill: String,
    pub profile: String,
    pub state: String,
    pub updated_at: i64,
}

#[derive(Debug, Clone, Serialize)]
pub struct OpsStatus {
    pub version: String,
    pub env: String,
    pub home: Option<String>,
    pub home_error: Option<String>,
    pub binary: Option<String>,
    pub binary_error: Option<String>,
    pub hub: ComponentStatus,
    pub worker: ComponentStatus,
    pub jobs_running: usize,
    pub jobs_recent: Vec<JobStub>,
    pub pty_running: bool,
}

pub fn list_profiles(home: &Path) -> Result<Vec<ProfileDto>, String> {
    let dir = home.join("profiles");
    if !dir.exists() {
        return Ok(vec![]);
    }
    let mut out = Vec::new();
    let mut seen = BTreeSet::new();
    let mut entries: Vec<_> = fs::read_dir(&dir)
        .map_err(|e| format!("read profiles dir: {e}"))?
        .filter_map(|e| e.ok())
        .collect();
    entries.sort_by_key(|e| e.file_name());

    for e in entries {
        let p = e.path();
        if p.is_dir() {
            let meta = p.join("profile.json");
            if meta.is_file() {
                if let Some(dto) = load_profile_dto(&meta) {
                    if seen.insert(dto.name.clone()) {
                        out.push(dto);
                    }
                }
            }
            continue;
        }
        if p.extension().and_then(|x| x.to_str()) != Some("json") {
            continue;
        }
        if p.file_name().and_then(|x| x.to_str()) == Some("cookie.json") {
            continue;
        }
        if let Some(dto) = load_profile_dto(&p) {
            if seen.insert(dto.name.clone()) {
                out.push(dto);
            }
        }
    }
    out.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(out)
}

pub fn list_skills(home: &Path) -> Result<SkillListDto, String> {
    let dir = home.join("skills");
    if !dir.exists() {
        return Ok(SkillListDto {
            skills: vec![],
            invalid: vec![],
        });
    }
    let mut found = Vec::new();
    let mut invalid = Vec::new();
    collect_skills(&dir, &mut found, &mut invalid)?;
    let mut by_name = std::collections::HashMap::new();
    for s in found {
        by_name.entry(s.name.clone()).or_insert(s);
    }
    let mut skills: Vec<_> = by_name.into_values().collect();
    skills.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(SkillListDto { skills, invalid })
}

pub fn ops_status(
    home: Option<&Path>,
    home_error: Option<String>,
    binary: Option<String>,
    binary_error: Option<String>,
    pty_running: bool,
) -> OpsStatus {
    let (hub, worker, jobs_running, jobs_recent) = match home {
        Some(root) => {
            let hub = hub_status(root);
            let worker = worker_status(root);
            let (running, recent) = list_job_stubs(root);
            (hub, worker, running, recent)
        }
        None => (
            ComponentStatus {
                state: "unknown".into(),
                detail: "CLOAKCLI_HOME unset; hub not probed".into(),
                pid: None,
                source: "absent".into(),
            },
            ComponentStatus {
                state: "unknown".into(),
                detail: "CLOAKCLI_HOME unset; worker not probed".into(),
                pid: None,
                source: "absent".into(),
            },
            0,
            vec![],
        ),
    };

    OpsStatus {
        version: DESKTOP_VERSION.to_string(),
        env: "local".into(),
        home: home.map(|p| p.display().to_string()),
        home_error: home_error.map(|s| redact_text(&s)),
        binary,
        binary_error: binary_error.map(|s| redact_text(&s)),
        hub,
        worker,
        jobs_running,
        jobs_recent,
        pty_running,
    }
}

fn load_profile_dto(path: &Path) -> Option<ProfileDto> {
    let text = fs::read_to_string(path).ok()?;
    let v: Value = serde_json::from_str(&text).ok()?;
    let name = v.get("name")?.as_str()?.to_string();
    if name.is_empty() {
        return None;
    }
    let proxy = v
        .get("proxy")
        .and_then(|x| x.as_str())
        .filter(|s| !s.is_empty())
        .map(redact_proxy);
    let notes = v
        .get("notes")
        .and_then(|x| x.as_str())
        .map(redact_text)
        .filter(|s| !s.is_empty());
    let created_at = v
        .get("created_at")
        .and_then(|x| x.as_str())
        .unwrap_or("")
        .to_string();

    let cookie_path = path
        .parent()
        .map(|dir| {
            if path.file_name().and_then(|s| s.to_str()) == Some("profile.json") {
                dir.join("cookie.json")
            } else {
                // legacy flat profiles/<name>.json — cookies live in the dir layout
                dir.join(&name).join("cookie.json")
            }
        })
        .unwrap_or_else(|| PathBuf::from("cookie.json"));

    let cookies = cookie_counts(&cookie_path);

    Some(ProfileDto {
        name,
        proxy,
        notes,
        created_at,
        cookie_present: cookies.present,
        cookie_count: cookies.count,
        cookie_valid: cookies.valid,
        cookie_expired: cookies.expired,
    })
}

struct CookieCounts {
    present: bool,
    count: usize,
    valid: usize,
    expired: usize,
}

/// Counts only. Cookie `name` / `value` / `path` are never stored on the DTO.
fn cookie_counts(path: &Path) -> CookieCounts {
    if !path.is_file() {
        return CookieCounts {
            present: false,
            count: 0,
            valid: 0,
            expired: 0,
        };
    }
    let Ok(text) = fs::read_to_string(path) else {
        return CookieCounts {
            present: true,
            count: 0,
            valid: 0,
            expired: 0,
        };
    };
    let Ok(v) = serde_json::from_str::<Value>(&text) else {
        return CookieCounts {
            present: true,
            count: 0,
            valid: 0,
            expired: 0,
        };
    };
    let cookies = match v.get("cookies").and_then(|c| c.as_array()) {
        Some(arr) => arr,
        None => {
            return CookieCounts {
                present: true,
                count: 0,
                valid: 0,
                expired: 0,
            }
        }
    };
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    let mut expired = 0usize;
    let mut valid = 0usize;
    for c in cookies {
        if cookie_is_expired(c, now) {
            expired += 1;
        } else {
            valid += 1;
        }
    }
    CookieCounts {
        present: true,
        count: cookies.len(),
        valid,
        expired,
    }
}

fn cookie_is_expired(c: &Value, now_unix: i64) -> bool {
    match c.get("expires") {
        None => false,
        Some(Value::Number(n)) => {
            if let Some(exp) = n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)) {
                if exp < 0 {
                    return false;
                }
                exp > 0 && exp < now_unix
            } else {
                false
            }
        }
        Some(Value::Null) => false,
        _ => false,
    }
}

fn collect_skills(
    dir: &Path,
    out: &mut Vec<SkillDto>,
    invalid: &mut Vec<InvalidSkillDto>,
) -> Result<(), String> {
    if !dir.is_dir() {
        return Ok(());
    }
    let entries = fs::read_dir(dir).map_err(|e| format!("read skills dir: {e}"))?;
    for e in entries {
        let e = e.map_err(|e| format!("read skills entry: {e}"))?;
        let p = e.path();
        if p.is_dir() {
            let sj = p.join("skill.json");
            if sj.is_file() {
                match load_skill_dto(&sj) {
                    Ok(s) => out.push(s),
                    Err(err) => invalid.push(InvalidSkillDto {
                        path: sj.display().to_string(),
                        error: redact_text(&err),
                    }),
                }
            } else {
                collect_skills(&p, out, invalid)?;
            }
        }
    }
    Ok(())
}

fn load_skill_dto(path: &Path) -> Result<SkillDto, String> {
    let text = fs::read_to_string(path).map_err(|_| "unreadable skill.json".to_string())?;
    let v: Value =
        serde_json::from_str(&text).map_err(|_| "invalid skill JSON".to_string())?;
    let name = v
        .get("name")
        .and_then(|x| x.as_str())
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string())
        .or_else(|| {
            path.parent()
                .and_then(|p| p.file_name())
                .and_then(|s| s.to_str())
                .map(|s| s.to_string())
        })
        .ok_or_else(|| "skill missing name".to_string())?;
    let description = v
        .get("description")
        .and_then(|x| x.as_str())
        .map(redact_text)
        .unwrap_or_default();
    let schema_version = v
        .get("schema_version")
        .and_then(|x| x.as_u64())
        .unwrap_or(1) as u32;
    let step_count = v
        .get("steps")
        .and_then(|x| x.as_array())
        .map(|a| a.len())
        .unwrap_or(0);
    let param_count = v
        .get("params")
        .and_then(|x| x.as_array())
        .map(|a| a.len())
        .unwrap_or(0);
    let on_stall = v
        .get("on_stall")
        .and_then(|x| x.as_str())
        .map(redact_text);
    Ok(SkillDto {
        name,
        description,
        schema_version,
        step_count,
        param_count,
        on_stall,
    })
}

fn hub_status(root: &Path) -> ComponentStatus {
    let meta = root.join("data").join("master.json");
    let sock = root.join("data").join("master_ctrl.sock");
    let bind = fs::read_to_string(&meta)
        .ok()
        .and_then(|t| serde_json::from_str::<Value>(&t).ok())
        .and_then(|v| v.get("bind").and_then(|b| b.as_str()).map(|s| s.to_string()));
    let sock_exists = sock.exists();
    let accepting = sock_exists && unix_socket_accepts(&sock);
    match (bind, sock_exists, accepting) {
        (Some(bind), true, true) => ComponentStatus {
            state: "running".into(),
            detail: redact_text(&format!(
                "control socket accepting · bind {bind} · data/master_ctrl.sock"
            )),
            pid: None,
            source: "sock-connect".into(),
        },
        (None, true, true) => ComponentStatus {
            state: "running".into(),
            detail: "control socket accepting · data/master_ctrl.sock (no master.json bind)"
                .into(),
            pid: None,
            source: "sock-connect".into(),
        },
        (Some(bind), true, false) => ComponentStatus {
            state: "stopped".into(),
            detail: redact_text(&format!(
                "stale control socket (not accepting) · metadata bind={bind}"
            )),
            pid: None,
            source: "files".into(),
        },
        (None, true, false) => ComponentStatus {
            state: "stopped".into(),
            detail: "stale control socket (not accepting) · data/master_ctrl.sock".into(),
            pid: None,
            source: "files".into(),
        },
        (Some(bind), false, _) => ComponentStatus {
            state: "stopped".into(),
            detail: redact_text(&format!(
                "stopped · metadata bind={bind} · no data/master_ctrl.sock"
            )),
            pid: None,
            source: "files".into(),
        },
        (None, false, _) => ComponentStatus {
            state: "stopped".into(),
            detail: "stopped · no hub metadata or control socket".into(),
            pid: None,
            source: "absent".into(),
        },
    }
}

fn worker_status(root: &Path) -> ComponentStatus {
    let sock = root.join("data").join("worker.sock");
    let pid_file = root.join("data").join("worker.pid");
    let pid = fs::read_to_string(&pid_file)
        .ok()
        .and_then(|s| s.trim().parse::<u32>().ok());
    let pid_alive = pid.map(process_alive).unwrap_or(false);
    let sock_exists = sock.exists();
    let sock_live = sock_exists && unix_socket_accepts(&sock);

    if pid_alive && sock_live {
        ComponentStatus {
            state: "running".into(),
            detail: format!(
                "running pid={} · data/worker.pid + worker.sock accepting",
                pid.unwrap_or(0)
            ),
            pid,
            source: "pid+sock".into(),
        }
    } else if pid_alive && sock_exists && !sock_live {
        ComponentStatus {
            state: "unknown".into(),
            detail: format!(
                "pid {} alive but worker.sock is not accepting",
                pid.unwrap_or(0)
            ),
            pid,
            source: "pid+sock".into(),
        }
    } else if sock_exists && !pid_alive {
        ComponentStatus {
            state: "stopped".into(),
            detail: "stale: data/worker.sock left behind (pid dead or missing)".into(),
            pid,
            source: "pid+sock".into(),
        }
    } else if pid_alive && !sock_exists {
        ComponentStatus {
            state: "unknown".into(),
            detail: format!(
                "pid {} listed in data/worker.pid but worker.sock missing",
                pid.unwrap_or(0)
            ),
            pid,
            source: "pid+sock".into(),
        }
    } else {
        ComponentStatus {
            state: "stopped".into(),
            detail: "stopped · no data/worker.pid".into(),
            pid: None,
            source: "pid+sock".into(),
        }
    }
}

fn unix_socket_accepts(path: &Path) -> bool {
    #[cfg(unix)]
    {
        std::os::unix::net::UnixStream::connect(path).is_ok()
    }
    #[cfg(not(unix))]
    {
        path.exists()
    }
}

fn process_alive(pid: u32) -> bool {
    if pid == 0 {
        return false;
    }
    #[cfg(unix)]
    {
        unsafe { libc::kill(pid as i32, 0) == 0 }
    }
    #[cfg(not(unix))]
    {
        false
    }
}

fn is_terminal_job(state: &str) -> bool {
    matches!(state, "succeeded" | "failed" | "cancelled" | "paused")
}

fn list_job_stubs(root: &Path) -> (usize, Vec<JobStub>) {
    list_job_stubs_n(root, 8)
}

/// Fleet job stubs. `limit == 0` means no truncation (caller still must not
/// copy `data` / extracts — this reader never does).
pub(crate) fn list_job_stubs_n(root: &Path, limit: usize) -> (usize, Vec<JobStub>) {
    let dir = root.join("data").join("jobs");
    if !dir.is_dir() {
        return (0, vec![]);
    }
    let mut jobs = Vec::new();
    if let Ok(entries) = fs::read_dir(&dir) {
        for e in entries.filter_map(|e| e.ok()) {
            let p = e.path();
            if p.extension().and_then(|x| x.to_str()) != Some("json") {
                continue;
            }
            let Ok(text) = fs::read_to_string(&p) else {
                continue;
            };
            let Ok(v) = serde_json::from_str::<Value>(&text) else {
                continue;
            };
            // Never copy `data` / extracts — those can hold page text.
            let job_id = v
                .get("job_id")
                .and_then(|x| x.as_str())
                .unwrap_or("")
                .to_string();
            if job_id.is_empty() {
                continue;
            }
            jobs.push(JobStub {
                job_id,
                skill: redact_text(v.get("skill").and_then(|x| x.as_str()).unwrap_or("")),
                profile: redact_text(v.get("profile").and_then(|x| x.as_str()).unwrap_or("")),
                state: v
                    .get("state")
                    .and_then(|x| x.as_str())
                    .unwrap_or("unknown")
                    .to_string(),
                updated_at: v.get("updated_at").and_then(|x| x.as_i64()).unwrap_or(0),
            });
        }
    }
    let running = jobs.iter().filter(|j| !is_terminal_job(&j.state)).count();
    jobs.sort_by(|a, b| b.updated_at.cmp(&a.updated_at));
    if limit > 0 {
        jobs.truncate(limit);
    }
    (running, jobs)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_home() -> PathBuf {
        let base = std::env::temp_dir().join(format!(
            "cloakcli-catalog-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&base).unwrap();
        base
    }

    #[test]
    fn redact_proxy_strips_userinfo() {
        assert_eq!(
            redact_proxy("http://user:s3cret@127.0.0.1:7890"),
            "http://***:***@127.0.0.1:7890"
        );
        assert_eq!(
            redact_proxy("http://127.0.0.1:7890"),
            "http://127.0.0.1:7890"
        );
    }

    #[test]
    fn profiles_redact_proxy_and_count_cookies_only() {
        let home = temp_home();
        let dir = home.join("profiles").join("secretbox");
        fs::create_dir_all(&dir).unwrap();
        fs::write(
            dir.join("profile.json"),
            r#"{
  "name": "secretbox",
  "proxy": "http://user:s3cretPASS@127.0.0.1:7890",
  "notes": "lab",
  "user_data_dir": "/tmp/ud",
  "created_at": "2026-01-01T00:00:00Z"
}
"#,
        )
        .unwrap();
        fs::write(
            dir.join("cookie.json"),
            r#"{
  "cookies": [
    {
      "domain": ".example.com",
      "expires": -1,
      "name": "sessionid",
      "value": "TEST_SECRET_VALUE_DO_NOT_LOG"
    },
    {
      "domain": "www.example.com",
      "expires": 1,
      "name": "old",
      "value": "expired-secret"
    }
  ],
  "origins": []
}
"#,
        )
        .unwrap();

        let list = list_profiles(&home).unwrap();
        assert_eq!(list.len(), 1);
        let p = &list[0];
        assert_eq!(p.name, "secretbox");
        assert_eq!(
            p.proxy.as_deref(),
            Some("http://***:***@127.0.0.1:7890")
        );
        assert_eq!(p.cookie_count, 2);
        assert_eq!(p.cookie_valid, 1);
        assert_eq!(p.cookie_expired, 1);
        assert!(p.cookie_present);

        let json = serde_json::to_string(&p).unwrap();
        assert!(!json.contains("s3cretPASS"), "{json}");
        assert!(!json.contains("TEST_SECRET_VALUE_DO_NOT_LOG"), "{json}");
        assert!(!json.contains("expired-secret"), "{json}");
        assert!(!json.contains("sessionid"), "{json}");
        assert!(!json.contains("user:"), "{json}");

        fs::remove_dir_all(&home).ok();
    }

    #[test]
    fn skills_list_metadata_not_steps() {
        let home = temp_home();
        let dir = home.join("skills").join("examples").join("hello");
        fs::create_dir_all(&dir).unwrap();
        fs::write(
            dir.join("skill.json"),
            r#"{
  "schema_version": 1,
  "name": "hello",
  "description": "open example.com",
  "params": [],
  "steps": [
    {"action": "goto", "url": "https://example.com"},
    {"action": "fill", "value": "super-secret-token"}
  ]
}
"#,
        )
        .unwrap();

        let dto = list_skills(&home).unwrap();
        assert_eq!(dto.skills.len(), 1);
        assert_eq!(dto.skills[0].name, "hello");
        assert_eq!(dto.skills[0].step_count, 2);
        let json = serde_json::to_string(&dto).unwrap();
        assert!(!json.contains("super-secret-token"), "{json}");
        assert!(!json.contains("goto"), "{json}");

        fs::remove_dir_all(&home).ok();
    }

    #[test]
    fn status_is_honest_without_hub_probe() {
        let home = temp_home();
        fs::create_dir_all(home.join("data").join("jobs")).unwrap();
        fs::write(
            home.join("data").join("master.json"),
            r#"{"bind":"127.0.0.1:7750","mode":"dev-stub"}"#,
        )
        .unwrap();
        fs::write(
            home.join("data").join("jobs").join("j1.json"),
            r#"{"job_id":"j1","client_id":"c","skill":"hello","profile":"demo","headed":false,"state":"succeeded","data":{"token":"NOPE"},"updated_at":1}"#,
        )
        .unwrap();

        let st = ops_status(
            Some(&home),
            None,
            Some("/bin/cloakcli".into()),
            None,
            false,
        );
        assert_eq!(st.hub.state, "stopped");
        assert!(st.hub.detail.contains("7750"), "{}", st.hub.detail);
        assert!(
            st.hub.detail.contains("no data/master_ctrl.sock")
                || st.hub.detail.contains("no hub metadata"),
            "{}",
            st.hub.detail
        );
        assert_eq!(st.worker.state, "stopped");
        assert!(st.worker.detail.contains("worker.pid"), "{}", st.worker.detail);
        assert_eq!(st.jobs_running, 0);
        assert_eq!(st.jobs_recent.len(), 1);
        let json = serde_json::to_string(&st).unwrap();
        assert!(!json.contains("NOPE"), "{json}");

        fs::remove_dir_all(&home).ok();
    }

    #[test]
    fn invalid_skill_error_does_not_echo_file_body() {
        let home = temp_home();
        let dir = home.join("skills").join("bad");
        fs::create_dir_all(&dir).unwrap();
        fs::write(dir.join("skill.json"), "api_key = \"LEAKME\" not json").unwrap();
        let dto = list_skills(&home).unwrap();
        assert_eq!(dto.invalid.len(), 1);
        assert_eq!(dto.invalid[0].error, "invalid skill JSON");
        let json = serde_json::to_string(&dto).unwrap();
        assert!(!json.contains("LEAKME"), "{json}");
        fs::remove_dir_all(&home).ok();
    }

    #[test]
    fn freetext_dto_redacts_token_authorization_cookie() {
        let home = temp_home();
        let pdir = home.join("profiles").join("secretbox");
        fs::create_dir_all(&pdir).unwrap();
        fs::write(
            pdir.join("profile.json"),
            r#"{
  "name": "secretbox",
  "proxy": "http://127.0.0.1:7890",
  "notes": "Authorization: Bearer tok_LIVE_abcDEF123456 cookie=SESSIONID_SUPER_SECRET token=abc123SECRETVALUE",
  "created_at": "2026-01-01T00:00:00Z"
}
"#,
        )
        .unwrap();

        let sdir = home.join("skills").join("examples").join("leaky");
        fs::create_dir_all(&sdir).unwrap();
        fs::write(
            sdir.join("skill.json"),
            r#"{
  "schema_version": 1,
  "name": "leaky",
  "description": "login with Authorization: Bearer sk-secretTEST99abc and cookie=SESSIONID_SUPER_SECRET",
  "params": [],
  "on_stall": "retry token=abc123SECRETVALUE then Authorization: Bearer tok_LIVE_abcDEF123456",
  "steps": []
}
"#,
        )
        .unwrap();

        let profiles = list_profiles(&home).unwrap();
        assert_eq!(profiles.len(), 1);
        let notes = profiles[0].notes.as_deref().unwrap_or("");
        assert!(!notes.contains("tok_LIVE_abcDEF123456"), "{notes}");
        assert!(!notes.contains("SESSIONID_SUPER_SECRET"), "{notes}");
        assert!(!notes.contains("abc123SECRETVALUE"), "{notes}");
        assert!(notes.contains("***"), "{notes}");

        let skills = list_skills(&home).unwrap();
        assert_eq!(skills.skills.len(), 1);
        let d = &skills.skills[0].description;
        let stall = skills.skills[0].on_stall.as_deref().unwrap_or("");
        assert!(!d.contains("sk-secretTEST99abc"), "{d}");
        assert!(!d.contains("SESSIONID_SUPER_SECRET"), "{d}");
        assert!(!stall.contains("abc123SECRETVALUE"), "{stall}");
        assert!(!stall.contains("tok_LIVE_abcDEF123456"), "{stall}");
        assert!(d.contains("***"), "{d}");
        assert!(stall.contains("***"), "{stall}");

        let json = serde_json::to_string(&(&profiles, &skills)).unwrap();
        for leak in [
            "tok_LIVE_abcDEF123456",
            "SESSIONID_SUPER_SECRET",
            "abc123SECRETVALUE",
            "sk-secretTEST99abc",
        ] {
            assert!(!json.contains(leak), "leaked {leak} in {json}");
        }

        fs::remove_dir_all(&home).ok();
    }

    #[cfg(unix)]
    #[test]
    fn hub_running_when_control_socket_accepts() {
        let home = temp_home();
        fs::create_dir_all(home.join("data")).unwrap();
        fs::write(
            home.join("data").join("master.json"),
            r#"{"bind":"127.0.0.1:7750","mode":"dev-stub"}"#,
        )
        .unwrap();
        let sock_path = home.join("data").join("master_ctrl.sock");
        let _ = fs::remove_file(&sock_path);
        let listener = std::os::unix::net::UnixListener::bind(&sock_path).unwrap();
        let st = ops_status(Some(&home), None, Some("/bin/cloakcli".into()), None, false);
        assert_eq!(st.hub.state, "running", "{}", st.hub.detail);
        assert_eq!(st.hub.source, "sock-connect");
        assert!(st.hub.detail.contains("7750"), "{}", st.hub.detail);
        drop(listener);
        let _ = fs::remove_file(&sock_path);
        fs::remove_dir_all(&home).ok();
    }
}
