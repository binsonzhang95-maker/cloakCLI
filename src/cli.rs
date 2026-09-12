use anyhow::{bail, Context, Result};
use clap::{Parser, Subcommand};
use std::path::{Path, PathBuf};
use std::time::Duration;

use crate::batch;
use crate::cookies;
use crate::locks::ProfileLock;
use crate::profiles;
use crate::skills;
use crate::state;
use crate::util::{self, redact_proxy};
use crate::worker::{self, Request};

#[derive(Parser, Debug)]
#[command(
    name = "cloakcli",
    about = "CloakCLI — stealth browser profiles + skills (TUI + CLI). Stealth ≠ anonymity guarantee."
)]
#[command(version)]
pub struct Cli {
    #[command(subcommand)]
    pub command: Option<Commands>,
}

#[derive(Subcommand, Debug)]
pub enum Commands {
    /// Open the interactive TUI (default when no args) — primary multi-session control plane
    Tui,
    /// Manage browser profiles
    Profile {
        #[command(subcommand)]
        action: ProfileCmd,
    },
    /// Open / list / close browser sessions (via persistent worker daemon)
    Browser {
        #[command(subcommand)]
        action: BrowserCmd,
    },
    /// List / run / import skills
    Skill {
        #[command(subcommand)]
        action: SkillCmd,
    },
    /// Batch-run jobs with concurrency
    Batch {
        #[command(subcommand)]
        action: BatchCmd,
    },
    /// Manage persistent Python worker daemon
    Worker {
        #[command(subcommand)]
        action: WorkerCmd,
    },
    /// Run master hub (accepts outbound client connections)
    Master {
        #[command(subcommand)]
        action: MasterCmd,
    },
    /// Run as remote client (outbound to master, or legacy HTTP serve)
    Client {
        #[command(subcommand)]
        action: ClientCmd,
    },
    /// Manage fleet of remote clients (master-side config)
    Fleet {
        #[command(subcommand)]
        action: FleetCmd,
    },
    /// Vision LLM stall-recovery config (`config/llm.json`, key via env var name only)
    Llm {
        #[command(subcommand)]
        action: LlmCmd,
    },
    /// Check Rust binary, Python worker, cloakbrowser, daemon
    Doctor,
}

#[derive(Subcommand, Debug)]
pub enum WorkerCmd {
    /// Start persistent daemon (unix socket data/worker.sock)
    Serve,
    /// Stop daemon (graceful close of browsers, then exit)
    Stop,
    /// Show daemon status
    Status,
}

#[derive(Subcommand, Debug)]
pub enum MasterCmd {
    /// Listen for outbound client connections (TCP JSONL) — DEV STUB (plaintext shared token)
    Serve {
        #[arg(long, default_value = "127.0.0.1:7750")]
        bind: String,
        #[arg(long, default_value = "dev-token", env = "CLOAKCLI_MASTER_TOKEN")]
        token: String,
    },
    /// Submit a job to a connected client (via master control socket)
    Submit {
        #[arg(long)]
        client: String,
        #[arg(long)]
        skill: String,
        #[arg(long)]
        profile: String,
        #[arg(long, group = "mode")]
        headed: bool,
        #[arg(long, group = "mode")]
        headless: bool,
        /// Optional stable job id (idempotent if already terminal)
        #[arg(long)]
        job_id: Option<String>,
    },
    /// List clients currently connected to the running master hub
    Clients,
    /// Query persisted / live job state
    JobState {
        #[arg(long)]
        job_id: String,
    },
    /// Cancel a running job on a client (kills local oneshot worker)
    Cancel {
        #[arg(long)]
        client: String,
        #[arg(long)]
        job_id: String,
    },
    /// Push desired config revision to connected clients (persisted under data/hub_desired.json)
    Config {
        #[arg(long)]
        concurrency: Option<usize>,
        #[arg(long, group = "mode")]
        headed: bool,
        #[arg(long, group = "mode")]
        headless: bool,
        /// Only print current desired (no push)
        #[arg(long)]
        get: bool,
    },
}

#[derive(Subcommand, Debug)]
pub enum ClientCmd {
    /// Outbound connect to master hub (preferred; NAT-friendly)
    Connect {
        #[arg(long, default_value = "127.0.0.1:7750")]
        master: String,
        #[arg(long, default_value = "dev-token", env = "CLOAKCLI_MASTER_TOKEN")]
        token: String,
        #[arg(long, default_value = "box1")]
        id: String,
    },
    /// Legacy inbound HTTP agent (optional; prefer `client connect`)
    Serve {
        #[arg(long, default_value = "0.0.0.0:7749")]
        bind: String,
        #[arg(long, default_value = "dev-token", env = "CLOAKCLI_CLIENT_TOKEN")]
        token: String,
    },
}

#[derive(Subcommand, Debug)]
pub enum FleetCmd {
    /// List configured remote clients
    List,
    /// Add a remote client endpoint
    Add {
        name: String,
        #[arg(long)]
        url: String,
        #[arg(long, default_value = "dev-token")]
        token: String,
        #[arg(long)]
        notes: Option<String>,
    },
    /// Remove a client from fleet.json
    Remove {
        name: String,
    },
    /// Ping a client (or all)
    Ping {
        name: Option<String>,
    },
    /// Set fleet defaults
    Set {
        #[arg(long)]
        concurrency: Option<usize>,
        #[arg(long, group = "mode")]
        headed: bool,
        #[arg(long, group = "mode")]
        headless: bool,
    },
}

#[derive(Subcommand, Debug)]
pub enum LlmCmd {
    /// Show config (never prints API keys or env values)
    Show,
    /// Create or update config/llm.json (mode 0600). Pass --api-key-env NAME, never a raw key.
    Set {
        #[arg(long)]
        base_url: Option<String>,
        #[arg(long)]
        model: Option<String>,
        /// Environment variable *name* that holds the API key (e.g. OPENAI_API_KEY)
        #[arg(long)]
        api_key_env: Option<String>,
        #[arg(long, group = "en")]
        enabled: bool,
        #[arg(long, group = "en")]
        disabled: bool,
        /// Recover wall-clock budget in seconds (default 300)
        #[arg(long)]
        recover_timeout_sec: Option<u64>,
        /// Comma-separated extra hosts allowed for recover goto (cross-origin)
        #[arg(long)]
        allow_hosts: Option<String>,
        #[arg(long)]
        clear_allow_hosts: bool,
        #[arg(long)]
        max_actions: Option<u32>,
        #[arg(long)]
        max_loops: Option<u32>,
    },
    /// Probe chat/completions connectivity (redacts secrets in output)
    Test,
}

#[derive(Subcommand, Debug)]
pub enum ProfileCmd {
    Create {
        name: String,
        #[arg(long)]
        proxy: Option<String>,
        #[arg(long)]
        notes: Option<String>,
    },
    List,
    Show {
        name: String,
    },
    /// Update proxy and/or notes
    Edit {
        name: String,
        #[arg(long)]
        proxy: Option<String>,
        /// Clear proxy
        #[arg(long)]
        clear_proxy: bool,
        #[arg(long)]
        notes: Option<String>,
    },
    Delete {
        name: String,
    },
    /// Import / export / clear / status profile cookies (storage_state)
    Cookie {
        #[command(subcommand)]
        action: CookieCmd,
    },
}

#[derive(Subcommand, Debug)]
pub enum CookieCmd {
    /// Import cookies from a file into profiles/<name>/cookie.json
    Import {
        profile: String,
        file: PathBuf,
        #[arg(long, default_value = "auto")]
        format: String,
    },
    /// Export cookie.json (stdout by default; --out FILE mode 0600; refuses data/|artifacts/|dirs)
    Export {
        profile: String,
        #[arg(long)]
        out: Option<PathBuf>,
    },
    /// Delete profile cookie.json (optional close of running sessions)
    Clear {
        profile: String,
        #[arg(long)]
        close_sessions: bool,
    },
    /// Show cookie counts (valid/expired)/domains only (never values)
    Status {
        profile: String,
    },
}

#[derive(Subcommand, Debug)]
pub enum BrowserCmd {
    Open {
        profile: String,
        #[arg(long)]
        url: Option<String>,
        #[arg(long, group = "mode")]
        headed: bool,
        #[arg(long, group = "mode")]
        headless: bool,
    },
    List,
    Close {
        /// Session id, profile name, or "all"
        target: String,
    },
}

#[derive(Subcommand, Debug)]
pub enum SkillCmd {
    List,
    Run {
        name: String,
        #[arg(long)]
        profile: String,
        #[arg(long, group = "mode")]
        headed: bool,
        #[arg(long, group = "mode")]
        headless: bool,
        /// Key=value vars (repeatable)
        #[arg(long = "var", value_name = "KEY=VALUE")]
        vars: Vec<String>,
    },
    Import {
        path: PathBuf,
        #[arg(long)]
        name: Option<String>,
    },
}

#[derive(Subcommand, Debug)]
pub enum BatchCmd {
    Run {
        /// JSON file: {"jobs":[{"profile":"...","skill":"...","vars":{}}]}
        #[arg(long)]
        jobs: Option<PathBuf>,
        /// Or: skill name (requires --profiles)
        #[arg(long)]
        skill: Option<String>,
        /// Comma-separated profile names (with --skill)
        #[arg(long)]
        profiles: Option<String>,
        #[arg(long, default_value_t = 2)]
        concurrency: usize,
        #[arg(long, group = "mode")]
        headed: bool,
        #[arg(long, group = "mode")]
        headless: bool,
    },
}

pub async fn handle_profile(root: &Path, action: ProfileCmd) -> Result<()> {
    match action {
        ProfileCmd::Create { name, proxy, notes } => {
            let p = profiles::create(root, &name, proxy, notes)?;
            println!("created profile {}", p.name);
            println!("  user_data_dir: {}", p.user_data_dir);
            if let Some(px) = &p.proxy {
                println!("  proxy: {}", redact_proxy(px));
            }
        }
        ProfileCmd::List => {
            let list = profiles::list(root)?;
            if list.is_empty() {
                println!("(no profiles)");
            } else {
                for p in list {
                    let ck = cookies::status_summary(root, &p.name);
                    println!(
                        "{:<16} proxy={} cookies={}",
                        p.name,
                        profiles::display_proxy(&p),
                        ck
                    );
                }
            }
        }
        ProfileCmd::Show { name } => {
            let p = profiles::get(root, &name)?;
            println!("{}", serde_json::to_string_pretty(&profiles::redacted_view(&p))?);
        }
        ProfileCmd::Edit {
            name,
            proxy,
            clear_proxy,
            notes,
        } => {
            let proxy_upd = if clear_proxy {
                Some(None)
            } else {
                proxy.map(Some)
            };
            let notes_upd = notes.map(Some);
            let p = profiles::update(root, &name, proxy_upd, notes_upd)?;
            println!("updated profile {}", p.name);
            println!("{}", serde_json::to_string_pretty(&profiles::redacted_view(&p))?);
        }
        ProfileCmd::Delete { name } => {
            profiles::delete(root, &name)?;
            println!("deleted profile {name}");
        }
        ProfileCmd::Cookie { action } => {
            handle_cookie(root, action).await?;
        }
    }
    Ok(())
}

async fn handle_cookie(root: &Path, action: CookieCmd) -> Result<()> {
    match action {
        CookieCmd::Import {
            profile,
            file,
            format,
        } => {
            let fmt = cookies::CookieFormat::parse(&format)?;
            // Import source may be anywhere readable; dest stays under profiles/
            let st = cookies::import(root, &profile, &file, fmt)?;
            println!(
                "imported {} cookies ({} origins) for profile {} → {}",
                st.cookie_count,
                st.origin_count,
                st.profile,
                st.path
            );
            if !st.domains.is_empty() {
                println!("  domains: {}", st.domains.join(", "));
            }
            println!("  note: open a new browser/session to apply (existing sessions not hot-updated)");
        }
        CookieCmd::Export { profile, out } => {
            if let Some(ref out_path) = out {
                let st = cookies::export(root, &profile, Some(out_path))?;
                println!(
                    "exported {} cookies → {} (mode 0600)",
                    st.cookie_count,
                    out_path.display()
                );
            } else {
                let (body, st) = cookies::export_string(root, &profile)?;
                print!("{body}");
                let _ = st;
            }
        }
        CookieCmd::Clear {
            profile,
            close_sessions,
        } => {
            let removed = cookies::clear(root, &profile)?;
            if removed {
                println!("cleared cookie.json for profile {profile}");
            } else {
                println!("no cookie.json for profile {profile}");
            }
            if close_sessions {
                let resp = worker::daemon_request(
                    root,
                    Request {
                        id: worker::next_id(),
                        cmd: "close".into(),
                        profile: None,
                        url: None,
                        headed: None,
                        skill: None,
                        vars: None,
                        session: Some(profile.clone()),
                        proxy: None,
                        user_data_dir: None,
                        skill_path: None,
                        root: Some(root.to_string_lossy().to_string()),
                        cookie_file: None,
                    },
                )
                .await?;
                if resp.ok {
                    println!("closed sessions for profile {profile}: {:?}", resp.data);
                } else {
                    eprintln!(
                        "close-sessions warning: {}",
                        resp.error.unwrap_or_else(|| "failed".into())
                    );
                }
            }
        }
        CookieCmd::Status { profile } => {
            let st = cookies::status(root, &profile)?;
            println!("{}", serde_json::to_string_pretty(&st)?);
        }
    }
    Ok(())
}

pub async fn handle_browser(root: &Path, action: BrowserCmd) -> Result<()> {
    match action {
        BrowserCmd::Open {
            profile,
            url,
            headed,
            headless,
        } => {
            let prof = profiles::get(root, &profile)?;
            let headed = resolve_headed(headed, headless);
            let _lock = ProfileLock::acquire(root, &prof.name, Duration::from_secs(60)).await?;
            // Hold lock only briefly around open start — actually for open we need the
            // profile dir while launching. Release after open returns (browser holds context).
            let resp = worker::daemon_request(
                root,
                Request {
                    id: worker::next_id(),
                    cmd: "open".into(),
                    profile: Some(prof.name.clone()),
                    url,
                    headed: Some(headed),
                    skill: None,
                    vars: None,
                    session: None,
                    proxy: prof.proxy.clone(),
                    user_data_dir: Some(prof.user_data_dir.clone()),
                    skill_path: None,
                    root: Some(root.to_string_lossy().to_string()),
                    cookie_file: cookies::cookie_file_for_open(root, &prof.name)?,
                },
            )
            .await?;
            drop(_lock);
            if !resp.ok {
                bail!("{}", resp.error.unwrap_or_else(|| "open failed".into()));
            }
            println!("{}", serde_json::to_string_pretty(&resp.data)?);
        }
        BrowserCmd::List => {
            let resp = worker::daemon_request(
                root,
                Request {
                    id: worker::next_id(),
                    cmd: "list_sessions".into(),
                    profile: None,
                    url: None,
                    headed: None,
                    skill: None,
                    vars: None,
                    session: None,
                    proxy: None,
                    user_data_dir: None,
                    skill_path: None,
                    root: Some(root.to_string_lossy().to_string()),
                    cookie_file: None,
                },
            )
            .await?;
            if !resp.ok {
                bail!("{}", resp.error.unwrap_or_else(|| "list failed".into()));
            }
            println!("{}", serde_json::to_string_pretty(&resp.data)?);
        }
        BrowserCmd::Close { target } => {
            let resp = worker::daemon_request(
                root,
                Request {
                    id: worker::next_id(),
                    cmd: "close".into(),
                    profile: None,
                    url: None,
                    headed: None,
                    skill: None,
                    vars: None,
                    session: Some(target),
                    proxy: None,
                    user_data_dir: None,
                    skill_path: None,
                    root: Some(root.to_string_lossy().to_string()),
                    cookie_file: None,
                },
            )
            .await?;
            if !resp.ok {
                bail!("{}", resp.error.unwrap_or_else(|| "close failed".into()));
            }
            println!("{}", serde_json::to_string_pretty(&resp.data)?);
        }
    }
    Ok(())
}

pub async fn handle_skill(root: &Path, action: SkillCmd) -> Result<()> {
    match action {
        SkillCmd::List => {
            let result = skills::list_with_errors(root)?;
            if result.skills.is_empty() && result.invalid.is_empty() {
                println!("(no skills)");
            }
            for s in &result.skills {
                println!("{:<20} {}", s.name, s.description);
            }
            for (path, err) in &result.invalid {
                eprintln!("INVALID {}: {err}", path.display());
            }
        }
        SkillCmd::Run {
            name,
            profile,
            headed,
            headless,
            vars,
        } => {
            let skill = skills::get(root, &name)?;
            let prof = profiles::get(root, &profile)?;
            let headed = resolve_headed(headed, headless);
            let mut var_map = serde_json::Map::new();
            for v in vars {
                let (k, val) = v
                    .split_once('=')
                    .with_context(|| format!("--var expects KEY=VALUE, got {v}"))?;
                var_map.insert(k.to_string(), serde_json::Value::String(val.to_string()));
            }
            let _lock = ProfileLock::acquire(root, &prof.name, Duration::from_secs(300)).await?;
            let resp = worker::oneshot(
                root,
                Request {
                    id: worker::next_id(),
                    cmd: "run_skill".into(),
                    profile: Some(prof.name.clone()),
                    url: None,
                    headed: Some(headed),
                    skill: Some(skill.name.clone()),
                    vars: Some(serde_json::Value::Object(var_map)),
                    session: None,
                    proxy: prof.proxy.clone(),
                    user_data_dir: Some(prof.user_data_dir.clone()),
                    skill_path: Some(skill.path.join("skill.json").to_string_lossy().to_string()),
                    root: Some(root.to_string_lossy().to_string()),
                    cookie_file: cookies::cookie_file_for_open(root, &prof.name)?,
                },
            )
            .await?;
            if !resp.ok {
                if let Some(data) = &resp.data {
                    eprintln!("{}", serde_json::to_string_pretty(data)?);
                }
                bail!("{}", resp.error.unwrap_or_else(|| "run_skill failed".into()));
            }
            println!("{}", serde_json::to_string_pretty(&resp.data)?);
        }
        SkillCmd::Import { path, name } => {
            if let Some(n) = &name {
                util::validate_name(n, "skill")?;
            }
            let s = skills::import(root, &path, name.as_deref())?;
            println!("imported skill {} → {}", s.name, s.path.display());
        }
    }
    Ok(())
}

pub async fn handle_batch(root: &Path, action: BatchCmd) -> Result<()> {
    match action {
        BatchCmd::Run {
            jobs,
            skill,
            profiles: profile_list,
            concurrency,
            headed,
            headless,
        } => {
            let headed = resolve_headed(headed, headless);
            if let Some(jobs_path) = jobs {
                batch::run_batch(root, &jobs_path, concurrency, headed).await?;
            } else if let (Some(skill), Some(plist)) = (skill, profile_list) {
                let names: Vec<String> = plist
                    .split(',')
                    .map(|s| s.trim().to_string())
                    .filter(|s| !s.is_empty())
                    .collect();
                if names.is_empty() {
                    bail!("--profiles requires at least one name");
                }
                batch::run_skill_on_profiles(root, &skill, &names, concurrency, headed).await?;
            } else {
                bail!("batch run requires --jobs FILE or --skill NAME --profiles a,b");
            }
        }
    }
    Ok(())
}

pub async fn handle_worker(root: &Path, action: WorkerCmd) -> Result<()> {
    match action {
        WorkerCmd::Serve => worker::daemon_serve(root).await,
        WorkerCmd::Stop => worker::daemon_stop(root).await,
        WorkerCmd::Status => {
            let st = worker::daemon_status(root);
            println!("daemon: {}", if st.running { "running" } else { "stopped" });
            println!("detail: {}", st.detail);
            println!("socket: {}", st.sock.display());
            if let Some(pid) = st.pid {
                println!("pid:    {pid}");
            }
            Ok(())
        }
    }
}

pub async fn handle_doctor(root: &Path) -> Result<()> {
    println!("CloakCLI doctor");
    println!("===============");
    println!("project_root: {}", root.display());
    println!("note: stealth features ≠ anonymity / anti-detect guarantee");

    if let Ok(exe) = std::env::current_exe() {
        println!("rust_binary:  {}", exe.display());
    } else {
        println!("rust_binary:  (unknown)");
    }
    println!("rustc/cargo:  cloakcli {}", env!("CARGO_PKG_VERSION"));

    let info = worker::doctor_python(root);
    println!("python:       {}", info.get("python").unwrap_or(&"?".into()));
    println!(
        "worker_path:  {}",
        info.get("worker_path").unwrap_or(&"?".into())
    );
    println!(
        "worker_import:{}",
        info.get("worker_import").unwrap_or(&"?".into())
    );
    println!(
        "cloakbrowser: {}",
        info.get("cloakbrowser").unwrap_or(&"?".into())
    );
    println!(
        "cloakbrowser_bin: {}",
        info.get("cloakbrowser_bin").unwrap_or(&"?".into())
    );
    println!(
        "daemon:       {}",
        info.get("daemon").unwrap_or(&"?".into())
    );
    println!(
        "daemon_sock:  {}",
        info.get("daemon_sock").unwrap_or(&"?".into())
    );

    // Ping oneshot worker
    match worker::oneshot(
        root,
        Request {
            id: "doctor".into(),
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
            root: Some(root.to_string_lossy().to_string()),
            cookie_file: None,
        },
    )
    .await
    {
        Ok(r) if r.ok => println!("worker_ipc:   ok {:?}", r.data),
        Ok(r) => println!("worker_ipc:   FAIL {}", r.error.unwrap_or_default()),
        Err(e) => println!("worker_ipc:   FAIL {e}"),
    }

    // Ping daemon if running
    let st = worker::daemon_status(root);
    if st.running {
        match worker::daemon_request(
            root,
            Request {
                id: "doctor-d".into(),
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
            },
        )
        .await
        {
            Ok(r) if r.ok => println!("daemon_ipc:   ok {:?}", r.data),
            Ok(r) => println!("daemon_ipc:   FAIL {}", r.error.unwrap_or_default()),
            Err(e) => println!("daemon_ipc:   FAIL {e}"),
        }
    } else {
        println!("daemon_ipc:   (daemon not running — start with: cloakcli worker serve)");
    }

    let n_prof = profiles::list(root)?.len();
    let skill_res = skills::list_with_errors(root)?;
    println!("profiles:     {n_prof}");
    println!("skills:       {}", skill_res.skills.len());
    if !skill_res.invalid.is_empty() {
        println!("skills_invalid: {}", skill_res.invalid.len());
        for (p, e) in &skill_res.invalid {
            println!("  - {}: {e}", p.display());
        }
    }
    println!(
        "default_headed (this OS / CLOAKCLI_HEADED): {}",
        state::default_headed()
    );
    println!("\nDaemon lifecycle:");
    println!("  cloakcli worker serve   # start persistent browser session daemon");
    println!("  cloakcli worker status  # show pid/socket");
    println!("  cloakcli worker stop    # close all browsers, stop daemon");
    println!("\nNote: full browser launch may fail in headless CI/boxes without display/deps;");
    println!("      code path uses cloakbrowser.launch_persistent_context via Python worker.");
    Ok(())
}


pub async fn handle_client(root: &Path, action: ClientCmd) -> Result<()> {
    match action {
        ClientCmd::Connect { master, token, id } => {
            crate::util::validate_name(&id, "client")?;
            let cfg = crate::client_daemon::ClientDaemonConfig {
                root: root.to_path_buf(),
                master,
                token,
                client_id: id,
            };
            crate::client_daemon::run(cfg).await
        }
        ClientCmd::Serve { bind, token } => {
            let cfg = crate::client_agent::AgentConfig {
                root: root.to_path_buf(),
                bind,
                token,
            };
            tokio::task::spawn_blocking(move || crate::client_agent::serve(cfg))
                .await
                .map_err(|e| anyhow::anyhow!("client agent task: {e}"))??;
            Ok(())
        }
    }
}

pub async fn handle_master(root: &Path, action: MasterCmd) -> Result<()> {
    match action {
        MasterCmd::Serve { bind, token } => {
            let hub = crate::master_hub::new_hub(root, &token);
            crate::master_hub::write_master_meta(root, &bind)?;
            let ctrl = crate::master_hub::control_sock_path(root);
            println!("master hub on {bind} token=*** (DEV STUB — plaintext shared token ≠ production)");
            println!("clients: cloakcli client connect --master {bind}");
            println!("control socket: {}", ctrl.display());
            crate::master_hub::serve_with_control(&bind, hub, Some(ctrl)).await
        }
        MasterCmd::Submit {
            client,
            skill,
            profile,
            headed,
            headless,
            job_id,
        } => {
            let headed = if headed {
                true
            } else if headless {
                false
            } else {
                crate::state::default_headed()
            };
            let mut body = serde_json::json!({
                "cmd": "submit",
                "client_id": client,
                "skill": skill,
                "profile": profile,
                "headed": headed,
            });
            if let Some(jid) = job_id {
                body["job_id"] = serde_json::Value::String(jid);
            }
            let resp = crate::master_hub::control_request(root, body).await?;
            println!("{}", serde_json::to_string_pretty(&resp)?);
            if resp.get("ok") != Some(&serde_json::Value::Bool(true)) {
                bail!("{}", resp.get("error").and_then(|e| e.as_str()).unwrap_or("submit failed"));
            }
            Ok(())
        }
        MasterCmd::Clients => {
            let resp = crate::master_hub::control_request(
                root,
                serde_json::json!({"cmd": "list_clients"}),
            )
            .await?;
            println!("{}", serde_json::to_string_pretty(&resp)?);
            Ok(())
        }
        MasterCmd::JobState { job_id } => {
            let resp = crate::master_hub::control_request(
                root,
                serde_json::json!({"cmd": "job_state", "job_id": job_id}),
            )
            .await?;
            println!("{}", serde_json::to_string_pretty(&resp)?);
            if resp.get("ok") != Some(&serde_json::Value::Bool(true)) {
                bail!("{}", resp.get("error").and_then(|e| e.as_str()).unwrap_or("job_state failed"));
            }
            Ok(())
        }
        MasterCmd::Cancel { client, job_id } => {
            let resp = crate::master_hub::control_request(
                root,
                serde_json::json!({"cmd": "cancel", "client_id": client, "job_id": job_id}),
            )
            .await?;
            println!("{}", serde_json::to_string_pretty(&resp)?);
            if resp.get("ok") != Some(&serde_json::Value::Bool(true)) {
                bail!("{}", resp.get("error").and_then(|e| e.as_str()).unwrap_or("cancel failed"));
            }
            Ok(())
        }
        MasterCmd::Config {
            concurrency,
            headed,
            headless,
            get,
        } => {
            if get {
                let resp = crate::master_hub::control_request(
                    root,
                    serde_json::json!({"cmd": "get_config"}),
                )
                .await?;
                println!("{}", serde_json::to_string_pretty(&resp)?);
                return Ok(());
            }
            let mut body = serde_json::json!({"cmd": "config_update"});
            if let Some(c) = concurrency {
                body["concurrency"] = serde_json::json!(c);
            }
            if headed {
                body["headed"] = serde_json::json!(true);
            } else if headless {
                body["headed"] = serde_json::json!(false);
            }
            let resp = crate::master_hub::control_request(root, body).await?;
            println!("{}", serde_json::to_string_pretty(&resp)?);
            if resp.get("ok") != Some(&serde_json::Value::Bool(true)) {
                bail!("{}", resp.get("error").and_then(|e| e.as_str()).unwrap_or("config_update failed"));
            }
            Ok(())
        }
    }
}

pub fn handle_fleet(root: &Path, action: FleetCmd) -> Result<()> {
    match action {
        FleetCmd::List => {
            let cfg = crate::fleet::load(root)?;
            println!(
                "defaults: concurrency={} headed={}",
                cfg.default_concurrency, cfg.default_headed
            );
            if cfg.clients.is_empty() {
                println!("(no clients)");
            } else {
                for c in cfg.clients {
                    println!(
                        "{:<12} {}  token={}…",
                        c.name,
                        c.url,
                        &c.token.chars().take(4).collect::<String>()
                    );
                }
            }
            println!("config: {}", crate::fleet::fleet_path(root).display());
        }
        FleetCmd::Add {
            name,
            url,
            token,
            notes,
        } => {
            crate::fleet::add_client(
                root,
                crate::fleet::FleetClient {
                    name: name.clone(),
                    url,
                    token,
                    notes,
                },
            )?;
            println!("added client {name}");
        }
        FleetCmd::Remove { name } => {
            crate::fleet::remove_client(root, &name)?;
            println!("removed client {name}");
        }
        FleetCmd::Ping { name } => {
            let cfg = crate::fleet::load(root)?;
            let targets: Vec<_> = match name {
                Some(n) => cfg.clients.into_iter().filter(|c| c.name == n).collect(),
                None => cfg.clients,
            };
            if targets.is_empty() {
                bail!("no matching clients");
            }
            println!("Live online status is on the master hub (TUI Clients pane / master serve).");
            println!("Configured (desired) clients:");
            for c in targets {
                println!("  {} → dial master {} ({})", c.name, c.url, c.notes.as_deref().unwrap_or("-"));
            }
        }
        FleetCmd::Set {
            concurrency,
            headed,
            headless,
        } => {
            let headed_opt = if headed {
                Some(true)
            } else if headless {
                Some(false)
            } else {
                None
            };
            let cfg = crate::fleet::update_defaults(root, concurrency, headed_opt)?;
            println!(
                "defaults: concurrency={} headed={}",
                cfg.default_concurrency, cfg.default_headed
            );
        }
    }
    Ok(())
}

pub async fn handle_llm(root: &Path, action: LlmCmd) -> Result<()> {
    match action {
        LlmCmd::Show => {
            let v = crate::llm::view(root);
            print!("{}", crate::llm::format_show(&v));
        }
        LlmCmd::Set {
            base_url,
            model,
            api_key_env,
            enabled,
            disabled,
            recover_timeout_sec,
            allow_hosts,
            clear_allow_hosts,
            max_actions,
            max_loops,
        } => {
            if let Some(ref k) = api_key_env {
                if k.to_ascii_lowercase().contains("sk-") || k.contains("Bearer") {
                    bail!("--api-key-env takes an environment variable NAME, not a raw key");
                }
            }
            let hosts = if clear_allow_hosts {
                Some(Vec::new())
            } else {
                allow_hosts.map(|s| {
                    s.split(',')
                        .map(|p| p.trim().to_string())
                        .filter(|p| !p.is_empty())
                        .collect::<Vec<_>>()
                })
            };
            let en = if enabled {
                Some(true)
            } else if disabled {
                Some(false)
            } else {
                None
            };
            let cfg = crate::llm::apply_set(
                root,
                crate::llm::LlmSetArgs {
                    base_url,
                    model,
                    api_key_env,
                    enabled: en,
                    recover_timeout_sec,
                    allow_hosts: hosts,
                    max_actions,
                    max_loops,
                    max_tokens_per_recover: None,
                },
            )?;
            println!("wrote {} (mode 0600)", crate::llm::config_path(root).display());
            print!("{}", crate::llm::format_show(&crate::llm::view(root)));
            let _ = cfg;
        }
        LlmCmd::Test => {
            let v = crate::llm::view(root);
            if !v.configured {
                bail!("no config/llm.json — run: cloakcli llm set --base-url URL --model MODEL --api-key-env VAR");
            }
            let extra_key = std::env::var(&v.api_key_env).ok();
            let resp = worker::oneshot(
                root,
                Request {
                    id: worker::next_id(),
                    cmd: "llm_test".into(),
                    profile: None,
                    url: None,
                    headed: None,
                    skill: None,
                    vars: None,
                    session: None,
                    proxy: None,
                    user_data_dir: None,
                    skill_path: None,
                    root: Some(root.to_string_lossy().to_string()),
                    cookie_file: None,
                },
            )
            .await?;
            let raw = if resp.ok {
                serde_json::to_string_pretty(&resp.data)?
            } else {
                let mut obj = serde_json::json!({
                    "ok": false,
                    "error": resp.error,
                });
                if let Some(data) = resp.data {
                    obj["data"] = data;
                }
                serde_json::to_string_pretty(&obj)?
            };
            let redacted = crate::llm::redact_secrets(&raw, extra_key.as_deref());
            println!("{redacted}");
            if !resp.ok {
                bail!("{}", resp.error.unwrap_or_else(|| "llm test failed".into()));
            }
            if extra_key
                .as_deref()
                .map(|k| k.len() >= 4 && redacted.contains(k))
                .unwrap_or(false)
            {
                bail!("internal error: llm test output leaked a secret (redaction failed)");
            }
        }
    }
    Ok(())
}

fn resolve_headed(headed_flag: bool, headless_flag: bool) -> bool {
    if headed_flag {
        true
    } else if headless_flag {
        false
    } else {
        state::default_headed()
    }
}
