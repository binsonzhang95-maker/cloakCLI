//! Ops dispositions and retry cooldown. "先放" (park) is an operator action,
//! never a skill terminal status and never written onto `JobBusinessResult.status`.

use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};

use crate::jobs::{self, JobRecord};
use crate::state;
use crate::util;

pub const DEFAULT_RETRY_COOLDOWN_SECS: i64 = 30;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Disposition {
    pub job_id: String,
    pub skill_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub profile: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub account_id: Option<String>,
    /// Operator action only. Current values: `park`.
    pub disposition: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
    pub updated_at: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct DispositionFile {
    #[serde(default)]
    entries: Vec<Disposition>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct CooldownEntry {
    key: String,
    last_retry_at: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct CooldownFile {
    #[serde(default)]
    entries: Vec<CooldownEntry>,
}

fn ops_dir(root: &Path) -> PathBuf {
    state::data_dir(root).join("ops")
}

fn dispositions_path(root: &Path) -> PathBuf {
    ops_dir(root).join("dispositions.json")
}

fn cooldown_path(root: &Path) -> PathBuf {
    ops_dir(root).join("retry_cooldown.json")
}

fn load_dispositions(root: &Path) -> DispositionFile {
    let p = dispositions_path(root);
    let Ok(text) = fs::read_to_string(&p) else {
        return DispositionFile::default();
    };
    serde_json::from_str(&text).unwrap_or_default()
}

fn save_dispositions(root: &Path, file: &DispositionFile) -> Result<()> {
    fs::create_dir_all(ops_dir(root))?;
    let p = dispositions_path(root);
    fs::write(p, format!("{}\n", serde_json::to_string_pretty(file)?))?;
    Ok(())
}

fn load_cooldown(root: &Path) -> CooldownFile {
    let p = cooldown_path(root);
    let Ok(text) = fs::read_to_string(&p) else {
        return CooldownFile::default();
    };
    serde_json::from_str(&text).unwrap_or_default()
}

fn save_cooldown(root: &Path, file: &CooldownFile) -> Result<()> {
    fs::create_dir_all(ops_dir(root))?;
    fs::write(
        cooldown_path(root),
        format!("{}\n", serde_json::to_string_pretty(file)?),
    )?;
    Ok(())
}

#[allow(dead_code)]
pub fn list_dispositions(root: &Path) -> Vec<Disposition> {
    load_dispositions(root).entries
}

#[allow(dead_code)]
pub fn disposition_for_job(root: &Path, job_id: &str) -> Option<Disposition> {
    load_dispositions(root)
        .entries
        .into_iter()
        .find(|e| e.job_id == job_id)
}

/// Record an ops park. Does **not** mutate the job's business `result`.
pub fn park_job(root: &Path, job_id: &str, reason: Option<&str>) -> Result<Disposition> {
    util::validate_name(job_id, "job")?;
    let rec = jobs::load(root, job_id)?.context("job not found")?;
    let reason = reason.map(str::trim).filter(|s| !s.is_empty()).map(|s| {
        crate::util::redact_proxy(s)
    });
    let entry = Disposition {
        job_id: rec.job_id.clone(),
        skill_id: rec.skill.clone(),
        profile: if rec.profile.is_empty() {
            None
        } else {
            Some(rec.profile.clone())
        },
        account_id: rec.account_id.clone(),
        disposition: "park".into(),
        reason: reason.map(|s| truncate_reason(&s)),
        updated_at: chrono::Utc::now().timestamp(),
    };
    let mut file = load_dispositions(root);
    if let Some(existing) = file.entries.iter_mut().find(|e| e.job_id == job_id) {
        *existing = entry.clone();
    } else {
        file.entries.push(entry.clone());
    }
    save_dispositions(root, &file)?;
    Ok(entry)
}

fn truncate_reason(s: &str) -> String {
    let t = s.chars().take(200).collect::<String>();
    crate::util::redact_proxy(&t)
}

pub fn retry_key(skill: &str, profile: &str, account_id: Option<&str>) -> String {
    format!(
        "{}|{}|{}",
        skill,
        profile,
        account_id.unwrap_or("")
    )
}

pub fn cooldown_remaining_secs(root: &Path, key: &str, now: i64, window: i64) -> i64 {
    let file = load_cooldown(root);
    let Some(ent) = file.entries.iter().find(|e| e.key == key) else {
        return 0;
    };
    let elapsed = now.saturating_sub(ent.last_retry_at);
    window.saturating_sub(elapsed).max(0)
}

pub fn note_retry(root: &Path, key: &str, now: i64) -> Result<()> {
    let mut file = load_cooldown(root);
    if let Some(ent) = file.entries.iter_mut().find(|e| e.key == key) {
        ent.last_retry_at = now;
    } else {
        file.entries.push(CooldownEntry {
            key: key.to_string(),
            last_retry_at: now,
        });
    }
    save_cooldown(root, &file)
}

/// Preconditions for an operator retry. Does not dispatch.
pub fn assert_retryable(rec: &JobRecord) -> Result<()> {
    let Some(result) = rec.result.as_ref() else {
        bail!("job {} has no validated business result — cannot retry", rec.job_id);
    };
    if !result.retryable {
        bail!(
            "job {} status '{}' is not retryable (retryable:false) — park or inspect instead",
            rec.job_id,
            result.status
        );
    }
    Ok(())
}

pub fn profile_has_active_job(jobs: &std::collections::HashMap<String, JobRecord>, profile: &str) -> bool {
    jobs.values().any(|j| j.profile == profile && !jobs::is_terminal(&j.state))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::jobs::{JobBusinessResult, JobRecord};
    use std::time::{SystemTime, UNIX_EPOCH};

    fn tmp() -> PathBuf {
        let p = std::env::temp_dir().join(format!(
            "cloakcli-ops-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&p).unwrap();
        p
    }

    fn seed_job(root: &Path, retryable: bool) -> JobRecord {
        let rec = JobRecord {
            job_id: "j1".into(),
            client_id: "box1".into(),
            skill: "pin-reg".into(),
            profile: "geo01".into(),
            headed: false,
            state: "failed".into(),
            error: None,
            data: Some(serde_json::json!({"token": "NOPE_SECRET"})),
            updated_at: 1,
            skill_version: Some("1.0.0".into()),
            skill_digest: Some("aaa".into()),
            account_id: Some("acct-1".into()),
            geo: Some("geo01".into()),
            result: Some(JobBusinessResult {
                skill_id: "pin-reg".into(),
                version: "1.0.0".into(),
                digest: Some("aaa".into()),
                status: if retryable { "oops_park".into() } else { "logged_in".into() },
                success: !retryable,
                retryable,
                label: if retryable { "风控先放".into() } else { "已登录".into() },
                optional: false,
            }),
            protocol_error: None,
            success_counted: !retryable,
        };
        jobs::save(root, &rec).unwrap();
        rec
    }

    #[test]
    fn park_does_not_rewrite_business_status() {
        let root = tmp();
        let rec = seed_job(&root, true);
        let d = park_job(&root, "j1", Some("ops hold")).unwrap();
        assert_eq!(d.disposition, "park");
        assert_eq!(d.skill_id, "pin-reg");
        let reloaded = jobs::load(&root, "j1").unwrap().unwrap();
        assert_eq!(reloaded.result.as_ref().unwrap().status, rec.result.as_ref().unwrap().status);
        assert_eq!(reloaded.result.as_ref().unwrap().status, "oops_park");
        let json = serde_json::to_string(&d).unwrap();
        assert!(!json.contains("NOPE_SECRET"), "{json}");
        let disk = jobs::load(&root, "j1").unwrap().unwrap();
        assert_eq!(disk.result.as_ref().unwrap().status, "oops_park");
        fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn retryable_false_is_rejected() {
        let root = tmp();
        seed_job(&root, false);
        let rec = jobs::load(&root, "j1").unwrap().unwrap();
        assert!(assert_retryable(&rec).is_err());
        fs::remove_dir_all(&root).ok();
    }

    #[test]
    fn cooldown_blocks_then_expires() {
        let root = tmp();
        let key = retry_key("pin-reg", "geo01", Some("acct-1"));
        note_retry(&root, &key, 1000).unwrap();
        assert_eq!(cooldown_remaining_secs(&root, &key, 1010, 30), 20);
        assert_eq!(cooldown_remaining_secs(&root, &key, 1040, 30), 0);
        fs::remove_dir_all(&root).ok();
    }
}
