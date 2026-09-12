use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::Path;
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::Semaphore;
use uuid::Uuid;

use crate::cookies;
use crate::locks::ProfileLock;
use crate::profiles;
use crate::skills;
use crate::state;
use crate::util::redact_proxy;
use crate::worker::{self, Request};

#[derive(Debug, Serialize, Deserialize)]
pub struct BatchJob {
    pub profile: String,
    pub skill: String,
    #[serde(default)]
    pub vars: serde_json::Value,
    #[serde(default)]
    pub headed: Option<bool>,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct BatchFile {
    pub jobs: Vec<BatchJob>,
}

pub async fn run_batch(
    root: &Path,
    jobs_path: &Path,
    concurrency: usize,
    default_headed: bool,
) -> Result<()> {
    let text = fs::read_to_string(jobs_path)
        .with_context(|| format!("read batch file {}", jobs_path.display()))?;
    let batch: BatchFile = serde_json::from_str(&text).context("parse batch JSON")?;
    let conc = concurrency.max(1);
    let sem = Arc::new(Semaphore::new(conc));
    let root = root.to_path_buf();
    let mut handles = Vec::new();

    println!(
        "batch: {} jobs, concurrency={conc}, headed={default_headed}",
        batch.jobs.len()
    );

    for (i, job) in batch.jobs.into_iter().enumerate() {
        let permit = sem.clone().acquire_owned().await?;
        let root = root.clone();
        handles.push(tokio::spawn(async move {
            let _permit = permit;
            let result = run_one(&root, &job, default_headed).await;
            match &result {
                Ok(data) => println!(
                    "[{i}] OK profile={} skill={} {:?}",
                    job.profile, job.skill, data
                ),
                Err(e) => eprintln!(
                    "[{i}] FAIL profile={} skill={}: {e}",
                    job.profile, job.skill
                ),
            }
            result
        }));
    }

    let mut ok = 0usize;
    let mut fail = 0usize;
    for h in handles {
        match h.await {
            Ok(Ok(_)) => ok += 1,
            _ => fail += 1,
        }
    }
    println!("batch done: ok={ok} fail={fail}");
    if fail > 0 {
        anyhow::bail!("{fail} job(s) failed");
    }
    Ok(())
}

async fn run_one(
    root: &Path,
    job: &BatchJob,
    default_headed: bool,
) -> Result<serde_json::Value> {
    let _lock = ProfileLock::acquire(root, &job.profile, Duration::from_secs(300)).await?;
    let profile = profiles::get(root, &job.profile)?;
    let skill = skills::get(root, &job.skill)?;
    let headed = job.headed.unwrap_or(default_headed);
    // Log with redacted proxy
    if let Some(px) = &profile.proxy {
        eprintln!(
            "  job profile={} proxy={}",
            profile.name,
            redact_proxy(px)
        );
    }
    let resp = worker::oneshot(
        root,
        Request {
            id: worker::next_id(),
            cmd: "run_skill".into(),
            profile: Some(profile.name.clone()),
            url: None,
            headed: Some(headed),
            skill: Some(skill.name.clone()),
            vars: Some(job.vars.clone()),
            session: None,
            proxy: profile.proxy.clone(),
            user_data_dir: Some(profile.user_data_dir.clone()),
            skill_path: Some(skill.path.join("skill.json").to_string_lossy().to_string()),
            root: Some(root.to_string_lossy().to_string()),
            cookie_file: cookies::cookie_file_for_open(root, &profile.name)?,
        },
    )
    .await?;
    if !resp.ok {
        anyhow::bail!(resp.error.unwrap_or_else(|| "unknown worker error".into()));
    }
    Ok(resp.data.unwrap_or(serde_json::json!({})))
}

/// Convenience: run the same skill across listed profiles.
pub async fn run_skill_on_profiles(
    root: &Path,
    skill_name: &str,
    profile_names: &[String],
    concurrency: usize,
    headed: bool,
) -> Result<()> {
    let jobs: Vec<BatchJob> = profile_names
        .iter()
        .map(|p| BatchJob {
            profile: p.clone(),
            skill: skill_name.to_string(),
            vars: serde_json::json!({}),
            headed: Some(headed),
        })
        .collect();
    let tmp_name = format!("batch_{}.json", Uuid::new_v4().simple());
    let tmp = state::data_dir(root).join(&tmp_name);
    fs::create_dir_all(state::data_dir(root))?;
    let file = BatchFile { jobs };
    fs::write(&tmp, serde_json::to_string_pretty(&file)?)?;
    let result = run_batch(root, &tmp, concurrency, headed).await;
    let _ = fs::remove_file(&tmp);
    result
}
