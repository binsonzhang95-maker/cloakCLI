//! TEACH PATH (not recover): headed recorder + skill export.
//!
//! Records via the MV3 extension at `extensions/teach/` with **no per-step LLM**.
//! On export, local post-process (merge fills, denoise, backup selector chains)
//! always runs. A default-ON one-shot "smart optimize" may then call the model
//! once for a structured patch; the patch is validated locally before save.
//! Optional mid-record assist is capped at 2 LLM calls and deferred when not cheap.
//!
//! Stall-recovery lives in `python/cloakcli_worker/recover/` — do not mix the paths.
//! Master/fleet never teach; they consume exported `skills/<name>/skill.json`.

use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{BTreeSet, HashMap};
use std::fs;
use std::net::TcpListener as StdTcpListener;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::process::Command;

use crate::locks::ProfileLock;
use crate::profiles;
use crate::state;
use crate::util;

const SECRET_QUERY_KEYS: &[&str] = &[
    "token",
    "access_token",
    "refresh_token",
    "id_token",
    "api_key",
    "apikey",
    "api-key",
    "auth",
    "authorization",
    "password",
    "passwd",
    "secret",
    "session",
    "sessionid",
    "jwt",
    "cookie",
    "client_secret",
    "code",
];

const SECRET_FIELD_MARKERS: &[&str] = &[
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
];

/// CLI / TUI options for `teach start`. Extension path is never a user argument.
pub struct TeachStartOpts {
    pub profile: String,
    pub url: Option<String>,
    pub allow_secrets: bool,
    /// TEACH PATH: one-shot LLM optimize after export (default ON).
    pub smart_optimize: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct ExtConfig {
    #[serde(rename = "exportOrigin")]
    export_origin: String,
    token: String,
    #[serde(rename = "allowOrigins")]
    allow_origins: Vec<String>,
    #[serde(rename = "allowSecrets")]
    allow_secrets: bool,
    #[serde(rename = "smartOptimize", default = "default_true")]
    smart_optimize: bool,
    profile: String,
    #[serde(rename = "hubUrl", skip_serializing_if = "Option::is_none")]
    hub_url: Option<String>,
    #[serde(rename = "pairingCode", skip_serializing_if = "Option::is_none")]
    pairing_code: Option<String>,
    #[serde(rename = "pairingId", skip_serializing_if = "Option::is_none")]
    pairing_id: Option<String>,
}

fn default_true() -> bool {
    true
}

#[derive(Debug, Deserialize)]
struct ExportBody {
    name: String,
    #[serde(default)]
    goal: Option<String>,
    #[serde(default)]
    events: Vec<RecordedEvent>,
    /// Popup/CLI toggle; default ON when omitted.
    #[serde(default, rename = "smartOptimize")]
    smart_optimize: Option<bool>,
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct RecordedEvent {
    #[serde(default)]
    pub kind: String,
    #[serde(default)]
    pub url: Option<String>,
    #[serde(default)]
    pub selector: Option<String>,
    /// Backup selector chain from the extension (id/name/testid/…).
    #[serde(default)]
    pub selectors: Vec<String>,
    #[serde(default)]
    pub value: Option<String>,
    #[serde(default)]
    pub text: Option<String>,
    #[serde(default)]
    pub field: Option<FieldHint>,
    #[serde(default)]
    #[allow(dead_code)]
    pub unstable: bool,
    #[serde(default)]
    pub role: Option<String>,
    #[serde(default)]
    pub label: Option<String>,
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct FieldHint {
    #[serde(default, rename = "type")]
    pub input_type: Option<String>,
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub id: Option<String>,
    #[serde(default)]
    pub autocomplete: Option<String>,
    #[serde(default)]
    pub tag: Option<String>,
    #[serde(default)]
    pub placeholder: Option<String>,
    #[serde(default)]
    pub testid: Option<String>,
}

#[derive(Debug, Deserialize)]
struct AssistBody {
    #[serde(default)]
    selector: Option<String>,
    #[serde(default)]
    selectors: Vec<String>,
    #[serde(default)]
    field: Option<Value>,
}

#[derive(Debug, Clone)]
pub struct MappedSkill {
    pub name: String,
    pub goal: Option<String>,
    pub description: String,
    pub params: Vec<Value>,
    pub steps: Vec<Value>,
    pub vars: Vec<String>,
    pub allow_secrets: bool,
    /// When false (default), refuse to overwrite an existing skill.json.
    pub overwrite: bool,
    pub from_chat: bool,
    pub audit_text: Option<String>,
}

#[derive(Debug, Clone)]
pub struct ChatExportAudit {
    pub n_steps: usize,
    pub n_agent: usize,
    pub n_human: usize,
    pub n_skipped: usize,
    pub params: Vec<String>,
    pub warnings: Vec<String>,
    pub path: Option<PathBuf>,
}

impl ChatExportAudit {
    pub fn summary_text(&self) -> String {
        let mut s = String::new();
        s.push_str("# Teach Chat export audit\n\n");
        s.push_str(&format!(
            "- steps: {} (agent={}, human={})\n",
            self.n_steps, self.n_agent, self.n_human
        ));
        s.push_str(&format!("- skipped: {}\n", self.n_skipped));
        if self.params.is_empty() {
            s.push_str("- params: none\n");
        } else {
            s.push_str(&format!("- params: {}\n", self.params.join(", ")));
        }
        if let Some(p) = &self.path {
            s.push_str(&format!("- path: {}\n", p.display()));
        }
        if !self.warnings.is_empty() {
            s.push_str("\n## Warnings\n\n");
            for w in &self.warnings {
                s.push_str(&format!("- {w}\n"));
            }
        }
        s.push_str("\nPassword/token/cookie/Authorization values were exported as `{{vars.NAME}}` (not plaintext).\n");
        s.push_str("Pairing codes and session tokens are never written to skill.json.\n");
        s.push_str("Existing skill.json is not overwritten unless the operator confirmed overwrite.\n");
        s
    }
}

#[derive(Debug, Clone)]
pub struct ChatExportResult {
    pub path: PathBuf,
    pub audit: ChatExportAudit,
    pub skill: Value,
}

struct ExportState {
    root: PathBuf,
    token: String,
    allow_secrets: bool,
    smart_optimize: bool,
    last: Mutex<Option<PathBuf>>,
    assist_calls: Mutex<u8>,
    last_assist_ms: Mutex<u64>,
}

/// Headed is a hard requirement. Headless teach is rejected before launch.
pub fn require_headed(headed: bool) -> Result<()> {
    if !headed {
        bail!("teach start requires a headed CloakBrowser; headless is not supported");
    }
    Ok(())
}

/// Resolve the bundled teach extension. Not a CLI-injectable path.
pub fn resolve_extension_dir(root: &Path) -> Result<PathBuf> {
    let mut candidates: Vec<PathBuf> = Vec::new();
    candidates.push(root.join("extensions").join("teach"));
    if let Ok(exe) = std::env::current_exe() {
        if let Some(parent) = exe.parent() {
            for ancestor in parent.ancestors().take(6) {
                candidates.push(ancestor.join("extensions").join("teach"));
                candidates.push(ancestor.join("share").join("cloakcli").join("extensions").join("teach"));
            }
        }
    }
    candidates.push(PathBuf::from("/usr/local/share/cloakcli/extensions/teach"));
    candidates.push(PathBuf::from("/usr/share/cloakcli/extensions/teach"));

    let mut seen = BTreeSet::new();
    for c in candidates {
        let Ok(canon) = c.canonicalize() else {
            continue;
        };
        if !seen.insert(canon.clone()) {
            continue;
        }
        if is_teach_extension(&canon) {
            return Ok(canon);
        }
    }
    bail!(
        "CloakCLI Teach extension not found (looked under {}/extensions/teach). \
         Install/build the repo; the path is not a CLI argument.",
        root.display()
    );
}

pub fn is_teach_extension(dir: &Path) -> bool {
    let man = dir.join("manifest.json");
    let Ok(text) = fs::read_to_string(&man) else {
        return false;
    };
    let Ok(v) = serde_json::from_str::<Value>(&text) else {
        return false;
    };
    v.get("name").and_then(|x| x.as_str()) == Some("CloakCLI Teach")
        && v.get("manifest_version").and_then(|x| x.as_u64()) == Some(3)
}

/// True when the packaged manifest would default-inject everywhere (forbidden).
pub fn manifest_has_all_urls(dir: &Path) -> Result<bool> {
    let text = fs::read_to_string(dir.join("manifest.json"))?;
    Ok(text.contains("<all_urls>"))
}

pub async fn start(root: &Path, opts: TeachStartOpts) -> Result<()> {
    require_headed(true)?;
    util::validate_name(&opts.profile, "profile")?;
    let prof = profiles::get(root, &opts.profile)?;
    let ext_src = resolve_extension_dir(root)?;
    if manifest_has_all_urls(&ext_src)? {
        bail!("teach extension manifest must not use <all_urls>");
    }
    if !ext_src.join("background.js").is_file() || !ext_src.join("content.js").is_file() {
        bail!("teach extension at {} is incomplete", ext_src.display());
    }

    let udir = PathBuf::from(&prof.user_data_dir);
    let _ = util::ensure_under_root(root, &udir)?;
    fs::create_dir_all(&udir)?;

    if opts.allow_secrets {
        eprintln!(
            "warning: --allow-secrets exports password/token/secret values in skill.json. \
             A .gitignore is written; do not commit secrets."
        );
    }

    let _lock = acquire_teach_lock(root, &prof.name).await?;

    let py = python_bin()?;
    preflight_browser_binary(&py)?;

    let std_listener = StdTcpListener::bind("127.0.0.1:0")
        .context("bind teach export server on 127.0.0.1")?;
    std_listener.set_nonblocking(true)?;
    let port = std_listener.local_addr()?.port();
    let listener = TcpListener::from_std(std_listener)?;
    let token = uuid::Uuid::new_v4().simple().to_string();
    let export_origin = format!("http://127.0.0.1:{port}");

    let mut allow_origins = Vec::new();
    if let Some(ref u) = opts.url {
        if let Some(o) = http_origin(u) {
            allow_origins.push(o);
        }
    }

    let hub = crate::teach_hub::spawn(crate::teach_hub::TeachHubOpts {
        allow_origins: allow_origins.clone(),
    })
    .await
    .context("start teach hub on 127.0.0.1")?;
    let hub_url = format!("ws://127.0.0.1:{}", hub.port());

    let staged = stage_extension(
        root,
        &ext_src,
        &ExtConfig {
            export_origin: export_origin.clone(),
            token: token.clone(),
            allow_origins,
            allow_secrets: opts.allow_secrets,
            smart_optimize: opts.smart_optimize,
            profile: prof.name.clone(),
            hub_url: Some(hub_url.clone()),
            pairing_code: Some(hub.pairing_code().to_string()),
            pairing_id: Some(hub.pairing_id().to_string()),
        },
    )?;

    let state = Arc::new(ExportState {
        root: root.to_path_buf(),
        token,
        allow_secrets: opts.allow_secrets,
        smart_optimize: opts.smart_optimize,
        last: Mutex::new(None),
        assist_calls: Mutex::new(0),
        last_assist_ms: Mutex::new(0),
    });
    let server_state = state.clone();
    let server = tokio::spawn(async move {
        run_export_server(listener, server_state).await;
    });

    println!("teach: headed CloakBrowser + CloakCLI Teach extension");
    println!("  profile:   {}", prof.name);
    println!("  extension: {}", staged.display());
    println!("  export:    {export_origin}/export");
    println!("  hub:       {hub_url} (loopback)");
    println!("  pairing:   {}", hub.pairing_code());
    println!("Record with the extension popup: record → mark goal → stop → export.");
    if opts.smart_optimize {
        println!("Smart optimize: ON (one LLM call after export; --no-smart-optimize to skip).");
    } else {
        println!("Smart optimize: OFF (local post-process only).");
    }
    println!("Close the browser window when finished (Ctrl-C also stops and releases the profile lock).");

    let result = run_browser(
        root,
        &prof.user_data_dir,
        &staged,
        opts.url.as_deref(),
        prof.proxy.as_deref(),
        Some(HubConnect {
            addr: format!("127.0.0.1:{}", hub.port()),
            pairing_id: hub.pairing_id().to_string(),
            pairing_code: hub.pairing_code().to_string(),
        }),
    )
    .await;

    server.abort();
    hub.abort();
    let _ = fs::remove_dir_all(&staged);

    if let Some(path) = state.last.lock().ok().and_then(|g| g.clone()) {
        println!("last export: {}", path.display());
    }

    result
}

/// Teach Chat: hub (+ optional headed browser) with TUI or JSONL `--events`.
pub async fn start_chat(
    root: &Path,
    opts: TeachStartOpts,
    mock_json: Option<String>,
    events: bool,
    spawn_browser: bool,
) -> Result<()> {
    util::validate_name(&opts.profile, "profile")?;
    let prof = profiles::get(root, &opts.profile)?;

    if spawn_browser {
        require_headed(true)?;
        let ext_src = resolve_extension_dir(root)?;
        if manifest_has_all_urls(&ext_src)? {
            bail!("teach extension manifest must not use <all_urls>");
        }
        if !ext_src.join("background.js").is_file() || !ext_src.join("content.js").is_file() {
            bail!("teach extension at {} is incomplete", ext_src.display());
        }
        let udir = PathBuf::from(&prof.user_data_dir);
        let _ = util::ensure_under_root(root, &udir)?;
        fs::create_dir_all(&udir)?;
        let py = python_bin()?;
        preflight_browser_binary(&py)?;
    }

    let _lock = acquire_teach_lock(root, &prof.name).await?;

    let mut allow_origins = Vec::new();
    if let Some(ref u) = opts.url {
        if let Some(o) = http_origin(u) {
            allow_origins.push(o);
        }
    }

    let hub = crate::teach_hub::spawn(crate::teach_hub::TeachHubOpts {
        allow_origins: allow_origins.clone(),
    })
    .await
    .context("start teach hub on 127.0.0.1")?;
    let hub_url = format!("ws://127.0.0.1:{}", hub.port());

    let mut staged: Option<PathBuf> = None;
    let mut child = None;
    if spawn_browser {
        let ext_src = resolve_extension_dir(root)?;
        let dest = stage_extension(
            root,
            &ext_src,
            &ExtConfig {
                export_origin: "http://127.0.0.1:0".into(),
                token: uuid::Uuid::new_v4().simple().to_string(),
                allow_origins,
                allow_secrets: false,
                smart_optimize: false,
                profile: prof.name.clone(),
                hub_url: Some(hub_url.clone()),
                pairing_code: Some(hub.pairing_code().to_string()),
                pairing_id: Some(hub.pairing_id().to_string()),
            },
        )?;
        let mut cmd = teach_browser_command(
            root,
            &prof.user_data_dir,
            &dest,
            opts.url.as_deref(),
            prof.proxy.as_deref(),
            Some(HubConnect {
                addr: format!("127.0.0.1:{}", hub.port()),
                pairing_id: hub.pairing_id().to_string(),
                pairing_code: hub.pairing_code().to_string(),
            }),
        )?;
        let log_path = state::data_dir(root).join("teach").join("chat-worker.log");
        if let Some(parent) = log_path.parent() {
            let _ = fs::create_dir_all(parent);
        }
        match fs::File::create(&log_path) {
            Ok(f) => {
                cmd.stdout(Stdio::from(f.try_clone()?));
                cmd.stderr(Stdio::from(f));
            }
            Err(_) => {
                cmd.stdout(Stdio::null());
                cmd.stderr(Stdio::null());
            }
        }
        child = Some(
            cmd.spawn()
                .with_context(|| "spawn teach browser for chat")?,
        );
        staged = Some(dest);
    }

    let profile = prof.name.clone();
    let chat_result = if events {
        crate::teach_events::run(
            root,
            hub,
            crate::teach_events::EventsOpts {
                profile,
                mock_json,
                spawn_browser,
            },
        )
        .await
    } else {
        crate::tui::chat::run_dedicated(
            root,
            hub,
            crate::tui::chat::DedicatedOpts {
                profile,
                mock_json,
            },
        )
        .await
    };

    if let Some(mut c) = child {
        let _ = c.kill().await;
        let _ = c.wait().await;
    }
    if let Some(path) = staged {
        let _ = fs::remove_dir_all(&path);
    }
    chat_result
}

async fn acquire_teach_lock(root: &Path, profile: &str) -> Result<ProfileLock> {
    match ProfileLock::acquire(root, profile, Duration::from_secs(2)).await {
        Ok(lock) => Ok(lock),
        Err(_) => {
            let path = state::profile_lock_dir(root, profile);
            bail!(
                "profile '{profile}' is already in use (batch worker or another browser/teach job). \
                 Wait for that job to finish. Lock: {}",
                path.display()
            );
        }
    }
}

fn stage_extension(root: &Path, src: &Path, cfg: &ExtConfig) -> Result<PathBuf> {
    let dest = state::data_dir(root)
        .join("teach")
        .join(format!("{}-{}", cfg.profile, uuid::Uuid::new_v4().simple()));
    let dest = util::ensure_under_root(root, &dest)?;
    if dest.exists() {
        bail!("teach staging path already exists: {}", dest.display());
    }
    copy_ext_dir(src, &dest)?;
    let dest = util::ensure_under_root(root, &dest)?;
    let session = dest.join("session.json");
    fs::write(&session, format!("{}\n", serde_json::to_string_pretty(cfg)?))?;
    let _ = util::ensure_under_root(root, &session)?;
    Ok(dest)
}

fn copy_ext_dir(src: &Path, dest: &Path) -> Result<()> {
    fs::create_dir_all(dest)?;
    for e in fs::read_dir(src)? {
        let e = e?;
        let name = e.file_name();
        let name_s = name.to_string_lossy();
        if name_s.starts_with('.') || name_s == "session.json" {
            continue;
        }
        let from = e.path();
        let to = dest.join(&name);
        if from.is_dir() {
            copy_ext_dir(&from, &to)?;
        } else if from.is_file() {
            fs::copy(&from, &to)?;
        }
    }
    Ok(())
}

struct HubConnect {
    addr: String,
    pairing_id: String,
    pairing_code: String,
}

fn teach_browser_command(
    root: &Path,
    user_data_dir: &str,
    extension: &Path,
    url: Option<&str>,
    proxy: Option<&str>,
    hub: Option<HubConnect>,
) -> Result<Command> {
    let py = python_bin()?;
    let mut cmd = Command::new(&py);
    cmd.arg("-m")
        .arg("cloakcli_worker.teach")
        .arg("--root")
        .arg(root)
        .arg("--user-data-dir")
        .arg(user_data_dir)
        .arg("--extension")
        .arg(extension)
        .arg("--headed");
    if let Some(u) = url {
        cmd.arg("--url").arg(u);
    }
    if let Some(p) = proxy {
        cmd.arg("--proxy").arg(p);
    }
    if let Some(h) = hub.as_ref() {
        cmd.arg("--hub").arg(&h.addr);
        cmd.arg("--pairing-id").arg(&h.pairing_id);
        cmd.env("CLOAKCLI_TEACH_HUB", &h.addr);
        cmd.env("CLOAKCLI_TEACH_PAIRING_ID", &h.pairing_id);
        cmd.env("CLOAKCLI_TEACH_PAIRING_CODE", &h.pairing_code);
    }
    for key in [
        "CLOAKCLI_TEACH_SMOKE_SECONDS",
        "CLOAKCLI_TEACH_M1_SMOKE",
        "CLOAKCLI_TEACH_SMOKE_DENY_URL",
        "CLOAKCLI_TEACH_SMOKE_SENTINEL_TOKEN",
        "CLOAKCLI_TEACH_SMOKE_SENTINEL_PASSWORD",
        "CLOAKCLI_TEACH_SMOKE_SENTINEL_COOKIE",
    ] {
        if let Ok(v) = std::env::var(key) {
            if !v.is_empty() {
                cmd.env(key, v);
            }
        }
    }
    let worker_parent = root.join("python");
    let mut pp = worker_parent.to_string_lossy().to_string();
    if let Ok(existing) = std::env::var("PYTHONPATH") {
        if !existing.is_empty() {
            pp = format!("{pp}:{existing}");
        }
    }
    cmd.env("PYTHONPATH", pp);
    cmd.env("CLOAKCLI_ROOT", root);
    cmd.env("PYTHONUNBUFFERED", "1");
    cmd.current_dir(root);
    cmd.stdin(Stdio::null());
    Ok(cmd)
}

async fn run_browser(
    root: &Path,
    user_data_dir: &str,
    extension: &Path,
    url: Option<&str>,
    proxy: Option<&str>,
    hub: Option<HubConnect>,
) -> Result<()> {
    let mut cmd = teach_browser_command(root, user_data_dir, extension, url, proxy, hub)?;
    cmd.stdout(Stdio::inherit());
    cmd.stderr(Stdio::inherit());

    let mut child = cmd
        .spawn()
        .context("spawn python -m cloakcli_worker.teach")?;

    tokio::select! {
        status = child.wait() => {
            let status = status.context("wait teach browser")?;
            if !status.success() {
                bail!("teach browser exited {}", status);
            }
            Ok(())
        }
        _ = tokio::signal::ctrl_c() => {
            let _ = child.kill().await;
            let _ = child.wait().await;
            bail!("teach interrupted");
        }
    }
}

fn python_bin() -> Result<String> {
    if let Ok(bin) = std::env::var("CLOAKCLI_PYTHON") {
        return Ok(bin);
    }
    for name in ["python3", "python"] {
        if which::which(name).is_ok() {
            return Ok(name.to_string());
        }
    }
    bail!("python3 not found; set CLOAKCLI_PYTHON");
}

/// Fail before launching the headed worker if CloakBrowser Chromium is missing.
pub fn preflight_browser_binary(py: &str) -> Result<PathBuf> {
    let script = r#"
import json, sys
try:
    from cloakbrowser import binary_info
except Exception as e:
    sys.stderr.write(f"import cloakbrowser failed: {type(e).__name__}: {e}\n")
    sys.exit(2)
info = binary_info()
path = str(info.get("binary_path") or "")
installed = bool(info.get("installed"))
print(json.dumps({"path": path, "installed": installed}))
sys.exit(0 if installed else 3)
"#;
    let out = std::process::Command::new(py)
        .args(["-c", script])
        .output()
        .with_context(|| format!("run {py} cloakbrowser binary preflight"))?;
    let stdout = String::from_utf8_lossy(&out.stdout).trim().to_string();
    let stderr = String::from_utf8_lossy(&out.stderr).trim().to_string();
    if out.status.code() == Some(2) {
        bail!(
            "teach: cloakbrowser is not importable with {py}. \
             Install worker deps (pip install -e python/) or set CLOAKCLI_PYTHON. {stderr}"
        );
    }
    let json_line = stdout
        .lines()
        .rev()
        .find(|l| l.starts_with('{'))
        .unwrap_or(stdout.as_str());
    let info: Value = serde_json::from_str(json_line).unwrap_or_else(|_| json!({}));
    let path = info
        .get("path")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let installed = info
        .get("installed")
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    if !out.status.success() || !installed || path.is_empty() || !Path::new(&path).is_file() {
        let shown = if path.is_empty() {
            "(unknown path)".to_string()
        } else {
            path
        };
        bail!(
            "teach: CloakBrowser Chromium binary not found at {shown}. \
             Download it before teach start: {py} -c \"from cloakbrowser import ensure_binary; print(ensure_binary())\". \
             See also: cloakcli doctor."
        );
    }
    Ok(PathBuf::from(path))
}

async fn run_export_server(listener: TcpListener, state: Arc<ExportState>) {
    loop {
        match listener.accept().await {
            Ok((stream, _)) => {
                let state = state.clone();
                tokio::spawn(async move {
                    if let Err(e) = handle_http(stream, state).await {
                        eprintln!("teach export server: {e}");
                    }
                });
            }
            Err(_) => break,
        }
    }
}

async fn handle_http(mut stream: TcpStream, state: Arc<ExportState>) -> Result<()> {
    let mut buf = Vec::with_capacity(4096);
    let mut tmp = [0u8; 2048];
    let header_end;
    loop {
        let n = stream.read(&mut tmp).await?;
        if n == 0 {
            return Ok(());
        }
        buf.extend_from_slice(&tmp[..n]);
        if buf.len() > 64 * 1024 {
            write_http(&mut stream, 413, "{\"ok\":false,\"error\":\"headers too large\"}").await?;
            return Ok(());
        }
        if let Some(pos) = find_header_end(&buf) {
            header_end = pos;
            break;
        }
    }
    let header_text = String::from_utf8_lossy(&buf[..header_end]).to_string();
    let mut lines = header_text.split("\r\n");
    let req_line = lines.next().unwrap_or("");
    let mut parts = req_line.split_whitespace();
    let method = parts.next().unwrap_or("").to_ascii_uppercase();
    let path = parts.next().unwrap_or("/");

    let mut content_length: usize = 0;
    let mut token_hdr: Option<String> = None;
    for line in lines {
        let Some((k, v)) = line.split_once(':') else {
            continue;
        };
        let k = k.trim().to_ascii_lowercase();
        let v = v.trim();
        if k == "content-length" {
            content_length = v.parse().unwrap_or(0);
        }
        if k == "x-cloakcli-token" {
            token_hdr = Some(v.to_string());
        }
    }

    if method == "OPTIONS" {
        write_http(&mut stream, 204, "").await?;
        return Ok(());
    }

    if method == "GET" && (path == "/health" || path.starts_with("/health?")) {
        write_http(&mut stream, 200, "{\"ok\":true}").await?;
        return Ok(());
    }

    if method != "POST" || (path != "/export" && path != "/assist") {
        write_http(
            &mut stream,
            404,
            "{\"ok\":false,\"error\":\"not found\"}",
        )
        .await?;
        return Ok(());
    }

    if content_length == 0 || content_length > 1_000_000 {
        write_http(
            &mut stream,
            400,
            "{\"ok\":false,\"error\":\"invalid content-length\"}",
        )
        .await?;
        return Ok(());
    }

    while buf.len() < header_end + content_length {
        let n = stream.read(&mut tmp).await?;
        if n == 0 {
            break;
        }
        buf.extend_from_slice(&tmp[..n]);
        if buf.len() > header_end + 1_000_000 {
            write_http(&mut stream, 413, "{\"ok\":false,\"error\":\"body too large\"}").await?;
            return Ok(());
        }
    }
    let body = &buf[header_end..header_end + content_length.min(buf.len() - header_end)];

    if token_hdr.as_deref() != Some(state.token.as_str()) {
        write_http(&mut stream, 401, "{\"ok\":false,\"error\":\"unauthorized\"}").await?;
        return Ok(());
    }

    if path == "/assist" {
        return handle_assist_body(&mut stream, &state, body).await;
    }

    let parsed: ExportBody = match serde_json::from_slice(body) {
        Ok(v) => v,
        Err(e) => {
            let msg = json!({"ok": false, "error": format!("invalid json: {e}")});
            write_http(&mut stream, 400, &msg.to_string()).await?;
            return Ok(());
        }
    };

    let smart = parsed.smart_optimize.unwrap_or(state.smart_optimize);
    match export_recorded(
        &state.root,
        &parsed.name,
        parsed.goal.as_deref(),
        &parsed.events,
        state.allow_secrets,
        smart,
    ) {
        Ok(path) => {
            if let Ok(mut g) = state.last.lock() {
                *g = Some(path.clone());
            }
            let msg = json!({
                "ok": true,
                "path": path.display().to_string(),
            });
            println!("teach export: {}", path.display());
            write_http(&mut stream, 200, &msg.to_string()).await?;
        }
        Err(e) => {
            let msg = json!({"ok": false, "error": e.to_string()});
            eprintln!("teach export rejected: {e}");
            write_http(&mut stream, 400, &msg.to_string()).await?;
        }
    }
    Ok(())
}

async fn handle_assist_body(
    stream: &mut TcpStream,
    state: &ExportState,
    body: &[u8],
) -> Result<()> {
    let parsed: AssistBody = match serde_json::from_slice(body) {
        Ok(v) => v,
        Err(e) => {
            let msg = json!({"ok": false, "error": format!("invalid json: {e}")});
            write_http(stream, 400, &msg.to_string()).await?;
            return Ok(());
        }
    };
    let primary = parsed.selector.as_deref().unwrap_or("").trim();
    let local = crate::teach_optimize::local_backup_chain(
        primary,
        &parsed.selectors,
        parsed.field.as_ref(),
    );

    let calls = state.assist_calls.lock().map(|g| *g).unwrap_or(ASSIST_MAX);
    let last_ms = state.last_assist_ms.lock().map(|g| *g).unwrap_or(0);
    let cheap = calls < crate::teach_optimize::ASSIST_MAX_LLM_CALLS
        && (calls == 0 || last_ms <= crate::teach_optimize::ASSIST_CHEAP_MS);
    let unstable = selector_looks_unstable(primary);
    let allow_llm = cheap && unstable && !primary.is_empty();

    let out = if allow_llm {
        let r = crate::teach_optimize::assist_selectors(
            &state.root,
            primary,
            &parsed.selectors,
            parsed.field.as_ref(),
            true,
        );
        if r.used_llm {
            if let Ok(mut g) = state.assist_calls.lock() {
                *g = g.saturating_add(1);
            }
            if let Ok(mut g) = state.last_assist_ms.lock() {
                *g = r.latency_ms;
            }
        }
        r
    } else {
        crate::teach_optimize::AssistOutcome {
            selectors: local,
            deferred: true,
            reason: if !unstable {
                "stable selector".into()
            } else if calls >= crate::teach_optimize::ASSIST_MAX_LLM_CALLS {
                "assist cap".into()
            } else {
                "deferred (not cheap)".into()
            },
            ..Default::default()
        }
    };

    let msg = json!({
        "ok": true,
        "selectors": out.selectors,
        "deferred": out.deferred,
        "used_llm": out.used_llm,
        "calls_used": state.assist_calls.lock().map(|g| *g).unwrap_or(0),
        "tokens": out.tokens,
        "reason": out.reason,
    });
    write_http(stream, 200, &msg.to_string()).await?;
    Ok(())
}

const ASSIST_MAX: u8 = 2;

fn selector_looks_unstable(sel: &str) -> bool {
    if sel.is_empty() {
        return true;
    }
    let lower = sel.to_ascii_lowercase();
    lower.contains(":nth-")
        || lower.matches('>').count() >= 3
        || sel.len() > 80
}

fn find_header_end(buf: &[u8]) -> Option<usize> {
    buf.windows(4).position(|w| w == b"\r\n\r\n").map(|i| i + 4)
}

async fn write_http(stream: &mut TcpStream, status: u16, body: &str) -> Result<()> {
    let reason = match status {
        200 => "OK",
        204 => "No Content",
        400 => "Bad Request",
        401 => "Unauthorized",
        404 => "Not Found",
        413 => "Payload Too Large",
        _ => "Error",
    };
    let resp = format!(
        "HTTP/1.1 {status} {reason}\r\n\
         Content-Type: application/json\r\n\
         Content-Length: {}\r\n\
         Access-Control-Allow-Origin: *\r\n\
         Access-Control-Allow-Headers: Content-Type, X-CloakCLI-Token\r\n\
         Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n\
         Connection: close\r\n\
         \r\n\
         {body}",
        body.len()
    );
    stream.write_all(resp.as_bytes()).await?;
    stream.flush().await?;
    Ok(())
}

/// Map recorded events → existing skill.json schema and write under `skills/<name>/`.
/// Local post-process always runs. Smart optimize is a single optional LLM call.
pub fn export_recorded(
    root: &Path,
    name: &str,
    goal: Option<&str>,
    events: &[RecordedEvent],
    allow_secrets: bool,
    smart_optimize: bool,
) -> Result<PathBuf> {
    let mapped = map_events_to_skill(name, goal, events, allow_secrets)?;
    let mapped = if smart_optimize {
        match crate::teach_optimize::maybe_optimize(root, &mapped, true) {
            Ok(out) => {
                if let Some(skip) = &out.skipped {
                    eprintln!("teach smart optimize skipped: {skip}");
                } else if out.applied {
                    eprintln!(
                        "teach smart optimize applied tokens={} latency_ms={}",
                        out.tokens, out.latency_ms
                    );
                }
                out.mapped
            }
            Err(e) => {
                eprintln!("teach smart optimize failed: {e}");
                mapped
            }
        }
    } else {
        mapped
    };
    write_skill_dir(root, &mapped)
}

pub fn map_events_to_skill(
    name: &str,
    goal: Option<&str>,
    events: &[RecordedEvent],
    allow_secrets: bool,
) -> Result<MappedSkill> {
    util::validate_name(name, "skill")?;
    let goal = normalize_goal(goal, events);
    let mut steps: Vec<Value> = Vec::new();
    let mut vars: Vec<String> = Vec::new();
    let mut var_counts: HashMap<String, usize> = HashMap::new();
    let mut last_nav: Option<String> = None;

    // Local post-process (TEACH PATH, 0 tokens): denoise then merge consecutive fills.
    let coalesced = coalesce_events(&denoise_events(events));

    for (i, ev) in coalesced.iter().enumerate() {
        let kind = ev.kind.trim().to_ascii_lowercase();
        match kind.as_str() {
            "goal" => continue,
            "navigation" | "nav" | "goto" => {
                let Some(url) = ev.url.as_deref() else {
                    continue;
                };
                let Ok(url) = sanitize_url(url) else {
                    continue;
                };
                if last_nav.as_deref() == Some(url.as_str()) {
                    continue;
                }
                last_nav = Some(url.clone());
                steps.push(json!({"action": "goto", "url": url}));
            }
            "click" => {
                let Some(sel) = ev.selector.as_deref().map(str::trim).filter(|s| !s.is_empty()) else {
                    continue;
                };
                // Skip click when the next coalesced event fills the same control.
                if let Some(next) = coalesced.get(i + 1) {
                    let nk = next.kind.trim().to_ascii_lowercase();
                    if matches!(nk.as_str(), "input" | "fill" | "type")
                        && next.selector.as_deref() == Some(sel)
                    {
                        continue;
                    }
                }
                let mut step = json!({"action": "click", "selector": sel});
                let chain = backup_selectors(ev);
                if chain.len() > 1 {
                    step["selectors"] = json!(chain);
                }
                if let Some(name) = semantic_field_name(ev) {
                    step["field_name"] = json!(name);
                }
                steps.push(step);
            }
            "input" | "fill" | "type" => {
                let Some(sel) = ev.selector.as_deref().map(str::trim).filter(|s| !s.is_empty()) else {
                    continue;
                };
                let raw_val = ev.value.clone().unwrap_or_default();
                let text = if looks_secret_field(ev.field.as_ref()) {
                    if allow_secrets {
                        raw_val
                    } else {
                        let base = var_name_for_field(ev.field.as_ref()).unwrap_or_else(|| "SECRET".into());
                        let n = var_counts.entry(base.clone()).or_insert(0);
                        *n += 1;
                        let name = if *n == 1 {
                            base
                        } else {
                            format!("{base}_{n}")
                        };
                        if !vars.iter().any(|v| v == &name) {
                            vars.push(name.clone());
                        }
                        format!("{{{{vars.{name}}}}}")
                    }
                } else {
                    strip_secretish_value(&raw_val)
                };
                let mut step = json!({"action": "fill", "selector": sel, "text": text});
                let chain = backup_selectors(ev);
                if chain.len() > 1 {
                    step["selectors"] = json!(chain);
                }
                if let Some(name) = semantic_field_name(ev) {
                    step["field_name"] = json!(name);
                }
                steps.push(step);
            }
            _ => continue,
        }
    }

    if steps.is_empty() {
        bail!("empty steps: record at least one click, input, or navigation before export");
    }

    let description = match &goal {
        Some(g) => format!("Taught skill: {g}"),
        None => "Taught skill".into(),
    };
    let params: Vec<Value> = vars
        .iter()
        .map(|n| json!({"name": n, "required": true}))
        .collect();

    Ok(MappedSkill {
        name: name.to_string(),
        goal,
        description,
        params,
        steps,
        vars,
        allow_secrets,
        overwrite: false,
        from_chat: false,
        audit_text: None,
    })
}

fn denoise_events(events: &[RecordedEvent]) -> Vec<RecordedEvent> {
    let mut out: Vec<RecordedEvent> = Vec::new();
    for ev in events {
        let kind = ev.kind.trim().to_ascii_lowercase();
        if matches!(
            kind.as_str(),
            "hover"
                | "blur"
                | "focus"
                | "mouseover"
                | "mouseout"
                | "mousemove"
                | "pointermove"
                | "scroll"
                | "keydown"
                | "keyup"
                | "keypress"
        ) {
            continue;
        }
        if kind == "click" {
            if let Some(last) = out.last() {
                if last.kind.trim().eq_ignore_ascii_case("click") && last.selector == ev.selector {
                    continue;
                }
            }
        }
        out.push(ev.clone());
    }
    out
}

fn backup_selectors(ev: &RecordedEvent) -> Vec<String> {
    let mut field_json = None;
    let owned;
    if let Some(f) = ev.field.as_ref() {
        owned = json!({
            "id": f.id,
            "name": f.name,
            "tag": f.tag,
            "autocomplete": f.autocomplete,
            "testid": f.testid,
            "type": f.input_type,
        });
        field_json = Some(&owned);
    }
    let mut extra = ev.selectors.clone();
    if let Some(label) = ev.label.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
        extra.push(format!("[aria-label=\"{}\"]", css_attr(label)));
    }
    crate::teach_optimize::local_backup_chain(
        ev.selector.as_deref().unwrap_or(""),
        &extra,
        field_json,
    )
}

fn css_attr(v: &str) -> String {
    v.replace('\\', "\\\\").replace('"', "\\\"")
}

fn semantic_field_name(ev: &RecordedEvent) -> Option<String> {
    let f = ev.field.as_ref();
    let hay = [
        f.and_then(|x| x.autocomplete.as_deref()).unwrap_or(""),
        f.and_then(|x| x.name.as_deref()).unwrap_or(""),
        f.and_then(|x| x.id.as_deref()).unwrap_or(""),
        f.and_then(|x| x.input_type.as_deref()).unwrap_or(""),
        f.and_then(|x| x.placeholder.as_deref()).unwrap_or(""),
        ev.label.as_deref().unwrap_or(""),
        ev.role.as_deref().unwrap_or(""),
    ]
    .join(" ")
    .to_ascii_lowercase();
    if hay.contains("email") || hay.contains("e-mail") {
        return Some("email".into());
    }
    if hay.contains("user") || hay.contains("login") {
        return Some("username".into());
    }
    if hay.contains("pass") {
        return Some("password".into());
    }
    if hay.contains("phone") || hay.contains("tel") || hay.contains("mobile") {
        return Some("phone".into());
    }
    if hay.contains("address") || hay.contains("shipping") {
        return Some("shipping_address".into());
    }
    if hay.contains("search") {
        return Some("search".into());
    }
    None
}

fn coalesce_events(events: &[RecordedEvent]) -> Vec<RecordedEvent> {
    let mut out: Vec<RecordedEvent> = Vec::new();
    for ev in events {
        let kind = ev.kind.trim().to_ascii_lowercase();
        if matches!(kind.as_str(), "input" | "fill" | "type") {
            if let Some(last) = out.last_mut() {
                let lk = last.kind.trim().to_ascii_lowercase();
                if matches!(lk.as_str(), "input" | "fill" | "type")
                    && last.selector == ev.selector
                {
                    *last = ev.clone();
                    continue;
                }
            }
        }
        out.push(ev.clone());
    }
    out
}

fn normalize_goal(goal: Option<&str>, events: &[RecordedEvent]) -> Option<String> {
    let mut g = goal.map(str::trim).filter(|s| !s.is_empty()).map(|s| s.to_string());
    for ev in events {
        if ev.kind.trim().eq_ignore_ascii_case("goal") {
            if let Some(t) = ev
                .text
                .as_deref()
                .or(ev.value.as_deref())
                .map(str::trim)
                .filter(|s| !s.is_empty())
            {
                g = Some(t.to_string());
            }
        }
    }
    g
}

pub fn write_skill_dir(root: &Path, mapped: &MappedSkill) -> Result<PathBuf> {
    util::validate_name(&mapped.name, "skill")?;
    let mut skill = json!({
        "schema_version": 1,
        "name": mapped.name,
        "description": mapped.description,
        "params": mapped.params,
        "steps": mapped.steps,
    });
    if let Some(g) = &mapped.goal {
        skill["goal"] = json!(g);
    }
    crate::skills::assert_no_plaintext_secrets(&skill)?;
    let body = format!("{}\n", serde_json::to_string_pretty(&skill)?);
    crate::skills::commit_skill_export(
        root,
        &mapped.name,
        mapped.overwrite,
        crate::skills::SkillExportFiles {
            skill_json: body,
            readme: Some(render_readme(mapped)),
            gitignore: Some("secrets.json\n.env\n*.secret\n".into()),
            audit: mapped.audit_text.clone(),
        },
    )
}

/// Teach Chat M4: export merged timeline Playwright steps as a skill.json draft.
/// Does not overwrite an existing skill unless `overwrite` is true.
pub fn export_chat_draft(
    root: &Path,
    name: &str,
    goal: Option<&str>,
    steps: &[Value],
    overwrite: bool,
) -> Result<ChatExportResult> {
    util::validate_name(name, "skill")?;
    let prep = crate::skills::prepare_teach_export_steps(steps, "agent")?;
    if prep.steps.is_empty() {
        bail!("empty steps: record at least one Playwright action before export");
    }
    let goal = goal
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string());
    let description = match &goal {
        Some(g) => format!("Taught skill: {g}"),
        None => "Taught skill (Teach Chat)".into(),
    };
    let params: Vec<Value> = prep
        .vars
        .iter()
        .map(|n| json!({"name": n, "required": true}))
        .collect();
    let mut warnings = prep.skipped.clone();
    if prep.n_human == 0 && prep.n_agent == 0 {
        warnings.push("no source-tagged steps".into());
    }
    let mut audit = ChatExportAudit {
        n_steps: prep.steps.len(),
        n_agent: prep.n_agent,
        n_human: prep.n_human,
        n_skipped: prep.skipped.len(),
        params: prep.vars.clone(),
        warnings,
        path: None,
    };
    audit.path = Some(state::skills_dir(root).join(name).join("skill.json"));
    let mapped = MappedSkill {
        name: name.to_string(),
        goal: goal.clone(),
        description,
        params,
        steps: prep.steps.clone(),
        vars: prep.vars.clone(),
        allow_secrets: false,
        overwrite,
        from_chat: true,
        audit_text: Some(audit.summary_text()),
    };
    let path = write_skill_dir(root, &mapped)?;
    audit.path = Some(path.clone());
    let skill: Value = serde_json::from_str(&fs::read_to_string(&path)?)?;
    crate::skills::assert_no_plaintext_secrets(&skill)?;
    Ok(ChatExportResult {
        path,
        audit,
        skill,
    })
}

fn render_readme(mapped: &MappedSkill) -> String {
    let mut s = String::new();
    s.push_str(&format!("# {}\n\n", mapped.name));
    if let Some(g) = &mapped.goal {
        s.push_str(&format!("Goal: {g}\n\n"));
    } else {
        s.push_str("Goal: (not marked — runner will use per-step fallbacks)\n\n");
    }
    if mapped.from_chat {
        s.push_str("Taught via `cloakcli teach chat` (Ctrl-E). Steps are unified Playwright actions with `source=human|agent`. Raw DOM events are never exported.\n\n");
    } else {
        s.push_str("Taught via `cloakcli teach start`. Compatible with existing skill.json (`goto` / `click` / `fill`).\n\n");
    }
    s.push_str("## Run\n\n```bash\n");
    s.push_str(&format!("cloakcli skill run {} --profile <profile>", mapped.name));
    for v in &mapped.vars {
        s.push_str(&format!(" --var {v}=..."));
    }
    s.push_str("\n```\n\n");
    if mapped.vars.is_empty() {
        s.push_str("## Variables\n\nNone. Password/token/secret fields were not recorded");
        if mapped.allow_secrets {
            s.push_str(", or were exported in plaintext because `--allow-secrets` was set");
        }
        s.push_str(".\n");
    } else {
        s.push_str("## Variables\n\nPassword/token/secret fields were exported as `{{vars.NAME}}` (not plaintext).\n\n");
        for v in &mapped.vars {
            s.push_str(&format!("- `{v}` — required (`--var {v}=...`)\n"));
        }
        s.push('\n');
    }
    s.push_str("Do not commit secrets. This directory has a `.gitignore` for `secrets.json`, `.env`, and `*.secret`.\n");
    if mapped.allow_secrets {
        s.push_str("\n**`--allow-secrets` was used:** skill.json may contain plaintext secrets. Do not git-add it.\n");
    }
    s
}

pub fn sanitize_url(raw: &str) -> Result<String> {
    let raw = raw.trim();
    if raw.is_empty() {
        bail!("empty url");
    }
    let mut u = url::Url::parse(raw).map_err(|_| anyhow::anyhow!("invalid url"))?;
    if u.scheme() != "http" && u.scheme() != "https" {
        bail!("refusing non-http(s) URL");
    }
    if u.cannot_be_a_base() {
        bail!("refusing non-base URL");
    }
    let _ = u.set_username("");
    let _ = u.set_password(None);
    let filtered: Vec<(String, String)> = u
        .query_pairs()
        .filter(|(k, _)| !is_secret_query_key(k))
        .map(|(k, v)| (k.into_owned(), v.into_owned()))
        .collect();
    u.set_query(None);
    u.set_fragment(None);
    if !filtered.is_empty() {
        let q = filtered
            .into_iter()
            .map(|(k, v)| format!("{}={}", k, v))
            .collect::<Vec<_>>()
            .join("&");
        u.set_query(Some(&q));
    }
    Ok(u.to_string())
}

fn is_secret_query_key(k: &str) -> bool {
    let k = k.trim().to_ascii_lowercase();
    SECRET_QUERY_KEYS.iter().any(|s| k == *s || k.contains(s))
}

pub fn looks_secret_field(field: Option<&FieldHint>) -> bool {
    let Some(f) = field else {
        return false;
    };
    let ty = f.input_type.as_deref().unwrap_or("").to_ascii_lowercase();
    if ty == "password" {
        return true;
    }
    let hay = [
        f.input_type.as_deref().unwrap_or(""),
        f.name.as_deref().unwrap_or(""),
        f.id.as_deref().unwrap_or(""),
        f.autocomplete.as_deref().unwrap_or(""),
        f.tag.as_deref().unwrap_or(""),
    ]
    .join(" ")
    .to_ascii_lowercase();
    SECRET_FIELD_MARKERS.iter().any(|m| hay.contains(m))
}

fn var_name_for_field(field: Option<&FieldHint>) -> Option<String> {
    let f = field?;
    let candidates = [
        f.name.as_deref(),
        f.id.as_deref(),
        f.autocomplete.as_deref(),
        f.input_type.as_deref(),
    ];
    for c in candidates.into_iter().flatten() {
        if let Some(n) = sanitize_var_ident(c) {
            return Some(n);
        }
    }
    Some("SECRET".into())
}

fn sanitize_var_ident(raw: &str) -> Option<String> {
    let mut s: String = raw
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() {
                c.to_ascii_uppercase()
            } else {
                '_'
            }
        })
        .collect();
    while s.contains("__") {
        s = s.replace("__", "_");
    }
    s = s.trim_matches('_').to_string();
    if s.is_empty() {
        return None;
    }
    if s.chars().next()?.is_ascii_digit() {
        s.insert(0, 'V');
    }
    if s.len() > 32 {
        s.truncate(32);
    }
    Some(s)
}

fn strip_secretish_value(v: &str) -> String {
    // Non-secret fields still must not carry Authorization / cookie dumps.
    let lower = v.to_ascii_lowercase();
    if lower.contains("bearer ")
        || lower.contains("authorization")
        || lower.contains("cookie=")
        || v.contains("sk-")
    {
        return "{{vars.REDACTED}}".into();
    }
    v.to_string()
}

fn http_origin(url: &str) -> Option<String> {
    let u = url::Url::parse(url).ok()?;
    if u.scheme() != "http" && u.scheme() != "https" {
        return None;
    }
    let origin = u.origin().ascii_serialization();
    if origin == "null" {
        None
    } else {
        Some(origin)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::symlink;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn tmp_root() -> PathBuf {
        let n = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let p = std::env::temp_dir().join(format!("cloakcli_teach_{n}"));
        let _ = fs::remove_dir_all(&p);
        fs::create_dir_all(p.join("skills")).unwrap();
        fs::create_dir_all(p.join("extensions").join("teach")).unwrap();
        fs::write(
            p.join("Cargo.toml"),
            "[package]\nname=\"t\"\nversion=\"0.0.0\"\n",
        )
        .unwrap();
        p
    }

    fn ev_nav(url: &str) -> RecordedEvent {
        RecordedEvent {
            kind: "navigation".into(),
            url: Some(url.into()),
            ..Default::default()
        }
    }
    fn ev_click(sel: &str) -> RecordedEvent {
        RecordedEvent {
            kind: "click".into(),
            selector: Some(sel.into()),
            ..Default::default()
        }
    }
    fn ev_input(sel: &str, value: &str, field: FieldHint) -> RecordedEvent {
        RecordedEvent {
            kind: "input".into(),
            selector: Some(sel.into()),
            value: Some(value.into()),
            field: Some(field),
            ..Default::default()
        }
    }

    #[test]
    fn headed_is_hard_error() {
        let err = require_headed(false).unwrap_err().to_string();
        assert!(err.contains("headed"), "{err}");
        assert!(err.contains("headless"), "{err}");
        assert!(require_headed(true).is_ok());
    }

    #[test]
    fn maps_click_input_nav_goal_fixture() {
        let events = vec![
            ev_nav("https://example.com/login?token=leakme&next=/app"),
            ev_input(
                "#user",
                "alice",
                FieldHint {
                    input_type: Some("text".into()),
                    name: Some("username".into()),
                    id: Some("user".into()),
                    ..Default::default()
                },
            ),
            ev_input(
                "#pass",
                "hunter2",
                FieldHint {
                    input_type: Some("password".into()),
                    name: Some("password".into()),
                    id: Some("pass".into()),
                    ..Default::default()
                },
            ),
            ev_click("button.submit"),
            RecordedEvent {
                kind: "goal".into(),
                text: Some("Sign in and open the dashboard".into()),
                ..Default::default()
            },
        ];
        let m = map_events_to_skill("taught-login", None, &events, false).unwrap();
        assert_eq!(m.goal.as_deref(), Some("Sign in and open the dashboard"));
        assert_eq!(m.steps.len(), 4);
        assert_eq!(m.steps[0]["action"], "goto");
        assert_eq!(m.steps[0]["url"], "https://example.com/login?next=/app");
        assert_eq!(m.steps[1]["action"], "fill");
        assert_eq!(m.steps[1]["selector"], "#user");
        assert_eq!(m.steps[1]["text"], "alice");
        assert_eq!(m.steps[1]["field_name"], "username");
        assert!(m.steps[1]["selectors"]
            .as_array()
            .unwrap()
            .iter()
            .any(|s| s.as_str() == Some("input[name=\"username\"]")));
        assert_eq!(m.steps[2]["action"], "fill");
        assert_eq!(m.steps[2]["selector"], "#pass");
        assert_eq!(m.steps[2]["text"], "{{vars.PASSWORD}}");
        assert_eq!(m.steps[3]["action"], "click");
        assert_eq!(m.steps[3]["selector"], "button.submit");
        assert!(m.vars.iter().any(|v| v == "PASSWORD"));
        assert!(!serde_json::to_string(&m.steps).unwrap().contains("hunter2"));
        assert!(!serde_json::to_string(&m.steps).unwrap().contains("leakme"));
    }

    #[test]
    fn password_allow_secrets_keeps_plaintext() {
        let events = vec![ev_input(
            "#pass",
            "hunter2",
            FieldHint {
                input_type: Some("password".into()),
                name: Some("password".into()),
                ..Default::default()
            },
        )];
        let m = map_events_to_skill("s", None, &events, true).unwrap();
        assert_eq!(m.steps[0]["text"], "hunter2");
    }

    #[test]
    fn denoise_drops_hover_and_duplicate_clicks() {
        let events = vec![
            ev_click("#go"),
            RecordedEvent {
                kind: "hover".into(),
                selector: Some("#go".into()),
                ..Default::default()
            },
            ev_click("#go"),
            ev_nav("https://example.com/"),
        ];
        let m = map_events_to_skill("s", None, &events, false).unwrap();
        let clicks: Vec<_> = m
            .steps
            .iter()
            .filter(|s| s["action"] == "click")
            .collect();
        assert_eq!(clicks.len(), 1, "{:?}", m.steps);
    }

    #[test]
    fn coalesce_consecutive_fills_keeps_last() {
        let events = vec![
            ev_input(
                "#user",
                "a",
                FieldHint {
                    name: Some("username".into()),
                    id: Some("user".into()),
                    ..Default::default()
                },
            ),
            ev_input(
                "#user",
                "alice",
                FieldHint {
                    name: Some("username".into()),
                    id: Some("user".into()),
                    ..Default::default()
                },
            ),
        ];
        let m = map_events_to_skill("s", None, &events, false).unwrap();
        assert_eq!(m.steps.len(), 1);
        assert_eq!(m.steps[0]["text"], "alice");
    }

    #[test]
    fn empty_steps_rejected() {
        let err = map_events_to_skill("s", Some("g"), &[], false)
            .unwrap_err()
            .to_string();
        assert!(err.contains("empty steps"), "{err}");
    }

    #[test]
    fn duplicate_goal_last_wins() {
        let events = vec![
            ev_nav("https://example.com/"),
            RecordedEvent {
                kind: "goal".into(),
                text: Some("first".into()),
                ..Default::default()
            },
            RecordedEvent {
                kind: "goal".into(),
                text: Some("second".into()),
                ..Default::default()
            },
        ];
        let m = map_events_to_skill("g", None, &events, false).unwrap();
        assert_eq!(m.goal.as_deref(), Some("second"));
    }

    #[test]
    fn missing_goal_ok() {
        let m = map_events_to_skill("g", None, &[ev_nav("https://example.com/")], false).unwrap();
        assert!(m.goal.is_none());
    }

    #[test]
    fn malicious_names_rejected() {
        for name in ["../etc", "foo/bar", "foo\\bar", "", "/etc/passwd", "a..b"] {
            let err = map_events_to_skill(name, None, &[ev_nav("https://example.com/")], false);
            assert!(err.is_err(), "accepted {name}");
        }
        assert!(util::validate_name("hello", "skill").is_ok());
    }

    #[test]
    fn write_rejects_symlink_escape() {
        let root = tmp_root();
        let outside = std::env::temp_dir().join(format!(
            "cloakcli_teach_outside_{}",
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&outside).unwrap();
        let link = state::skills_dir(&root).join("evil");
        symlink(&outside, &link).unwrap();
        let mapped = map_events_to_skill(
            "evil",
            Some("x"),
            &[ev_nav("https://example.com/")],
            false,
        )
        .unwrap();
        let err = write_skill_dir(&root, &mapped).unwrap_err().to_string();
        assert!(
            err.contains("escape") || err.contains("symlink") || err.contains("root"),
            "{err}"
        );
        let _ = fs::remove_dir_all(&root);
        let _ = fs::remove_dir_all(&outside);
    }

    #[test]
    fn write_happy_path_under_skills() {
        let root = tmp_root();
        let events = vec![
            ev_nav("https://example.com/login"),
            ev_click("#go"),
        ];
        let mapped = map_events_to_skill("okskill", Some("do it"), &events, false).unwrap();
        let path = write_skill_dir(&root, &mapped).unwrap();
        assert!(path.ends_with("skill.json"));
        let text = fs::read_to_string(&path).unwrap();
        assert!(text.contains("\"action\": \"goto\""));
        assert!(text.contains("\"action\": \"click\""));
        assert!(text.contains("do it"));
        let gi = path.parent().unwrap().join(".gitignore");
        let gi_text = fs::read_to_string(gi).unwrap();
        assert!(gi_text.contains("secrets.json"));
        assert!(path.parent().unwrap().join("README.md").is_file());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn write_skill_dir_sidecar_failure_leaves_no_skill_json() {
        let root = tmp_root();
        let mapped = map_events_to_skill(
            "halfskill",
            Some("do it"),
            &[ev_nav("https://example.com/")],
            false,
        )
        .unwrap();
        let skills = state::skills_dir(&root);
        {
            let _fail = crate::skills::fail_export_write("README.md");
            let err = write_skill_dir(&root, &mapped).unwrap_err().to_string();
            assert!(
                err.contains("README.md") || err.contains("simulated"),
                "{err}"
            );
        }
        assert!(
            !skills.join("halfskill").join("skill.json").exists(),
            "sidecar failure must not leave final skill.json"
        );
        assert!(
            !skills.join("halfskill").exists(),
            "failed new export must not leave dest dir"
        );
        for e in fs::read_dir(&skills).unwrap() {
            let name = e.unwrap().file_name();
            let name = name.to_string_lossy();
            assert!(
                !name.starts_with(".tmp-export-"),
                "leftover staging {name}"
            );
        }

        let path = write_skill_dir(&root, &mapped).unwrap();
        let original = fs::read_to_string(&path).unwrap();
        let mut mapped_ow = mapped.clone();
        mapped_ow.overwrite = true;
        mapped_ow.audit_text = Some("# audit\n".into());
        {
            let _fail = crate::skills::fail_export_write("AUDIT.md");
            let err = write_skill_dir(&root, &mapped_ow)
                .unwrap_err()
                .to_string();
            assert!(
                err.contains("AUDIT.md") || err.contains("simulated"),
                "{err}"
            );
        }
        assert_eq!(fs::read_to_string(&path).unwrap(), original);
        assert!(!path.parent().unwrap().join("AUDIT.md").exists());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn sanitize_strips_userinfo_and_token() {
        let u = sanitize_url("https://user:secret@example.com/p?token=abc&q=1#frag").unwrap();
        assert!(!u.contains("secret"));
        assert!(!u.contains("user:"));
        assert!(!u.contains("token=abc"));
        assert!(u.contains("q=1"));
        assert!(!u.contains("#frag"));
        assert!(sanitize_url("file:///etc/passwd").is_err());
        assert!(sanitize_url("javascript:alert(1)").is_err());
    }

    #[test]
    fn fixture_file_round_trip_if_present() {
        let here = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        let events_path = here.join("fixtures/teach/recorded-events.json");
        if !events_path.is_file() {
            return;
        }
        let body: Value = serde_json::from_str(&fs::read_to_string(&events_path).unwrap()).unwrap();
        let events: Vec<RecordedEvent> =
            serde_json::from_value(body.get("events").cloned().unwrap()).unwrap();
        let name = body.get("name").and_then(|v| v.as_str()).unwrap();
        let goal = body.get("goal").and_then(|v| v.as_str());
        let mapped = map_events_to_skill(name, goal, &events, false).unwrap();
        let expected_path = here.join("fixtures/teach/expected-skill.json");
        let expected: Value =
            serde_json::from_str(&fs::read_to_string(expected_path).unwrap()).unwrap();
        assert_eq!(mapped.steps, expected["steps"].as_array().unwrap().clone());
        assert_eq!(mapped.goal.as_deref(), expected["goal"].as_str());
        assert_eq!(mapped.name, expected["name"]);
    }

    #[test]
    fn chat_export_matches_expected_skill_fixture() {
        let here = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        let expected_path = here.join("fixtures/teach/expected-skill.json");
        let expected: Value =
            serde_json::from_str(&fs::read_to_string(&expected_path).unwrap()).unwrap();
        let steps = vec![
            json!({
                "action": "goto",
                "url": "https://example.com/login?token=leakme&next=/app",
                "source": "llm"
            }),
            json!({
                "action": "fill",
                "selector": "#user",
                "text": "alice",
                "field_name": "username",
                "selectors": ["#user", "input[name=\"username\"]"],
                "source": "human"
            }),
            json!({
                "action": "fill",
                "selector": "#pass",
                "text": "hunter2",
                "field_name": "password",
                "selectors": ["#pass", "input[name=\"password\"]", "input[type=\"password\"]"],
                "source": "human"
            }),
            json!({"action": "click", "selector": "button.submit", "source": "human"}),
            json!({"kind": "click", "selector": "#raw-dom"}),
        ];
        // Raw DOM in the batch must fail the safety gate (not silently dropped
        // as a skip) when mixed? Spec: no raw-DOM-only steps. A raw event
        // without `action` is rejected so a bad merge cannot land in skill.json.
        let err = export_chat_draft(
            &tmp_root(),
            "taught-login",
            expected["goal"].as_str(),
            &steps,
            false,
        )
        .unwrap_err()
        .to_string();
        assert!(err.contains("raw DOM"), "{err}");

        let clean: Vec<Value> = steps
            .into_iter()
            .filter(|s| s.get("action").is_some())
            .collect();
        let root = tmp_root();
        let out = export_chat_draft(
            &root,
            "taught-login",
            expected["goal"].as_str(),
            &clean,
            false,
        )
        .unwrap();
        assert!(out.path.ends_with("skill.json"));
        for (got, exp) in out.skill["steps"]
            .as_array()
            .unwrap()
            .iter()
            .zip(expected["steps"].as_array().unwrap())
        {
            assert_eq!(got["action"], exp["action"]);
            if exp.get("url").is_some() {
                assert_eq!(got["url"], exp["url"]);
            }
            if exp.get("selector").is_some() {
                assert_eq!(got["selector"], exp["selector"]);
            }
            if exp.get("text").is_some() {
                assert_eq!(got["text"], exp["text"]);
            }
        }
        assert_eq!(out.skill["steps"][0]["source"], "agent");
        assert_eq!(out.skill["steps"][1]["source"], "human");
        assert_eq!(out.skill["params"], expected["params"]);
        let blob = serde_json::to_string(&out.skill).unwrap();
        assert!(!blob.contains("hunter2"));
        assert!(!blob.contains("leakme"));
        assert!(!blob.contains("pairing"));
        let parent = out.path.parent().unwrap();
        assert!(parent.join("AUDIT.md").is_file());
        assert!(parent.join("README.md").is_file());
        let err = export_chat_draft(
            &root,
            "taught-login",
            expected["goal"].as_str(),
            &clean,
            false,
        )
        .unwrap_err()
        .to_string();
        assert!(err.contains("already exists"), "{err}");
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn chat_export_rejects_danger_without_writing() {
        let root = tmp_root();
        let err = export_chat_draft(
            &root,
            "evil",
            Some("pwn"),
            &[json!({"action":"eval","code":"1"})],
            false,
        )
        .unwrap_err()
        .to_string();
        assert!(
            err.contains("forbidden") || err.contains("unknown") || err.contains("eval"),
            "{err}"
        );
        assert!(!state::skills_dir(&root).join("evil").join("skill.json").exists());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn chat_export_audit_failure_is_not_success() {
        let root = tmp_root();
        {
            let _fail = crate::skills::fail_export_write("AUDIT.md");
            let err = export_chat_draft(
                &root,
                "noaudit",
                Some("g"),
                &[json!({"action":"goto","url":"https://example.com/","source":"agent"})],
                false,
            )
            .unwrap_err()
            .to_string();
            assert!(
                err.contains("AUDIT.md") || err.contains("simulated"),
                "{err}"
            );
        }
        assert!(!state::skills_dir(&root)
            .join("noaudit")
            .join("skill.json")
            .exists());
        let _ = fs::remove_dir_all(&root);
    }

    fn write_exec(path: &Path, body: &str) {
        fs::write(path, body).unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(path, fs::Permissions::from_mode(0o755)).unwrap();
        }
    }

    #[test]
    fn preflight_reports_missing_binary() {
        let root = tmp_root();
        let fake = root.join("fake_python");
        write_exec(
            &fake,
            r#"#!/usr/bin/env python3
import json, sys
if len(sys.argv) >= 3 and sys.argv[1] == "-c" and "binary_info" in sys.argv[2]:
    print(json.dumps({"path": "/nonexistent/cloak-chrome", "installed": False}))
    sys.exit(3)
sys.stderr.write("unexpected %r\n" % (sys.argv,))
sys.exit(1)
"#,
        );
        let err = preflight_browser_binary(&fake.to_string_lossy())
            .unwrap_err()
            .to_string();
        assert!(err.contains("binary not found"), "{err}");
        assert!(err.contains("ensure_binary") || err.contains("doctor"), "{err}");
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn preflight_reports_import_failure() {
        let root = tmp_root();
        let fake = root.join("fake_python");
        write_exec(
            &fake,
            r#"#!/usr/bin/env python3
import sys
if len(sys.argv) >= 3 and sys.argv[1] == "-c":
    sys.stderr.write("import cloakbrowser failed: ModuleNotFoundError: No module named 'cloakbrowser'\n")
    sys.exit(2)
sys.exit(1)
"#,
        );
        let err = preflight_browser_binary(&fake.to_string_lossy())
            .unwrap_err()
            .to_string();
        assert!(err.contains("not importable"), "{err}");
        let _ = fs::remove_dir_all(&root);
    }
}
