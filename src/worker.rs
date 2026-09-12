use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;
use std::env;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::UnixStream;
use tokio::process::{Child, ChildStdin, ChildStdout, Command};
use tokio::sync::Mutex;
use tokio::time::{sleep, timeout};

use crate::state;

static NEXT_ID: AtomicU64 = AtomicU64::new(1);

const DAEMON_START_WAIT: Duration = Duration::from_secs(15);
const CONNECT_TIMEOUT: Duration = Duration::from_secs(5);

#[derive(Debug, Serialize)]
pub struct Request {
    pub id: String,
    pub cmd: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub profile: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub url: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub headed: Option<bool>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub skill: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub vars: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub session: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub proxy: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub user_data_dir: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub skill_path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub root: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub cookie_file: Option<String>,
}

#[derive(Debug, Deserialize)]
pub struct Response {
    pub id: String,
    pub ok: bool,
    #[serde(default)]
    pub data: Option<Value>,
    #[serde(default)]
    pub error: Option<String>,
}

/// Short-lived stdin/stdout worker (skill run / oneshot).
pub struct Worker {
    child: Child,
    stdin: Mutex<ChildStdin>,
    stdout: Mutex<BufReader<ChildStdout>>,
}

impl Worker {
    pub async fn spawn(root: &Path) -> Result<Self> {
        let (python, mut cmd) = python_command(root)?;
        cmd.stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .current_dir(root)
            .env("PYTHONUNBUFFERED", "1");

        let worker_parent = root.join("python");
        let mut pp = worker_parent.to_string_lossy().to_string();
        if let Ok(existing) = env::var("PYTHONPATH") {
            if !existing.is_empty() {
                pp = format!("{pp}:{existing}");
            }
        }
        cmd.env("PYTHONPATH", pp);
        cmd.env("CLOAKCLI_ROOT", root);

        let mut child = cmd
            .spawn()
            .with_context(|| format!("spawn worker via {python}"))?;
        let stdin = child.stdin.take().context("worker stdin")?;
        let stdout = child.stdout.take().context("worker stdout")?;
        Ok(Self {
            child,
            stdin: Mutex::new(stdin),
            stdout: Mutex::new(BufReader::new(stdout)),
        })
    }

    pub fn pid(&self) -> Option<u32> {
        self.child.id()
    }

    pub async fn request(&self, mut req: Request) -> Result<Response> {
        if req.id.is_empty() {
            req.id = NEXT_ID.fetch_add(1, Ordering::SeqCst).to_string();
        }
        let want_id = req.id.clone();
        let line = serde_json::to_string(&req)?;
        {
            let mut stdin = self.stdin.lock().await;
            stdin.write_all(line.as_bytes()).await?;
            stdin.write_all(b"\n").await?;
            stdin.flush().await?;
        }

        let ipc_timeout = Duration::from_secs(state::ipc_timeout_secs());
        let result = timeout(ipc_timeout, async {
            let mut stdout = self.stdout.lock().await;
            let mut buf = String::new();
            loop {
                buf.clear();
                let n = stdout.read_line(&mut buf).await?;
                if n == 0 {
                    bail!("worker closed stdout (process died?)");
                }
                let trimmed = buf.trim();
                if trimmed.is_empty() {
                    continue;
                }
                if !trimmed.starts_with('{') {
                    eprintln!("[worker] {trimmed}");
                    continue;
                }
                let resp: Response = serde_json::from_str(trimmed)
                    .with_context(|| format!("parse worker response: {trimmed}"))?;
                if resp.id == want_id {
                    return Ok(resp);
                }
                // id mismatch: skip stale/out-of-order; do not hang forever (timeout wraps us)
                eprintln!(
                    "[worker] ignoring response id={} (want {want_id})",
                    resp.id
                );
            }
        })
        .await;

        match result {
            Ok(inner) => inner,
            Err(_) => bail!(
                "IPC timeout after {}s waiting for worker response id={want_id}",
                state::ipc_timeout_secs()
            ),
        }
    }

    /// Graceful shutdown: send shutdown, wait briefly, then kill if needed.
    pub async fn shutdown(mut self) -> Result<()> {
        let _ = timeout(
            Duration::from_secs(5),
            self.request(Request {
                id: "shutdown".into(),
                cmd: "shutdown".into(),
                profile: None,
                url: None,
                headed: None,
                skill: None,
                vars: None,
                session: None,
                proxy: None,
                user_data_dir: None,
                skill_path: None,
                root: None,
            cookie_file: None,
            }),
        )
        .await;

        match timeout(Duration::from_secs(3), self.child.wait()).await {
            Ok(Ok(_)) => Ok(()),
            _ => {
                let _ = self.child.kill().await;
                let _ = self.child.wait().await;
                Ok(())
            }
        }
    }
}

fn python_command(root: &Path) -> Result<(String, Command)> {
    if let Ok(bin) = env::var("CLOAKCLI_PYTHON") {
        let mut c = Command::new(&bin);
        c.arg("-m").arg("cloakcli_worker");
        return Ok((bin, c));
    }

    let candidates = ["python3", "python"];
    for name in candidates {
        if which::which(name).is_ok() {
            let mut c = Command::new(name);
            c.arg("-m").arg("cloakcli_worker");
            let _ = root;
            return Ok((name.to_string(), c));
        }
    }
    bail!("python3 not found; set CLOAKCLI_PYTHON");
}

/// One-shot: spawn worker, send one request, graceful shutdown.
pub async fn oneshot(root: &Path, req: Request) -> Result<Response> {
    let worker = Worker::spawn(root).await?;
    let resp = worker.request(req).await;
    let _ = worker.shutdown().await;
    resp
}

/// One-shot that can be killed when `cancel` becomes true (fleet job_cancel).
pub async fn oneshot_killable(
    root: &Path,
    req: Request,
    cancel: std::sync::Arc<std::sync::atomic::AtomicBool>,
) -> Result<Response> {
    use std::sync::atomic::Ordering;
    let worker = Worker::spawn(root).await?;
    let pid = worker.pid();
    let cancel_watch = cancel.clone();
    // Separate stop flag so we do NOT flip the caller's cancel bit on normal exit.
    let stop = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    let stop_watch = stop.clone();
    let killer = tokio::spawn(async move {
        loop {
            if stop_watch.load(Ordering::SeqCst) {
                break;
            }
            if cancel_watch.load(Ordering::SeqCst) {
                if let Some(pid) = pid {
                    let _ = std::process::Command::new("kill")
                        .args(["-TERM", &pid.to_string()])
                        .status();
                    sleep(Duration::from_millis(400)).await;
                    let _ = std::process::Command::new("kill")
                        .args(["-KILL", &pid.to_string()])
                        .status();
                }
                break;
            }
            sleep(Duration::from_millis(100)).await;
        }
    });

    let resp = worker.request(req).await;
    let was_cancelled = cancel.load(Ordering::SeqCst);
    stop.store(true, Ordering::SeqCst);
    let _ = killer.await;
    let _ = worker.shutdown().await;

    if was_cancelled {
        match resp {
            Ok(r) if r.ok => Ok(r),
            Ok(r) => bail!(r.error.unwrap_or_else(|| "cancelled".into())),
            Err(_) => bail!("cancelled"),
        }
    } else {
        resp
    }
}

// ── Daemon (unix socket) ──────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct DaemonStatus {
    pub running: bool,
    pub pid: Option<u32>,
    pub sock: PathBuf,
    pub detail: String,
}

pub fn daemon_status(root: &Path) -> DaemonStatus {
    let sock = state::worker_sock(root);
    let pid_file = state::worker_pid_file(root);
    let pid = std::fs::read_to_string(&pid_file)
        .ok()
        .and_then(|s| s.trim().parse::<u32>().ok());

    let pid_alive = pid.map(process_alive).unwrap_or(false);
    let sock_exists = sock.exists();

    if pid_alive && sock_exists {
        DaemonStatus {
            running: true,
            pid,
            sock,
            detail: format!("running pid={}", pid.unwrap_or(0)),
        }
    } else if sock_exists && !pid_alive {
        DaemonStatus {
            running: false,
            pid,
            sock,
            detail: "stale socket (pid dead)".into(),
        }
    } else if pid_alive && !sock_exists {
        DaemonStatus {
            running: true,
            pid,
            sock,
            detail: format!("pid {} alive but socket missing", pid.unwrap_or(0)),
        }
    } else {
        DaemonStatus {
            running: false,
            pid: None,
            sock,
            detail: "stopped".into(),
        }
    }
}

fn process_alive(pid: u32) -> bool {
    Path::new(&format!("/proc/{pid}")).exists()
}

/// Start daemon in background (python -m cloakcli_worker serve).
pub async fn daemon_serve(root: &Path) -> Result<()> {
    let st = daemon_status(root);
    if st.running {
        println!("worker daemon already running ({})", st.detail);
        return Ok(());
    }

    // Clean stale socket
    let sock = state::worker_sock(root);
    let pid_file = state::worker_pid_file(root);
    std::fs::create_dir_all(state::data_dir(root))?;
    if sock.exists() {
        let _ = std::fs::remove_file(&sock);
    }

    let (python, mut cmd) = python_command(root)?;
    cmd.arg("serve")
        .arg("--root")
        .arg(root)
        .arg("--socket")
        .arg(&sock)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::inherit())
        .current_dir(root)
        .env("PYTHONUNBUFFERED", "1")
        .kill_on_drop(false);

    let worker_parent = root.join("python");
    let mut pp = worker_parent.to_string_lossy().to_string();
    if let Ok(existing) = env::var("PYTHONPATH") {
        if !existing.is_empty() {
            pp = format!("{pp}:{existing}");
        }
    }
    cmd.env("PYTHONPATH", pp);
    cmd.env("CLOAKCLI_ROOT", root);

    let child = cmd
        .spawn()
        .with_context(|| format!("spawn daemon via {python}"))?;
    let pid = child.id().unwrap_or(0);
    // Detach: leak/forget the Child so we don't kill on drop
    std::mem::forget(child);

    std::fs::write(&pid_file, format!("{pid}\n"))?;

    // Wait for socket
    let start = std::time::Instant::now();
    while start.elapsed() < DAEMON_START_WAIT {
        if sock.exists() {
            // quick ping
            match daemon_request_raw(root, Request {
                id: "boot".into(),
                cmd: "ping".into(),
                profile: None,
                url: None,
                headed: None,
                skill: None,
                vars: None,
                session: None,
                proxy: None,
                user_data_dir: None,
                skill_path: None,
                root: None,
            cookie_file: None,
            })
            .await
            {
                Ok(r) if r.ok => {
                    println!("worker daemon started pid={pid} sock={}", sock.display());
                    return Ok(());
                }
                _ => {}
            }
        }
        sleep(Duration::from_millis(100)).await;
    }
    bail!(
        "daemon started (pid={pid}) but socket not ready within {}s: {}",
        DAEMON_START_WAIT.as_secs(),
        sock.display()
    );
}

/// Stop daemon: send shutdown over socket, wait, then SIGTERM/SIGKILL.
pub async fn daemon_stop(root: &Path) -> Result<()> {
    let st = daemon_status(root);
    let sock = state::worker_sock(root);
    let pid_file = state::worker_pid_file(root);

    if sock.exists() {
        let _ = timeout(
            Duration::from_secs(10),
            daemon_request_raw(
                root,
                Request {
                    id: "stop".into(),
                    cmd: "shutdown".into(),
                    profile: None,
                    url: None,
                    headed: None,
                    skill: None,
                    vars: None,
                    session: None,
                    proxy: None,
                    user_data_dir: None,
                    skill_path: None,
                    root: None,
                    cookie_file: None,
                },
            ),
        )
        .await;
    }

    if let Some(pid) = st.pid {
        // wait for graceful exit
        let deadline = std::time::Instant::now() + Duration::from_secs(5);
        while process_alive(pid) && std::time::Instant::now() < deadline {
            sleep(Duration::from_millis(100)).await;
        }
        if process_alive(pid) {
            let _ = std::process::Command::new("kill")
                .args(["-TERM", &pid.to_string()])
                .status();
            sleep(Duration::from_millis(500)).await;
        }
        if process_alive(pid) {
            let _ = std::process::Command::new("kill")
                .args(["-KILL", &pid.to_string()])
                .status();
        }
    }

    let _ = std::fs::remove_file(&sock);
    let _ = std::fs::remove_file(&pid_file);
    println!("worker daemon stopped");
    Ok(())
}

/// Ensure daemon is running (auto-spawn if needed).
pub async fn ensure_daemon(root: &Path) -> Result<()> {
    let st = daemon_status(root);
    if st.running && st.sock.exists() {
        // verify with quick ping
        if let Ok(r) = timeout(Duration::from_secs(3), daemon_request_raw(root, Request {
            id: "ensure".into(),
            cmd: "ping".into(),
            profile: None,
            url: None,
            headed: None,
            skill: None,
            vars: None,
            session: None,
            proxy: None,
            user_data_dir: None,
            skill_path: None,
            root: None,
        cookie_file: None,
        }))
        .await
        {
            if let Ok(resp) = r {
                if resp.ok {
                    return Ok(());
                }
            }
        }
        // ping failed — restart
        let _ = daemon_stop(root).await;
    } else if st.sock.exists() && !st.running {
        let _ = std::fs::remove_file(&st.sock);
    }
    daemon_serve(root).await
}

/// Send a request to the persistent daemon (auto-spawns).
pub async fn daemon_request(root: &Path, req: Request) -> Result<Response> {
    ensure_daemon(root).await?;
    daemon_request_raw(root, req).await
}

async fn daemon_request_raw(root: &Path, mut req: Request) -> Result<Response> {
    if req.id.is_empty() {
        req.id = NEXT_ID.fetch_add(1, Ordering::SeqCst).to_string();
    }
    let want_id = req.id.clone();
    let sock = state::worker_sock(root);

    let stream = timeout(CONNECT_TIMEOUT, UnixStream::connect(&sock))
        .await
        .map_err(|_| anyhow::anyhow!("connect timeout to {}", sock.display()))?
        .with_context(|| format!("connect to daemon socket {}", sock.display()))?;

    let (reader, mut writer) = stream.into_split();
    let line = serde_json::to_string(&req)?;
    writer.write_all(line.as_bytes()).await?;
    writer.write_all(b"\n").await?;
    writer.flush().await?;

    let ipc_timeout = Duration::from_secs(state::ipc_timeout_secs());
    let result = timeout(ipc_timeout, async {
        let mut reader = BufReader::new(reader);
        let mut buf = String::new();
        loop {
            buf.clear();
            let n = reader.read_line(&mut buf).await?;
            if n == 0 {
                bail!("daemon closed connection (dead?)");
            }
            let trimmed = buf.trim();
            if trimmed.is_empty() {
                continue;
            }
            if !trimmed.starts_with('{') {
                eprintln!("[daemon] {trimmed}");
                continue;
            }
            let resp: Response = serde_json::from_str(trimmed)
                .with_context(|| format!("parse daemon response: {trimmed}"))?;
            if resp.id == want_id {
                return Ok(resp);
            }
            eprintln!(
                "[daemon] ignoring response id={} (want {want_id})",
                resp.id
            );
        }
    })
    .await;

    match result {
        Ok(inner) => inner,
        Err(_) => bail!(
            "IPC timeout after {}s waiting for daemon response id={want_id}",
            state::ipc_timeout_secs()
        ),
    }
}

pub fn doctor_python(root: &Path) -> HashMap<&'static str, String> {
    let mut info = HashMap::new();
    let py = env::var("CLOAKCLI_PYTHON").unwrap_or_else(|_| "python3".into());
    info.insert("python", py.clone());

    let worker_pkg = root.join("python").join("cloakcli_worker");
    info.insert(
        "worker_path",
        if worker_pkg.is_dir() {
            worker_pkg.display().to_string()
        } else {
            "MISSING".into()
        },
    );

    let status = std::process::Command::new(&py)
        .args([
            "-c",
            "import cloakbrowser; print(getattr(cloakbrowser,'__version__','?'))",
        ])
        .output();
    match status {
        Ok(o) if o.status.success() => {
            info.insert(
                "cloakbrowser",
                String::from_utf8_lossy(&o.stdout).trim().to_string(),
            );
        }
        Ok(o) => {
            info.insert(
                "cloakbrowser",
                format!(
                    "IMPORT FAILED: {}",
                    String::from_utf8_lossy(&o.stderr).trim()
                ),
            );
        }
        Err(e) => {
            info.insert("cloakbrowser", format!("python error: {e}"));
        }
    }

    let mut cmd = std::process::Command::new(&py);
    cmd.env(
        "PYTHONPATH",
        root.join("python").to_string_lossy().as_ref(),
    );
    cmd.args(["-c", "import cloakcli_worker; print('ok')"]);
    match cmd.output() {
        Ok(o) if o.status.success() => {
            info.insert("worker_import", "ok".into());
        }
        Ok(o) => {
            info.insert(
                "worker_import",
                format!("FAIL: {}", String::from_utf8_lossy(&o.stderr).trim()),
            );
        }
        Err(e) => {
            info.insert("worker_import", format!("error: {e}"));
        }
    }

    if let Ok(p) = which::which("cloakbrowser") {
        info.insert("cloakbrowser_bin", p.display().to_string());
    } else {
        info.insert("cloakbrowser_bin", "not on PATH".into());
    }

    let ds = daemon_status(root);
    info.insert(
        "daemon",
        if ds.running {
            ds.detail
        } else {
            format!("stopped ({})", ds.detail)
        },
    );
    info.insert("daemon_sock", ds.sock.display().to_string());

    info
}

pub fn next_id() -> String {
    NEXT_ID.fetch_add(1, Ordering::SeqCst).to_string()
}
