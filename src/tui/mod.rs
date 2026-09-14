//! Master TUI — control plane for local profiles/skills and remote clients.
//! Local multi-browser sessions are shown from the node daemon; fleet clients
//! register via outbound connections to the embedded master hub.

pub mod chat;
mod theme;
mod ui;

use anyhow::Result;
use crossterm::event::{self, Event, KeyCode, KeyEventKind, KeyModifiers};
use crossterm::execute;
use crossterm::terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen};
use ratatui::backend::CrosstermBackend;
use ratatui::widgets::ListState;
use ratatui::Terminal;
use std::collections::HashMap;
use std::io;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};
use tokio::task::JoinHandle;
use uuid::Uuid;

use crate::cookies::{self, CookieFormat};
use crate::fleet;
use crate::master_hub::{self, ClientInfo, SharedHub};
use crate::profiles::{self, Profile};
use crate::skills::{self, Skill};
use crate::state;
use crate::llm::{self, LlmView};
use crate::util::redact_proxy;
use crate::worker::{self, Request};

#[derive(Clone, Copy, PartialEq, Eq)]
enum Tab {
    Profiles = 0,
    Skills = 1,
    Sessions = 2,
    Clients = 3,
    Config = 4,
    Logs = 5,
    Chat = 6,
}

impl Tab {
    fn next(self) -> Self {
        match self {
            Tab::Profiles => Tab::Skills,
            Tab::Skills => Tab::Sessions,
            Tab::Sessions => Tab::Clients,
            Tab::Clients => Tab::Config,
            Tab::Config => Tab::Logs,
            Tab::Logs => Tab::Chat,
            Tab::Chat => Tab::Profiles,
        }
    }
    fn prev(self) -> Self {
        match self {
            Tab::Profiles => Tab::Chat,
            Tab::Skills => Tab::Profiles,
            Tab::Sessions => Tab::Skills,
            Tab::Clients => Tab::Sessions,
            Tab::Config => Tab::Clients,
            Tab::Logs => Tab::Config,
            Tab::Chat => Tab::Logs,
        }
    }
    fn title_bilingual(self) -> &'static str {
        match self {
            Tab::Profiles => "Profiles",
            Tab::Skills => "Skills",
            Tab::Sessions => "Sessions",
            Tab::Clients => "Clients",
            Tab::Config => "Config",
            Tab::Logs => "Logs",
            Tab::Chat => "Chat",
        }
    }
    fn all() -> [Tab; 7] {
        [
            Tab::Profiles,
            Tab::Skills,
            Tab::Sessions,
            Tab::Clients,
            Tab::Config,
            Tab::Logs,
            Tab::Chat,
        ]
    }
}

#[derive(Clone, Debug)]
enum InputMode {
    NewProfile,
    EditProxy,
    CookieImport,
    CookieExport,
    LlmBaseUrl,
    LlmApiKey,
    LlmTimeout,
}

/// Real waits that may show a throbber. Idle / input / error do not.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum BusyKind {
    WorkerStart,
    WorkerRefresh,
    SkillRun,
    HubConnect,
    SessionsLoad,
    LlmFetch,
    TeachStart,
}

impl BusyKind {
    fn label(self) -> &'static str {
        match self {
            BusyKind::WorkerStart => "starting worker",
            BusyKind::WorkerRefresh => "refreshing worker",
            BusyKind::SkillRun => "running skill",
            BusyKind::HubConnect => "connecting hub",
            BusyKind::SessionsLoad => "loading sessions",
            BusyKind::LlmFetch => "fetching models",
            BusyKind::TeachStart => "teaching (headed)",
        }
    }
}

enum PendingDone {
    HubConnect { ok: bool },
    WorkerStart { result: Result<(), String> },
    Sessions {
        rows: Result<Vec<SessionRow>, String>,
        done_status: String,
    },
    SkillRun {
        log: String,
        status: String,
        sessions: Result<Vec<SessionRow>, String>,
    },
    LlmModels {
        result: Result<crate::llm_client::ModelsList, String>,
        saved_model: String,
        requested_base: String,
    },
    Teach {
        log: String,
        status: String,
    },
}

#[derive(Clone, Debug)]
struct SessionRow {
    id: String,
    profile: String,
    headed: bool,
    url: String,
}

struct App {
    root: PathBuf,
    tab: Tab,
    profile_rows: Vec<Profile>,
    skill_rows: Vec<Skill>,
    session_rows: Vec<SessionRow>,
    sessions_hint: Option<String>,
    client_rows: Vec<ClientInfo>,
    cookie_summaries: HashMap<String, String>,
    logs: Vec<String>,
    profile_state: ListState,
    skill_state: ListState,
    session_state: ListState,
    client_state: ListState,
    status: String,
    headed: bool,
    concurrency: usize,
    llm: LlmView,
    llm_draft_base_url: String,
    llm_session_key_set: bool,
    llm_models: Vec<String>,
    llm_models_base_url: String,
    llm_models_truncated: bool,
    llm_model_state: ListState,
    llm_fetch_err: Option<String>,
    hub: SharedHub,
    hub_bind: String,
    hub_bind_ok: bool,
    desired_rev: u64,
    input_mode: Option<InputMode>,
    input_buf: String,
    last_refresh: Instant,
    last_anim: Instant,
    logged_invalid_skills: bool,
    animation_phase: u32,
    animations_enabled: bool,
    busy: Option<BusyKind>,
    pending: Option<JoinHandle<PendingDone>>,
    throbber_state: throbber_widgets_tui::ThrobberState,
    chat: crate::teach_chat::ChatSession,
}

impl App {
    fn new(root: &Path, hub: SharedHub, hub_bind: String) -> Result<Self> {
        let fleet_cfg = fleet::load(root).unwrap_or_default();
        let mut app = Self {
            root: root.to_path_buf(),
            tab: Tab::Profiles,
            profile_rows: Vec::new(),
            skill_rows: Vec::new(),
            session_rows: Vec::new(),
            sessions_hint: None,
            client_rows: Vec::new(),
            cookie_summaries: HashMap::new(),
            logs: vec![
                "CloakCLI master TUI ready — ops console".into(),
                "Clients dial outbound to master hub; Sessions = local worker daemon.".into(),
                "Stealth ≠ anonymity guarantee. Fleet protocol = DEV STUB.".into(),
            ],
            profile_state: ListState::default(),
            skill_state: ListState::default(),
            session_state: ListState::default(),
            client_state: ListState::default(),
            status: "ready".into(),
            headed: fleet_cfg.default_headed || state::default_headed(),
            concurrency: fleet_cfg.default_concurrency.max(1),
            llm: llm::view(root),
            llm_draft_base_url: String::new(),
            llm_session_key_set: false,
            llm_models: Vec::new(),
            llm_models_base_url: String::new(),
            llm_models_truncated: false,
            llm_model_state: ListState::default(),
            llm_fetch_err: None,
            hub,
            hub_bind,
            hub_bind_ok: false,
            desired_rev: 1,
            input_mode: None,
            input_buf: String::new(),
            last_refresh: Instant::now(),
            last_anim: Instant::now(),
            logged_invalid_skills: false,
            animation_phase: 0,
            animations_enabled: detect_animations_enabled(),
            busy: None,
            pending: None,
            throbber_state: throbber_widgets_tui::ThrobberState::default(),
            chat: crate::teach_chat::ChatSession::default(),
        };
        app.reload_static()?;
        Ok(app)
    }

    fn set_busy(&mut self, kind: BusyKind) {
        self.busy = Some(kind);
        self.status = kind.label().into();
    }

    fn clear_busy(&mut self) {
        self.busy = None;
        self.pending = None;
    }

    fn tick_animation(&mut self) {
        if !self.animations_enabled {
            return;
        }
        if self.last_anim.elapsed() < Duration::from_millis(90) {
            return;
        }
        self.last_anim = Instant::now();
        self.animation_phase = self.animation_phase.wrapping_add(1);
        if self.busy.is_some() {
            self.throbber_state.calc_next();
        }
    }

    fn start_boot(&mut self) {
        self.set_busy(BusyKind::HubConnect);
        let ctrl = master_hub::control_sock_path(&self.root);
        self.pending = Some(tokio::spawn(async move {
            PendingDone::HubConnect {
                ok: wait_hub_ready(ctrl).await,
            }
        }));
    }

    fn begin_worker_start(&mut self) {
        self.set_busy(BusyKind::WorkerStart);
        let root = self.root.clone();
        self.pending = Some(tokio::spawn(async move {
            PendingDone::WorkerStart {
                result: worker::ensure_daemon(&root)
                    .await
                    .map_err(|e| e.to_string()),
            }
        }));
    }

    fn begin_sessions_load(&mut self, kind: BusyKind, done_status: &str) {
        self.set_busy(kind);
        let root = self.root.clone();
        let done_status = done_status.to_string();
        self.pending = Some(tokio::spawn(async move {
            PendingDone::Sessions {
                rows: list_sessions_async(&root)
                    .await
                    .map_err(|e| e.to_string()),
                done_status,
            }
        }));
    }

    fn apply_sessions_result(&mut self, rows: Result<Vec<SessionRow>, String>) {
        match rows {
            Ok(rows) => {
                if rows.is_empty() {
                    self.session_rows.clear();
                    self.sessions_hint =
                        Some("(no open browser sessions on local daemon)".into());
                } else {
                    self.session_rows = rows;
                    self.sessions_hint = None;
                }
            }
            Err(e) => {
                self.session_rows.clear();
                self.sessions_hint = Some(format!("(sessions error: {e})"));
            }
        }
        self.ensure_selections();
        self.last_refresh = Instant::now();
    }

    fn apply_pending(&mut self, done: PendingDone) {
        match done {
            PendingDone::HubConnect { ok } => {
                self.hub_bind_ok = ok;
                if ok {
                    self.log(format!("hub listening {}", self.hub_bind));
                } else {
                    self.log(format!("hub {} not ready", self.hub_bind));
                }
                self.begin_worker_start();
            }
            PendingDone::WorkerStart { result } => {
                match result {
                    Ok(()) => self.log("worker daemon ready"),
                    Err(e) => self.log(format!("worker start: {e}")),
                }
                self.begin_sessions_load(BusyKind::SessionsLoad, "ready");
            }
            PendingDone::Sessions { rows, done_status } => {
                self.apply_sessions_result(rows);
                self.clear_busy();
                self.status = done_status;
            }
            PendingDone::SkillRun {
                log,
                status,
                sessions,
            } => {
                self.log(log);
                self.apply_sessions_result(sessions);
                self.clear_busy();
                self.status = status;
            }
            PendingDone::LlmModels {
                result,
                saved_model,
                requested_base,
            } => {
                self.clear_busy();
                match result {
                    Ok(list) => {
                        self.llm_fetch_err = None;
                        let current = self.current_llm_base();
                        if !llm::models_bound_to_request_base(&requested_base, &current) {
                            self.clear_llm_models_list();
                            self.status = "llm fetch completed for a previous base_url (discarded); fetch again".into();
                            self.log("llm fetch discarded: base_url changed during request");
                            self.llm = llm::view(&self.root);
                            return;
                        }
                        self.llm_models = list.ids;
                        self.llm_models_truncated = list.truncated;
                        self.llm_models_base_url = requested_base.clone();
                        if let Some(pos) = self
                            .llm_models
                            .iter()
                            .position(|id| id == &saved_model)
                        {
                            self.llm_model_state.select(Some(pos));
                        } else if !self.llm_models.is_empty() {
                            self.llm_model_state.select(Some(0));
                        }
                        let _ = llm::save_models_cache(
                            &self.root,
                            &llm::ModelsCache {
                                base_url: requested_base,
                                ids: self.llm_models.clone(),
                                truncated: self.llm_models_truncated,
                            },
                        );
                        // Fetch must not change the saved model.
                        self.llm = llm::view(&self.root);
                        let n = self.llm_models.len();
                        self.status = format!(
                            "fetched {n} models{}",
                            if list.truncated { " (truncated)" } else { "" }
                        );
                        self.log(format!(
                            "llm models fetched n={n} truncated={} (saved model unchanged)",
                            list.truncated
                        ));
                    }
                    Err(e) => {
                        let e = llm::redact_secrets(&e, None);
                        // Same-base refetch failure must drop the picker so Enter
                        // cannot persist ids from the previous successful fetch.
                        self.clear_llm_models_list();
                        self.llm_fetch_err = Some(e.clone());
                        self.llm = llm::view(&self.root);
                        self.status = format!("llm fetch failed: {e}");
                        self.log(format!("llm fetch failed: {e}"));
                    }
                }
            }
            PendingDone::Teach { log, status } => {
                self.log(log);
                let _ = self.reload_static();
                self.clear_busy();
                self.status = status;
            }
        }
    }

    fn log(&mut self, msg: impl Into<String>) {
        let ts = chrono::Local::now().format("%H:%M:%S");
        self.logs.push(format!("[{ts}] {}", msg.into()));
        if self.logs.len() > 300 {
            self.logs.drain(0..self.logs.len() - 300);
        }
    }

    /// Full reload of profiles/skills + live panes.
    fn reload_static(&mut self) -> Result<()> {
        self.profile_rows = profiles::list(&self.root)?;
        self.cookie_summaries.clear();
        for p in &self.profile_rows {
            self.cookie_summaries
                .insert(p.name.clone(), cookies::status_summary(&self.root, &p.name));
        }

        let sk = skills::list_with_errors(&self.root)?;
        self.skill_rows = sk.skills;
        if !self.logged_invalid_skills {
            for (p, e) in sk.invalid {
                self.log(format!("INVALID SKILL {}: {e}", p.display()));
            }
            self.logged_invalid_skills = true;
        }

        {
            let hub = self.hub.try_read().map_err(|_| anyhow::anyhow!("hub lock unavailable"))?;
            self.desired_rev = hub.desired.revision;
            self.client_rows = hub.list_clients();
            self.client_rows
                .sort_by(|a, b| a.client_id.cmp(&b.client_id));
        }

        self.llm = llm::view(&self.root);
        if self.llm_draft_base_url.is_empty() {
            self.llm_draft_base_url = if self.llm.base_url.is_empty() {
                llm::DEFAULT_BASE_URL.to_string()
            } else {
                self.llm.base_url.clone()
            };
        }
        // Do not revive a list we just invalidated (in-flight fetch or last fetch failed).
        if self.llm_models.is_empty()
            && self.llm_fetch_err.is_none()
            && self.busy != Some(BusyKind::LlmFetch)
        {
            if let Some(cache) = llm::load_models_cache(&self.root) {
                let draft = self.current_llm_base();
                if llm::models_bound_to_request_base(&cache.base_url, &draft) {
                    self.llm_models = cache.ids;
                    self.llm_models_truncated = cache.truncated;
                    self.llm_models_base_url = cache.base_url;
                    if let Some(pos) = self
                        .llm_models
                        .iter()
                        .position(|id| id == &self.llm.model)
                    {
                        self.llm_model_state.select(Some(pos));
                    } else if !self.llm_models.is_empty() {
                        self.llm_model_state.select(Some(0));
                    }
                }
            }
        }
        self.ensure_selections();
        Ok(())
    }

    /// Non-blocking-ish live refresh: clients + sessions metadata.
    async fn refresh_live(&mut self) {
        {
            let hub = self.hub.read().await;
            self.desired_rev = hub.desired.revision;
            self.client_rows = hub.list_clients();
            self.client_rows
                .sort_by(|a, b| a.client_id.cmp(&b.client_id));
        }
        master_hub::reap_stale(&self.hub).await;
        self.refresh_sessions().await;
        // refresh cookie summaries lightly (status only, no values)
        for p in &self.profile_rows {
            self.cookie_summaries
                .insert(p.name.clone(), cookies::status_summary(&self.root, &p.name));
        }
        self.ensure_selections();
        self.last_refresh = Instant::now();
    }

    fn ensure_selections(&mut self) {
        if self.profile_state.selected().is_none() && !self.profile_rows.is_empty() {
            self.profile_state.select(Some(0));
        }
        if let Some(i) = self.profile_state.selected() {
            if i >= self.profile_rows.len() {
                self.profile_state.select(if self.profile_rows.is_empty() {
                    None
                } else {
                    Some(self.profile_rows.len() - 1)
                });
            }
        }
        if self.skill_state.selected().is_none() && !self.skill_rows.is_empty() {
            self.skill_state.select(Some(0));
        }
        if let Some(i) = self.skill_state.selected() {
            if i >= self.skill_rows.len() {
                self.skill_state.select(if self.skill_rows.is_empty() {
                    None
                } else {
                    Some(self.skill_rows.len() - 1)
                });
            }
        }
        if self.session_state.selected().is_none() && !self.session_rows.is_empty() {
            self.session_state.select(Some(0));
        }
        if let Some(i) = self.session_state.selected() {
            if i >= self.session_rows.len() {
                self.session_state.select(if self.session_rows.is_empty() {
                    None
                } else {
                    Some(self.session_rows.len() - 1)
                });
            }
        }
        if self.client_state.selected().is_none() && !self.client_rows.is_empty() {
            self.client_state.select(Some(0));
        }
        if let Some(i) = self.client_state.selected() {
            if i >= self.client_rows.len() {
                self.client_state.select(if self.client_rows.is_empty() {
                    None
                } else {
                    Some(self.client_rows.len() - 1)
                });
            }
        }
        if self.llm_model_state.selected().is_none() && !self.llm_models.is_empty() {
            self.llm_model_state.select(Some(0));
        }
        if let Some(i) = self.llm_model_state.selected() {
            if i >= self.llm_models.len() {
                self.llm_model_state.select(if self.llm_models.is_empty() {
                    None
                } else {
                    Some(self.llm_models.len() - 1)
                });
            }
        }
    }

    fn selected_profile_name(&self) -> Option<String> {
        let i = self.profile_state.selected()?;
        self.profile_rows.get(i).map(|p| p.name.clone())
    }

    fn selected_skill_name(&self) -> Option<String> {
        let i = self.skill_state.selected()?;
        self.skill_rows.get(i).map(|s| s.name.clone())
    }

    fn selected_session_id(&self) -> Option<String> {
        let i = self.session_state.selected()?;
        self.session_rows.get(i).map(|s| s.id.clone())
    }

    fn selected_client_id(&self) -> Option<String> {
        let i = self.client_state.selected()?;
        self.client_rows.get(i).map(|c| c.client_id.clone())
    }

    async fn refresh_sessions(&mut self) {
        match list_sessions_async(&self.root).await {
            Ok(rows) => {
                if rows.is_empty() {
                    self.session_rows.clear();
                    self.sessions_hint =
                        Some("(no open browser sessions on local daemon)".into());
                } else {
                    self.session_rows = rows;
                    self.sessions_hint = None;
                }
            }
            Err(e) => {
                self.session_rows.clear();
                self.sessions_hint = Some(format!("(sessions error: {e})"));
            }
        }
        self.ensure_selections();
    }

    fn begin_skill_run(&mut self) -> Result<()> {
        let Some(profile_name) = self.selected_profile_name() else {
            self.status = "select a profile first".into();
            self.log("no profile selected");
            return Ok(());
        };
        let Some(skill_name) = self.selected_skill_name() else {
            self.status = "select a skill first".into();
            self.log("no skill selected");
            return Ok(());
        };
        let prof = profiles::get(&self.root, &profile_name)?;
        let skill = skills::get(&self.root, &skill_name)?;
        let cookie_file = cookies::cookie_file_for_open(&self.root, &prof.name)?;
        let root = self.root.clone();
        let headed = self.headed;
        let prof_name = prof.name.clone();
        let skill_name_owned = skill.name.clone();
        let proxy = prof.proxy.clone();
        let user_data_dir = prof.user_data_dir.clone();
        let skill_path = skill.path.join("skill.json").to_string_lossy().to_string();
        let root_str = self.root.to_string_lossy().to_string();
        self.log(format!(
            "local skill={skill_name} profile={profile_name} headed={headed}"
        ));
        self.set_busy(BusyKind::SkillRun);
        self.status = format!("running {skill_name}@{profile_name}");
        self.pending = Some(tokio::spawn(async move {
            let outcome = async {
                let _lock = crate::locks::ProfileLock::acquire(
                    &root,
                    &prof_name,
                    Duration::from_secs(300),
                )
                .await?;
                worker::oneshot(
                    &root,
                    Request {
                        id: worker::next_id(),
                        cmd: "run_skill".into(),
                        profile: Some(prof_name.clone()),
                        url: None,
                        headed: Some(headed),
                        skill: Some(skill_name_owned.clone()),
                        vars: Some(serde_json::json!({})),
                        session: None,
                        proxy,
                        user_data_dir: Some(user_data_dir),
                        skill_path: Some(skill_path),
                        root: Some(root_str),
                        cookie_file,
                    },
                )
                .await
            }
            .await;
            let (log, status) = match outcome {
                Ok(r) if r.ok => (
                    format!("OK {}", r.data.unwrap_or_default()),
                    "ok".into(),
                ),
                Ok(r) => {
                    let err = r.error.unwrap_or_else(|| "failed".into());
                    (format!("FAIL {err}"), format!("fail: {err}"))
                }
                Err(e) => (format!("ERR {e}"), format!("err: {e}")),
            };
            let sessions = list_sessions_async(&root)
                .await
                .map_err(|e| e.to_string());
            PendingDone::SkillRun {
                log,
                status,
                sessions,
            }
        }));
        Ok(())
    }

    fn begin_teach(&mut self) -> Result<()> {
        let Some(profile_name) = self.selected_profile_name() else {
            self.status = "select a profile first".into();
            self.log("teach: select a profile first");
            return Ok(());
        };
        let root = self.root.clone();
        self.log(format!("teach start profile={profile_name} (headed)"));
        self.set_busy(BusyKind::TeachStart);
        self.status = format!("teach {profile_name} (headed)");
        self.pending = Some(tokio::spawn(async move {
            let outcome = crate::teach::start(
                &root,
                crate::teach::TeachStartOpts {
                    profile: profile_name.clone(),
                    url: None,
                    allow_secrets: false,
                    smart_optimize: true,
                },
            )
            .await;
            match outcome {
                Ok(()) => PendingDone::Teach {
                    log: format!("teach ended profile={profile_name}"),
                    status: "teach done".into(),
                },
                Err(e) => PendingDone::Teach {
                    log: format!("teach: {e}"),
                    status: format!("teach: {e}"),
                },
            }
        }));
        Ok(())
    }

    async fn open_browser(&mut self) -> Result<()> {
        let Some(profile_name) = self.selected_profile_name() else {
            self.status = "select a profile first".into();
            self.log("select a profile first");
            return Ok(());
        };
        let prof = profiles::get(&self.root, &profile_name)?;
        self.log(format!(
            "browser open profile={profile_name} headed={}",
            self.headed
        ));
        let resp = worker::daemon_request(
            &self.root,
            Request {
                id: worker::next_id(),
                cmd: "open".into(),
                profile: Some(prof.name.clone()),
                url: Some("about:blank".into()),
                headed: Some(self.headed),
                skill: None,
                vars: None,
                session: None,
                proxy: prof.proxy.clone(),
                user_data_dir: Some(prof.user_data_dir.clone()),
                skill_path: None,
                root: Some(self.root.to_string_lossy().to_string()),
                cookie_file: cookies::cookie_file_for_open(&self.root, &prof.name)?,
            },
        )
        .await?;
        if resp.ok {
            self.log(format!("opened {:?}", resp.data));
            self.status = format!("opened {profile_name}");
        } else {
            let err = resp.error.unwrap_or_default();
            self.log(format!("open fail: {err}"));
            self.status = format!("fail: {err}");
        }
        self.refresh_sessions().await;
        Ok(())
    }

    async fn close_selected_session(&mut self) -> Result<()> {
        let target = self
            .selected_session_id()
            .unwrap_or_else(|| "all".into());
        self.log(format!("browser close {target}"));
        let resp = worker::daemon_request(
            &self.root,
            Request {
                id: worker::next_id(),
                cmd: "close".into(),
                profile: None,
                url: None,
                headed: None,
                skill: None,
                vars: None,
                session: Some(target.clone()),
                proxy: None,
                user_data_dir: None,
                skill_path: None,
                root: None,
                cookie_file: None,
            },
        )
        .await?;
        self.log(format!("close {:?}", resp.data));
        self.status = format!("closed {target}");
        self.refresh_sessions().await;
        Ok(())
    }

    async fn job_to_client(&mut self) -> Result<()> {
        let Some(cid) = self.selected_client_id() else {
            self.status = "no client selected".into();
            self.log("no client selected / none online");
            return Ok(());
        };
        let Some(profile_name) = self.selected_profile_name() else {
            self.status = "select profile for remote job".into();
            self.log("select profile for remote job");
            return Ok(());
        };
        let Some(skill_name) = self.selected_skill_name() else {
            self.status = "select skill for remote job".into();
            self.log("select skill for remote job");
            return Ok(());
        };
        let job_id = Uuid::new_v4().simple().to_string();
        self.log(format!(
            "submit job {job_id} → client={cid} skill={skill_name} profile={profile_name}"
        ));
        master_hub::submit_job(
            &self.hub,
            &cid,
            &job_id,
            &skill_name,
            &profile_name,
            self.headed,
            serde_json::json!({}),
        )
        .await?;
        self.status = format!("job {job_id} submitted");
        Ok(())
    }

    fn start_new_profile(&mut self) {
        self.input_mode = Some(InputMode::NewProfile);
        self.input_buf.clear();
        self.status = "new profile — enter name".into();
    }

    fn start_edit_proxy(&mut self) {
        if self.selected_profile_name().is_none() {
            self.status = "select profile to edit proxy".into();
            self.log("select profile to edit proxy");
            return;
        }
        self.input_mode = Some(InputMode::EditProxy);
        self.input_buf.clear();
        self.status = "edit proxy — enter URL (empty=clear)".into();
    }

    fn start_cookie_import(&mut self) {
        if self.selected_profile_name().is_none() {
            self.status = "select profile to import cookies".into();
            self.log("select profile to import cookies");
            return;
        }
        self.input_mode = Some(InputMode::CookieImport);
        self.input_buf.clear();
        self.status = "cookie import — enter file path".into();
    }

    fn start_cookie_export(&mut self) {
        if self.selected_profile_name().is_none() {
            self.status = "select profile to export cookies".into();
            self.log("select profile to export cookies");
            return;
        }
        self.input_mode = Some(InputMode::CookieExport);
        self.input_buf.clear();
        self.status = "cookie export — enter output path".into();
    }

    fn clear_cookies_selected(&mut self) -> Result<()> {
        let Some(name) = self.selected_profile_name() else {
            self.status = "select profile to clear cookies".into();
            self.log("select profile to clear cookies");
            return Ok(());
        };
        let removed = cookies::clear(&self.root, &name)?;
        if removed {
            self.log(format!("cleared cookies for {name} (re-open session to apply)"));
            self.status = format!("cleared cookies {name}");
        } else {
            self.log(format!("no cookies for {name}"));
            self.status = format!("no cookies for {name}");
        }
        self.reload_static()?;
        Ok(())
    }

    fn start_llm_base_url(&mut self) {
        self.input_mode = Some(InputMode::LlmBaseUrl);
        self.input_buf = if self.llm_draft_base_url.is_empty() {
            llm::DEFAULT_BASE_URL.to_string()
        } else {
            self.llm_draft_base_url.clone()
        };
        self.status = "llm base_url — http(s) OpenAI-compatible /v1".into();
    }

    fn start_llm_api_key(&mut self) {
        self.input_mode = Some(InputMode::LlmApiKey);
        self.input_buf.clear();
        self.status = "llm API key (masked, session env only — not saved to disk)".into();
    }

    fn start_llm_timeout(&mut self) {
        self.input_mode = Some(InputMode::LlmTimeout);
        self.input_buf = self.llm.recover_timeout_sec.to_string();
        self.status = "llm recover_timeout_sec (default 90; 5..=3600; 300 advanced)".into();
    }

    fn current_llm_base(&self) -> String {
        if !self.llm_draft_base_url.is_empty() {
            self.llm_draft_base_url.clone()
        } else {
            self.llm.base_url.clone()
        }
    }

    fn clear_llm_models_list(&mut self) {
        invalidate_llm_picker(
            &mut self.llm_models,
            &mut self.llm_models_truncated,
            &mut self.llm_models_base_url,
        );
        self.llm_model_state.select(None);
    }

    fn begin_llm_fetch(&mut self) {
        let base = self.current_llm_base();
        if base.is_empty() {
            self.status = "llm: set base_url first (b)".into();
            return;
        }
        let Ok(base) = llm::normalize_base_url(&base) else {
            self.status = "llm: set a valid http(s) base_url first (b)".into();
            return;
        };
        let env_name = if self.llm.api_key_env.is_empty() {
            llm::DEFAULT_API_KEY_ENV.to_string()
        } else {
            self.llm.api_key_env.clone()
        };
        let Some(key) = llm::resolve_api_key_from_env(&env_name) else {
            self.status = format!("llm: session key missing — press K or export {env_name}");
            self.llm_fetch_err = Some(self.status.clone());
            return;
        };
        let saved_model = self.llm.model.clone();
        let requested_base = base.clone();
        // Drop ids before the request so mid-fetch Enter cannot save a stale model.
        self.clear_llm_models_list();
        self.llm_fetch_err = None;
        self.set_busy(BusyKind::LlmFetch);
        self.pending = Some(tokio::spawn(async move {
            let result = tokio::task::spawn_blocking(move || {
                crate::llm_client::fetch_models(&base, &key)
                    .map_err(|e| llm::redact_secrets(&e.to_string(), Some(&key)))
            })
            .await;
            PendingDone::LlmModels {
                result: match result {
                    Ok(Ok(list)) => Ok(list),
                    Ok(Err(e)) => Err(e),
                    Err(e) => Err(format!("fetch join: {e}")),
                },
                saved_model,
                requested_base,
            }
        }));
    }

    fn save_selected_llm_model(&mut self) -> Result<()> {
        let base = self.current_llm_base();
        if !llm_picker_can_save(&self.llm_models, &self.llm_models_base_url, &base) {
            self.status = "llm: fetch models (f) for this base_url before saving".into();
            return Ok(());
        }
        let Some(i) = self.llm_model_state.selected() else {
            self.status = "llm: fetch models (f) then select one".into();
            return Ok(());
        };
        let Some(model) = self.llm_models.get(i).cloned() else {
            self.status = "llm: fetch models (f) then select one".into();
            return Ok(());
        };
        let prev = self.llm.model.clone();
        let env_name = if self.llm.api_key_env.is_empty() {
            llm::DEFAULT_API_KEY_ENV.to_string()
        } else {
            self.llm.api_key_env.clone()
        };
        match llm::apply_set(
            &self.root,
            llm::LlmSetArgs {
                base_url: Some(base),
                model: Some(model.clone()),
                api_key_env: Some(env_name),
                enabled: if self.llm.configured {
                    None
                } else {
                    Some(true)
                },
                ..Default::default()
            },
        ) {
            Ok(_) => {
                self.llm = llm::view(&self.root);
                self.status = format!("llm model saved: {model}");
                self.log(format!("llm model saved: {model} (key not written)"));
            }
            Err(e) => {
                let e = llm::redact_secrets(&e.to_string(), None);
                self.llm = llm::view(&self.root);
                self.status = format!("llm save failed (model unchanged={prev}): {e}");
                self.log(format!("llm save failed: {e}"));
            }
        }
        Ok(())
    }

    fn commit_input(&mut self) -> Result<()> {
        let mode = self.input_mode.take();
        let buf = std::mem::take(&mut self.input_buf);
        match mode {
            Some(InputMode::NewProfile) => {
                let name = buf.trim();
                if name.is_empty() {
                    self.status = "cancelled".into();
                    return Ok(());
                }
                profiles::create(&self.root, name, None, None)?;
                self.log(format!("created profile {name}"));
                self.status = format!("created {name}");
                self.reload_static()?;
            }
            Some(InputMode::EditProxy) => {
                let Some(name) = self.selected_profile_name() else {
                    return Ok(());
                };
                let proxy = if buf.trim().is_empty() {
                    None
                } else {
                    Some(buf.trim().to_string())
                };
                profiles::update(&self.root, &name, Some(proxy.clone()), None)?;
                self.log(format!(
                    "updated {name} proxy={}",
                    proxy
                        .as_deref()
                        .map(redact_proxy)
                        .unwrap_or_else(|| "-".into())
                ));
                self.status = format!("proxy updated {name}");
                self.reload_static()?;
            }
            Some(InputMode::CookieImport) => {
                let Some(name) = self.selected_profile_name() else {
                    return Ok(());
                };
                let path = buf.trim();
                if path.is_empty() {
                    self.status = "cancelled".into();
                    return Ok(());
                }
                let st = cookies::import(
                    &self.root,
                    &name,
                    Path::new(path),
                    CookieFormat::Auto,
                )?;
                // status only — never log cookie values
                self.log(format!(
                    "imported cookies → {name} count={} domains={}",
                    st.cookie_count,
                    st.domains.len()
                ));
                self.status = format!("cookies imported {name} ({})", st.cookie_count);
                self.reload_static()?;
            }
            Some(InputMode::CookieExport) => {
                let Some(name) = self.selected_profile_name() else {
                    return Ok(());
                };
                let path = buf.trim();
                if path.is_empty() {
                    self.status = "export path required (use CLI for stdout)".into();
                    return Ok(());
                }
                let st = cookies::export(&self.root, &name, Some(Path::new(path)))?;
                self.log(format!(
                    "exported cookies {name} → {path} count={}",
                    st.cookie_count
                ));
                self.status = format!("cookies exported {name}");
            }
            Some(InputMode::LlmBaseUrl) => {
                let raw = buf.trim();
                if raw.is_empty() {
                    self.status = "cancelled".into();
                    return Ok(());
                }
                match llm::normalize_base_url(raw) {
                    Ok(u) => {
                        if !llm::models_bound_to_request_base(&self.llm_models_base_url, &u) {
                            self.clear_llm_models_list();
                        }
                        self.llm_draft_base_url = u.clone();
                        // Draft only until a successful refetch + Enter save for this base.
                        self.status = format!("llm base_url={u} (fetch models before save)");
                    }
                    Err(e) => {
                        self.status = format!("llm base_url: {e}");
                    }
                }
            }
            Some(InputMode::LlmApiKey) => {
                // Masked value is session env only. Never write to llm.json / logs.
                let key = buf;
                if key.is_empty() {
                    self.status = "cancelled".into();
                    return Ok(());
                }
                let env_name = if self.llm.api_key_env.is_empty() {
                    llm::DEFAULT_API_KEY_ENV.to_string()
                } else {
                    self.llm.api_key_env.clone()
                };
                std::env::set_var(&env_name, &key);
                drop(key);
                self.llm_session_key_set = true;
                if self.llm.configured {
                    let _ = llm::apply_set(
                        &self.root,
                        llm::LlmSetArgs {
                            api_key_env: Some(env_name.clone()),
                            ..Default::default()
                        },
                    );
                }
                self.llm = llm::view(&self.root);
                self.status = format!(
                    "session env {env_name} set (not saved to disk; restart worker for recover)"
                );
                self.log(format!(
                    "llm: session key set for {env_name} (not persisted)"
                ));
            }
            Some(InputMode::LlmTimeout) => {
                let raw = buf.trim();
                if raw.is_empty() {
                    self.status = "cancelled".into();
                    return Ok(());
                }
                match raw.parse::<u64>() {
                    Ok(t) if (5..=3600).contains(&t) => {
                        if self.llm.configured {
                            match llm::apply_set(
                                &self.root,
                                llm::LlmSetArgs {
                                    recover_timeout_sec: Some(t),
                                    ..Default::default()
                                },
                            ) {
                                Ok(_) => {
                                    self.llm = llm::view(&self.root);
                                    self.status = format!("llm recover_timeout_sec={t}");
                                    self.log(format!("llm recover_timeout_sec={t}"));
                                }
                                Err(e) => {
                                    self.status = format!("llm timeout: {e}");
                                }
                            }
                        } else {
                            self.status =
                                format!("timeout {t} remembered; save a model to persist");
                        }
                    }
                    _ => {
                        self.status = "llm recover_timeout_sec must be 5..=3600".into();
                    }
                }
            }
            None => {}
        }
        Ok(())
    }

    fn chat_send(&mut self) {
        let goal = self.chat.input.clone();
        if goal.trim().is_empty() {
            return;
        }
        if let Some(mock) = crate::teach_chat::mock_llm_from_env() {
            chat::dry_send(&mut self.chat, &mock.text, &[]);
            return;
        }
        match crate::teach_chat::plan_from_mock_or_llm(&self.root, &goal, None) {
            Ok(p) => {
                self.chat.input.clear();
                self.chat.push_user(&goal);
                crate::teach_chat::apply_plan(&mut self.chat, &p);
            }
            Err(e) => {
                let msg = crate::llm::redact_secrets(&e.to_string(), None);
                self.chat.push_system(&format!(
                    "{msg}  (or set CLOAKCLI_TEACH_CHAT_MOCK / cloakcli teach turn --mock-json)"
                ));
                self.chat.phase = crate::teach_protocol::TeachMachine::Chat;
            }
        }
    }
}

async fn list_sessions_async(root: &Path) -> Result<Vec<SessionRow>> {
    let st = worker::daemon_status(root);
    if !st.running {
        anyhow::bail!("local worker daemon not running — cloakcli worker serve");
    }
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
            root: None,
            cookie_file: None,
        },
    )
    .await?;
    if !resp.ok {
        anyhow::bail!(resp.error.unwrap_or_else(|| "list_sessions failed".into()));
    }
    let mut rows = Vec::new();
    if let Some(data) = resp.data {
        if let Some(arr) = data.get("sessions").and_then(|s| s.as_array()) {
            for s in arr {
                let id = s
                    .get("id")
                    .and_then(|v| v.as_str())
                    .unwrap_or("?")
                    .to_string();
                let profile = s
                    .get("profile")
                    .and_then(|v| v.as_str())
                    .unwrap_or("?")
                    .to_string();
                let url = s
                    .get("url")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                let headed = s.get("headed").and_then(|v| v.as_bool()).unwrap_or(false);
                rows.push(SessionRow {
                    id,
                    profile,
                    headed,
                    url,
                });
            }
        }
    }
    Ok(rows)
}

pub async fn run(root: &Path) -> Result<()> {
    let token = master_hub::master_token_default();
    let bind = master_hub::master_bind_default();
    let hub = master_hub::new_hub(root, &token);
    master_hub::write_master_meta(root, &bind)?;
    let hub_clone = hub.clone();
    let bind_clone = bind.clone();
    let ctrl = master_hub::control_sock_path(root);
    tokio::spawn(async move {
        if let Err(e) = master_hub::serve_with_control(&bind_clone, hub_clone, Some(ctrl)).await {
            eprintln!("master hub error: {e}");
        }
    });

    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;

    let mut app = App::new(root, hub, bind)?;
    app.start_boot();
    let result = event_loop(&mut terminal, &mut app).await;

    disable_raw_mode()?;
    execute!(terminal.backend_mut(), LeaveAlternateScreen)?;
    terminal.show_cursor()?;
    // Note: master hub + local worker daemon keep running after TUI exit
    app.log("TUI exit — daemon/hub left running (use worker stop / kill master if needed)");
    result
}

async fn event_loop(
    terminal: &mut Terminal<CrosstermBackend<io::Stdout>>,
    app: &mut App,
) -> Result<()> {
    loop {
        terminal.draw(|f| ui::draw(f, app))?;

        if app.pending.as_ref().is_some_and(|h| h.is_finished()) {
            if let Some(handle) = app.pending.take() {
                match handle.await {
                    Ok(done) => app.apply_pending(done),
                    Err(e) => {
                        if app.busy == Some(BusyKind::LlmFetch) {
                            app.clear_llm_models_list();
                        }
                        app.clear_busy();
                        app.log(format!("task join: {e}"));
                        app.status = format!("err: {e}");
                    }
                }
            }
        }

        // Auto-refresh sessions/clients ~every 2s while idle (no throbber)
        if app.busy.is_none()
            && app.input_mode.is_none()
            && app.last_refresh.elapsed() >= Duration::from_secs(2)
        {
            app.refresh_live().await;
        }

        if event::poll(Duration::from_millis(100))? {
            if let Event::Key(key) = event::read()? {
                if key.kind != KeyEventKind::Press {
                    continue;
                }

                if app.busy.is_some() {
                    match key.code {
                        KeyCode::Char('q') | KeyCode::Esc => break,
                        _ => {}
                    }
                    continue;
                }

                if app.tab == Tab::Chat && app.input_mode.is_none() {
                    match key.code {
                        KeyCode::Tab => {
                            if key.modifiers.contains(KeyModifiers::SHIFT) {
                                app.tab = app.tab.prev();
                            } else {
                                app.tab = app.tab.next();
                            }
                            continue;
                        }
                        KeyCode::BackTab => {
                            app.tab = app.tab.prev();
                            continue;
                        }
                        _ => {}
                    }
                    let cmd = chat::handle_key(&mut app.chat, key.code, key.modifiers);
                    if chat::apply_cmd_local(&mut app.chat, cmd) {
                        break;
                    }
                    if cmd == chat::ChatCmd::Send {
                        app.chat_send();
                    } else if cmd == chat::ChatCmd::ConfirmYes {
                        app.chat.status = "confirmed (dry-run — cloakcli teach chat executes)".into();
                        app.chat.confirm = None;
                        app.chat.phase = crate::teach_protocol::TeachMachine::Chat;
                    } else if cmd == chat::ChatCmd::ExportCommit
                        || cmd == chat::ChatCmd::ExportOverwrite
                    {
                        let overwrite = cmd == chat::ChatCmd::ExportOverwrite;
                        if let Some(name) = app.chat.export_overwrite_name.clone() {
                            if overwrite {
                                app.chat.input = name;
                            }
                        }
                        let mut name = app.chat.input.trim().to_string();
                        if name.is_empty() {
                            name = crate::teach_chat::default_export_name(&app.chat.last_goal);
                        }
                        let steps = app.chat.draft_steps.clone();
                        let goal = if app.chat.last_goal.is_empty() {
                            None
                        } else {
                            Some(app.chat.last_goal.as_str())
                        };
                        match crate::teach::export_chat_draft(
                            &app.root,
                            &name,
                            goal,
                            &steps,
                            overwrite,
                        ) {
                            Ok(r) => {
                                app.chat.export_prompt = false;
                                app.chat.export_overwrite_name = None;
                                app.chat.input.clear();
                                app.chat.phase = crate::teach_protocol::TeachMachine::Chat;
                                app.chat.status = format!("exported {}", r.path.display());
                                app.chat.push_system(&format!(
                                    "exported {} ({} steps)",
                                    r.path.display(),
                                    r.audit.n_steps
                                ));
                            }
                            Err(e) => {
                                let msg = e.to_string();
                                if msg.contains("already exists") && !overwrite {
                                    app.chat.export_overwrite_name = Some(name);
                                    app.chat.status =
                                        "skill exists — Y overwrite / N cancel".into();
                                } else {
                                    app.chat.export_prompt = false;
                                    app.chat.export_overwrite_name = None;
                                    app.chat.status = format!("export failed: {msg}");
                                }
                                app.chat.push_system(&msg);
                            }
                        }
                    }
                    app.status = app.chat.status.clone();
                    continue;
                }

                if app.input_mode.is_some() {
                    match key.code {
                        KeyCode::Esc => {
                            app.input_mode = None;
                            app.input_buf.clear();
                            app.status = "cancelled".into();
                        }
                        KeyCode::Enter => {
                            if let Err(e) = app.commit_input() {
                                app.log(format!("input error: {e}"));
                                app.status = format!("err: {e}");
                            }
                        }
                        KeyCode::Backspace => {
                            app.input_buf.pop();
                        }
                        KeyCode::Char(c) => {
                            app.input_buf.push(c);
                        }
                        _ => {}
                    }
                    continue;
                }

                match key.code {
                    KeyCode::Char('q') => break,
                    KeyCode::Esc => break,
                    KeyCode::Tab => {
                        if key.modifiers.contains(KeyModifiers::SHIFT) {
                            app.tab = app.tab.prev();
                        } else {
                            app.tab = app.tab.next();
                        }
                    }
                    KeyCode::BackTab => app.tab = app.tab.prev(),
                    KeyCode::Char('1') => app.tab = Tab::Profiles,
                    KeyCode::Char('2') => app.tab = Tab::Skills,
                    KeyCode::Char('3') => app.tab = Tab::Sessions,
                    KeyCode::Char('4') => app.tab = Tab::Clients,
                    KeyCode::Char('5') => app.tab = Tab::Config,
                    KeyCode::Char('6') => app.tab = Tab::Logs,
                    KeyCode::Char('7') => app.tab = Tab::Chat,
                    KeyCode::Char('r') => {
                        let _ = app.reload_static();
                        app.log("reloaded");
                        app.begin_sessions_load(BusyKind::WorkerRefresh, "reloaded");
                    }
                    KeyCode::Char('l') => {
                        if app.tab == Tab::Config {
                            match llm::toggle_enabled(&app.root) {
                                Ok(v) => {
                                    app.llm = v;
                                    app.log(format!("llm recover enabled={}", app.llm.enabled));
                                    app.status = format!("llm enabled={}", app.llm.enabled);
                                }
                                Err(e) => {
                                    app.log(format!("llm: {e}"));
                                    app.status = format!("llm: {e}");
                                }
                            }
                        }
                    }
                    KeyCode::Char('b') => {
                        if app.tab == Tab::Config {
                            app.start_llm_base_url();
                        }
                    }
                    KeyCode::Char('K') => {
                        if app.tab == Tab::Config {
                            app.start_llm_api_key();
                        }
                    }
                    KeyCode::Char('f') => {
                        if app.tab == Tab::Config {
                            app.begin_llm_fetch();
                        }
                    }
                    KeyCode::Char('t') => {
                        if app.tab == Tab::Config {
                            app.start_llm_timeout();
                        }
                    }
                    KeyCode::Char('h') | KeyCode::Char('H') => {
                        app.headed = !app.headed;
                        let _ = fleet::update_defaults(&app.root, None, Some(app.headed));
                        app.log(format!("headed={}", app.headed));
                        app.status = format!("headed={}", app.headed);
                    }
                    KeyCode::Char('c') => {
                        // bump concurrency (goals: c = concurrency)
                        app.concurrency += 1;
                        let _ = fleet::update_defaults(&app.root, Some(app.concurrency), None);
                        app.status = format!("concurrency={}", app.concurrency);
                        app.log(format!("concurrency={}", app.concurrency));
                    }
                    KeyCode::Char(']') => {
                        app.concurrency += 1;
                        let _ = fleet::update_defaults(&app.root, Some(app.concurrency), None);
                        app.status = format!("concurrency={}", app.concurrency);
                    }
                    KeyCode::Char('[') => {
                        app.concurrency = (app.concurrency.saturating_sub(1)).max(1);
                        let _ = fleet::update_defaults(&app.root, Some(app.concurrency), None);
                        app.status = format!("concurrency={}", app.concurrency);
                    }
                    KeyCode::Char('n') => app.start_new_profile(),
                    KeyCode::Char('e') => app.start_edit_proxy(),
                    KeyCode::Char('i') => {
                        if app.tab == Tab::Profiles {
                            app.start_cookie_import();
                        }
                    }
                    KeyCode::Char('E') => {
                        if app.tab == Tab::Profiles {
                            app.start_cookie_export();
                        }
                    }
                    KeyCode::Char('C') => {
                        if app.tab == Tab::Profiles {
                            if let Err(e) = app.clear_cookies_selected() {
                                app.log(format!("clear cookies error: {e}"));
                                app.status = format!("err: {e}");
                            }
                        }
                    }
                    KeyCode::Char('T') => {
                        if app.tab != Tab::Config {
                            if let Err(e) = app.begin_teach() {
                                app.log(format!("teach error: {e}"));
                                app.status = format!("err: {e}");
                            }
                        }
                    }
                    KeyCode::Char('o') => {
                        if let Err(e) = app.open_browser().await {
                            app.log(format!("open error: {e}"));
                            app.status = format!("err: {e}");
                        }
                    }
                    KeyCode::Char('x') => {
                        if let Err(e) = app.close_selected_session().await {
                            app.log(format!("close error: {e}"));
                            app.status = format!("err: {e}");
                        }
                    }
                    KeyCode::Char('J') => {
                        if let Err(e) = app.job_to_client().await {
                            app.log(format!("job error: {e}"));
                            app.status = format!("err: {e}");
                        }
                    }
                    KeyCode::Down | KeyCode::Char('j') => move_sel(app, 1),
                    KeyCode::Up | KeyCode::Char('k') => move_sel(app, -1),
                    KeyCode::Enter => {
                        if app.tab == Tab::Config {
                            if let Err(e) = app.save_selected_llm_model() {
                                app.log(format!("llm save: {e}"));
                                app.status = format!("err: {e}");
                            }
                        } else if let Err(e) = app.begin_skill_run() {
                            app.log(format!("run error: {e}"));
                            app.status = format!("err: {e}");
                        }
                    }
                    _ => {}
                }
            }
        }

        app.tick_animation();
    }
    Ok(())
}

/// Honor `NO_COLOR` (any value) and `CLOAKCLI_ANIMATIONS=0/false/off`.
fn detect_animations_enabled() -> bool {
    animations_enabled_from(
        std::env::var_os("NO_COLOR").is_some(),
        std::env::var("CLOAKCLI_ANIMATIONS").ok().as_deref(),
    )
}

fn animations_enabled_from(no_color: bool, cloakcli_animations: Option<&str>) -> bool {
    if no_color {
        return false;
    }
    match cloakcli_animations {
        Some(v) => {
            let v = v.trim().to_ascii_lowercase();
            !matches!(v.as_str(), "0" | "false" | "off" | "no" | "disable" | "disabled")
        }
        None => true,
    }
}

async fn wait_hub_ready(ctrl: PathBuf) -> bool {
    let deadline = Instant::now() + Duration::from_secs(3);
    while Instant::now() < deadline {
        if ctrl.exists() {
            return true;
        }
        tokio::time::sleep(Duration::from_millis(40)).await;
    }
    false
}

/// Enter may persist a picker id only after a successful fetch for this base.
fn llm_picker_can_save(ids: &[String], list_base: &str, current_base: &str) -> bool {
    !ids.is_empty() && llm::models_bound_to_request_base(list_base, current_base)
}

/// Drop in-memory picker ids (fetch start or failed refetch).
fn invalidate_llm_picker(ids: &mut Vec<String>, truncated: &mut bool, list_base: &mut String) {
    ids.clear();
    *truncated = false;
    list_base.clear();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn animations_disabled_when_no_color_set() {
        assert!(!animations_enabled_from(true, None));
        assert!(!animations_enabled_from(true, Some("1")));
    }

    #[test]
    fn animations_follow_cloakcli_animations_env() {
        assert!(animations_enabled_from(false, None));
        assert!(animations_enabled_from(false, Some("1")));
        assert!(animations_enabled_from(false, Some("on")));
        assert!(!animations_enabled_from(false, Some("0")));
        assert!(!animations_enabled_from(false, Some("false")));
        assert!(!animations_enabled_from(false, Some("OFF")));
        assert!(!animations_enabled_from(false, Some("no")));
    }

    #[test]
    fn busy_kind_labels_describe_the_wait() {
        assert_eq!(BusyKind::WorkerStart.label(), "starting worker");
        assert_eq!(BusyKind::WorkerRefresh.label(), "refreshing worker");
        assert_eq!(BusyKind::SkillRun.label(), "running skill");
        assert_eq!(BusyKind::HubConnect.label(), "connecting hub");
        assert_eq!(BusyKind::SessionsLoad.label(), "loading sessions");
        assert_eq!(BusyKind::LlmFetch.label(), "fetching models");
        assert_eq!(BusyKind::TeachStart.label(), "teaching (headed)");
    }

    #[test]
    fn changing_base_unbinds_stale_model_list() {
        let old = "https://api.example.com/v1";
        let new = "https://other.example.com/v1";
        let ids = vec!["stale-model".to_string()];
        assert!(!llm_picker_can_save(&ids, old, new));
        assert!(llm_picker_can_save(&ids, old, old));
        assert!(!llm_picker_can_save(&ids, "", new));
    }

    #[test]
    fn same_base_refetch_start_and_failure_invalidate_picker() {
        let base = "https://api.example.com/v1";
        let mut ids = vec!["stale-model".to_string()];
        let mut truncated = true;
        let mut list_base = base.to_string();
        assert!(llm_picker_can_save(&ids, &list_base, base));

        // Start of fetch (same base_url): list must not remain saveable.
        invalidate_llm_picker(&mut ids, &mut truncated, &mut list_base);
        assert!(ids.is_empty());
        assert!(!truncated);
        assert!(list_base.is_empty());
        assert!(!llm_picker_can_save(&ids, &list_base, base));

        // Simulate a previous successful list still sitting in memory, then fail.
        ids = vec!["stale-model".to_string()];
        truncated = false;
        list_base = base.to_string();
        assert!(llm_picker_can_save(&ids, &list_base, base));
        invalidate_llm_picker(&mut ids, &mut truncated, &mut list_base);
        assert!(!llm_picker_can_save(&ids, &list_base, base));
    }
}

fn move_sel(app: &mut App, delta: i32) {
    let (len, state) = match app.tab {
        Tab::Profiles => (app.profile_rows.len(), &mut app.profile_state),
        Tab::Skills => (app.skill_rows.len(), &mut app.skill_state),
        Tab::Sessions => (app.session_rows.len(), &mut app.session_state),
        Tab::Clients => (app.client_rows.len(), &mut app.client_state),
        Tab::Config => (app.llm_models.len(), &mut app.llm_model_state),
        _ => return,
    };
    if len == 0 {
        return;
    }
    let cur = state.selected().unwrap_or(0) as i32;
    let next = (cur + delta).rem_euclid(len as i32) as usize;
    state.select(Some(next));
}
