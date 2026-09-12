//! Outbound client daemon: dials master, heartbeats, accepts job_submit.
//! NAT-friendly — no inbound port required on the client.
//!
//! DEV STUB: plaintext TCP + shared token. Cancel kills local oneshot worker.

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
            },
            "observed": obs_snapshot,
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
                        eprintln!("[client] skill_sync stub (hashes/TODO) — ack only");
                        let ack = Envelope::new("config_ack")
                            .with_client(&client_id)
                            .with_data(json!({
                                "kind": "skill_sync",
                                "ok": false,
                                "error": "skill_sync not implemented (dev stub)",
                                "observed": observed.lock().await.clone(),
                            }));
                        send_line(&writer, &ack).await?;
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
        .get("skill")
        .and_then(|v| v.as_str())
        .ok_or_else(|| anyhow::anyhow!("skill required"))?
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

    // Idempotent recovery: if already terminal locally, report and skip re-run.
    if let Ok(Some(existing)) = jobs::load(root, &job_id) {
        if jobs::is_terminal(&existing.state) {
            eprintln!(
                "[client] job {job_id} already {} — idempotent skip",
                existing.state
            );
            send_line(
                writer,
                &Envelope::new("job_state").with_client(client_id).with_data(json!({
                    "job_id": job_id,
                    "state": existing.state,
                    "error": existing.error,
                    "data": existing.data,
                    "idempotent": true,
                })),
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
        let _lock = ProfileLock::acquire(root, &profile, Duration::from_secs(300)).await?;
        let prof = profiles::get(root, &profile)?;
        let skill = skills::get(root, &skill_name)?;
        let resp = worker::oneshot_killable(
            root,
            WorkerReq {
                id: worker::next_id(),
                cmd: "run_skill".into(),
                profile: Some(prof.name.clone()),
                url: None,
                headed: Some(headed),
                skill: Some(skill.name.clone()),
                vars: Some(vars),
                session: None,
                proxy: prof.proxy.clone(),
                user_data_dir: Some(prof.user_data_dir.clone()),
                skill_path: Some(skill.path.join("skill.json").to_string_lossy().to_string()),
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
        Ok::<_, anyhow::Error>(resp.data.unwrap_or(json!({})))
    }
    .await;

    {
        let mut map = cancels.lock().await;
        map.remove(&job_id);
    }

    match result {
        Ok(data) => {
            let _ = jobs::upsert_state(
                root,
                &job_id,
                client_id,
                &skill_name,
                &profile,
                headed,
                "succeeded",
                None,
                Some(data.clone()),
            );
            send_line(
                writer,
                &Envelope::new("job_state").with_client(client_id).with_data(json!({
                    "job_id": job_id,
                    "state": "succeeded",
                    "data": data,
                })),
            )
            .await?;
        }
        Err(e) => {
            let cancelled = cancel_flag.load(Ordering::SeqCst)
                || e.to_string().contains("cancelled");
            let state_name = if cancelled { "cancelled" } else { "failed" };
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
            send_line(
                writer,
                &Envelope::new("job_state").with_client(client_id).with_data(json!({
                    "job_id": job_id,
                    "state": state_name,
                    "error": e.to_string(),
                })),
            )
            .await?;
        }
    }
    Ok(())
}
