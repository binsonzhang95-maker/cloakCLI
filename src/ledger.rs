//! Per-skill account ledger. Partitioned by `skill_id`; labels come from the
//! result-bound digest snapshot. There is no global `email_confirmed` column.

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};

use crate::jobs::JobBusinessResult;
use crate::state;
use crate::util;

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct LedgerPartition {
    pub skill_id: String,
    #[serde(default)]
    pub success_count: u64,
    #[serde(default)]
    pub entries: Vec<LedgerEntry>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LedgerEntry {
    pub job_id: String,
    pub skill_id: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub digest: Option<String>,
    pub status: String,
    pub success: bool,
    pub label: String,
    #[serde(default)]
    pub optional: bool,
    #[serde(default)]
    pub retryable: bool,
    pub updated_at: i64,
}

fn ledgers_dir(root: &Path) -> PathBuf {
    state::data_dir(root).join("ledgers")
}

fn partition_path(root: &Path, skill_id: &str) -> PathBuf {
    ledgers_dir(root).join(format!("{skill_id}.json"))
}

pub fn load_partition(root: &Path, skill_id: &str) -> Result<LedgerPartition> {
    util::validate_name(skill_id, "skill")?;
    let p = partition_path(root, skill_id);
    if !p.exists() {
        return Ok(LedgerPartition {
            skill_id: skill_id.to_string(),
            success_count: 0,
            entries: vec![],
        });
    }
    let text = fs::read_to_string(&p).with_context(|| format!("read {}", p.display()))?;
    let mut part: LedgerPartition =
        serde_json::from_str(&text).with_context(|| format!("parse {}", p.display()))?;
    part.skill_id = skill_id.to_string();
    Ok(part)
}

fn save_partition(root: &Path, part: &LedgerPartition) -> Result<()> {
    let dir = ledgers_dir(root);
    fs::create_dir_all(&dir)?;
    let p = partition_path(root, &part.skill_id);
    fs::write(p, format!("{}\n", serde_json::to_string_pretty(part)?))?;
    Ok(())
}

/// Record a finalized business result. Idempotent on job_id: duplicates do not
/// double-count; conflicting late results do not overwrite.
pub fn record(
    root: &Path,
    result: &JobBusinessResult,
    job_id: &str,
    _already_counted: bool,
) -> Result<u64> {
    let skill_id = result.skill_id.as_str();
    if skill_id.is_empty() {
        return Ok(0);
    }
    let mut part = load_partition(root, skill_id)?;
    if let Some(existing) = part.entries.iter().find(|e| e.job_id == job_id) {
        return Ok(if existing.success {
            // Keep stored count; do not increment again.
            part.success_count
        } else {
            part.success_count
        });
    }
    let entry = LedgerEntry {
        job_id: job_id.to_string(),
        skill_id: result.skill_id.clone(),
        digest: result.digest.clone(),
        status: result.status.clone(),
        success: result.success,
        label: result.label.clone(),
        optional: result.optional,
        retryable: result.retryable,
        updated_at: chrono::Utc::now().timestamp(),
    };
    if entry.success {
        part.success_count = part.success_count.saturating_add(1);
    }
    part.entries.push(entry);
    save_partition(root, &part)?;
    Ok(part.success_count)
}

pub fn success_count(root: &Path, skill_id: &str) -> Result<u64> {
    Ok(load_partition(root, skill_id)?.success_count)
}

pub fn list_partitions(root: &Path) -> Result<Vec<LedgerPartition>> {
    let dir = ledgers_dir(root);
    if !dir.exists() {
        return Ok(vec![]);
    }
    let mut out = Vec::new();
    for ent in fs::read_dir(&dir)? {
        let ent = ent?;
        let path = ent.path();
        if path.extension().and_then(|s| s.to_str()) != Some("json") {
            continue;
        }
        let Some(stem) = path.file_stem().and_then(|s| s.to_str()) else {
            continue;
        };
        if let Ok(part) = load_partition(root, stem) {
            out.push(part);
        }
    }
    out.sort_by(|a, b| a.skill_id.cmp(&b.skill_id));
    Ok(out)
}

pub fn partition_to_json(part: &LedgerPartition) -> serde_json::Value {
    serde_json::to_value(part).unwrap_or(serde_json::json!({}))
}
