//! Outbound client daemon: dials master, heartbeats, accepts job_submit.
//! NAT-friendly — no inbound port required on the client.
//!
//! DEV STUB: plaintext TCP + shared token. Cancel kills local oneshot worker.
//! TODO(prod): authenticated TLS/mTLS (or a controlled tunnel). Digest checks
//! do not replace source authentication.

use anyhow::{bail, Context, Result};
use serde_json::json;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::TcpStream;
use tokio::sync::Mutex;
use tokio::time::{interval, sleep};

use crate::cookies;
use crate::jobs;
use crate::locks::ProfileLock;
use crate::profiles;
use crate::protocol::{ConfigRevision, Envelope, PROTOCOL_VERSION};
use crate::skill_pkg;
use crate::skill_status;
use crate::skills;
use crate::state;
use crate::worker::{self, Request as WorkerReq};

pub struct ClientDaemonConfig {
    pub root: PathBuf,
    pub master: String, // host:port
    pub token: String,
    pub client_id: String,
}

/// Running job cancel flags (job_id → set true to kill worker).
type CancelMap = Arc<Mutex<HashMap<String, Arc<AtomicBool>>>>;

pub async fn run(cfg: ClientDaemonConfig) -> Result<()> {
    eprintln!(
        "client daemon id={} → master {} (outbound)",
        cfg.client_id, cfg.master
    );
    eprintln!("DEV STUB: plaintext shared token — not production");
    let mut backoff = 1u64;
    loop {
        match connect_session(&cfg).await {
            Ok(()) => {
                eprintln!("[client] session ended cleanly; reconnecting…");
                backoff = 1;
            }
            Err(e) => {
                eprintln!("[client] disconnected: {e}; retry in {backoff}s");
            }
        }
        sleep(Duration::from_secs(backoff)).await;
        backoff = (backoff * 2).min(30);
    }
}

async fn connect_session(cfg: &ClientDaemonConfig) -> Result<()> {
    let stream = TcpStream::connect(&cfg.master)
        .await
        .with_context(|| format!("connect to master {}", cfg.master))?;
    let (reader, writer) = stream.into_split();
    let writer = Arc::new(Mutex::new(writer));
    let mut reader = BufReader::new(reader);

    let observed = Arc::new(Mutex::new(ConfigRevision {
        revision: 0,
        concurrency: 2,
        headed: state::default_headed(),
        labels: vec![],
    }));

    let cancels: CancelMap = Arc::new(Mutex::new(HashMap::new()));

    // hello
    let obs_snapshot = observed.lock().await.clone();
    let hello = Envelope::new("hello")
        .with_client(&cfg.client_id)
        .with_data(json!({
            "token": cfg.token,
            "client_id": cfg.client_id,
            "capabilities": {
                "browser": true,
                "skills": true,
                "protocol": PROTOCOL_VERSION,
                "cancel": true,
                "config_update": true,
                "skill_sync": true,
                "skill_digest": true,
            },
            "observed": obs_snapshot,
            "installed_skills": skill_pkg::installed_for_hello(&cfg.root),
        }));
    send_line(&writer, &hello).await?;

    // wait hello_ok
    let mut buf = String::new();
    buf.clear();
    let n = reader.read_line(&mut buf).await?;
    if n == 0 {
        bail!("master closed during hello");
    }
    let resp: Envelope = serde_json::from_str(buf.trim())?;
    if resp.msg_type != "hello_ok" {
        bail!("expected hello_ok, got {}: {:?}", resp.msg_type, resp.data);
    }
    // Apply desired from hello_ok into observed (real revision, not echo-only).
    if let Some(desired_val) = resp.data.get("desired") {
        if let Ok(desired) = serde_json::from_value::<ConfigRevision>(desired_val.clone()) {
            let mut obs = observed.lock().await;
            *obs = desired;
            eprintln!(
                "[client] applied desired from hello_ok rev={}",
                obs.revision
            );
        }
    } else if let Some(rev) = resp.data.get("revision").and_then(|v| v.as_u64()) {
        observed.lock().await.revision = rev;
    }
    eprintln!("[client] registered with master OK");
    // Immediately report observed so master does not keep hello's rev=0.
    {
        let obs = observed.lock().await.clone();
        let ack = Envelope::new("config_ack").with_client(&cfg.client_id).with_data(json!({
            "ok": true,
            "kind": "hello_ok",
            "observed": obs,
        }));
        send_line(&writer, &ack).await?;
    }

    let mut hb = interval(Duration::from_secs(15));
    let root = cfg.root.clone();
    let client_id = cfg.client_id.clone();
    let writer_hb = writer.clone();
    let observed_hb = observed.clone();

    loop {
        tokio::select! {
            _ = hb.tick() => {
                let st = worker::daemon_status(&root);
                let obs = observed_hb.lock().await.clone();
                let env = Envelope::new("heartbeat")
                    .with_client(&client_id)
                    .with_data(json!({
                        "daemon_running": st.running,
                        "observed": obs,
                        "installed_skills": skill_pkg::installed_for_hello(&root),
                    }));
                send_line(&writer_hb, &env).await?;
            }
            line = read_line(&mut reader) => {
                let line = match line? {
                    Some(l) => l,
                    None => bail!("master closed connection"),
                };
                let env: Envelope = serde_json::from_str(&line)
                    .with_context(|| format!("parse master msg: {line}"))?;
                match env.msg_type.as_str() {
                    "job_submit" => {
                        let writer = writer.clone();
                        let root = root.clone();
                        let client_id = client_id.clone();
                        let cancels = cancels.clone();
                        tokio::spawn(async move {
                            if let Err(e) = handle_job(&root, &client_id, &env, &writer, &cancels).await {
                                eprintln!("[client] job error: {e}");
                            }
                        });
                    }
                    "job_cancel" => {
                        let job_id = env.data.get("job_id").and_then(|v| v.as_str()).unwrap_or("?").to_string();
                        eprintln!("[client] job_cancel {job_id} — signalling kill");
                        let map = cancels.lock().await;
                        if let Some(flag) = map.get(&job_id) {
                            flag.store(true, Ordering::SeqCst);
                        } else {
                            // No running handle: mark cancelled locally anyway.
                            let _ = jobs::upsert_state(
                                &root, &job_id, &client_id, "", "", false, "cancelled", None, None,
                            );
                        }
                        drop(map);
                        let st = Envelope::new("job_state")
                            .with_client(&client_id)
                            .with_data(json!({
                                "job_id": job_id,
                                "state": "cancelled",
                            }));
                        send_line(&writer, &st).await?;
                    }
                    "config_update" => {
                        handle_config_update(&client_id, &env, &observed, &writer).await?;
                    }
                    "skill_sync" => {
                        let writer = writer.clone();
                        let root = root.clone();
                        let client_id = client_id.clone();
                        tokio::spawn(async move {
                            if let Err(e) = handle_skill_sync(&root, &client_id, &env, &writer).await {
                                eprintln!("[client] skill_sync error: {e}");
                            }
                        });
                    }
                    "error" => {
                        eprintln!("[client] master error: {:?}", env.data);
                    }
                    other => {
                        eprintln!("[client] ignore msg type={other}");
                    }
                }
            }
        }
    }
}

async fn handle_config_update<W: AsyncWriteExt + Unpin>(
    client_id: &str,
    env: &Envelope,
    observed: &Arc<Mutex<ConfigRevision>>,
    writer: &Arc<Mutex<W>>,
) -> Result<()> {
    let prev = observed.lock().await.clone();
    let desired: ConfigRevision = if let Some(d) = env.data.get("desired") {
        serde_json::from_value(d.clone()).unwrap_or(prev.clone())
    } else {
        prev.clone()
    };

    let diff = json!({
        "revision": {"from": prev.revision, "to": desired.revision},
        "concurrency": {"from": prev.concurrency, "to": desired.concurrency},
        "headed": {"from": prev.headed, "to": desired.headed},
        "labels": {"from": prev.labels, "to": desired.labels},
    });

    {
        let mut obs = observed.lock().await;
        *obs = desired.clone();
    }
    eprintln!(
        "[client] config_update applied rev {} → {}",
        prev.revision, desired.revision
    );

    let ack = Envelope::new("config_ack").with_client(client_id).with_data(json!({
        "ok": true,
        "kind": "config_update",
        "diff": diff,
        "observed": desired,
    }));
    send_line(writer, &ack).await?;
    Ok(())
}

async fn read_line(
    reader: &mut BufReader<tokio::net::tcp::OwnedReadHalf>,
) -> Result<Option<String>> {
    let mut buf = String::new();
    let n = reader.read_line(&mut buf).await?;
    if n == 0 {
        return Ok(None);
    }
    Ok(Some(buf.trim().to_string()))
}

async fn send_line<W: AsyncWriteExt + Unpin>(
    writer: &Arc<Mutex<W>>,
    env: &Envelope,
) -> Result<()> {
    let line = serde_json::to_string(env)?;
    let mut w = writer.lock().await;
    w.write_all(line.as_bytes()).await?;
    w.write_all(b"\n").await?;
    w.flush().await?;
    Ok(())
}

async fn handle_skill_sync<W: AsyncWriteExt + Unpin>(
    root: &Path,
    client_id: &str,
    env: &Envelope,
    writer: &Arc<Mutex<W>>,
) -> Result<()> {
    let skill_id = env
        .data
        .get("skill_id")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let version = env
        .data
        .get("version")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let digest = env
        .data
        .get("digest")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let size = env.data.get("size").and_then(|v| v.as_u64());

    let result = (|| -> Result<skill_pkg::InstalledSkill> {
        if skill_id.is_empty() || digest.is_empty() {
            bail!("skill_sync requires skill_id and digest");
        }
        let b64 = env
            .data
            .get("package_b64")
            .and_then(|v| v.as_str())
            .ok_or_else(|| anyhow::anyhow!("skill_sync missing package_b64"))?;
        let bytes = skill_pkg::decode_package_b64(b64)?;
        if let Some(sz) = size {
            if sz != bytes.len() as u64 {
                bail!("package size mismatch: b64 decoded {} bytes, header size {sz}", bytes.len());
            }
        }
        skill_pkg::install_package(root, &skill_id, &version, &digest, &bytes)
    })();

    let (ok, error, ack_digest) = match &result {
        Ok(inst) => {
            eprintln!(
                "[client] skill_sync installed {}@{} digest={}",
                inst.skill_id, inst.version, inst.digest
            );
            (true, None, inst.digest.clone())
        }
        Err(e) => {
            eprintln!("[client] skill_sync failed: {e}");
            (false, Some(e.to_string()), digest)
        }
    };

    let mut ack = Envelope::new("skill_sync_ack")
        .with_client(client_id)
        .with_data(json!({
            "kind": "skill_sync",
            "ok": ok,
            "skill_id": skill_id,
            "version": version,
            "digest": ack_digest,
            "error": error,
        }));
    if let Some(rid) = &env.request_id {
        ack = ack.with_request(rid);
    }
    send_line(writer, &ack).await?;
    Ok(())
}

async fn handle_job<W: AsyncWriteExt + Unpin + Send + 'static>(
    root: &Path,
    client_id: &str,
    env: &Envelope,
    writer: &Arc<Mutex<W>>,
    cancels: &CancelMap,
) -> Result<()> {
    let job_id = env
        .data
        .get("job_id")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown")
        .to_string();
    let skill_name = env
        .data
        .get("skill_id")
        .or_else(|| env.data.get("skill"))
        .and_then(|v| v.as_str())
        .ok_or_else(|| anyhow::anyhow!("skill_id required"))?
        .to_string();
    let version = env
        .data
        .get("version")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let digest = env
        .data
        .get("digest")
        .and_then(|v| v.as_str())
        .ok_or_else(|| {
            anyhow::anyhow!(
                "job_submit requires skill digest (no local same-name fallback)"
            )
        })?
        .to_string();
    let profile = env
        .data
        .get("profile")
        .and_then(|v| v.as_str())
        .ok_or_else(|| anyhow::anyhow!("profile required"))?
        .to_string();
    let headed = env
        .data
        .get("headed")
        .and_then(|v| v.as_bool())
        .unwrap_or_else(state::default_headed);
    let vars = env.data.get("vars").cloned().unwrap_or(json!({}));
    let timeout_secs = env
        .data
        .get("timeout_secs")
        .and_then(|v| v.as_u64())
        .unwrap_or(300);
    skills::assert_no_plaintext_secrets(&vars)?;

    // Idempotent recovery: if already terminal locally, report and skip re-run.
    if let Ok(Some(existing)) = jobs::load(root, &job_id) {
        if jobs::is_terminal(&existing.state) {
            eprintln!(
                "[client] job {job_id} already {} — idempotent skip",
                existing.state
            );
            send_line(
                writer,
                &Envelope::new("job_state").with_client(client_id).with_data({
                    let mut v = json!({
                        "job_id": job_id,
                        "state": existing.state,
                        "error": existing.error,
                        "data": existing.data,
                        "idempotent": true,
                    });
                    if let Some(pe) = &existing.protocol_error {
                        v["protocol_error"] = json!(pe);
                    }
                    if let Some(r) = &existing.result {
                        v["result"] = skill_status::result_to_json(r);
                    }
                    v
                }),
            )
            .await?;
            return Ok(());
        }
    }

    let cancel_flag = Arc::new(AtomicBool::new(false));
    {
        let mut map = cancels.lock().await;
        map.insert(job_id.clone(), cancel_flag.clone());
    }

    let _ = jobs::upsert_state(
        root,
        &job_id,
        client_id,
        &skill_name,
        &profile,
        headed,
        "running",
        None,
        None,
    );

    send_line(
        writer,
        &Envelope::new("job_state").with_client(client_id).with_data(json!({
            "job_id": job_id,
            "state": "running",
        })),
    )
    .await?;
    send_line(
        writer,
        &Envelope::new("log_chunk").with_client(client_id).with_data(json!({
            "job_id": job_id,
            "seq": 1,
            "text": format!("starting skill={skill_name} profile={profile}"),
        })),
    )
    .await?;

    let result = async {
        if cancel_flag.load(Ordering::SeqCst) {
            bail!("cancelled before start");
        }
        let _lock = ProfileLock::acquire(root, &profile, Duration::from_secs(timeout_secs)).await?;
        let prof = profiles::get(root, &profile)?;
        // Digest cache only — never fall back to skills/<name> on this node.
        let pkg_dir = skill_pkg::lookup_installed(root, &skill_name, &digest)?;
        let manifest = skill_pkg::entry_from_package(&pkg_dir)?;
        match manifest.entry.kind {
            skill_pkg::SkillEntryKind::PythonRunner => {
                let rel = manifest
                    .entry
                    .path
                    .as_deref()
                    .ok_or_else(|| anyhow::anyhow!("python_runner missing path"))?;
                let payload = json!({
                    "job_id": job_id,
                    "skill_id": skill_name,
                    "version": version,
                    "digest": digest,
                    "profile": profile,
                    "geo": env.data.get("geo"),
                    "account_id": env.data.get("account_id"),
                    "vars": vars.clone(),
                });
                skill_pkg::run_python_runner(
                    &pkg_dir,
                    rel,
                    &payload,
                    Some(cancel_flag.clone()),
                    Duration::from_secs(timeout_secs),
                )
                .await
            }
            skill_pkg::SkillEntryKind::SkillSteps => {
                let skill_json = pkg_dir.join("skill.json");
                let resp = worker::oneshot_killable(
                    root,
                    WorkerReq {
                        id: worker::next_id(),
                        cmd: "run_skill".into(),
                        profile: Some(prof.name.clone()),
                        url: None,
                        headed: Some(headed),
                        skill: Some(skill_name.clone()),
                        vars: Some(vars),
                        session: None,
                        proxy: prof.proxy.clone(),
                        user_data_dir: Some(prof.user_data_dir.clone()),
                        skill_path: Some(skill_json.to_string_lossy().to_string()),
                        root: Some(root.to_string_lossy().to_string()),
                        cookie_file: cookies::cookie_file_for_open(root, &prof.name)?,
                    },
                    cancel_flag.clone(),
                )
                .await?;
                if cancel_flag.load(Ordering::SeqCst) {
                    bail!("cancelled");
                }
                if !resp.ok {
                    bail!(resp.error.unwrap_or_else(|| "run_skill failed".into()));
                }
                Ok(resp.data.unwrap_or(json!({})))
            }
        }
    }
    .await;

    {
        let mut map = cancels.lock().await;
        map.remove(&job_id);
    }

    match result {
        Ok(data) => {
            if skill_status::is_paused_report(&data) {
                let _ = jobs::upsert_state(
                    root,
                    &job_id,
                    client_id,
                    &skill_name,
                    &profile,
                    headed,
                    "paused",
                    None,
                    Some(data.clone()),
                );
                send_line(
                    writer,
                    &Envelope::new("job_state").with_client(client_id).with_data(json!({
                        "job_id": job_id,
                        "state": "paused",
                        "data": data,
                    })),
                )
                .await?;
                return Ok(());
            }
            let identity = match skill_status::identity_from_job(&skill_name, &version, &digest) {
                Ok(id) => id,
                Err(e) => {
                    send_protocol_fail(
                        root,
                        writer,
                        client_id,
                        &job_id,
                        &skill_name,
                        &profile,
                        headed,
                        &e.to_string(),
                        Some(data),
                    )
                    .await?;
                    return Ok(());
                }
            };
            let manifest = match skill_pkg::lookup_installed(root, &skill_name, &digest)
                .and_then(|d| skill_pkg::entry_from_package(&d))
            {
                Ok(m) => m,
                Err(e) => {
                    send_protocol_fail(
                        root,
                        writer,
                        client_id,
                        &job_id,
                        &skill_name,
                        &profile,
                        headed,
                        &e.to_string(),
                        Some(data),
                    )
                    .await?;
                    return Ok(());
                }
            };
            let decls = skill_status::decls_from_manifest(&manifest);
            let adapted = match manifest.entry.kind {
                skill_pkg::SkillEntryKind::PythonRunner => {
                    skill_status::adapt_python_report(&data, &identity, decls.as_deref())
                }
                skill_pkg::SkillEntryKind::SkillSteps => {
                    skill_status::adapt_skill_steps_report(&data, &identity, decls.as_deref())
                }
            };
            match adapted {
                Ok(res) => {
                    let state_name = skill_status::scheduler_state_for(&res);
                    let _ = jobs::upsert_state(
                        root,
                        &job_id,
                        client_id,
                        &skill_name,
                        &profile,
                        headed,
                        state_name,
                        None,
                        Some(data.clone()),
                    );
                    send_line(
                        writer,
                        &Envelope::new("job_state").with_client(client_id).with_data(json!({
                            "job_id": job_id,
                            "state": state_name,
                            "data": data,
                            // Master re-validates; success/label here are not authoritative.
                        })),
                    )
                    .await?;
                }
                Err(pe) => {
                    send_protocol_fail(
                        root,
                        writer,
                        client_id,
                        &job_id,
                        &skill_name,
                        &profile,
                        headed,
                        &pe.as_message(),
                        Some(data),
                    )
                    .await?;
                }
            }
        }
        Err(e) => {
            let msg = e.to_string();
            let cancelled = cancel_flag.load(Ordering::SeqCst) || msg.contains("cancelled");
            let paused = msg.contains("ASK_HUMAN") || msg.contains("paused_ask_human");
            let protocol = msg.starts_with("protocol:");
            let state_name = if cancelled {
                "cancelled"
            } else if paused {
                "paused"
            } else {
                "failed"
            };
            let _ = jobs::upsert_state(
                root,
                &job_id,
                client_id,
                &skill_name,
                &profile,
                headed,
                state_name,
                Some(e.to_string()),
                None,
            );
            let mut payload = json!({
                "job_id": job_id,
                "state": state_name,
                "error": e.to_string(),
            });
            if protocol {
                payload["protocol_error"] = json!(e.to_string());
            }
            send_line(
                writer,
                &Envelope::new("job_state").with_client(client_id).with_data(payload),
            )
            .await?;
        }
    }
    Ok(())
}

async fn send_protocol_fail<W: AsyncWriteExt + Unpin>(
    root: &Path,
    writer: &Arc<Mutex<W>>,
    client_id: &str,
    job_id: &str,
    skill_name: &str,
    profile: &str,
    headed: bool,
    message: &str,
    data: Option<serde_json::Value>,
) -> Result<()> {
    let _ = jobs::upsert_state(
        root,
        job_id,
        client_id,
        skill_name,
        profile,
        headed,
        "failed",
        Some(message.to_string()),
        data.clone(),
    );
    send_line(
        writer,
        &Envelope::new("job_state").with_client(client_id).with_data(json!({
            "job_id": job_id,
            "state": "failed",
            "error": message,
            "protocol_error": message,
            "data": data,
        })),
    )
    .await?;
    Ok(())
}
