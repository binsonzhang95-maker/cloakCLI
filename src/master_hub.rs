//! Master hub: accepts outbound connections from remote clients.
//! Clients dial in (NAT-friendly); master never requires inbound on clients.
//!
//! DEV STUB: plaintext TCP JSONL + shared token — not production security.
//! TODO(prod): replace plaintext shared-token hub with authenticated TLS/mTLS
//! or a controlled tunnel. Digest checks do not replace source authentication.

use anyhow::{bail, Context, Result};
use serde_json::json;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::{mpsc, RwLock};

use crate::jobs::{self, JobRecord};
use crate::protocol::{ConfigRevision, Envelope, PROTOCOL_VERSION};
use crate::skill_pkg::{self, InstalledSkill};

#[derive(Debug, Clone)]
pub struct ClientInfo {
    pub client_id: String,
    pub last_seen: Instant,
    pub online: bool,
    pub capabilities: serde_json::Value,
    pub observed: ConfigRevision,
    pub last_job_state: Option<serde_json::Value>,
    /// skill_id → last ACK'd installed digest (from hello / skill_sync_ack).
    pub installed_skills: HashMap<String, InstalledSkill>,
    pub last_skill_sync: Option<serde_json::Value>,
}

struct ClientHandle {
    info: ClientInfo,
    tx: mpsc::UnboundedSender<String>,
}

pub struct HubState {
    pub root: PathBuf,
    pub token: String,
    pub desired: ConfigRevision,
    clients: HashMap<String, ClientHandle>,
    /// In-memory job index (also mirrored under data/jobs/).
    pub jobs: HashMap<String, JobRecord>,
    pub job_log: Vec<String>,
    last_dispatch: Option<Instant>,
}

impl HubState {
    pub fn new(root: PathBuf, token: String) -> Self {
        let desired = load_desired(&root).unwrap_or(ConfigRevision {
            revision: 1,
            concurrency: 2,
            headed: false,
            interval_ms: 0,
            labels: vec![],
        });
        let jobs = load_all_jobs(&root);
        Self {
            root,
            token,
            desired,
            clients: HashMap::new(),
            jobs,
            job_log: vec![],
            last_dispatch: None,
        }
    }

    pub fn list_clients(&self) -> Vec<ClientInfo> {
        self.clients.values().map(|c| c.info.clone()).collect()
    }

    pub fn push_log(&mut self, msg: impl Into<String>) {
        self.job_log.push(msg.into());
        if self.job_log.len() > 500 {
            self.job_log.drain(0..self.job_log.len() - 500);
        }
    }
}

pub type SharedHub = Arc<RwLock<HubState>>;

pub fn new_hub(root: &Path, token: &str) -> SharedHub {
    Arc::new(RwLock::new(HubState::new(
        root.to_path_buf(),
        token.to_string(),
    )))
}

pub fn desired_path(root: &Path) -> PathBuf {
    root.join("data").join("hub_desired.json")
}

pub fn load_desired(root: &Path) -> Option<ConfigRevision> {
    let path = desired_path(root);
    let text = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&text).ok()
}

pub fn save_desired(root: &Path, desired: &ConfigRevision) -> Result<()> {
    std::fs::create_dir_all(root.join("data"))?;
    let path = desired_path(root);
    std::fs::write(
        path,
        format!("{}\n", serde_json::to_string_pretty(desired)?),
    )?;
    Ok(())
}

fn load_all_jobs(root: &Path) -> HashMap<String, JobRecord> {
    let mut map = HashMap::new();
    let dir = jobs::jobs_dir(root);
    let Ok(entries) = std::fs::read_dir(&dir) else {
        return map;
    };
    for ent in entries.flatten() {
        let path = ent.path();
        if path.extension().and_then(|e| e.to_str()) != Some("json") {
            continue;
        }
        if let Ok(text) = std::fs::read_to_string(&path) {
            if let Ok(rec) = serde_json::from_str::<JobRecord>(&text) {
                map.insert(rec.job_id.clone(), rec);
            }
        }
    }
    map
}

/// Bind TCP and accept client outbound connections.
pub async fn serve(bind: &str, hub: SharedHub) -> Result<()> {
    serve_with_control(bind, hub, None).await
}

pub async fn serve_with_control(
    bind: &str,
    hub: SharedHub,
    control_sock: Option<std::path::PathBuf>,
) -> Result<()> {
    let listener = TcpListener::bind(bind)
        .await
        .with_context(|| format!("bind master hub {bind}"))?;
    eprintln!("master hub listening on {bind} (clients dial out to here)");
    eprintln!("DEV STUB: plaintext TCP + shared token — not for production");

    // Persist desired on start so restart keeps revision.
    {
        let state = hub.read().await;
        let _ = save_desired(&state.root, &state.desired);
    }

    if let Some(ctrl) = control_sock {
        let hub_c = hub.clone();
        tokio::spawn(async move {
            if let Err(e) = serve_control(ctrl, hub_c).await {
                eprintln!("[master] control socket ended: {e}");
            }
        });
    }

    loop {
        let (stream, addr) = listener.accept().await?;
        let hub = hub.clone();
        tokio::spawn(async move {
            if let Err(e) = handle_connection(stream, hub).await {
                eprintln!("[master] connection {addr} ended: {e}");
            }
        });
    }
}

async fn serve_control(path: std::path::PathBuf, hub: SharedHub) -> Result<()> {
    use tokio::net::UnixListener;
    if path.exists() {
        let _ = std::fs::remove_file(&path);
    }
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let listener = UnixListener::bind(&path)
        .with_context(|| format!("bind control {}", path.display()))?;
    eprintln!("master control socket {}", path.display());
    loop {
        let (stream, _) = listener.accept().await?;
        let hub = hub.clone();
        tokio::spawn(async move {
            let _ = handle_control(stream, hub).await;
        });
    }
}

async fn handle_control(stream: tokio::net::UnixStream, hub: SharedHub) -> Result<()> {
    let (reader, mut writer) = stream.into_split();
    let mut reader = BufReader::new(reader);
    let mut buf = String::new();
    let n = reader.read_line(&mut buf).await?;
    if n == 0 {
        return Ok(());
    }
    let req: serde_json::Value = serde_json::from_str(buf.trim())?;
    let cmd = req.get("cmd").and_then(|v| v.as_str()).unwrap_or("");
    let resp = match cmd {
        "list_clients" => {
            let state = hub.read().await;
            let clients: Vec<_> = state
                .list_clients()
                .into_iter()
                .map(|c| {
                    json!({
                        "client_id": c.client_id,
                        "online": c.online,
                        "observed_revision": c.observed.revision,
                        "desired_revision": state.desired.revision,
                        "last_job_state": c.last_job_state,
                        "installed_skills": c.installed_skills.values().map(|s| json!({
                            "skill_id": s.skill_id,
                            "version": s.version,
                            "digest": s.digest,
                        })).collect::<Vec<_>>(),
                    })
                })
                .collect();
            json!({"ok": true, "clients": clients, "log": state.job_log, "desired": state.desired})
        }
        "submit" => {
            let client_id = req.get("client_id").and_then(|v| v.as_str()).unwrap_or("");
            let skill = req
                .get("skill_id")
                .or_else(|| req.get("skill"))
                .and_then(|v| v.as_str())
                .unwrap_or("hello");
            let profile = req.get("profile").and_then(|v| v.as_str()).unwrap_or("noproxy");
            let headed = req.get("headed").and_then(|v| v.as_bool()).unwrap_or(false);
            let job_id = req
                .get("job_id")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string())
                .unwrap_or_else(|| uuid::Uuid::new_v4().simple().to_string());
            let spec = JobSubmit {
                client_id: client_id.to_string(),
                job_id: job_id.clone(),
                skill_id: skill.to_string(),
                profile: profile.to_string(),
                headed,
                vars: req.get("vars").cloned().unwrap_or(json!({})),
                version: req
                    .get("version")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string()),
                digest: req
                    .get("digest")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string()),
                account_id: req
                    .get("account_id")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string()),
                geo: req.get("geo").and_then(|v| v.as_str()).map(|s| s.to_string()),
            };
            match submit_job(&hub, spec).await {
                Ok(rec) => json!({
                    "ok": true,
                    "job_id": rec.job_id,
                    "skill_id": rec.skill,
                    "version": rec.skill_version,
                    "digest": rec.skill_digest,
                }),
                Err(e) => json!({"ok": false, "error": e.to_string()}),
            }
        }
        "skill_pack" => {
            let skill = req.get("skill").and_then(|v| v.as_str()).unwrap_or("");
            let version = req.get("version").and_then(|v| v.as_str());
            let draft = req.get("draft").and_then(|v| v.as_bool()).unwrap_or(false);
            let root = hub.read().await.root.clone();
            match skill_pkg::pack_skill(&root, skill, version, !draft) {
                Ok(rec) => json!({"ok": true, "release": skill_pkg::release_to_json(&rec)}),
                Err(e) => json!({"ok": false, "error": e.to_string()}),
            }
        }
        "skill_publish" => {
            let skill = req.get("skill").and_then(|v| v.as_str()).unwrap_or("");
            let version = req.get("version").and_then(|v| v.as_str());
            let root = hub.read().await.root.clone();
            match skill_pkg::publish_skill(&root, skill, version) {
                Ok(rec) => json!({"ok": true, "release": skill_pkg::release_to_json(&rec)}),
                Err(e) => json!({"ok": false, "error": e.to_string()}),
            }
        }
        "skill_sync" => {
            let client_id = req.get("client_id").and_then(|v| v.as_str()).unwrap_or("");
            let skill = req.get("skill").and_then(|v| v.as_str()).unwrap_or("");
            let version = req.get("version").and_then(|v| v.as_str());
            match push_skill_sync(&hub, client_id, skill, version).await {
                Ok(rec) => json!({"ok": true, "release": skill_pkg::release_to_json(&rec)}),
                Err(e) => json!({"ok": false, "error": e.to_string()}),
            }
        }
        "job_state" => {
            let job_id = req.get("job_id").and_then(|v| v.as_str()).unwrap_or("");
            let state = hub.read().await;
            if let Some(rec) = state.jobs.get(job_id) {
                json!({"ok": true, "job": jobs::to_json(rec)})
            } else if let Ok(Some(rec)) = jobs::load(&state.root, job_id) {
                json!({"ok": true, "job": jobs::to_json(&rec)})
            } else {
                json!({"ok": false, "error": format!("job not found: {job_id}")})
            }
        }
        "cancel" => {
            let client_id = req.get("client_id").and_then(|v| v.as_str()).unwrap_or("");
            let job_id = req.get("job_id").and_then(|v| v.as_str()).unwrap_or("");
            match cancel_job(&hub, client_id, job_id).await {
                Ok(()) => json!({"ok": true, "job_id": job_id}),
                Err(e) => json!({"ok": false, "error": e.to_string()}),
            }
        }
        "config_update" => {
            match push_config_update(&hub, &req).await {
                Ok(desired) => json!({"ok": true, "desired": desired}),
                Err(e) => json!({"ok": false, "error": e.to_string()}),
            }
        }
        "get_config" => {
            let state = hub.read().await;
            json!({"ok": true, "desired": state.desired})
        }
        "ledger" => {
            let root = hub.read().await.root.clone();
            let skill = req.get("skill").and_then(|v| v.as_str());
            match skill {
                Some(s) if !s.is_empty() => match crate::ledger::load_partition(&root, s) {
                    Ok(p) => json!({"ok": true, "ledger": crate::ledger::partition_to_json(&p)}),
                    Err(e) => json!({"ok": false, "error": e.to_string()}),
                },
                _ => match crate::ledger::list_partitions(&root) {
                    Ok(list) => json!({
                        "ok": true,
                        "ledgers": list.iter().map(crate::ledger::partition_to_json).collect::<Vec<_>>(),
                    }),
                    Err(e) => json!({"ok": false, "error": e.to_string()}),
                },
            }
        }
        "park" => {
            let root = hub.read().await.root.clone();
            let job_id = req.get("job_id").and_then(|v| v.as_str()).unwrap_or("");
            let reason = req.get("reason").and_then(|v| v.as_str());
            match crate::ops::park_job(&root, job_id, reason) {
                Ok(d) => json!({"ok": true, "disposition": d, "note": "ops park; not a skill status"}),
                Err(e) => json!({"ok": false, "error": e.to_string()}),
            }
        }
        "retry" => {
            let job_id = req.get("job_id").and_then(|v| v.as_str()).unwrap_or("");
            match retry_job(&hub, job_id).await {
                Ok(rec) => json!({
                    "ok": true,
                    "job_id": rec.job_id,
                    "skill_id": rec.skill,
                    "profile": rec.profile,
                    "digest": rec.skill_digest,
                    "account_id": rec.account_id,
                    "geo": rec.geo,
                }),
                Err(e) => json!({"ok": false, "error": e.to_string()}),
            }
        }
        "submit_batch" => {
            match submit_batch(&hub, &req).await {
                Ok(rows) => json!({"ok": true, "jobs": rows}),
                Err(e) => json!({"ok": false, "error": e.to_string()}),
            }
        }
        _ => json!({"ok": false, "error": format!("unknown control cmd: {cmd}")}),
    };
    let line = serde_json::to_string(&resp)? + "\n";
    writer.write_all(line.as_bytes()).await?;
    writer.flush().await?;
    Ok(())
}

pub fn control_sock_path(root: &Path) -> PathBuf {
    root.join("data").join("master_ctrl.sock")
}

pub async fn control_request(root: &Path, req: serde_json::Value) -> Result<serde_json::Value> {
    use tokio::net::UnixStream;
    let path = control_sock_path(root);
    let stream = UnixStream::connect(&path)
        .await
        .with_context(|| format!("connect control {}", path.display()))?;
    let (reader, mut writer) = stream.into_split();
    let line = serde_json::to_string(&req)? + "\n";
    writer.write_all(line.as_bytes()).await?;
    writer.flush().await?;
    let mut reader = BufReader::new(reader);
    let mut buf = String::new();
    reader.read_line(&mut buf).await?;
    Ok(serde_json::from_str(buf.trim())?)
}

async fn handle_connection(stream: TcpStream, hub: SharedHub) -> Result<()> {
    let (reader, mut writer) = stream.into_split();
    let mut reader = BufReader::new(reader);
    let (tx, mut rx) = mpsc::unbounded_channel::<String>();

    // Writer task
    let write_task = tokio::spawn(async move {
        while let Some(line) = rx.recv().await {
            if writer.write_all(line.as_bytes()).await.is_err() {
                break;
            }
            if writer.write_all(b"\n").await.is_err() {
                break;
            }
            if writer.flush().await.is_err() {
                break;
            }
        }
    });

    let mut buf = String::new();
    let mut client_id: Option<String> = None;

    loop {
        buf.clear();
        let n = reader.read_line(&mut buf).await?;
        if n == 0 {
            break;
        }
        let trimmed = buf.trim();
        if trimmed.is_empty() {
            continue;
        }
        let env: Envelope = match serde_json::from_str(trimmed) {
            Ok(e) => e,
            Err(e) => {
                let _ = tx.send(
                    serde_json::to_string(
                        &Envelope::new("error").with_data(json!({"error": format!("bad json: {e}")})),
                    )
                    .unwrap_or_default(),
                );
                continue;
            }
        };
        if env.v != PROTOCOL_VERSION {
            let _ = tx.send(
                serde_json::to_string(&Envelope::new("error").with_data(json!({
                    "error": format!("unsupported protocol v={}", env.v)
                })))
                .unwrap_or_default(),
            );
            continue;
        }

        match env.msg_type.as_str() {
            "hello" => {
                let token = env
                    .data
                    .get("token")
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                let cid = env
                    .client_id
                    .clone()
                    .or_else(|| {
                        env.data
                            .get("client_id")
                            .and_then(|v| v.as_str())
                            .map(|s| s.to_string())
                    })
                    .unwrap_or_else(|| format!("anon-{}", uuid::Uuid::new_v4().simple()));

                {
                    let state = hub.read().await;
                    if !state.token.is_empty() && token != state.token {
                        let _ = tx.send(
                            serde_json::to_string(&Envelope::new("hello_reject").with_data(
                                json!({"error": "invalid pairing token"}),
                            ))
                            .unwrap_or_default(),
                        );
                        bail!("auth failed for {cid}");
                    }
                }

                let caps = env.data.get("capabilities").cloned().unwrap_or(json!({}));
                let observed: ConfigRevision = serde_json::from_value(
                    env.data.get("observed").cloned().unwrap_or(json!({})),
                )
                .unwrap_or_default();

                let installed = env
                    .data
                    .get("installed_skills")
                    .map(skill_pkg::parse_installed_list)
                    .unwrap_or_default();

                let info = ClientInfo {
                    client_id: cid.clone(),
                    last_seen: Instant::now(),
                    online: true,
                    capabilities: caps,
                    observed,
                    last_job_state: None,
                    installed_skills: installed,
                    last_skill_sync: None,
                };

                {
                    let mut state = hub.write().await;
                    state.clients.insert(
                        cid.clone(),
                        ClientHandle {
                            info: info.clone(),
                            tx: tx.clone(),
                        },
                    );
                    let rev = state.desired.revision;
                    let desired = state.desired.clone();
                    state.push_log(format!("client registered: {cid}"));
                    let _ = tx.send(
                        serde_json::to_string(
                            &Envelope::new("hello_ok")
                                .with_client(&cid)
                                .with_data(json!({
                                    "revision": rev,
                                    "desired": desired,
                                })),
                        )
                        .unwrap_or_default(),
                    );
                }
                client_id = Some(cid);
            }
            "heartbeat" => {
                if let Some(cid) = env.client_id.clone().or(client_id.clone()) {
                    let mut state = hub.write().await;
                    if let Some(h) = state.clients.get_mut(&cid) {
                        h.info.last_seen = Instant::now();
                        h.info.online = true;
                        if let Some(obs) = env.data.get("observed") {
                            if let Ok(o) = serde_json::from_value::<ConfigRevision>(obs.clone()) {
                                h.info.observed = o;
                            }
                        }
                        if let Some(inst) = env.data.get("installed_skills") {
                            h.info.installed_skills = skill_pkg::parse_installed_list(inst);
                        }
                    }
                }
            }
            "skill_sync_ack" => {
                let cid = env.client_id.clone().or(client_id.clone()).unwrap_or_default();
                let mut state = hub.write().await;
                let mut ack = env.data.clone();
                if let Some(rid) = &env.request_id {
                    ack["request_id"] = json!(rid);
                }
                state.push_log(format!(
                    "{cid} skill_sync_ack: {}",
                    serde_json::to_string(&ack).unwrap_or_default()
                ));
                if let Some(h) = state.clients.get_mut(&cid) {
                    h.info.last_skill_sync = Some(ack.clone());
                    let ok = ack.get("ok").and_then(|v| v.as_bool()).unwrap_or(false);
                    if ok {
                        if let (Some(sid), Some(ver), Some(dig)) = (
                            ack.get("skill_id").and_then(|v| v.as_str()),
                            ack.get("version").and_then(|v| v.as_str()),
                            ack.get("digest").and_then(|v| v.as_str()),
                        ) {
                            if let Ok(digest) = skill_pkg::normalize_digest(dig) {
                                h.info.installed_skills.insert(
                                    sid.to_string(),
                                    InstalledSkill {
                                        skill_id: sid.to_string(),
                                        version: ver.to_string(),
                                        digest,
                                        installed_at: chrono::Utc::now().timestamp(),
                                    },
                                );
                            }
                        }
                    }
                }
            }
            "config_ack" => {
                let cid = env.client_id.clone().or(client_id.clone()).unwrap_or_default();
                let mut state = hub.write().await;
                state.push_log(format!(
                    "{cid} config_ack: {}",
                    serde_json::to_string(&env.data).unwrap_or_default()
                ));
                if let Some(obs) = env.data.get("observed") {
                    if let Ok(o) = serde_json::from_value::<ConfigRevision>(obs.clone()) {
                        if let Some(h) = state.clients.get_mut(&cid) {
                            h.info.observed = o;
                        }
                    }
                }
            }
            "job_state" | "log_chunk" | "screenshot" => {
                let mut state = hub.write().await;
                let cid = env.client_id.clone().or(client_id.clone()).unwrap_or_default();
                state.push_log(format!(
                    "{cid} {}: {}",
                    env.msg_type,
                    serde_json::to_string(&env.data).unwrap_or_default()
                ));
                if env.msg_type == "job_state" {
                    let job_id = env
                        .data
                        .get("job_id")
                        .and_then(|v| v.as_str())
                        .unwrap_or("");
                    let st = env
                        .data
                        .get("state")
                        .and_then(|v| v.as_str())
                        .unwrap_or("unknown");
                    let mut shown = env.data.clone();
                    if !job_id.is_empty() {
                        let err = env
                            .data
                            .get("error")
                            .and_then(|v| v.as_str())
                            .map(|s| s.to_string());
                        let data = env.data.get("data").cloned();
                        let protocol_error = env
                            .data
                            .get("protocol_error")
                            .and_then(|v| v.as_str())
                            .map(|s| s.to_string());
                        let upd = crate::skill_status::ClientJobUpdate {
                            job_id: job_id.to_string(),
                            client_id: cid.clone(),
                            reported_state: st.to_string(),
                            error: err,
                            protocol_error,
                            data,
                        };
                        match crate::skill_status::ingest_client_update(&state.root, upd) {
                            Ok(rec) => {
                                shown["state"] = json!(rec.state);
                                if let Some(r) = &rec.result {
                                    shown["result"] = crate::skill_status::result_to_json(r);
                                }
                                if let Some(pe) = &rec.protocol_error {
                                    shown["protocol_error"] = json!(pe);
                                }
                                state.jobs.insert(job_id.to_string(), rec);
                            }
                            Err(e) => {
                                state.push_log(format!(
                                    "ingest job_state {job_id} failed: {e}"
                                ));
                            }
                        }
                    }
                    if let Some(h) = state.clients.get_mut(&cid) {
                        h.info.last_job_state = Some(shown);
                    }
                }
            }
            other => {
                let _ = tx.send(
                    serde_json::to_string(&Envelope::new("error").with_data(json!({
                        "error": format!("unknown type: {other}")
                    })))
                    .unwrap_or_default(),
                );
            }
        }
    }

    if let Some(cid) = client_id {
        let mut state = hub.write().await;
        if let Some(h) = state.clients.get_mut(&cid) {
            h.info.online = false;
        }
        state.push_log(format!("client disconnected: {cid}"));
    }
    write_task.abort();
    Ok(())
}

/// Digest-bound job submit. Caller may omit version/digest to use the latest published release.
#[derive(Debug, Clone)]
pub struct JobSubmit {
    pub client_id: String,
    pub job_id: String,
    pub skill_id: String,
    pub profile: String,
    pub headed: bool,
    pub vars: serde_json::Value,
    pub version: Option<String>,
    pub digest: Option<String>,
    pub account_id: Option<String>,
    pub geo: Option<String>,
}

/// Submit a job to a connected client.
/// Idempotent: if job_id already terminal on disk, do not re-dispatch (return Ok).
/// Requires a published skill digest that the target client has ACK'd via skill_sync.
pub async fn submit_job(hub: &SharedHub, spec: JobSubmit) -> Result<JobRecord> {
    crate::skills::assert_no_plaintext_secrets(&spec.vars)?;
    let client_id = spec.client_id.as_str();
    let job_id = spec.job_id.as_str();

    {
        let state = hub.read().await;
        if let Some(existing) = state.jobs.get(job_id) {
            if jobs::is_terminal(&existing.state) {
                return Ok(existing.clone());
            }
        }
    }

    let release = {
        let state = hub.read().await;
        skill_pkg::resolve_published(
            &state.root,
            &spec.skill_id,
            spec.version.as_deref(),
            spec.digest.as_deref(),
        )?
    };

    {
        let state = hub.read().await;
        let Some(h) = state.clients.get(client_id) else {
            bail!("client not connected: {client_id} (refusing silent retarget)");
        };
        if !h.info.online {
            bail!("client offline: {client_id} (refusing silent retarget)");
        }
        if !skill_pkg::client_has_digest(&h.info.installed_skills, &spec.skill_id, &release.digest)
        {
            bail!(
                "client {client_id} has not ACK'd skill '{}' digest {} — run `cloakcli master skill-sync --client {client_id} --skill {}` first (refusing silent retarget)",
                spec.skill_id,
                release.digest,
                spec.skill_id
            );
        }
        if crate::ops::profile_has_active_job(&state.jobs, &spec.profile) {
            bail!(
                "profile '{}' is occupied by an in-flight job (refusing concurrent reuse)",
                spec.profile
            );
        }
    }

    wait_dispatch_interval(hub).await;

    let rec = {
        let mut state = hub.write().await;
        if let Some(existing) = state.jobs.get(job_id) {
            if jobs::is_terminal(&existing.state) {
                let rec = existing.clone();
                state.push_log(format!(
                    "job {job_id} already {} — idempotent skip",
                    rec.state
                ));
                return Ok(rec);
            }
        }
        if crate::ops::profile_has_active_job(&state.jobs, &spec.profile)
            && !state
                .jobs
                .get(job_id)
                .map(|j| j.profile == spec.profile && !jobs::is_terminal(&j.state))
                .unwrap_or(false)
        {
            bail!(
                "profile '{}' is occupied by an in-flight job (refusing concurrent reuse)",
                spec.profile
            );
        }
        let rec = jobs::upsert_state(
            &state.root,
            job_id,
            client_id,
            &spec.skill_id,
            &spec.profile,
            spec.headed,
            "queued",
            None,
            None,
        )?;
        let rec = jobs::set_binding(
            &state.root,
            job_id,
            Some(&release.version),
            Some(&release.digest),
            spec.account_id.as_deref(),
            spec.geo.as_deref(),
        )
        .unwrap_or(rec);
        state.jobs.insert(job_id.to_string(), rec.clone());
        state.push_log(format!(
            "submit {job_id} → {client_id} skill={}@{} digest={}",
            spec.skill_id, release.version, release.digest
        ));
        rec
    };

    let env = Envelope::new("job_submit")
        .with_client(client_id)
        .with_request(job_id)
        .with_data(json!({
            "job_id": job_id,
            "skill": spec.skill_id,
            "skill_id": spec.skill_id,
            "version": release.version,
            "digest": release.digest,
            "profile": spec.profile,
            "geo": spec.geo,
            "account_id": spec.account_id,
            "headed": spec.headed,
            "vars": spec.vars,
            "timeout_secs": 300,
        }));
    let line = serde_json::to_string(&env)?;
    {
        let state = hub.read().await;
        let Some(h) = state.clients.get(client_id) else {
            bail!("client not connected: {client_id}");
        };
        h.tx
            .send(line)
            .map_err(|_| anyhow::anyhow!("failed to send to client {client_id}"))?;
    }
    {
        let mut state = hub.write().await;
        state.last_dispatch = Some(Instant::now());
    }
    Ok(rec)
}

/// Operator retry: same client + profile + account + digest. Never auto-fired.
pub async fn retry_job(hub: &SharedHub, job_id: &str) -> Result<JobRecord> {
    if job_id.is_empty() {
        bail!("job_id required");
    }
    let rec = {
        let state = hub.read().await;
        if let Some(j) = state.jobs.get(job_id) {
            j.clone()
        } else {
            jobs::load(&state.root, job_id)?.context("job not found")?
        }
    };
    crate::ops::assert_retryable(&rec)?;
    let key = crate::ops::retry_key(&rec.skill, &rec.profile, rec.account_id.as_deref());
    let now = chrono::Utc::now().timestamp();
    let remain = {
        let state = hub.read().await;
        crate::ops::cooldown_remaining_secs(
            &state.root,
            &key,
            now,
            crate::ops::DEFAULT_RETRY_COOLDOWN_SECS,
        )
    };
    if remain > 0 {
        bail!("retry cooldown {remain}s for profile '{}' skill '{}'", rec.profile, rec.skill);
    }
    let digest = rec
        .skill_digest
        .clone()
        .context("job has no bound digest; cannot retry")?;
    {
        let state = hub.read().await;
        crate::ops::note_retry(&state.root, &key, now)?;
    }
    submit_job(
        hub,
        JobSubmit {
            client_id: rec.client_id.clone(),
            job_id: uuid::Uuid::new_v4().simple().to_string(),
            skill_id: rec.skill.clone(),
            profile: rec.profile.clone(),
            headed: rec.headed,
            vars: serde_json::json!({}),
            version: rec.skill_version.clone(),
            digest: Some(digest),
            account_id: rec.account_id.clone(),
            geo: rec.geo.clone(),
        },
    )
    .await
}

async fn submit_batch(hub: &SharedHub, req: &serde_json::Value) -> Result<Vec<serde_json::Value>> {
    let items = req
        .get("jobs")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();
    if items.is_empty() {
        bail!("submit_batch requires jobs[]");
    }
    let mut out = Vec::new();
    for item in items {
        let client_id = item
            .get("client_id")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let skill = item
            .get("skill_id")
            .or_else(|| item.get("skill"))
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let profile = item
            .get("profile")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let headed = item.get("headed").and_then(|v| v.as_bool()).unwrap_or(false);
        let job_id = item
            .get("job_id")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .unwrap_or_else(|| uuid::Uuid::new_v4().simple().to_string());
        let spec = JobSubmit {
            client_id: client_id.clone(),
            job_id: job_id.clone(),
            skill_id: skill.clone(),
            profile: profile.clone(),
            headed,
            vars: item.get("vars").cloned().unwrap_or(json!({})),
            version: item
                .get("version")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
            digest: item
                .get("digest")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
            account_id: item
                .get("account_id")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
            geo: item
                .get("geo")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string()),
        };
        match submit_job(hub, spec).await {
            Ok(rec) => out.push(json!({
                "ok": true,
                "job_id": rec.job_id,
                "client_id": rec.client_id,
                "skill_id": rec.skill,
                "profile": rec.profile,
                "digest": rec.skill_digest,
            })),
            Err(e) => out.push(json!({
                "ok": false,
                "job_id": job_id,
                "client_id": client_id,
                "skill_id": skill,
                "profile": profile,
                "error": e.to_string(),
            })),
        }
    }
    Ok(out)
}

async fn wait_dispatch_interval(hub: &SharedHub) {
    let (interval_ms, last) = {
        let state = hub.read().await;
        (state.desired.interval_ms, state.last_dispatch)
    };
    if interval_ms == 0 {
        return;
    }
    if let Some(last) = last {
        let need = Duration::from_millis(interval_ms);
        if let Some(remain) = need.checked_sub(last.elapsed()) {
            if !remain.is_zero() {
                tokio::time::sleep(remain).await;
            }
        }
    }
}

/// Push a published package to a connected client and wait for skill_sync_ack.
pub async fn push_skill_sync(
    hub: &SharedHub,
    client_id: &str,
    skill_id: &str,
    version: Option<&str>,
) -> Result<skill_pkg::ReleaseRecord> {
    if client_id.is_empty() {
        bail!("client_id required");
    }
    if skill_id.is_empty() {
        bail!("skill required");
    }
    let release = {
        let state = hub.read().await;
        skill_pkg::resolve_published(&state.root, skill_id, version, None)?
    };
    let bytes = {
        let state = hub.read().await;
        skill_pkg::read_package_bytes(&state.root, &release)?
    };
    let req_id = uuid::Uuid::new_v4().simple().to_string();
    let env = Envelope::new("skill_sync")
        .with_client(client_id)
        .with_request(&req_id)
        .with_data(json!({
            "skill_id": release.skill_id,
            "version": release.version,
            "digest": release.digest,
            "package_b64": skill_pkg::encode_package_b64(&bytes),
            "size": bytes.len(),
        }));
    let line = serde_json::to_string(&env)?;

    {
        let mut state = hub.write().await;
        let Some(h) = state.clients.get_mut(client_id) else {
            bail!("client not connected: {client_id}");
        };
        if !h.info.online {
            bail!("client offline: {client_id}");
        }
        h.info.last_skill_sync = None;
        h.tx
            .send(line)
            .map_err(|_| anyhow::anyhow!("failed to send skill_sync to {client_id}"))?;
        state.push_log(format!(
            "skill_sync → {client_id} skill={}@{} digest={}",
            release.skill_id, release.version, release.digest
        ));
    }

    wait_skill_sync_ack(hub, client_id, &req_id, &release.digest, Duration::from_secs(20)).await?;
    Ok(release)
}

async fn wait_skill_sync_ack(
    hub: &SharedHub,
    client_id: &str,
    request_id: &str,
    digest: &str,
    timeout: Duration,
) -> Result<()> {
    let start = Instant::now();
    loop {
        {
            let state = hub.read().await;
            if let Some(h) = state.clients.get(client_id) {
                if let Some(ack) = &h.info.last_skill_sync {
                    let rid = ack.get("request_id").and_then(|v| v.as_str()).unwrap_or("");
                    let d = ack.get("digest").and_then(|v| v.as_str()).unwrap_or("");
                    let ok_digest = skill_pkg::normalize_digest(d).ok()
                        == skill_pkg::normalize_digest(digest).ok();
                    if rid == request_id || ok_digest {
                        let ok = ack.get("ok").and_then(|v| v.as_bool()).unwrap_or(false);
                        if ok {
                            return Ok(());
                        }
                        let err = ack
                            .get("error")
                            .and_then(|v| v.as_str())
                            .unwrap_or("skill_sync failed");
                        bail!("{err}");
                    }
                }
            } else {
                bail!("client disconnected during skill_sync: {client_id}");
            }
        }
        if start.elapsed() > timeout {
            bail!("timeout waiting for skill_sync ACK (digest={digest})");
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
}

/// Cancel job on client (client must stop local worker work).
pub async fn cancel_job(hub: &SharedHub, client_id: &str, job_id: &str) -> Result<()> {
    {
        let mut state = hub.write().await;
        if let Some(existing) = state.jobs.get(job_id) {
            if jobs::is_terminal(&existing.state) || existing.result.is_some() {
                return Ok(());
            }
        }
        let _ = jobs::upsert_state(
            &state.root,
            job_id,
            client_id,
            "",
            "",
            false,
            "cancelling",
            None,
            None,
        );
        if let Some(rec) = state.jobs.get_mut(job_id) {
            rec.state = "cancelling".into();
        }
        state.push_log(format!("cancel {job_id} on {client_id}"));
    }
    let state = hub.read().await;
    let Some(h) = state.clients.get(client_id) else {
        bail!("client not connected: {client_id}");
    };
    let env = Envelope::new("job_cancel")
        .with_client(client_id)
        .with_data(json!({"job_id": job_id}));
    h.tx
        .send(serde_json::to_string(&env)?)
        .map_err(|_| anyhow::anyhow!("send failed"))?;
    Ok(())
}

/// Bump desired config, persist to disk, push config_update to online clients.
pub async fn push_config_update(
    hub: &SharedHub,
    req: &serde_json::Value,
) -> Result<ConfigRevision> {
    let mut state = hub.write().await;
    let mut desired = state.desired.clone();
    if let Some(c) = req.get("concurrency").and_then(|v| v.as_u64()) {
        desired.concurrency = c as usize;
    }
    if let Some(h) = req.get("headed").and_then(|v| v.as_bool()) {
        desired.headed = h;
    }
    if let Some(ms) = req.get("interval_ms").and_then(|v| v.as_u64()) {
        desired.interval_ms = ms;
    }
    if let Some(labels) = req.get("labels").and_then(|v| v.as_array()) {
        desired.labels = labels
            .iter()
            .filter_map(|v| v.as_str().map(|s| s.to_string()))
            .collect();
    }
    desired.revision = desired.revision.saturating_add(1);
    save_desired(&state.root, &desired)?;
    state.desired = desired.clone();
    state.push_log(format!(
        "config_update rev={} concurrency={} interval_ms={} headed={}",
        desired.revision, desired.concurrency, desired.interval_ms, desired.headed
    ));

    let env = Envelope::new("config_update").with_data(json!({
        "desired": desired,
        "prev_revision": desired.revision.saturating_sub(1),
    }));
    let line = serde_json::to_string(&env)?;
    let online: Vec<(String, mpsc::UnboundedSender<String>)> = state
        .clients
        .iter()
        .filter(|(_, h)| h.info.online)
        .map(|(cid, h)| (cid.clone(), h.tx.clone()))
        .collect();
    for (cid, tx) in online {
        let _ = tx.send(line.clone());
        state.push_log(format!("config_update → {cid}"));
    }
    Ok(desired)
}

pub fn master_bind_default() -> String {
    std::env::var("CLOAKCLI_MASTER_BIND").unwrap_or_else(|_| "127.0.0.1:7750".into())
}

pub fn master_token_default() -> String {
    std::env::var("CLOAKCLI_MASTER_TOKEN").unwrap_or_else(|_| "dev-token".into())
}

/// Persist hub bind info for doctor/TUI.
pub fn write_master_meta(root: &Path, bind: &str) -> Result<()> {
    let path = root.join("data").join("master.json");
    std::fs::create_dir_all(root.join("data"))?;
    std::fs::write(
        path,
        serde_json::to_string_pretty(&json!({
            "bind": bind,
            "protocol": PROTOCOL_VERSION,
            "mode": "dev-stub",
            "security": "plaintext shared token — NOT production",
        }))?
            + "\n",
    )?;
    Ok(())
}

pub fn read_master_meta(root: &Path) -> Option<PathBuf> {
    let p = root.join("data").join("master.json");
    if p.exists() {
        Some(p)
    } else {
        None
    }
}

/// Mark clients stale if heartbeat older than 60s.
pub async fn reap_stale(hub: &SharedHub) {
    let mut state = hub.write().await;
    let now = Instant::now();
    for h in state.clients.values_mut() {
        if h.info.online && now.duration_since(h.info.last_seen) > Duration::from_secs(60) {
            h.info.online = false;
        }
    }
}

#[cfg(test)]
mod digest_bind_tests {
    use super::*;
    use crate::client_daemon::{self, ClientDaemonConfig};
    use crate::profiles;
    use crate::state;
    use std::fs;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn tmp(prefix: &str) -> PathBuf {
        let n = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let p = std::env::temp_dir().join(format!("cloakcli_{prefix}_{n}"));
        fs::create_dir_all(p.join("skills")).unwrap();
        fs::create_dir_all(p.join("data")).unwrap();
        fs::create_dir_all(p.join("profiles")).unwrap();
        p
    }

    fn write_echo(root: &Path) {
        let dir = state::skills_dir(root).join("echo-runner");
        fs::create_dir_all(dir.join("scripts")).unwrap();
        fs::write(
            dir.join("skill.json"),
            r#"{"schema_version":1,"name":"echo-runner","description":"t","params":[],"steps":[]}"#,
        )
        .unwrap();
        fs::write(
            dir.join("manifest.json"),
            r#"{"version":"1.0.0","entry":{"kind":"python_runner","path":"scripts/echo.py"},"secrets":[]}"#,
        )
        .unwrap();
        fs::write(
            dir.join("scripts").join("echo.py"),
            "import json,sys\np=json.loads(sys.stdin.read() or '{}')\nprint(json.dumps({'ok': True, 'echo': True, 'digest': p.get('digest')}))\n",
        )
        .unwrap();
    }

    fn free_port() -> u16 {
        let l = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        l.local_addr().unwrap().port()
    }

    #[tokio::test]
    async fn pack_sync_submit_and_reject_wrong_digest() {
        let master_root = tmp("m");
        let client_root = tmp("c");
        write_echo(&master_root);
        let decoy = state::skills_dir(&client_root).join("echo-runner");
        fs::create_dir_all(&decoy).unwrap();
        fs::write(
            decoy.join("skill.json"),
            r#"{"schema_version":1,"name":"echo-runner","steps":[]}"#,
        )
        .unwrap();
        profiles::create(&client_root, "noproxy", None, None).unwrap();

        let rec = skill_pkg::pack_skill(&master_root, "echo-runner", Some("1.0.0"), true).unwrap();

        let port = free_port();
        let bind = format!("127.0.0.1:{port}");
        let token = "dev-token-test";
        let hub = new_hub(&master_root, token);
        write_master_meta(&master_root, &bind).unwrap();
        let ctrl = control_sock_path(&master_root);
        let hub_s = hub.clone();
        let bind_s = bind.clone();
        let serve = tokio::spawn(async move {
            let _ = serve_with_control(&bind_s, hub_s, Some(ctrl)).await;
        });

        for _ in 0..80 {
            if control_sock_path(&master_root).exists() {
                break;
            }
            tokio::time::sleep(Duration::from_millis(25)).await;
        }

        let client = tokio::spawn({
            let client_root = client_root.clone();
            let bind = bind.clone();
            async move {
                let _ = client_daemon::run(ClientDaemonConfig {
                    root: client_root,
                    master: bind,
                    token: token.into(),
                    client_id: "test-box".into(),
                })
                .await;
            }
        });

        let mut online = false;
        for _ in 0..100 {
            {
                let st = hub.read().await;
                if st
                    .clients
                    .get("test-box")
                    .map(|h| h.info.online)
                    .unwrap_or(false)
                {
                    online = true;
                    break;
                }
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
        assert!(online, "client never registered");

        push_skill_sync(&hub, "test-box", "echo-runner", Some("1.0.0"))
            .await
            .expect("skill_sync");

        let job = submit_job(
            &hub,
            JobSubmit {
                client_id: "test-box".into(),
                job_id: "job-ok".into(),
                skill_id: "echo-runner".into(),
                profile: "noproxy".into(),
                headed: false,
                vars: json!({}),
                version: Some("1.0.0".into()),
                digest: Some(rec.digest.clone()),
                account_id: None,
                geo: Some("geo01".into()),
            },
        )
        .await
        .expect("submit matching digest");
        assert_eq!(job.skill_digest.as_deref(), Some(rec.digest.as_str()));

        let mut ok_state = String::new();
        for _ in 0..100 {
            {
                let st = hub.read().await;
                if let Some(j) = st.jobs.get("job-ok") {
                    if jobs::is_terminal(&j.state) {
                        ok_state = j.state.clone();
                        if let Some(err) = &j.error {
                            panic!("job failed: {err}");
                        }
                        break;
                    }
                }
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
        assert_eq!(ok_state, "succeeded", "digest-bound python_runner should succeed");
        {
            let st = hub.read().await;
            let j = st.jobs.get("job-ok").expect("job-ok");
            let res = j.result.as_ref().expect("legacy result");
            assert_eq!(res.status, "ok");
            assert!(res.success);
            assert_eq!(j.skill_digest.as_deref(), Some(rec.digest.as_str()));
        }

        let err = submit_job(
            &hub,
            JobSubmit {
                client_id: "test-box".into(),
                job_id: "job-bad".into(),
                skill_id: "echo-runner".into(),
                profile: "noproxy".into(),
                headed: false,
                vars: json!({}),
                version: None,
                digest: Some(
                    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa".into(),
                ),
                account_id: None,
                geo: None,
            },
        )
        .await
        .unwrap_err()
        .to_string();
        assert!(
            err.contains("digest") || err.contains("published"),
            "{err}"
        );

        client.abort();
        serve.abort();
        let _ = fs::remove_dir_all(&master_root);
        let _ = fs::remove_dir_all(&client_root);
    }

    fn write_status_skill(root: &Path, name: &str, statuses: &str) {
        let dir = state::skills_dir(root).join(name);
        fs::create_dir_all(dir.join("scripts")).unwrap();
        fs::write(
            dir.join("skill.json"),
            format!(
                r#"{{"schema_version":1,"name":"{name}","description":"t","params":[],"steps":[]}}"#
            ),
        )
        .unwrap();
        fs::write(
            dir.join("manifest.json"),
            format!(
                r#"{{"version":"1.0.0","entry":{{"kind":"python_runner","path":"scripts/run.py"}},"secrets":[],"statuses":{statuses}}}"#
            ),
        )
        .unwrap();
        fs::write(
            dir.join("scripts").join("run.py"),
            "import json,sys\np=json.loads(sys.stdin.read() or '{}')\nprint(json.dumps({'skill_id':p.get('skill_id'),'version':p.get('version'),'digest':p.get('digest'),'status':(p.get('vars') or {}).get('status') or 'logged_in'}))\n",
        )
        .unwrap();
    }

    async fn wait_job(hub: &SharedHub, job_id: &str) -> JobRecord {
        for _ in 0..100 {
            {
                let st = hub.read().await;
                if let Some(j) = st.jobs.get(job_id) {
                    if jobs::is_terminal(&j.state) || j.protocol_error.is_some() {
                        return j.clone();
                    }
                }
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
        panic!("timeout waiting for {job_id}");
    }

    #[tokio::test]
    async fn custom_statuses_count_once_and_cross_reject() {
        let master_root = tmp("ms");
        let client_root = tmp("cs");
        write_status_skill(
            &master_root,
            "pin-reg",
            r#"[{"id":"logged_in","success":true,"retryable":false,"label":"已登录"},{"id":"email_confirmed","success":true,"retryable":false,"label":"邮箱已确认","optional":true},{"id":"oops_park","success":false,"retryable":true,"label":"风控先放"}]"#,
        );
        write_status_skill(
            &master_root,
            "ship-demo",
            r#"[{"id":"shipped","success":true,"retryable":false,"label":"Shipped"},{"id":"returned","success":false,"retryable":true,"label":"Returned"}]"#,
        );
        profiles::create(&client_root, "noproxy", None, None).unwrap();
        let pin = skill_pkg::pack_skill(&master_root, "pin-reg", Some("1.0.0"), true).unwrap();
        let ship = skill_pkg::pack_skill(&master_root, "ship-demo", Some("1.0.0"), true).unwrap();

        let port = free_port();
        let bind = format!("127.0.0.1:{port}");
        let token = "dev-token-test";
        let hub = new_hub(&master_root, token);
        write_master_meta(&master_root, &bind).unwrap();
        let ctrl = control_sock_path(&master_root);
        let hub_s = hub.clone();
        let bind_s = bind.clone();
        let serve = tokio::spawn(async move {
            let _ = serve_with_control(&bind_s, hub_s, Some(ctrl)).await;
        });
        for _ in 0..80 {
            if control_sock_path(&master_root).exists() {
                break;
            }
            tokio::time::sleep(Duration::from_millis(25)).await;
        }
        let client = tokio::spawn({
            let client_root = client_root.clone();
            let bind = bind.clone();
            async move {
                let _ = client_daemon::run(ClientDaemonConfig {
                    root: client_root,
                    master: bind,
                    token: token.into(),
                    client_id: "box-st".into(),
                })
                .await;
            }
        });
        for _ in 0..100 {
            let st = hub.read().await;
            if st.clients.get("box-st").map(|h| h.info.online).unwrap_or(false) {
                break;
            }
            drop(st);
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
        push_skill_sync(&hub, "box-st", "pin-reg", Some("1.0.0"))
            .await
            .expect("sync pin");
        push_skill_sync(&hub, "box-st", "ship-demo", Some("1.0.0"))
            .await
            .expect("sync ship");

        let spec = |job_id: &str, skill: &str, digest: &str, status: &str| JobSubmit {
            client_id: "box-st".into(),
            job_id: job_id.into(),
            skill_id: skill.into(),
            profile: "noproxy".into(),
            headed: false,
            vars: json!({"status": status}),
            version: Some("1.0.0".into()),
            digest: Some(digest.into()),
            account_id: None,
            geo: None,
        };

        submit_job(&hub, spec("j-login", "pin-reg", &pin.digest, "logged_in"))
            .await
            .unwrap();
        let rec = wait_job(&hub, "j-login").await;
        assert_eq!(rec.result.as_ref().unwrap().status, "logged_in");
        assert!(rec.success_counted);
        assert_eq!(crate::ledger::success_count(&master_root, "pin-reg").unwrap(), 1);

        submit_job(&hub, spec("j-oops", "pin-reg", &pin.digest, "oops_park"))
            .await
            .unwrap();
        let rec = wait_job(&hub, "j-oops").await;
        assert_eq!(rec.result.as_ref().unwrap().status, "oops_park");
        assert!(!rec.success_counted);
        assert_eq!(crate::ledger::success_count(&master_root, "pin-reg").unwrap(), 1);

        submit_job(&hub, spec("j-cross", "pin-reg", &pin.digest, "shipped"))
            .await
            .unwrap();
        let rec = wait_job(&hub, "j-cross").await;
        assert!(rec.result.is_none(), "cross-skill status must not land");
        assert!(rec.protocol_error.as_ref().unwrap().contains("unknown status"));
        assert_eq!(crate::ledger::success_count(&master_root, "pin-reg").unwrap(), 1);

        submit_job(&hub, spec("j-ship", "ship-demo", &ship.digest, "shipped"))
            .await
            .unwrap();
        let rec = wait_job(&hub, "j-ship").await;
        assert_eq!(rec.result.as_ref().unwrap().status, "shipped");
        assert_eq!(crate::ledger::success_count(&master_root, "ship-demo").unwrap(), 1);
        assert_eq!(crate::ledger::success_count(&master_root, "pin-reg").unwrap(), 1);

        client.abort();
        serve.abort();
        let _ = fs::remove_dir_all(&master_root);
        let _ = fs::remove_dir_all(&client_root);
    }
}
