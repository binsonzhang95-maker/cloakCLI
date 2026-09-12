//! Simple job persistence for fleet stub (dev).
//! Idempotent recovery: same job_id that already finished is not re-run.

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::fs;
use std::path::{Path, PathBuf};

use crate::state;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JobRecord {
    pub job_id: String,
    pub client_id: String,
    pub skill: String,
    pub profile: String,
    #[serde(default)]
    pub headed: bool,
    pub state: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub data: Option<serde_json::Value>,
    pub updated_at: i64,
}

pub fn jobs_dir(root: &Path) -> PathBuf {
    state::data_dir(root).join("jobs")
}

pub fn job_path(root: &Path, job_id: &str) -> PathBuf {
    jobs_dir(root).join(format!("{job_id}.json"))
}

pub fn load(root: &Path, job_id: &str) -> Result<Option<JobRecord>> {
    let path = job_path(root, job_id);
    if !path.exists() {
        return Ok(None);
    }
    let text = fs::read_to_string(&path).with_context(|| format!("read {}", path.display()))?;
    Ok(Some(serde_json::from_str(&text).context("parse job json")?))
}

pub fn save(root: &Path, rec: &JobRecord) -> Result<()> {
    fs::create_dir_all(jobs_dir(root))?;
    let path = job_path(root, &rec.job_id);
    fs::write(
        &path,
        format!("{}\n", serde_json::to_string_pretty(rec)?),
    )?;
    Ok(())
}

pub fn upsert_state(
    root: &Path,
    job_id: &str,
    client_id: &str,
    skill: &str,
    profile: &str,
    headed: bool,
    state: &str,
    error: Option<String>,
    data: Option<serde_json::Value>,
) -> Result<JobRecord> {
    let mut rec = load(root, job_id)?.unwrap_or(JobRecord {
        job_id: job_id.to_string(),
        client_id: client_id.to_string(),
        skill: skill.to_string(),
        profile: profile.to_string(),
        headed,
        state: "queued".into(),
        error: None,
        data: None,
        updated_at: 0,
    });
    rec.client_id = client_id.to_string();
    if !skill.is_empty() {
        rec.skill = skill.to_string();
    }
    if !profile.is_empty() {
        rec.profile = profile.to_string();
    }
    rec.headed = headed;
    rec.state = state.to_string();
    rec.error = error;
    if data.is_some() {
        rec.data = data;
    }
    rec.updated_at = chrono::Utc::now().timestamp();
    save(root, &rec)?;
    Ok(rec)
}

/// Terminal states that make re-submit of the same job_id a no-op (idempotent).
pub fn is_terminal(state: &str) -> bool {
    matches!(state, "succeeded" | "failed" | "cancelled" | "paused")
}

pub fn to_json(rec: &JobRecord) -> serde_json::Value {
    json!({
        "job_id": rec.job_id,
        "client_id": rec.client_id,
        "skill": rec.skill,
        "profile": rec.profile,
        "headed": rec.headed,
        "state": rec.state,
        "error": rec.error,
        "data": rec.data,
        "updated_at": rec.updated_at,
    })
}
