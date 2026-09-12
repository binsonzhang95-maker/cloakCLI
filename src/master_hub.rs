//! Master hub: accepts outbound connections from remote clients.
//! Clients dial in (NAT-friendly); master never requires inbound on clients.
//!
//! DEV STUB: plaintext TCP JSONL + shared token — not production security.

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

#[derive(Debug, Clone)]
pub struct ClientInfo {
    pub client_id: String,
    pub last_seen: Instant,
    pub online: bool,
    pub capabilities: serde_json::Value,
    pub observed: ConfigRevision,
    pub last_job_state: Option<serde_json::Value>,
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
}

impl HubState {
    pub fn new(root: PathBuf, token: String) -> Self {
        let desired = load_desired(&root).unwrap_or(ConfigRevision {
            revision: 1,
            concurrency: 2,
            headed: false,
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
                    })
                })
                .collect();
            json!({"ok": true, "clients": clients, "log": state.job_log, "desired": state.desired})
        }
        "submit" => {
            let client_id = req.get("client_id").and_then(|v| v.as_str()).unwrap_or("");
            let skill = req.get("skill").and_then(|v| v.as_str()).unwrap_or("hello");
            let profile = req.get("profile").and_then(|v| v.as_str()).unwrap_or("noproxy");
            let headed = req.get("headed").and_then(|v| v.as_bool()).unwrap_or(false);
            let job_id = req
                .get("job_id")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string())
                .unwrap_or_else(|| uuid::Uuid::new_v4().simple().to_string());
            match submit_job(
                &hub,
                client_id,
                &job_id,
                skill,
                profile,
                headed,
                req.get("vars").cloned().unwrap_or(json!({})),
            )
            .await
            {
                Ok(()) => json!({"ok": true, "job_id": job_id}),
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

                let info = ClientInfo {
                    client_id: cid.clone(),
                    last_seen: Instant::now(),
                    online: true,
                    capabilities: caps,
                    observed,
                    last_job_state: None,
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
                    if let Some(h) = state.clients.get_mut(&cid) {
                        h.info.last_job_state = Some(env.data.clone());
                    }
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
                    if !job_id.is_empty() {
                        let err = env
                            .data
                            .get("error")
                            .and_then(|v| v.as_str())
                            .map(|s| s.to_string());
                        let data = env.data.get("data").cloned();
                        let (skill, profile, headed) = state
                            .jobs
                            .get(job_id)
                            .map(|r| (r.skill.clone(), r.profile.clone(), r.headed))
                            .unwrap_or_else(|| ("".into(), "".into(), false));
                        if let Ok(rec) = jobs::upsert_state(
                            &state.root,
                            job_id,
                            &cid,
                            &skill,
                            &profile,
                            headed,
                            st,
                            err,
                            data,
                        ) {
                            state.jobs.insert(job_id.to_string(), rec);
                        }
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

/// Submit a job to a connected client.
/// Idempotent: if job_id already terminal on disk, do not re-dispatch (return Ok).
pub async fn submit_job(
    hub: &SharedHub,
    client_id: &str,
    job_id: &str,
    skill: &str,
    profile: &str,
    headed: bool,
    vars: serde_json::Value,
) -> Result<()> {
    {
        let mut state = hub.write().await;
        if let Some(existing) = state.jobs.get(job_id) {
            if jobs::is_terminal(&existing.state) {
                let st = existing.state.clone();
                state.push_log(format!("job {job_id} already {st} — idempotent skip"));
                return Ok(());
            }
        }
        let rec = jobs::upsert_state(
            &state.root,
            job_id,
            client_id,
            skill,
            profile,
            headed,
            "queued",
            None,
            None,
        )?;
        state.jobs.insert(job_id.to_string(), rec);
        state.push_log(format!("submit {job_id} → {client_id} skill={skill}"));
    }

    let state = hub.read().await;
    let Some(h) = state.clients.get(client_id) else {
        bail!("client not connected: {client_id}");
    };
    if !h.info.online {
        bail!("client offline: {client_id}");
    }
    let env = Envelope::new("job_submit")
        .with_client(client_id)
        .with_request(job_id)
        .with_data(json!({
            "job_id": job_id,
            "skill": skill,
            "profile": profile,
            "headed": headed,
            "vars": vars,
            "timeout_secs": 300,
        }));
    let line = serde_json::to_string(&env)?;
    h.tx
        .send(line)
        .map_err(|_| anyhow::anyhow!("failed to send to client {client_id}"))?;
    Ok(())
}

/// Cancel job on client (client must stop local worker work).
pub async fn cancel_job(hub: &SharedHub, client_id: &str, job_id: &str) -> Result<()> {
    {
        let mut state = hub.write().await;
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
        "config_update rev={} concurrency={} headed={}",
        desired.revision, desired.concurrency, desired.headed
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
