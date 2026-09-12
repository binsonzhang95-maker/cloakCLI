//! Master TUI — control plane for local profiles/skills and remote clients.
//! Local multi-browser sessions are shown from the node daemon; fleet clients
//! register via outbound connections to the embedded master hub.

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
use uuid::Uuid;

use crate::cookies::{self, CookieFormat};
use crate::fleet;
use crate::master_hub::{self, ClientInfo, SharedHub};
use crate::profiles::{self, Profile};
use crate::skills::{self, Skill};
use crate::state;
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
}

impl Tab {
    fn next(self) -> Self {
        match self {
            Tab::Profiles => Tab::Skills,
            Tab::Skills => Tab::Sessions,
            Tab::Sessions => Tab::Clients,
            Tab::Clients => Tab::Config,
            Tab::Config => Tab::Logs,
            Tab::Logs => Tab::Profiles,
        }
    }
    fn prev(self) -> Self {
        match self {
            Tab::Profiles => Tab::Logs,
            Tab::Skills => Tab::Profiles,
            Tab::Sessions => Tab::Skills,
            Tab::Clients => Tab::Sessions,
            Tab::Config => Tab::Clients,
            Tab::Logs => Tab::Config,
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
        }
    }
    fn all() -> [Tab; 6] {
        [
            Tab::Profiles,
            Tab::Skills,
            Tab::Sessions,
            Tab::Clients,
            Tab::Config,
            Tab::Logs,
        ]
    }
}

#[derive(Clone, Debug)]
enum InputMode {
    NewProfile,
    EditProxy,
    CookieImport,
    CookieExport,
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
    hub: SharedHub,
    hub_bind: String,
    hub_bind_ok: bool,
    desired_rev: u64,
    input_mode: Option<InputMode>,
    input_buf: String,
    last_refresh: Instant,
    logged_invalid_skills: bool,
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
            hub,
            hub_bind,
            hub_bind_ok: true,
            desired_rev: 1,
            input_mode: None,
            input_buf: String::new(),
            last_refresh: Instant::now(),
            logged_invalid_skills: false,
        };
        app.reload_static()?;
        Ok(app)
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

    async fn run_selected_local(&mut self) -> Result<()> {
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
        self.status = format!("local run {skill_name}@{profile_name}…");
        self.log(format!(
            "local skill={skill_name} profile={profile_name} headed={}",
            self.headed
        ));
        let prof = profiles::get(&self.root, &profile_name)?;
        let skill = skills::get(&self.root, &skill_name)?;
        let _lock =
            crate::locks::ProfileLock::acquire(&self.root, &prof.name, Duration::from_secs(300))
                .await?;
        let resp = worker::oneshot(
            &self.root,
            Request {
                id: worker::next_id(),
                cmd: "run_skill".into(),
                profile: Some(prof.name.clone()),
                url: None,
                headed: Some(self.headed),
                skill: Some(skill.name.clone()),
                vars: Some(serde_json::json!({})),
                session: None,
                proxy: prof.proxy.clone(),
                user_data_dir: Some(prof.user_data_dir.clone()),
                skill_path: Some(skill.path.join("skill.json").to_string_lossy().to_string()),
                root: Some(self.root.to_string_lossy().to_string()),
                cookie_file: cookies::cookie_file_for_open(&self.root, &prof.name)?,
            },
        )
        .await;
        match resp {
            Ok(r) if r.ok => {
                self.log(format!("OK {}", r.data.unwrap_or_default()));
                self.status = "ok".into();
            }
            Ok(r) => {
                let err = r.error.unwrap_or_else(|| "failed".into());
                self.log(format!("FAIL {err}"));
                self.status = format!("fail: {err}");
            }
            Err(e) => {
                self.log(format!("ERR {e}"));
                self.status = format!("err: {e}");
            }
        }
        self.refresh_sessions().await;
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

    fn commit_input(&mut self) -> Result<()> {
        let mode = self.input_mode.take();
        let buf = self.input_buf.clone();
        self.input_buf.clear();
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
            None => {}
        }
        Ok(())
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

    let _ = worker::ensure_daemon(root).await;

    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;

    let mut app = App::new(root, hub, bind)?;
    app.refresh_sessions().await;
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

        // Auto-refresh sessions/clients ~every 2s while idle
        if app.input_mode.is_none() && app.last_refresh.elapsed() >= Duration::from_secs(2) {
            app.refresh_live().await;
        }

        if event::poll(Duration::from_millis(100))? {
            if let Event::Key(key) = event::read()? {
                if key.kind != KeyEventKind::Press {
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
                    KeyCode::Char('r') => {
                        let _ = app.reload_static();
                        app.refresh_sessions().await;
                        app.log("reloaded");
                        app.status = "reloaded".into();
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
                        if let Err(e) = app.run_selected_local().await {
                            app.log(format!("run error: {e}"));
                            app.status = format!("err: {e}");
                        }
                    }
                    _ => {}
                }
            }
        }
    }
    Ok(())
}

fn move_sel(app: &mut App, delta: i32) {
    let (len, state) = match app.tab {
        Tab::Profiles => (app.profile_rows.len(), &mut app.profile_state),
        Tab::Skills => (app.skill_rows.len(), &mut app.skill_state),
        Tab::Sessions => (app.session_rows.len(), &mut app.session_state),
        Tab::Clients => (app.client_rows.len(), &mut app.client_state),
        _ => return,
    };
    if len == 0 {
        return;
    }
    let cur = state.selected().unwrap_or(0) as i32;
    let next = (cur + delta).rem_euclid(len as i32) as usize;
    state.select(Some(next));
}
