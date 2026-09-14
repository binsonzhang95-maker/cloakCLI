//! Persisted, redacted teach/job run history for the Runs/History page.
//!
//! The desktop crate does not path-dep on `cloakcli`. History is a JSON file
//! under `CLOAKCLI_HOME/data/teach/desktop-runs.json` plus fleet stubs from
//! `data/jobs/*.json`. Free-text is redacted; job `data` / extracts are never
//! copied. Crash/reconnect reads the M2 snapshot (`events-snapshot.json`)
//! without forwarding message bodies to the UI.

use crate::catalog::{self, JobStub};
use crate::redact::{redact_proxy, redact_text};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

const RUNS_SCHEMA_V: u32 = 1;
const MAX_RUNS: usize = 50;
const SNAPSHOT_REL: &str = "data/teach/events-snapshot.json";
const RESUME_NOTE: &str =
    "Reconnect restores the transcript from this snapshot. Teach hub binds a new ephemeral port (hub_resume=new_hub); re-pair the extension for new browser actions.";

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct RunDto {
    pub id: String,
    /// `teach` (JSONL turn) or `job` (fleet stub under data/jobs).
    pub kind: String,
    pub job_id: String,
    pub profile: String,
    pub skill: String,
    pub state: String,
    pub summary: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    pub updated_at: i64,
    pub source: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct RunsFile {
    #[serde(default)]
    v: u32,
    #[serde(default)]
    runs: Vec<RunDto>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ResumeHintDto {
    pub present: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub profile: Option<String>,
    pub message_count: usize,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub last_request_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub phase: Option<String>,
    pub relative_path: String,
    pub note: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LlmStatusDto {
    pub configured: bool,
    pub enabled: bool,
    pub model: String,
    pub base_url: String,
    /// Environment variable *name* only. Never a key value.
    pub api_key_env: String,
    pub key_present: bool,
    pub teach_smart_optimize: bool,
    pub relative_path: String,
}

pub fn runs_path(home: &Path) -> PathBuf {
    home.join("data").join("teach").join("desktop-runs.json")
}

pub fn snapshot_path(home: &Path) -> PathBuf {
    home.join("data").join("teach").join("events-snapshot.json")
}

fn now_unix() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

fn sanitize_run(mut run: RunDto) -> RunDto {
    run.kind = match run.kind.as_str() {
        "job" => "job".into(),
        _ => "teach".into(),
    };
    if run.job_id.is_empty() {
        run.job_id = "unknown".into();
    }
    if run.id.is_empty() {
        run.id = format!("{}:{}", run.kind, run.job_id);
    }
    run.profile = redact_text(&run.profile);
    run.skill = redact_text(&run.skill);
    run.summary = redact_text(&run.summary);
    run.error = run.error.map(|e| redact_text(&e)).filter(|s| !s.is_empty());
    if run.source.is_empty() {
        run.source = if run.kind == "job" {
            "data/jobs".into()
        } else {
            "teach_chat".into()
        };
    }
    run
}

fn load_file(home: &Path) -> RunsFile {
    let path = runs_path(home);
    let Ok(bytes) = fs::read(&path) else {
        return RunsFile {
            v: RUNS_SCHEMA_V,
            runs: vec![],
        };
    };
    let mut file: RunsFile = serde_json::from_slice(&bytes).unwrap_or_default();
    file.v = RUNS_SCHEMA_V;
    file.runs = file.runs.into_iter().map(sanitize_run).collect();
    file
}

fn save_file(home: &Path, file: &RunsFile) -> Result<(), String> {
    let path = runs_path(home);
    if let Some(dir) = path.parent() {
        fs::create_dir_all(dir).map_err(|e| format!("create runs dir: {e}"))?;
    }
    let json = serde_json::to_vec_pretty(file).map_err(|e| e.to_string())?;
    fs::write(&path, json).map_err(|e| format!("write runs: {e}"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(&path, fs::Permissions::from_mode(0o600));
    }
    Ok(())
}

/// Upsert a redacted teach-chat job event into the on-disk history.
pub fn record_teach_run(home: &Path, event: &Value, profile: &str) -> Result<(), String> {
    let job_id = event
        .get("job_id")
        .and_then(|x| x.as_str())
        .unwrap_or("pending")
        .to_string();
    let state = event
        .get("state")
        .and_then(|x| x.as_str())
        .unwrap_or("unknown")
        .to_string();
    let summary = event
        .get("summary")
        .and_then(|x| x.as_str())
        .unwrap_or("")
        .to_string();
    let error = event
        .get("error")
        .and_then(|x| x.as_str())
        .map(|s| s.to_string());
    let skill = event
        .get("skill")
        .and_then(|x| x.as_str())
        .unwrap_or("")
        .to_string();
    let profile = event
        .get("profile")
        .and_then(|x| x.as_str())
        .unwrap_or(profile)
        .to_string();

    let run = sanitize_run(RunDto {
        id: format!("teach:{job_id}"),
        kind: "teach".into(),
        job_id,
        profile,
        skill,
        state,
        summary,
        error,
        updated_at: now_unix(),
        source: "teach_chat".into(),
    });

    let mut file = load_file(home);
    if let Some(existing) = file.runs.iter_mut().find(|r| r.id == run.id) {
        *existing = run;
    } else {
        file.runs.insert(0, run);
    }
    file.runs.sort_by(|a, b| b.updated_at.cmp(&a.updated_at));
    file.runs.truncate(MAX_RUNS);
    file.v = RUNS_SCHEMA_V;
    save_file(home, &file)
}

fn from_job_stub(j: JobStub) -> RunDto {
    sanitize_run(RunDto {
        id: format!("job:{}", j.job_id),
        kind: "job".into(),
        job_id: j.job_id,
        profile: j.profile,
        skill: j.skill,
        state: j.state,
        summary: String::new(),
        error: None,
        updated_at: j.updated_at,
        source: "data/jobs".into(),
    })
}

fn snapshot_run(home: &Path) -> Option<RunDto> {
    let hint = resume_hint(home);
    if !hint.present {
        return None;
    }
    let mtime = snapshot_path(home)
        .metadata()
        .and_then(|m| m.modified())
        .ok()
        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);
    Some(sanitize_run(RunDto {
        id: "teach:snapshot".into(),
        kind: "teach".into(),
        job_id: "snapshot".into(),
        profile: hint.profile.unwrap_or_default(),
        skill: String::new(),
        state: "resume_available".into(),
        summary: format!(
            "{} messages · reconnect restores transcript (new hub port)",
            hint.message_count
        ),
        error: None,
        updated_at: mtime,
        source: SNAPSHOT_REL.into(),
    }))
}

/// Merged, newest-first, redacted history (teach log + fleet stubs + snapshot).
pub fn list_runs(home: &Path) -> Vec<RunDto> {
    let mut runs = load_file(home).runs;
    let (_running, jobs) = catalog::list_job_stubs_n(home, 0);
    for j in jobs {
        let dto = from_job_stub(j);
        if !runs.iter().any(|r| r.id == dto.id) {
            runs.push(dto);
        }
    }
    if let Some(snap) = snapshot_run(home) {
        if !runs.iter().any(|r| r.id == snap.id) {
            runs.push(snap);
        }
    }
    runs.sort_by(|a, b| b.updated_at.cmp(&a.updated_at).then(b.id.cmp(&a.id)));
    runs.truncate(MAX_RUNS);
    runs
}

/// Read the M2 events snapshot without returning message bodies.
pub fn resume_hint(home: &Path) -> ResumeHintDto {
    let path = snapshot_path(home);
    let empty = |note: &str| ResumeHintDto {
        present: false,
        profile: None,
        message_count: 0,
        last_request_id: None,
        phase: None,
        relative_path: SNAPSHOT_REL.into(),
        note: note.into(),
    };
    if !path.is_file() {
        return empty("No Teach Chat snapshot. Start a session from Chat.");
    }
    let Ok(text) = fs::read_to_string(&path) else {
        return empty("Teach Chat snapshot is unreadable.");
    };
    let Ok(v) = serde_json::from_str::<Value>(&text) else {
        return empty("Teach Chat snapshot is not valid JSON.");
    };
    let profile = v
        .get("profile")
        .and_then(|p| p.as_str())
        .filter(|s| !s.is_empty())
        .map(redact_text);
    let message_count = v
        .get("messages")
        .and_then(|m| m.as_array())
        .map(|a| a.len())
        .unwrap_or(0);
    let last_request_id = v
        .get("last_request_id")
        .and_then(|s| s.as_str())
        .filter(|s| !s.is_empty())
        .map(|s| redact_text(s));
    let phase = v
        .get("phase")
        .and_then(|s| s.as_str())
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string());
    ResumeHintDto {
        present: true,
        profile,
        message_count,
        last_request_id,
        phase,
        relative_path: SNAPSHOT_REL.into(),
        note: RESUME_NOTE.into(),
    }
}

fn valid_env_name(name: &str) -> bool {
    let mut chars = name.chars();
    match chars.next() {
        Some(c) if c.is_ascii_alphabetic() || c == '_' => {}
        _ => return false,
    }
    chars.all(|c| c.is_ascii_alphanumeric() || c == '_') && name.len() <= 128
}

fn env_key_present(name: &str) -> bool {
    if !valid_env_name(name) {
        return false;
    }
    match std::env::var(name) {
        Ok(v) if !v.trim().is_empty() => true,
        _ => {
            if name == "CLOAKCLI_LLM_API_KEY" {
                std::env::var("OPENAI_API_KEY")
                    .map(|v| !v.trim().is_empty())
                    .unwrap_or(false)
            } else {
                false
            }
        }
    }
}

/// Read-only LLM status from `config/llm.json`. Never copies API key values.
pub fn llm_status(home: &Path) -> LlmStatusDto {
    let rel = "config/llm.json";
    let path = home.join("config").join("llm.json");
    let unset = |configured: bool| LlmStatusDto {
        configured,
        enabled: false,
        model: String::new(),
        base_url: String::new(),
        api_key_env: "CLOAKCLI_LLM_API_KEY".into(),
        key_present: env_key_present("CLOAKCLI_LLM_API_KEY"),
        teach_smart_optimize: true,
        relative_path: rel.into(),
    };
    if !path.is_file() {
        return unset(false);
    }
    let Ok(text) = fs::read_to_string(&path) else {
        return unset(false);
    };
    let Ok(v) = serde_json::from_str::<Value>(&text) else {
        return unset(false);
    };
    // Known fields only. A mistaken `api_key` value in the file is ignored.
    let enabled = v.get("enabled").and_then(|x| x.as_bool()).unwrap_or(false);
    let model = v
        .get("model")
        .and_then(|x| x.as_str())
        .map(redact_text)
        .unwrap_or_default();
    let base_url = v
        .get("base_url")
        .and_then(|x| x.as_str())
        .map(redact_proxy)
        .map(|s| redact_text(&s))
        .unwrap_or_default();
    let api_key_env = v
        .get("api_key_env")
        .and_then(|x| x.as_str())
        .map(str::trim)
        .filter(|s| valid_env_name(s))
        .unwrap_or("CLOAKCLI_LLM_API_KEY")
        .to_string();
    let teach_smart_optimize = v
        .get("teach_smart_optimize")
        .and_then(|x| x.as_bool())
        .unwrap_or(true);
    LlmStatusDto {
        configured: true,
        enabled,
        model,
        base_url,
        key_present: env_key_present(&api_key_env),
        api_key_env,
        teach_smart_optimize,
        relative_path: rel.into(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::fs;

    fn temp_home() -> PathBuf {
        let base = std::env::temp_dir().join(format!(
            "cloakcli-runs-{}-{}",
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
    fn record_run_redacts_summary_and_skips_extracts() {
        let home = temp_home();
        let event = json!({
            "kind": "job",
            "job_id": "t1",
            "state": "done",
            "summary": "Authorization: Bearer sk-secretTEST99abc cookie=SESSIONID_SUPER_SECRET",
            "error": null,
            "data": {"token": "NOPE_EXTRACT"}
        });
        record_teach_run(&home, &event, "demo").unwrap();
        let list = list_runs(&home);
        assert_eq!(list.len(), 1);
        assert_eq!(list[0].job_id, "t1");
        assert_eq!(list[0].kind, "teach");
        assert!(!list[0].summary.contains("sk-secretTEST99abc"), "{}", list[0].summary);
        assert!(!list[0].summary.contains("SESSIONID_SUPER_SECRET"), "{}", list[0].summary);
        let disk = fs::read_to_string(runs_path(&home)).unwrap();
        assert!(!disk.contains("sk-secretTEST99abc"), "{disk}");
        assert!(!disk.contains("NOPE_EXTRACT"), "{disk}");
        assert!(!disk.contains("SESSIONID_SUPER_SECRET"), "{disk}");
        fs::remove_dir_all(&home).ok();
    }

    #[test]
    fn list_runs_merges_fleet_jobs_without_payload() {
        let home = temp_home();
        let dir = home.join("data").join("jobs");
        fs::create_dir_all(&dir).unwrap();
        fs::write(
            dir.join("j1.json"),
            r#"{"job_id":"j1","skill":"hello","profile":"demo","state":"succeeded","data":{"token":"NOPE"},"updated_at":9}"#,
        )
        .unwrap();
        let list = list_runs(&home);
        assert_eq!(list.len(), 1);
        assert_eq!(list[0].kind, "job");
        assert_eq!(list[0].job_id, "j1");
        let json = serde_json::to_string(&list).unwrap();
        assert!(!json.contains("NOPE"), "{json}");
        fs::remove_dir_all(&home).ok();
    }

    #[test]
    fn resume_hint_hides_message_bodies() {
        let home = temp_home();
        let dir = home.join("data").join("teach");
        fs::create_dir_all(&dir).unwrap();
        fs::write(
            dir.join("events-snapshot.json"),
            r#"{
  "v": 1,
  "profile": "demo",
  "messages": [
    {"role": "user", "text": "click with token=abc123SECRETVALUE"},
    {"role": "assistant", "text": "ok"}
  ],
  "phase": "chat",
  "last_request_id": "req-1"
}"#,
        )
        .unwrap();
        let hint = resume_hint(&home);
        assert!(hint.present);
        assert_eq!(hint.profile.as_deref(), Some("demo"));
        assert_eq!(hint.message_count, 2);
        assert_eq!(hint.last_request_id.as_deref(), Some("req-1"));
        assert_eq!(hint.relative_path, SNAPSHOT_REL);
        assert!(hint.note.contains("new ephemeral port"), "{}", hint.note);
        let json = serde_json::to_string(&hint).unwrap();
        assert!(!json.contains("abc123SECRETVALUE"), "{json}");
        assert!(!json.contains("click with"), "{json}");

        let list = list_runs(&home);
        assert!(list.iter().any(|r| r.id == "teach:snapshot"));
        let json = serde_json::to_string(&list).unwrap();
        assert!(!json.contains("abc123SECRETVALUE"), "{json}");
        fs::remove_dir_all(&home).ok();
    }

    #[test]
    fn resume_hint_absent_when_no_file() {
        let home = temp_home();
        let hint = resume_hint(&home);
        assert!(!hint.present);
        assert_eq!(hint.message_count, 0);
        fs::remove_dir_all(&home).ok();
    }

    #[test]
    fn llm_status_never_copies_key_values() {
        let home = temp_home();
        let dir = home.join("config");
        fs::create_dir_all(&dir).unwrap();
        fs::write(
            dir.join("llm.json"),
            r#"{
  "schema_version": 1,
  "enabled": true,
  "model": "gpt-test",
  "base_url": "http://user:s3cretPASS@127.0.0.1:9/v1",
  "api_key_env": "CLOAKCLI_LLM_API_KEY",
  "api_key": "sk-secretTEST99abc",
  "teach_smart_optimize": false
}"#,
        )
        .unwrap();
        let st = llm_status(&home);
        assert!(st.configured);
        assert!(st.enabled);
        assert_eq!(st.model, "gpt-test");
        assert!(!st.base_url.contains("s3cretPASS"), "{}", st.base_url);
        assert_eq!(st.api_key_env, "CLOAKCLI_LLM_API_KEY");
        assert!(!st.teach_smart_optimize);
        let json = serde_json::to_string(&st).unwrap();
        assert!(!json.contains("sk-secretTEST99abc"), "{json}");
        assert!(!json.contains("s3cretPASS"), "{json}");
        fs::remove_dir_all(&home).ok();
    }

    #[test]
    fn llm_status_unconfigured_without_file() {
        let home = temp_home();
        let st = llm_status(&home);
        assert!(!st.configured);
        assert_eq!(st.relative_path, "config/llm.json");
        fs::remove_dir_all(&home).ok();
    }
}
