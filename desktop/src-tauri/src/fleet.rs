//! Fleet snapshot + digest-bound submit via the master control socket.
//!
//! Desktop does not path-dep on `cloakcli`. Live ops go through
//! `data/master_ctrl.sock` JSON (same cmds as `cloakcli master …`).
//! Disk readers never copy job `data`, cookies, tokens, or fleet tokens.

use crate::catalog::{self, latest_release, load_releases, profile_occupancy};
use crate::redact::redact_text;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashSet;
use std::fs;
use std::io::{BufRead, BufReader, Write};
use std::path::Path;
use std::sync::{Mutex, OnceLock};
use std::time::Duration;

const CTRL_WRITE_TIMEOUT: Duration = Duration::from_secs(8);
const CTRL_READ_TIMEOUT: Duration = Duration::from_secs(25);

fn inflight() -> &'static Mutex<HashSet<String>> {
    static SLOT: OnceLock<Mutex<HashSet<String>>> = OnceLock::new();
    SLOT.get_or_init(|| Mutex::new(HashSet::new()))
}

#[derive(Debug, Clone, Serialize)]
pub struct FleetClientDto {
    pub client_id: String,
    pub online: bool,
    pub url: Option<String>,
    pub token_set: bool,
    pub observed_revision: Option<u64>,
    pub desired_revision: Option<u64>,
    pub installed: Vec<InstalledSkillDto>,
    pub notes: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct InstalledSkillDto {
    pub skill_id: String,
    pub version: String,
    pub digest: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct FleetDesiredDto {
    pub revision: u64,
    pub concurrency: usize,
    pub headed: bool,
    pub interval_ms: u64,
}

#[derive(Debug, Clone, Serialize)]
pub struct QueueJobDto {
    pub job_id: String,
    pub client_id: String,
    pub skill: String,
    pub profile: String,
    pub geo: Option<String>,
    pub state: String,
    pub digest: Option<String>,
    pub version: Option<String>,
    pub updated_at: i64,
}

#[derive(Debug, Clone, Serialize)]
pub struct FleetSnapshotDto {
    pub hub_running: bool,
    pub hub_error: Option<String>,
    pub desired: FleetDesiredDto,
    pub clients: Vec<FleetClientDto>,
    pub queue: Vec<QueueJobDto>,
    pub published: Vec<PublishedSkillDto>,
}

#[derive(Debug, Clone, Serialize)]
pub struct PublishedSkillDto {
    pub skill_id: String,
    pub version: String,
    pub digest: String,
    pub published: bool,
}

#[derive(Debug, Clone, Serialize)]
pub struct FleetSubmitResult {
    pub ok: bool,
    pub via: String,
    pub job_id: Option<String>,
    pub client_id: String,
    pub skill_id: String,
    pub profile: String,
    pub digest: Option<String>,
    pub version: Option<String>,
    pub geo: Option<String>,
    pub error: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct FleetSubmitSpec {
    pub client_id: String,
    pub skill_id: String,
    pub profile: String,
    #[serde(default)]
    pub digest: Option<String>,
    #[serde(default)]
    pub version: Option<String>,
    #[serde(default)]
    pub geo: Option<String>,
    #[serde(default)]
    pub account_id: Option<String>,
    #[serde(default)]
    pub headed: bool,
    #[serde(default)]
    pub job_id: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct FleetBatchSpec {
    pub jobs: Vec<FleetSubmitSpec>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct FleetConfigSpec {
    #[serde(default)]
    pub concurrency: Option<u64>,
    #[serde(default)]
    pub interval_ms: Option<u64>,
    #[serde(default)]
    pub headed: Option<bool>,
}

#[derive(Debug, Clone, Serialize)]
pub struct LedgerPartitionDto {
    pub skill_id: String,
    pub success_count: u64,
    pub entries: Vec<LedgerEntryDto>,
}

#[derive(Debug, Clone, Serialize)]
pub struct LedgerEntryDto {
    pub job_id: String,
    pub skill_id: String,
    pub status: String,
    pub success: bool,
    pub retryable: bool,
    pub label: String,
    pub optional: bool,
    pub digest: Option<String>,
    pub profile: Option<String>,
    pub account_id: Option<String>,
    pub geo: Option<String>,
    pub updated_at: i64,
    /// Ops overlay. Never a skill status. `park` or null.
    pub disposition: Option<String>,
}

fn validate_name(name: &str, kind: &str) -> Result<(), String> {
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
        return Err(format!("invalid {kind} name"));
    }
    Ok(())
}

fn control_sock(home: &Path) -> std::path::PathBuf {
    home.join("data").join("master_ctrl.sock")
}

pub fn control_request(home: &Path, req: Value) -> Result<Value, String> {
    #[cfg(unix)]
    {
        use std::os::unix::net::UnixStream;
        let path = control_sock(home);
        if !path.exists() {
            return Err("master hub is not running (no data/master_ctrl.sock)".into());
        }
        let stream = UnixStream::connect(&path).map_err(|e| {
            format!(
                "master hub is not accepting control connections: {}",
                redact_text(&e.to_string())
            )
        })?;
        let _ = stream.set_write_timeout(Some(CTRL_WRITE_TIMEOUT));
        let _ = stream.set_read_timeout(Some(CTRL_READ_TIMEOUT));
        let mut stream = stream;
        let line = serde_json::to_string(&req).map_err(|e| e.to_string())? + "\n";
        stream
            .write_all(line.as_bytes())
            .map_err(|e| format!("control write: {}", redact_text(&e.to_string())))?;
        stream
            .flush()
            .map_err(|e| format!("control flush: {}", redact_text(&e.to_string())))?;
        let mut reader = BufReader::new(stream);
        let mut buf = String::new();
        reader
            .read_line(&mut buf)
            .map_err(|e| format!("control read: {}", redact_text(&e.to_string())))?;
        serde_json::from_str(buf.trim()).map_err(|e| {
            format!(
                "control response is not JSON: {}",
                redact_text(&e.to_string())
            )
        })
    }
    #[cfg(not(unix))]
    {
        let _ = (home, req);
        Err("master control socket is unix-only".into())
    }
}

fn load_desired(home: &Path) -> FleetDesiredDto {
    let path = home.join("data").join("hub_desired.json");
    let Ok(text) = fs::read_to_string(&path) else {
        return FleetDesiredDto {
            revision: 0,
            concurrency: 2,
            headed: false,
            interval_ms: 0,
        };
    };
    let v: Value = serde_json::from_str(&text).unwrap_or(json!({}));
    FleetDesiredDto {
        revision: v.get("revision").and_then(|x| x.as_u64()).unwrap_or(0),
        concurrency: v.get("concurrency").and_then(|x| x.as_u64()).unwrap_or(2) as usize,
        headed: v.get("headed").and_then(|x| x.as_bool()).unwrap_or(false),
        interval_ms: v.get("interval_ms").and_then(|x| x.as_u64()).unwrap_or(0),
    }
}

fn load_configured_clients(home: &Path) -> Vec<FleetClientDto> {
    let path = home.join("data").join("fleet.json");
    let Ok(text) = fs::read_to_string(&path) else {
        return vec![];
    };
    let v: Value = serde_json::from_str(&text).unwrap_or(json!({}));
    let Some(arr) = v.get("clients").and_then(|x| x.as_array()) else {
        return vec![];
    };
    arr.iter()
        .filter_map(|c| {
            let client_id = c.get("name").and_then(|x| x.as_str())?.to_string();
            if client_id.is_empty() {
                return None;
            }
            Some(FleetClientDto {
                client_id,
                online: false,
                url: c
                    .get("url")
                    .and_then(|x| x.as_str())
                    .map(redact_text)
                    .filter(|s| !s.is_empty()),
                token_set: c
                    .get("token")
                    .and_then(|x| x.as_str())
                    .map(|s| !s.is_empty())
                    .unwrap_or(false),
                observed_revision: None,
                desired_revision: None,
                installed: vec![],
                notes: c
                    .get("notes")
                    .and_then(|x| x.as_str())
                    .map(redact_text)
                    .filter(|s| !s.is_empty()),
            })
        })
        .collect()
}

fn queue_from_disk(home: &Path) -> Vec<QueueJobDto> {
    let dir = home.join("data").join("jobs");
    if !dir.is_dir() {
        return vec![];
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
            let job_id = v
                .get("job_id")
                .and_then(|x| x.as_str())
                .unwrap_or("")
                .to_string();
            if job_id.is_empty() {
                continue;
            }
            let state = v
                .get("state")
                .and_then(|x| x.as_str())
                .unwrap_or("unknown")
                .to_string();
            jobs.push(QueueJobDto {
                job_id,
                client_id: redact_text(v.get("client_id").and_then(|x| x.as_str()).unwrap_or("")),
                skill: redact_text(v.get("skill").and_then(|x| x.as_str()).unwrap_or("")),
                profile: redact_text(v.get("profile").and_then(|x| x.as_str()).unwrap_or("")),
                geo: v
                    .get("geo")
                    .and_then(|x| x.as_str())
                    .map(redact_text)
                    .filter(|s| !s.is_empty()),
                state,
                digest: v
                    .get("skill_digest")
                    .and_then(|x| x.as_str())
                    .map(|s| s.to_string()),
                version: v
                    .get("skill_version")
                    .and_then(|x| x.as_str())
                    .map(|s| s.to_string()),
                updated_at: v.get("updated_at").and_then(|x| x.as_i64()).unwrap_or(0),
            });
        }
    }
    jobs.sort_by(|a, b| b.updated_at.cmp(&a.updated_at).then(b.job_id.cmp(&a.job_id)));
    jobs.truncate(80);
    jobs
}

pub fn fleet_status(home: &Path) -> FleetSnapshotDto {
    let mut clients = load_configured_clients(home);
    let desired = load_desired(home);
    let mut hub_running = false;
    let mut hub_error = None;
    match control_request(home, json!({"cmd": "list_clients"})) {
        Ok(resp) => {
            hub_running = resp.get("ok") == Some(&Value::Bool(true));
            if hub_running {
                if let Some(live) = resp.get("clients").and_then(|x| x.as_array()) {
                    merge_live_clients(&mut clients, live, desired.revision);
                }
                if let Some(d) = resp.get("desired") {
                    return FleetSnapshotDto {
                        hub_running: true,
                        hub_error: None,
                        desired: FleetDesiredDto {
                            revision: d.get("revision").and_then(|x| x.as_u64()).unwrap_or(desired.revision),
                            concurrency: d
                                .get("concurrency")
                                .and_then(|x| x.as_u64())
                                .unwrap_or(desired.concurrency as u64)
                                as usize,
                            headed: d.get("headed").and_then(|x| x.as_bool()).unwrap_or(desired.headed),
                            interval_ms: d
                                .get("interval_ms")
                                .and_then(|x| x.as_u64())
                                .unwrap_or(desired.interval_ms),
                        },
                        clients,
                        queue: queue_from_disk(home),
                        published: published_skills(home),
                    };
                }
            } else {
                hub_error = resp
                    .get("error")
                    .and_then(|x| x.as_str())
                    .map(redact_text);
            }
        }
        Err(e) => hub_error = Some(redact_text(&e)),
    }
    FleetSnapshotDto {
        hub_running,
        hub_error,
        desired,
        clients,
        queue: queue_from_disk(home),
        published: published_skills(home),
    }
}

fn published_skills(home: &Path) -> Vec<PublishedSkillDto> {
    load_releases(home)
        .into_iter()
        .map(|r| PublishedSkillDto {
            skill_id: r.skill_id,
            version: r.version,
            digest: r.digest,
            published: r.published,
        })
        .collect()
}

fn merge_live_clients(configured: &mut Vec<FleetClientDto>, live: &[Value], desired_rev: u64) {
    for c in live {
        let id = c
            .get("client_id")
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .to_string();
        if id.is_empty() {
            continue;
        }
        let installed = c
            .get("installed_skills")
            .and_then(|x| x.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|s| {
                        Some(InstalledSkillDto {
                            skill_id: s.get("skill_id")?.as_str()?.to_string(),
                            version: s.get("version").and_then(|x| x.as_str()).unwrap_or("").to_string(),
                            digest: s.get("digest")?.as_str()?.to_string(),
                        })
                    })
                    .collect()
            })
            .unwrap_or_default();
        let online = c.get("online").and_then(|x| x.as_bool()).unwrap_or(false);
        let observed = c.get("observed_revision").and_then(|x| x.as_u64());
        if let Some(existing) = configured.iter_mut().find(|x| x.client_id == id) {
            existing.online = online;
            existing.installed = installed;
            existing.observed_revision = observed;
            existing.desired_revision = Some(desired_rev);
        } else {
            configured.push(FleetClientDto {
                client_id: id,
                online,
                url: None,
                token_set: false,
                observed_revision: observed,
                desired_revision: Some(desired_rev),
                installed,
                notes: None,
            });
        }
    }
}

fn profile_geo(home: &Path, profile: &str) -> Option<String> {
    catalog::list_profiles(home)
        .ok()?
        .into_iter()
        .find(|p| p.name == profile)
        .and_then(|p| p.geo)
}

fn resolve_digest(home: &Path, spec: &FleetSubmitSpec) -> Result<(String, String), String> {
    let releases = load_releases(home);
    if let Some(d) = spec.digest.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
        let rel = releases
            .iter()
            .find(|r| r.skill_id == spec.skill_id && r.digest == d)
            .ok_or_else(|| {
                format!(
                    "digest {} is not a published release for skill '{}' (refusing silent retarget)",
                    d, spec.skill_id
                )
            })?;
        if !rel.published {
            return Err(format!(
                "digest {} for '{}' is not published",
                d, spec.skill_id
            ));
        }
        return Ok((rel.version.clone(), rel.digest.clone()));
    }
    let rel = latest_release(&releases, &spec.skill_id).ok_or_else(|| {
        format!(
            "no published release for skill '{}' (pack + publish first)",
            spec.skill_id
        )
    })?;
    if !rel.published {
        return Err(format!("skill '{}' has no published digest", spec.skill_id));
    }
    if let Some(v) = spec.version.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
        if v != rel.version {
            let match_v = releases.iter().find(|r| {
                r.skill_id == spec.skill_id && r.version == v && r.published
            });
            let rel = match_v.ok_or_else(|| {
                format!(
                    "version {v} is not a published release for '{}' (refusing silent retarget)",
                    spec.skill_id
                )
            })?;
            return Ok((rel.version.clone(), rel.digest.clone()));
        }
    }
    Ok((rel.version.clone(), rel.digest.clone()))
}

fn precheck_submit(home: &Path, spec: &FleetSubmitSpec) -> Result<(String, String, Option<String>), String> {
    validate_name(&spec.client_id, "client")?;
    validate_name(&spec.skill_id, "skill")?;
    validate_name(&spec.profile, "profile")?;
    if let Some(a) = spec.account_id.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
        validate_name(a, "account")?;
    }
    let (version, digest) = resolve_digest(home, spec)?;
    let profile_geo = profile_geo(home, &spec.profile);
    if let Some(want) = spec.geo.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
        if let Some(have) = profile_geo.as_deref() {
            if have != want {
                return Err(format!(
                    "geo mismatch: profile '{}' is {have}, request asked for {want} (refusing silent retarget)",
                    spec.profile
                ));
            }
        }
    }
    let (occupied, by) = profile_occupancy(Some(home), &spec.profile);
    if occupied {
        return Err(format!(
            "profile '{}' is occupied ({}) — retry keeps the same profile; no wipe",
            spec.profile,
            by.unwrap_or_else(|| "in-flight".into())
        ));
    }
    Ok((version, digest, spec.geo.clone().or(profile_geo)))
}

fn submit_key(spec: &FleetSubmitSpec, digest: &str) -> String {
    format!(
        "{}|{}|{}|{}|{}",
        spec.client_id,
        spec.skill_id,
        spec.profile,
        digest,
        spec.account_id.as_deref().unwrap_or("")
    )
}

pub fn fleet_submit(home: &Path, spec: FleetSubmitSpec) -> Result<FleetSubmitResult, String> {
    let (version, digest, geo) = precheck_submit(home, &spec)?;
    let key = submit_key(&spec, &digest);
    {
        let mut g = inflight().lock().map_err(|e| e.to_string())?;
        if !g.insert(key.clone()) {
            return Err("submit already in flight for this client/skill/profile/digest (duplicate click ignored)".into());
        }
    }
    let result = fleet_submit_inner(home, &spec, &version, &digest, geo.as_deref());
    if let Ok(mut g) = inflight().lock() {
        g.remove(&key);
    }
    result
}

fn fleet_submit_inner(
    home: &Path,
    spec: &FleetSubmitSpec,
    version: &str,
    digest: &str,
    geo: Option<&str>,
) -> Result<FleetSubmitResult, String> {
    let mut body = json!({
        "cmd": "submit",
        "client_id": spec.client_id,
        "skill": spec.skill_id,
        "skill_id": spec.skill_id,
        "profile": spec.profile,
        "headed": spec.headed,
        "version": version,
        "digest": digest,
    });
    if let Some(jid) = spec.job_id.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
        validate_name(jid, "job")?;
        body["job_id"] = json!(jid);
    }
    if let Some(a) = spec.account_id.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
        body["account_id"] = json!(a);
    }
    if let Some(g) = geo {
        body["geo"] = json!(g);
    }
    let resp = control_request(home, body)?;
    if resp.get("ok") != Some(&Value::Bool(true)) {
        let err = resp
            .get("error")
            .and_then(|x| x.as_str())
            .unwrap_or("submit failed");
        return Ok(FleetSubmitResult {
            ok: false,
            via: "fleet".into(),
            job_id: None,
            client_id: spec.client_id.clone(),
            skill_id: spec.skill_id.clone(),
            profile: spec.profile.clone(),
            digest: Some(digest.to_string()),
            version: Some(version.to_string()),
            geo: geo.map(|s| s.to_string()),
            error: Some(redact_text(err)),
        });
    }
    Ok(FleetSubmitResult {
        ok: true,
        via: "fleet".into(),
        job_id: resp
            .get("job_id")
            .and_then(|x| x.as_str())
            .map(|s| s.to_string()),
        client_id: spec.client_id.clone(),
        skill_id: spec.skill_id.clone(),
        profile: spec.profile.clone(),
        digest: resp
            .get("digest")
            .and_then(|x| x.as_str())
            .map(|s| s.to_string())
            .or_else(|| Some(digest.to_string())),
        version: resp
            .get("version")
            .and_then(|x| x.as_str())
            .map(|s| s.to_string())
            .or_else(|| Some(version.to_string())),
        geo: geo.map(|s| s.to_string()),
        error: None,
    })
}

pub fn fleet_submit_batch(home: &Path, batch: FleetBatchSpec) -> Result<Vec<FleetSubmitResult>, String> {
    if batch.jobs.is_empty() {
        return Err("batch requires at least one job".into());
    }
    if batch.jobs.len() > 50 {
        return Err("batch is capped at 50 jobs".into());
    }
    let mut out = Vec::new();
    for spec in batch.jobs {
        match fleet_submit(home, spec.clone()) {
            Ok(r) => out.push(r),
            Err(e) => out.push(FleetSubmitResult {
                ok: false,
                via: "fleet".into(),
                job_id: None,
                client_id: spec.client_id,
                skill_id: spec.skill_id,
                profile: spec.profile,
                digest: spec.digest,
                version: spec.version,
                geo: spec.geo,
                error: Some(redact_text(&e)),
            }),
        }
    }
    Ok(out)
}

pub fn fleet_sync(
    home: &Path,
    client_id: String,
    skill_id: String,
    version: Option<String>,
) -> Result<Value, String> {
    validate_name(&client_id, "client")?;
    validate_name(&skill_id, "skill")?;
    let mut body = json!({
        "cmd": "skill_sync",
        "client_id": client_id,
        "skill": skill_id,
    });
    if let Some(v) = version.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
        body["version"] = json!(v);
    }
    let resp = control_request(home, body)?;
    Ok(strip_package_bytes(resp))
}

pub fn fleet_config(home: &Path, spec: FleetConfigSpec) -> Result<Value, String> {
    let mut body = json!({"cmd": "config_update"});
    if let Some(c) = spec.concurrency {
        if c == 0 || c > 32 {
            return Err("concurrency must be 1..=32".into());
        }
        body["concurrency"] = json!(c);
    }
    if let Some(ms) = spec.interval_ms {
        if ms > 600_000 {
            return Err("interval_ms must be <= 600000".into());
        }
        body["interval_ms"] = json!(ms);
    }
    if let Some(h) = spec.headed {
        body["headed"] = json!(h);
    }
    let resp = control_request(home, body)?;
    Ok(resp)
}

pub fn fleet_park(home: &Path, job_id: String, reason: Option<String>) -> Result<Value, String> {
    validate_name(&job_id, "job")?;
    let reason = reason
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(redact_text);
    match control_request(
        home,
        json!({"cmd": "park", "job_id": job_id, "reason": reason}),
    ) {
        Ok(resp) => Ok(resp),
        Err(_) => park_on_disk(home, &job_id, reason.as_deref()),
    }
}

fn park_on_disk(home: &Path, job_id: &str, reason: Option<&str>) -> Result<Value, String> {
    let job_path = home.join("data").join("jobs").join(format!("{job_id}.json"));
    if !job_path.is_file() {
        return Err(format!("job not found: {job_id}"));
    }
    let text = fs::read_to_string(&job_path).map_err(|e| e.to_string())?;
    let v: Value = serde_json::from_str(&text).map_err(|_| "invalid job json".to_string())?;
    let skill_id = v.get("skill").and_then(|x| x.as_str()).unwrap_or("").to_string();
    let profile = v.get("profile").and_then(|x| x.as_str()).map(|s| s.to_string());
    let account_id = v
        .get("account_id")
        .and_then(|x| x.as_str())
        .map(|s| s.to_string());
    let entry = json!({
        "job_id": job_id,
        "skill_id": skill_id,
        "profile": profile,
        "account_id": account_id,
        "disposition": "park",
        "reason": reason,
        "updated_at": now_unix(),
    });
    let dir = home.join("data").join("ops");
    fs::create_dir_all(&dir).map_err(|e| e.to_string())?;
    let path = dir.join("dispositions.json");
    let mut file = if path.is_file() {
        serde_json::from_str::<Value>(&fs::read_to_string(&path).unwrap_or_else(|_| "{}".into()))
            .unwrap_or(json!({"entries": []}))
    } else {
        json!({"entries": []})
    };
    let mut entries = file
        .get("entries")
        .and_then(|x| x.as_array())
        .cloned()
        .unwrap_or_default();
    entries.retain(|e| e.get("job_id").and_then(|x| x.as_str()) != Some(job_id));
    entries.push(entry.clone());
    file["entries"] = Value::Array(entries);
    fs::write(&path, format!("{}\n", serde_json::to_string_pretty(&file).unwrap_or_default()))
        .map_err(|e| e.to_string())?;
    Ok(json!({"ok": true, "disposition": entry, "note": "ops park; not a skill status"}))
}

pub fn fleet_retry(home: &Path, job_id: String) -> Result<FleetSubmitResult, String> {
    validate_name(&job_id, "job")?;
    let resp = control_request(home, json!({"cmd": "retry", "job_id": job_id}))?;
    if resp.get("ok") != Some(&Value::Bool(true)) {
        return Err(redact_text(
            resp.get("error")
                .and_then(|x| x.as_str())
                .unwrap_or("retry failed"),
        ));
    }
    Ok(FleetSubmitResult {
        ok: true,
        via: "fleet".into(),
        job_id: resp.get("job_id").and_then(|x| x.as_str()).map(|s| s.to_string()),
        client_id: resp
            .get("client_id")
            .or_else(|| resp.get("client"))
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .to_string(),
        skill_id: resp
            .get("skill_id")
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .to_string(),
        profile: resp
            .get("profile")
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .to_string(),
        digest: resp.get("digest").and_then(|x| x.as_str()).map(|s| s.to_string()),
        version: resp.get("version").and_then(|x| x.as_str()).map(|s| s.to_string()),
        geo: resp.get("geo").and_then(|x| x.as_str()).map(|s| s.to_string()),
        error: None,
    })
}

fn strip_package_bytes(mut resp: Value) -> Value {
    if let Some(rel) = resp.get_mut("release") {
        if let Some(obj) = rel.as_object_mut() {
            obj.remove("package_b64");
            obj.remove("bytes");
        }
    }
    resp
}

fn now_unix() -> i64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

fn load_dispositions(home: &Path) -> Vec<Value> {
    let path = home.join("data").join("ops").join("dispositions.json");
    let Ok(text) = fs::read_to_string(&path) else {
        return vec![];
    };
    let Ok(v) = serde_json::from_str::<Value>(&text) else {
        return vec![];
    };
    v.get("entries")
        .and_then(|x| x.as_array())
        .cloned()
        .unwrap_or_default()
}

pub fn list_ledgers(home: &Path) -> Vec<LedgerPartitionDto> {
    let dir = home.join("data").join("ledgers");
    let parks = load_dispositions(home);
    let mut out = Vec::new();
    if dir.is_dir() {
        if let Ok(entries) = fs::read_dir(&dir) {
            for e in entries.filter_map(|e| e.ok()) {
                let p = e.path();
                if p.extension().and_then(|x| x.to_str()) != Some("json") {
                    continue;
                }
                let Some(stem) = p.file_stem().and_then(|s| s.to_str()) else {
                    continue;
                };
                if let Some(part) = load_ledger_file(&p, stem, &parks) {
                    out.push(part);
                }
            }
        }
    }
    out.sort_by(|a, b| a.skill_id.cmp(&b.skill_id));
    out
}

fn load_ledger_file(path: &Path, skill_id: &str, parks: &[Value]) -> Option<LedgerPartitionDto> {
    let text = fs::read_to_string(path).ok()?;
    let v: Value = serde_json::from_str(&text).ok()?;
    let entries = v.get("entries").and_then(|x| x.as_array()).cloned().unwrap_or_default();
    let mapped: Vec<LedgerEntryDto> = entries
        .into_iter()
        .filter_map(|e| {
            let job_id = e.get("job_id").and_then(|x| x.as_str())?.to_string();
            let status = e.get("status").and_then(|x| x.as_str()).unwrap_or("").to_string();
            let disposition = parks.iter().find_map(|d| {
                if d.get("job_id").and_then(|x| x.as_str()) == Some(job_id.as_str()) {
                    d.get("disposition").and_then(|x| x.as_str()).map(|s| s.to_string())
                } else {
                    None
                }
            });
            Some(LedgerEntryDto {
                job_id,
                skill_id: e
                    .get("skill_id")
                    .and_then(|x| x.as_str())
                    .unwrap_or(skill_id)
                    .to_string(),
                status,
                success: e.get("success").and_then(|x| x.as_bool()).unwrap_or(false),
                retryable: e.get("retryable").and_then(|x| x.as_bool()).unwrap_or(false),
                label: redact_text(e.get("label").and_then(|x| x.as_str()).unwrap_or("")),
                optional: e.get("optional").and_then(|x| x.as_bool()).unwrap_or(false),
                digest: e.get("digest").and_then(|x| x.as_str()).map(|s| s.to_string()),
                profile: e.get("profile").and_then(|x| x.as_str()).map(|s| s.to_string()),
                account_id: e
                    .get("account_id")
                    .and_then(|x| x.as_str())
                    .map(|s| s.to_string()),
                geo: e.get("geo").and_then(|x| x.as_str()).map(|s| s.to_string()),
                updated_at: e.get("updated_at").and_then(|x| x.as_i64()).unwrap_or(0),
                disposition,
            })
        })
        .collect();
    Some(LedgerPartitionDto {
        skill_id: skill_id.to_string(),
        success_count: v.get("success_count").and_then(|x| x.as_u64()).unwrap_or(0),
        entries: mapped,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn temp_home() -> PathBuf {
        let base = std::env::temp_dir().join(format!(
            "cloakcli-fleet-{}-{}",
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
    fn fleet_json_never_leaks_token() {
        let home = temp_home();
        fs::create_dir_all(home.join("data")).unwrap();
        fs::write(
            home.join("data").join("fleet.json"),
            r#"{
  "default_concurrency": 2,
  "clients": [{"name":"box1","url":"http://user:s3cretPASS@127.0.0.1:9","token":"super-secret-token","notes":"Authorization: Bearer sk-secretTEST99abc"}]
}"#,
        )
        .unwrap();
        let snap = fleet_status(&home);
        let json = serde_json::to_string(&snap).unwrap();
        assert!(!json.contains("super-secret-token"), "{json}");
        assert!(!json.contains("s3cretPASS"), "{json}");
        assert!(!json.contains("sk-secretTEST99abc"), "{json}");
        assert_eq!(snap.clients[0].token_set, true);
        assert_eq!(snap.hub_running, false);
        fs::remove_dir_all(&home).ok();
    }

    #[test]
    fn submit_refuses_without_hub_and_does_not_swap() {
        let home = temp_home();
        fs::create_dir_all(home.join("data").join("skill_releases")).unwrap();
        fs::write(
            home.join("data").join("skill_releases").join("catalog.json"),
            r#"{"releases":[{"skill_id":"hello","version":"0.1.0","digest":"abc123","path":"hello/0.1.0/package.tar","published":true,"created_at":1,"entry":"skill_steps","secret_names":[]}]}"#,
        )
        .unwrap();
        let err = fleet_submit(
            &home,
            FleetSubmitSpec {
                client_id: "offline-box".into(),
                skill_id: "hello".into(),
                profile: "geo01".into(),
                digest: Some("abc123".into()),
                version: None,
                geo: None,
                account_id: None,
                headed: false,
                job_id: None,
            },
        )
        .unwrap_err();
        assert!(err.contains("not running") || err.contains("not accepting"), "{err}");
        assert!(!err.contains("local"), "{err}");
        fs::remove_dir_all(&home).ok();
    }

    #[test]
    fn digest_mismatch_refuses_silent_retarget() {
        let home = temp_home();
        fs::create_dir_all(home.join("data").join("skill_releases")).unwrap();
        fs::write(
            home.join("data").join("skill_releases").join("catalog.json"),
            r#"{"releases":[{"skill_id":"hello","version":"0.1.0","digest":"abc123","path":"x","published":true,"created_at":1,"entry":"skill_steps","secret_names":[]}]}"#,
        )
        .unwrap();
        let err = precheck_submit(
            &home,
            &FleetSubmitSpec {
                client_id: "box1".into(),
                skill_id: "hello".into(),
                profile: "geo01".into(),
                digest: Some("ffff".into()),
                version: None,
                geo: None,
                account_id: None,
                headed: false,
                job_id: None,
            },
        )
        .unwrap_err();
        assert!(err.contains("refusing silent retarget"), "{err}");
        fs::remove_dir_all(&home).ok();
    }

    #[test]
    fn park_on_disk_does_not_copy_job_data() {
        let home = temp_home();
        let dir = home.join("data").join("jobs");
        fs::create_dir_all(&dir).unwrap();
        fs::write(
            dir.join("j1.json"),
            r#"{"job_id":"j1","client_id":"c","skill":"hello","profile":"geo01","state":"failed","data":{"token":"NOPE_SECRET"},"updated_at":1}"#,
        )
        .unwrap();
        let resp = park_on_disk(&home, "j1", Some("ops hold")).unwrap();
        let json = serde_json::to_string(&resp).unwrap();
        assert!(!json.contains("NOPE_SECRET"), "{json}");
        assert_eq!(resp["disposition"]["disposition"], "park");
        let job = fs::read_to_string(dir.join("j1.json")).unwrap();
        assert!(job.contains("failed"));
        fs::remove_dir_all(&home).ok();
    }

    #[test]
    fn ledger_has_no_global_email_confirmed() {
        let home = temp_home();
        let dir = home.join("data").join("ledgers");
        fs::create_dir_all(&dir).unwrap();
        fs::write(
            dir.join("pin-reg.json"),
            r#"{
  "skill_id": "pin-reg",
  "success_count": 1,
  "entries": [{"job_id":"p1","skill_id":"pin-reg","status":"logged_in","success":true,"retryable":false,"label":"已登录","updated_at":1}]
}"#,
        )
        .unwrap();
        fs::write(
            dir.join("ship-demo.json"),
            r#"{
  "skill_id": "ship-demo",
  "success_count": 1,
  "entries": [{"job_id":"s1","skill_id":"ship-demo","status":"shipped","success":true,"retryable":false,"label":"Shipped","updated_at":1}]
}"#,
        )
        .unwrap();
        let list = list_ledgers(&home);
        let json = serde_json::to_string(&list).unwrap();
        assert_eq!(list.len(), 2);
        assert!(json.contains("logged_in"));
        assert!(json.contains("shipped"));
        assert!(!json.contains("email_confirmed"), "{json}");
        fs::remove_dir_all(&home).ok();
    }

    #[test]
    fn duplicate_inflight_is_rejected() {
        let home = temp_home();
        fs::create_dir_all(home.join("data").join("skill_releases")).unwrap();
        fs::write(
            home.join("data").join("skill_releases").join("catalog.json"),
            r#"{"releases":[{"skill_id":"hello","version":"0.1.0","digest":"abc123","path":"x","published":true,"created_at":1,"entry":"skill_steps","secret_names":[]}]}"#,
        )
        .unwrap();
        let spec = FleetSubmitSpec {
            client_id: "box1".into(),
            skill_id: "hello".into(),
            profile: "geo01".into(),
            digest: Some("abc123".into()),
            version: None,
            geo: None,
            account_id: None,
            headed: false,
            job_id: None,
        };
        inflight()
            .lock()
            .unwrap()
            .insert(submit_key(&spec, "abc123"));
        let err = fleet_submit(&home, spec).unwrap_err();
        assert!(err.contains("duplicate click") || err.contains("in flight"), "{err}");
        inflight().lock().unwrap().clear();
        fs::remove_dir_all(&home).ok();
    }
}
