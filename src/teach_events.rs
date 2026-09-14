//! JSONL Teach Chat protocol for the desktop adapter.
//!
//! `cloakcli teach chat --events` speaks this instead of the ratatui UI.
//! stdin: one JSON object per line (`cmd` = send|cancel|confirm|status|stop).
//! stdout: one JSON object per line (typed `kind`, schema `v=1`).
//! All free-text is redacted before write. Playwright / LLM stay in this process.
//!
//! Incremental assistant text is emitted as `assistant_delta` chunks *during*
//! generation; a final `assistant` event (`done: true`) follows. Cancel is
//! honored between chunks (and during live SSE).
//!
//! Reconnect: a snapshot of messages + request/profile context is persisted
//! under `data/teach/events-snapshot.json`. A new child always binds a **new**
//! teach-hub port (pairing codes are not reused). `kind=resume` restores the
//! transcript so the UI can continue.

use anyhow::Result;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::io::{self, BufRead, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::Mutex;
use uuid::Uuid;

use crate::teach_chat::{
    apply_plan, build_messages_ctx, execute_planned, live_llm_from_root, mock_llm_from_env,
    plan_turn, ChatLine, ChatSession, MockLlm, PlannedTurn, TeachLlm, ToolLine, TurnContext,
};
use crate::teach_hub::TeachHubHandle;
use crate::teach_protocol::{redact_for_log, TeachMachine};

pub const EVENT_SCHEMA_V: u32 = 1;

/// Honest limit: a new `cloakcli teach chat --events` process always starts a
/// new hub on an ephemeral loopback port. The previous worker/extension pair
/// is not reused. Transcript + profile context are restored.
pub const HUB_RESUME_NOTE: &str = "Teach hub binds a new ephemeral port each spawn; pairing codes are not reused. Chat transcript, last request id, and profile context are restored so you can continue. Re-pair the headed worker if browser actions are needed.";

#[derive(Debug, Clone)]
pub struct EventsOpts {
    pub profile: String,
    pub mock_json: Option<String>,
    pub spawn_browser: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct WireEvent {
    pub v: u32,
    #[serde(flatten)]
    pub body: EventKind,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum EventKind {
    Session {
        session_id: String,
        pairing_id: String,
        pairing_code: String,
        hub_url: String,
        profile: String,
        spawn_browser: bool,
    },
    Status {
        phase: String,
        status: String,
        hub: bool,
        worker: bool,
        extension: bool,
        #[serde(default)]
        page_url: String,
        #[serde(default)]
        page_origin: String,
        #[serde(default)]
        page_title: String,
        busy: bool,
        mode: String,
        profile: String,
        #[serde(default)]
        tools: Vec<ToolStatusDto>,
        #[serde(default)]
        last_request_id: Option<String>,
    },
    User {
        role: String,
        text: String,
    },
    AssistantDelta {
        role: String,
        text: String,
        seq: u32,
        done: bool,
    },
    Assistant {
        role: String,
        text: String,
        #[serde(default = "default_true")]
        done: bool,
    },
    System {
        role: String,
        text: String,
    },
    Tool {
        tools: Vec<ToolStatusDto>,
    },
    Job {
        job_id: String,
        state: String,
        summary: String,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        error: Option<String>,
        #[serde(default, skip_serializing_if = "Option::is_none")]
        ok: Option<bool>,
    },
    Error {
        code: String,
        message: String,
    },
    Closed {
        reason: String,
        profile: String,
    },
    Resume {
        profile: String,
        messages: Vec<ChatLine>,
        #[serde(default)]
        tools: Vec<ToolStatusDto>,
        phase: String,
        status: String,
        #[serde(default)]
        last_request_id: Option<String>,
        #[serde(default)]
        page_url: String,
        #[serde(default)]
        page_origin: String,
        #[serde(default)]
        page_title: String,
        hub_resume: String,
        note: String,
    },
}

fn default_true() -> bool {
    true
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ToolStatusDto {
    pub summary: String,
    pub status: String,
}

impl From<&ToolLine> for ToolStatusDto {
    fn from(t: &ToolLine) -> Self {
        Self {
            summary: t.summary.clone(),
            status: t.status.clone(),
        }
    }
}

#[allow(dead_code)]
const EVENT_KINDS: &[&str] = &[
    "session",
    "status",
    "user",
    "assistant_delta",
    "assistant",
    "system",
    "tool",
    "job",
    "error",
    "closed",
    "resume",
];

#[allow(dead_code)]
const JOB_STATES: &[&str] = &["running", "done", "failed", "cancelled", "needs_confirm"];

#[derive(Debug, Deserialize)]
#[serde(tag = "cmd", rename_all = "snake_case", deny_unknown_fields)]
enum InCmd {
    Send {
        #[serde(default)]
        goal: Option<String>,
        #[serde(default)]
        profile: Option<String>,
        #[serde(default)]
        skill: Option<String>,
    },
    Cancel,
    Confirm {
        #[serde(default)]
        yes: Option<bool>,
    },
    Status,
    Stop,
    Quit,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct SessionSnapshot {
    v: u32,
    profile: String,
    messages: Vec<ChatLine>,
    #[serde(default)]
    tools: Vec<ToolLine>,
    #[serde(default)]
    phase: String,
    #[serde(default)]
    status: String,
    #[serde(default)]
    last_request_id: Option<String>,
    #[serde(default)]
    page_url: String,
    #[serde(default)]
    page_origin: String,
    #[serde(default)]
    page_title: String,
}

struct Shared {
    session: Mutex<ChatSession>,
    pending: Mutex<Option<PlannedTurn>>,
    busy: AtomicBool,
    profile: String,
    root: PathBuf,
}

pub fn snapshot_path(root: &Path) -> PathBuf {
    crate::state::data_dir(root)
        .join("teach")
        .join("events-snapshot.json")
}

/// Parse a JSONL event. Unknown `kind` / schema version / malformed JSON fail.
#[allow(dead_code)]
pub fn parse_wire_event(raw: &str) -> Result<WireEvent, String> {
    let v: Value = serde_json::from_str(raw).map_err(|e| format!("malformed JSON: {e}"))?;
    let obj = v
        .as_object()
        .ok_or_else(|| "event must be a JSON object".to_string())?;
    let kind = obj
        .get("kind")
        .and_then(|k| k.as_str())
        .unwrap_or("")
        .to_string();
    if kind.is_empty() {
        return Err("event missing kind".into());
    }
    if !EVENT_KINDS.contains(&kind.as_str()) {
        return Err(format!("unknown event kind: {kind}"));
    }
    let ver = obj.get("v").and_then(|x| x.as_u64()).unwrap_or(EVENT_SCHEMA_V as u64);
    if ver != EVENT_SCHEMA_V as u64 {
        return Err(format!("unsupported event schema version {ver}"));
    }
    if kind == "job" {
        if let Some(state) = obj.get("state").and_then(|s| s.as_str()) {
            if !JOB_STATES.contains(&state) {
                return Err(format!("unknown job state: {state}"));
            }
        }
    }
    serde_json::from_value(v).map_err(|e| format!("malformed {kind} event: {e}"))
}

pub fn parse_cmd_json(raw: &str) -> Result<String, String> {
    let cmd: InCmd = serde_json::from_str(raw).map_err(|e| e.to_string())?;
    Ok(match cmd {
        InCmd::Send { .. } => "send".into(),
        InCmd::Cancel => "cancel".into(),
        InCmd::Confirm { .. } => "confirm".into(),
        InCmd::Status => "status".into(),
        InCmd::Stop => "stop".into(),
        InCmd::Quit => "quit".into(),
    })
}

pub async fn run(root: &Path, hub: TeachHubHandle, opts: EventsOpts) -> Result<()> {
    let hub = Arc::new(hub);
    let mut session = ChatSession::default();
    session.hub_connected = true;
    session.push_system(
        "Teach Chat events — send a JSONL goal. LLM / Playwright stay in cloakcli; the UI only displays.",
    );

    let mut restored: Option<SessionSnapshot> = None;
    if let Some(snap) = load_snapshot(root) {
        if snap.profile == opts.profile && !snap.messages.is_empty() {
            session.messages = snap.messages.clone();
            session.tools = snap.tools.clone();
            session.page_url = snap.page_url.clone();
            session.page_origin = snap.page_origin.clone();
            session.page_title = snap.page_title.clone();
            session.last_request_id = snap.last_request_id.clone();
            session.phase = TeachMachine::Chat;
            session.status = format!("restored (new hub); previous: {}", snap.status);
            if let Some(ChatLine { role, text }) = session
                .messages
                .iter()
                .rev()
                .find(|m| m.role == "user")
            {
                if role == "user" {
                    session.last_goal = text.clone();
                }
            }
            restored = Some(snap);
        }
    }

    let shared = Arc::new(Shared {
        session: Mutex::new(session),
        pending: Mutex::new(None),
        busy: AtomicBool::new(false),
        profile: opts.profile.clone(),
        root: root.to_path_buf(),
    });

    emit_kind(EventKind::Session {
        session_id: hub.session_id().to_string(),
        pairing_id: hub.pairing_id().to_string(),
        pairing_code: hub.pairing_code().to_string(),
        hub_url: format!("ws://127.0.0.1:{}", hub.port()),
        profile: opts.profile.clone(),
        spawn_browser: opts.spawn_browser,
    });

    if let Some(snap) = restored {
        let tools: Vec<ToolStatusDto> = snap.tools.iter().map(ToolStatusDto::from).collect();
        emit_kind(EventKind::Resume {
            profile: snap.profile,
            messages: snap.messages,
            tools,
            phase: "chat".into(),
            status: shared.session.lock().await.status.clone(),
            last_request_id: snap.last_request_id,
            page_url: snap.page_url,
            page_origin: snap.page_origin,
            page_title: snap.page_title,
            hub_resume: "new_hub".into(),
            note: HUB_RESUME_NOTE.into(),
        });
    }

    emit_status(&hub, &shared).await;

    let mock = opts
        .mock_json
        .clone()
        .map(|t| MockLlm { text: t })
        .or_else(mock_llm_from_env);
    let live = if mock.is_none() {
        live_llm_from_root(root).ok()
    } else {
        None
    };
    let mock = Arc::new(mock);
    let live = Arc::new(live);

    let (line_tx, mut line_rx) = tokio::sync::mpsc::unbounded_channel::<String>();
    std::thread::spawn(move || {
        let reader = io::BufReader::new(io::stdin());
        for line in reader.lines() {
            match line {
                Ok(s) => {
                    if line_tx.send(s).is_err() {
                        break;
                    }
                }
                Err(_) => break,
            }
        }
    });
    let mut interval = tokio::time::interval(Duration::from_millis(400));
    interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    let mut inflight: Option<tokio::task::JoinHandle<()>> = None;

    loop {
        tokio::select! {
            _ = interval.tick() => {
                if inflight.as_ref().is_some_and(|h| h.is_finished()) {
                    if let Some(h) = inflight.take() {
                        let _ = h.await;
                    }
                    shared.busy.store(false, Ordering::SeqCst);
                }
                emit_status(&hub, &shared).await;
            }
            line = line_rx.recv() => {
                match line {
                    Some(raw) => {
                        let raw = raw.trim();
                        if raw.is_empty() {
                            continue;
                        }
                        let parsed: InCmd = match serde_json::from_str(raw) {
                            Ok(v) => v,
                            Err(e) => {
                                emit_kind(EventKind::Error {
                                    code: "bad_cmd".into(),
                                    message: format!("invalid command JSON: {e}"),
                                });
                                continue;
                            }
                        };
                        match parsed {
                            InCmd::Stop | InCmd::Quit => {
                                cancel_inflight(&hub, &shared).await;
                                if let Some(h) = inflight.take() {
                                    let _ = tokio::time::timeout(Duration::from_secs(2), h).await;
                                }
                                persist(&shared).await;
                                break;
                            }
                            InCmd::Status => emit_status(&hub, &shared).await,
                            InCmd::Cancel => {
                                cancel_inflight(&hub, &shared).await;
                                persist(&shared).await;
                            }
                            InCmd::Confirm { yes } => {
                                let yes = yes.unwrap_or(false);
                                if inflight.is_some() || shared.busy.load(Ordering::SeqCst) {
                                    emit_kind(EventKind::Error {
                                        code: "busy".into(),
                                        message: "a turn is already running".into(),
                                    });
                                    continue;
                                }
                                shared.busy.store(true, Ordering::SeqCst);
                                let hub2 = hub.clone();
                                let shared2 = shared.clone();
                                inflight = Some(tokio::spawn(async move {
                                    handle_confirm(hub2, shared2, yes).await;
                                }));
                            }
                            InCmd::Send { goal, profile, skill } => {
                                let goal = goal.unwrap_or_default();
                                if goal.trim().is_empty() {
                                    emit_kind(EventKind::Error {
                                        code: "bad_cmd".into(),
                                        message: "send requires goal".into(),
                                    });
                                    continue;
                                }
                                if let Some(p) = profile.as_deref() {
                                    if !p.is_empty() && p != shared.profile {
                                        emit_kind(EventKind::Error {
                                            code: "profile_mismatch".into(),
                                            message: format!(
                                                "session profile is {}; restart Teach Chat to use {p}",
                                                shared.profile
                                            ),
                                        });
                                        continue;
                                    }
                                }
                                if inflight.is_some() || shared.busy.load(Ordering::SeqCst) {
                                    emit_kind(EventKind::Error {
                                        code: "busy".into(),
                                        message: "a turn is already running; cancel first".into(),
                                    });
                                    continue;
                                }
                                shared.busy.store(true, Ordering::SeqCst);
                                let hub2 = hub.clone();
                                let shared2 = shared.clone();
                                let mock2 = mock.clone();
                                let live2 = live.clone();
                                let skill = skill.filter(|s| !s.is_empty());
                                inflight = Some(tokio::spawn(async move {
                                    handle_send(hub2, shared2, mock2, live2, goal, skill).await;
                                }));
                            }
                        }
                    }
                    None => break,
                }
            }
        }
    }

    persist(&shared).await;
    emit_kind(EventKind::Closed {
        reason: "stop".into(),
        profile: opts.profile,
    });
    hub.abort();
    Ok(())
}

async fn persist(shared: &Shared) {
    let s = shared.session.lock().await;
    persist_snapshot(&shared.root, &shared.profile, &s);
}

fn persist_snapshot(root: &Path, profile: &str, session: &ChatSession) {
    let snap = SessionSnapshot {
        v: EVENT_SCHEMA_V,
        profile: profile.to_string(),
        messages: session.messages.clone(),
        tools: session.tools.clone(),
        phase: session.phase.as_str().to_string(),
        status: session.status.clone(),
        last_request_id: session.last_request_id.clone(),
        page_url: session.page_url.clone(),
        page_origin: session.page_origin.clone(),
        page_title: session.page_title.clone(),
    };
    let path = snapshot_path(root);
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let tmp = path.with_extension("json.tmp");
    if let Ok(body) = serde_json::to_string_pretty(&snap) {
        if std::fs::write(&tmp, body).is_ok() {
            let _ = std::fs::rename(&tmp, &path);
        }
    }
}

fn load_snapshot(root: &Path) -> Option<SessionSnapshot> {
    let path = snapshot_path(root);
    let raw = std::fs::read_to_string(path).ok()?;
    let snap: SessionSnapshot = serde_json::from_str(&raw).ok()?;
    if snap.v != EVENT_SCHEMA_V {
        return None;
    }
    Some(snap)
}

async fn cancel_inflight(hub: &TeachHubHandle, shared: &Shared) {
    let id = {
        let mut s = shared.session.lock().await;
        s.cancel.store(true, Ordering::SeqCst);
        s.confirm = None;
        if s.phase == TeachMachine::AgentActing || s.phase == TeachMachine::AwaitingConfirm {
            s.phase = TeachMachine::Cancel;
            s.status = "cancelled".into();
            s.push_system("cancelled in-flight action");
            emit_session_messages(&s);
        }
        s.last_request_id.clone()
    };
    if let Some(ref rid) = id {
        let _ = hub.cancel_request(Some(rid)).await;
    }
    *shared.pending.lock().await = None;
    emit_kind(EventKind::Job {
        job_id: id.unwrap_or_else(|| "none".into()),
        state: "cancelled".into(),
        summary: "cancelled".into(),
        error: None,
        ok: None,
    });
}

async fn emit_status(hub: &TeachHubHandle, shared: &Shared) {
    let (ext, wrk) = hub.connection_status().await;
    let page = hub.last_page_brief().await;
    let mut s = shared.session.lock().await;
    s.extension_connected = ext;
    s.worker_connected = wrk;
    s.hub_connected = true;
    if let Some((url, origin, title)) = page {
        s.page_url = url;
        s.page_origin = origin;
        s.page_title = title;
    }
    let tools: Vec<ToolStatusDto> = s.tools.iter().map(ToolStatusDto::from).collect();
    emit_kind(EventKind::Status {
        phase: s.phase.as_str().to_string(),
        status: s.status.clone(),
        hub: s.hub_connected,
        worker: s.worker_connected,
        extension: s.extension_connected,
        page_url: s.page_url.clone(),
        page_origin: s.page_origin.clone(),
        page_title: s.page_title.clone(),
        busy: shared.busy.load(Ordering::SeqCst),
        mode: s.mode.clone(),
        profile: shared.profile.clone(),
        tools,
        last_request_id: s.last_request_id.clone(),
    });
}

fn emit_session_messages(session: &ChatSession) {
    if let Some(m) = session.messages.last() {
        let role = m.role.clone();
        let text = m.text.clone();
        match role.as_str() {
            "user" => emit_kind(EventKind::User { role, text }),
            "assistant" => emit_kind(EventKind::Assistant {
                role,
                text,
                done: true,
            }),
            _ => emit_kind(EventKind::System { role, text }),
        }
    }
    if !session.tools.is_empty() {
        let tools: Vec<ToolStatusDto> = session.tools.iter().map(ToolStatusDto::from).collect();
        emit_kind(EventKind::Tool { tools });
    }
}

fn tools_dto(session: &ChatSession) -> Vec<ToolStatusDto> {
    session.tools.iter().map(ToolStatusDto::from).collect()
}

fn stream_chunk_delay() -> Duration {
    let ms = std::env::var("CLOAKCLI_TEACH_STREAM_CHUNK_MS")
        .ok()
        .and_then(|s| s.parse::<u64>().ok())
        .unwrap_or(8);
    Duration::from_millis(ms.min(250))
}

fn stream_chunk_chars() -> usize {
    std::env::var("CLOAKCLI_TEACH_STREAM_CHUNK_CHARS")
        .ok()
        .and_then(|s| s.parse::<usize>().ok())
        .unwrap_or(8)
        .clamp(1, 64)
}

async fn emit_text_deltas(text: &str, cancel: &AtomicBool) -> Result<(), ()> {
    let chars: Vec<char> = text.chars().collect();
    let n = stream_chunk_chars();
    let delay = stream_chunk_delay();
    let mut seq = 0u32;
    for chunk in chars.chunks(n) {
        if cancel.load(Ordering::SeqCst) {
            return Err(());
        }
        let s: String = chunk.iter().collect();
        emit_kind(EventKind::AssistantDelta {
            role: "assistant".into(),
            text: redact_for_log(&s),
            seq,
            done: false,
        });
        seq = seq.saturating_add(1);
        if !delay.is_zero() {
            tokio::time::sleep(delay).await;
        }
    }
    if cancel.load(Ordering::SeqCst) {
        return Err(());
    }
    Ok(())
}

async fn handle_send(
    hub: Arc<TeachHubHandle>,
    shared: Arc<Shared>,
    mock: Arc<Option<MockLlm>>,
    live: Arc<Option<crate::teach_chat::LiveLlm>>,
    goal: String,
    skill: Option<String>,
) {
    let cancel = {
        let mut s = shared.session.lock().await;
        s.cancel.store(false, Ordering::SeqCst);
        s.push_user(&goal);
        s.phase = TeachMachine::AgentActing;
        s.status = "planning".into();
        emit_kind(EventKind::User {
            role: "user".into(),
            text: s
                .messages
                .last()
                .map(|m| m.text.clone())
                .unwrap_or_default(),
        });
        emit_kind(EventKind::Job {
            job_id: "pending".into(),
            state: "running".into(),
            summary: "planning".into(),
            error: None,
            ok: None,
        });
        persist_snapshot(&shared.root, &shared.profile, &s);
        s.cancel.clone()
    };

    let allow = hub.allow_origins().await;
    let (page_origin, page, human) = {
        let s = shared.session.lock().await;
        let page = if s.page_url.is_empty() {
            None
        } else {
            Some(crate::teach_chat::PageBrief {
                url: s.page_url.clone(),
                origin: s.page_origin.clone(),
                title: s.page_title.clone(),
            })
        };
        let human = if s.last_human_summary.is_empty() {
            None
        } else {
            Some(s.last_human_summary.clone())
        };
        (s.page_origin.clone(), page, human)
    };

    let llm_text = if let Some(m) = mock.as_ref() {
        if emit_text_deltas(&m.text, &cancel).await.is_err() {
            emit_cancelled_planning(&shared).await;
            return;
        }
        m.text.clone()
    } else if let Some(llm) = live.as_ref() {
        let ctx = TurnContext {
            human_summary: human.as_deref(),
            profile: Some(shared.profile.as_str()),
            skill: skill.as_deref(),
        };
        let messages = build_messages_ctx(&goal, page.as_ref(), &allow, ctx);
        match stream_live_llm(llm, messages, &cancel).await {
            Ok(t) => t,
            Err(LiveStreamErr::Cancelled) => {
                emit_cancelled_planning(&shared).await;
                return;
            }
            Err(LiveStreamErr::Other(e)) => {
                let mut s = shared.session.lock().await;
                s.phase = TeachMachine::Error;
                s.status = e.clone();
                s.push_system(&format!("llm: {e}"));
                emit_kind(EventKind::Error {
                    code: "llm".into(),
                    message: e.clone(),
                });
                emit_kind(EventKind::Job {
                    job_id: "pending".into(),
                    state: "failed".into(),
                    summary: "llm failed".into(),
                    error: Some(e),
                    ok: None,
                });
                persist_snapshot(&shared.root, &shared.profile, &s);
                return;
            }
        }
    } else {
        let mut s = shared.session.lock().await;
        s.phase = TeachMachine::Chat;
        s.status = "no LLM".into();
        s.push_system("no LLM configured and no --mock-json / CLOAKCLI_TEACH_CHAT_MOCK");
        emit_kind(EventKind::Error {
            code: "no_llm".into(),
            message: "no LLM configured and no mock JSON".into(),
        });
        emit_kind(EventKind::Job {
            job_id: "pending".into(),
            state: "failed".into(),
            summary: "no LLM".into(),
            error: Some("no LLM configured".into()),
            ok: None,
        });
        persist_snapshot(&shared.root, &shared.profile, &s);
        return;
    };

    if cancel.load(Ordering::SeqCst) {
        emit_cancelled_planning(&shared).await;
        return;
    }

    let current = if page_origin.is_empty() {
        None
    } else {
        Some(page_origin.as_str())
    };
    let planned = plan_turn(&llm_text, &allow, current);

    if cancel.load(Ordering::SeqCst) {
        emit_cancelled_planning(&shared).await;
        return;
    }

    {
        let mut s = shared.session.lock().await;
        apply_plan(&mut s, &planned);
        let assistant_text = s
            .messages
            .iter()
            .rev()
            .find(|m| m.role == "assistant")
            .map(|m| m.text.clone())
            .unwrap_or_else(|| planned.assistant_text.clone());
        emit_kind(EventKind::Assistant {
            role: "assistant".into(),
            text: assistant_text,
            done: true,
        });
        emit_kind(EventKind::Tool {
            tools: tools_dto(&s),
        });
        persist_snapshot(&shared.root, &shared.profile, &s);
    }

    if planned.needs_confirm.is_some() {
        *shared.pending.lock().await = Some(planned);
        emit_kind(EventKind::Job {
            job_id: "confirm".into(),
            state: "needs_confirm".into(),
            summary: "navigation needs confirmation".into(),
            error: None,
            ok: None,
        });
        return;
    }

    if !planned.errors.is_empty() {
        emit_kind(EventKind::Job {
            job_id: "pending".into(),
            state: "failed".into(),
            summary: "validate failed".into(),
            error: Some(planned.errors.join("; ")),
            ok: None,
        });
        return;
    }

    if planned.actions.is_empty() {
        emit_kind(EventKind::Job {
            job_id: "pending".into(),
            state: "done".into(),
            summary: "no actions".into(),
            error: None,
            ok: Some(true),
        });
        return;
    }

    dispatch_planned(hub, shared, planned, false).await;
}

enum LiveStreamErr {
    Cancelled,
    Other(String),
}

async fn stream_live_llm(
    llm: &crate::teach_chat::LiveLlm,
    messages: Vec<Value>,
    cancel: &Arc<AtomicBool>,
) -> Result<String, LiveStreamErr> {
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<String>();
    let cancel_b = cancel.clone();
    let llm = llm.clone();
    let handle = tokio::task::spawn_blocking(move || {
        let mut tx = tx;
        llm.complete_streaming(
            &messages,
            &mut |d| {
                let _ = tx.send(d.to_string());
            },
            &cancel_b,
        )
    });

    let mut seq = 0u32;
    while let Some(d) = rx.recv().await {
        emit_kind(EventKind::AssistantDelta {
            role: "assistant".into(),
            text: redact_for_log(&d),
            seq,
            done: false,
        });
        seq = seq.saturating_add(1);
    }
    match handle.await {
        Ok(Ok(text)) => {
            if seq == 0 && !text.is_empty() {
                let _ = emit_text_deltas(&text, cancel).await;
            }
            if cancel.load(Ordering::SeqCst) {
                Err(LiveStreamErr::Cancelled)
            } else {
                Ok(text)
            }
        }
        Ok(Err(e)) => {
            let msg = e.to_string();
            if msg.contains("cancelled") || cancel.load(Ordering::SeqCst) {
                Err(LiveStreamErr::Cancelled)
            } else {
                Err(LiveStreamErr::Other(msg))
            }
        }
        Err(e) => Err(LiveStreamErr::Other(format!("llm join: {e}"))),
    }
}

async fn emit_cancelled_planning(shared: &Shared) {
    let mut s = shared.session.lock().await;
    s.phase = TeachMachine::Chat;
    s.status = "cancelled".into();
    emit_kind(EventKind::Job {
        job_id: "pending".into(),
        state: "cancelled".into(),
        summary: "cancelled during planning".into(),
        error: None,
        ok: None,
    });
    persist_snapshot(&shared.root, &shared.profile, &s);
}

async fn handle_confirm(hub: Arc<TeachHubHandle>, shared: Arc<Shared>, yes: bool) {
    let pending = shared.pending.lock().await.take();
    let Some(planned) = pending else {
        emit_kind(EventKind::Error {
            code: "bad_cmd".into(),
            message: "no pending confirmation".into(),
        });
        return;
    };
    {
        let mut s = shared.session.lock().await;
        s.confirm = None;
        if !yes {
            s.phase = TeachMachine::Chat;
            s.status = "navigation rejected".into();
            s.push_system("high-risk navigation rejected");
            emit_kind(EventKind::System {
                role: "system".into(),
                text: "high-risk navigation rejected".into(),
            });
            emit_kind(EventKind::Job {
                job_id: "confirm".into(),
                state: "cancelled".into(),
                summary: "confirmation rejected".into(),
                error: None,
                ok: None,
            });
            persist_snapshot(&shared.root, &shared.profile, &s);
            return;
        }
    }
    dispatch_planned(hub, shared, planned, true).await;
}

async fn dispatch_planned(
    hub: Arc<TeachHubHandle>,
    shared: Arc<Shared>,
    planned: PlannedTurn,
    confirmed: bool,
) {
    let request_id = planned
        .needs_confirm
        .as_ref()
        .map(|c| c.request_id.clone())
        .unwrap_or_else(|| format!("req-{}", Uuid::new_v4()));
    let cancel = {
        let mut s = shared.session.lock().await;
        s.phase = TeachMachine::AgentActing;
        s.status = "executing".into();
        s.last_request_id = Some(request_id.clone());
        s.cancel.store(false, Ordering::SeqCst);
        for t in s.tools.iter_mut() {
            t.status = "running".into();
        }
        emit_kind(EventKind::Job {
            job_id: request_id.clone(),
            state: "running".into(),
            summary: "executing".into(),
            error: None,
            ok: None,
        });
        emit_kind(EventKind::Tool {
            tools: tools_dto(&s),
        });
        persist_snapshot(&shared.root, &shared.profile, &s);
        s.cancel.clone()
    };

    match execute_planned(hub.as_ref(), &planned, &cancel, confirmed, Some(&request_id)).await {
        Ok(v) => {
            let cancelled = v.get("cancelled").and_then(|x| x.as_bool()).unwrap_or(false);
            let mut s = shared.session.lock().await;
            if cancelled || cancel.load(Ordering::SeqCst) {
                s.phase = TeachMachine::Chat;
                s.status = "cancelled".into();
                for t in s.tools.iter_mut() {
                    t.status = "cancelled".into();
                }
                emit_kind(EventKind::Job {
                    job_id: request_id,
                    state: "cancelled".into(),
                    summary: "cancelled".into(),
                    error: None,
                    ok: None,
                });
            } else {
                s.phase = TeachMachine::Chat;
                s.status = "done".into();
                apply_action_results(&mut s, v.get("data").unwrap_or(&v));
                let ok = v.get("ok").and_then(|x| x.as_bool()).unwrap_or(false);
                let err = v
                    .get("data")
                    .and_then(|d| d.get("error"))
                    .and_then(|e| e.as_str())
                    .unwrap_or("");
                emit_kind(EventKind::Tool {
                    tools: tools_dto(&s),
                });
                emit_kind(EventKind::System {
                    role: "system".into(),
                    text: s
                        .messages
                        .last()
                        .map(|m| m.text.clone())
                        .unwrap_or_else(|| "results".into()),
                });
                emit_kind(EventKind::Job {
                    job_id: request_id,
                    state: if ok { "done".into() } else { "failed".into() },
                    summary: if ok {
                        "executed".into()
                    } else {
                        "execute failed".into()
                    },
                    error: if err.is_empty() {
                        None
                    } else {
                        Some(err.to_string())
                    },
                    ok: Some(ok),
                });
            }
            persist_snapshot(&shared.root, &shared.profile, &s);
        }
        Err(e) => {
            let mut s = shared.session.lock().await;
            s.phase = TeachMachine::Error;
            s.status = e.to_string();
            s.push_system(&format!("execute: {e}"));
            emit_kind(EventKind::Error {
                code: "execute".into(),
                message: e.to_string(),
            });
            emit_kind(EventKind::Job {
                job_id: request_id,
                state: "failed".into(),
                summary: "execute failed".into(),
                error: Some(e.to_string()),
                ok: Some(false),
            });
            persist_snapshot(&shared.root, &shared.profile, &s);
        }
    }
}

fn apply_action_results(session: &mut ChatSession, data: &Value) {
    if let Some(arr) = data.get("results").and_then(|v| v.as_array()) {
        for (i, r) in arr.iter().enumerate() {
            let st = r.get("status").and_then(|s| s.as_str()).unwrap_or("?");
            if let Some(t) = session.tools.get_mut(i) {
                t.status = st.to_string();
            }
            if let Some(page) = r.get("page") {
                if let Some(u) = page.get("url").and_then(|x| x.as_str()) {
                    session.page_url = u.to_string();
                }
                if let Some(o) = page.get("origin").and_then(|x| x.as_str()) {
                    session.page_origin = o.to_string();
                }
                if let Some(t) = page.get("title").and_then(|x| x.as_str()) {
                    session.page_title = t.to_string();
                }
            }
        }
        session.push_system(&format!("results: {} step(s)", arr.len()));
    } else if let Some(err) = data.get("error").and_then(|e| e.as_str()) {
        session.push_system(&format!("execute: {err}"));
        for t in session.tools.iter_mut() {
            if t.status == "running" {
                t.status = "fail".into();
            }
        }
    }
}

fn emit_kind(kind: EventKind) {
    let ev = WireEvent {
        v: EVENT_SCHEMA_V,
        body: kind,
    };
    let mut v = serde_json::to_value(&ev).unwrap_or_else(|_| json!({"kind": "error", "code": "encode", "message": "event encode failed", "v": 1}));
    redact_value(&mut v);
    if v.get("v").is_none() {
        v["v"] = json!(EVENT_SCHEMA_V);
    }
    let mut out = io::stdout().lock();
    let _ = writeln!(out, "{v}");
    let _ = out.flush();
}

fn redact_value(v: &mut Value) {
    match v {
        Value::String(s) => *s = redact_for_log(s),
        Value::Array(arr) => {
            for item in arr {
                redact_value(item);
            }
        }
        Value::Object(map) => {
            for (k, item) in map.iter_mut() {
                if matches!(
                    k.as_str(),
                    "kind"
                        | "v"
                        | "role"
                        | "code"
                        | "phase"
                        | "state"
                        | "job_id"
                        | "session_id"
                        | "pairing_id"
                        | "pairing_code"
                        | "hub_url"
                        | "profile"
                        | "spawn_browser"
                        | "hub"
                        | "worker"
                        | "extension"
                        | "busy"
                        | "ok"
                        | "mode"
                        | "last_request_id"
                        | "seq"
                        | "done"
                        | "hub_resume"
                ) {
                    continue;
                }
                redact_value(item);
            }
        }
        _ => {}
    }
}

#[allow(dead_code)]
pub fn parse_cmd_for_test(raw: &str) -> Result<String, String> {
    parse_cmd_json(raw)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_known_cmds() {
        assert_eq!(
            parse_cmd_for_test(r#"{"cmd":"send","goal":"click"}"#).unwrap(),
            "send"
        );
        assert_eq!(parse_cmd_for_test(r#"{"cmd":"cancel"}"#).unwrap(), "cancel");
        assert_eq!(
            parse_cmd_for_test(r#"{"cmd":"confirm","yes":true}"#).unwrap(),
            "confirm"
        );
        assert_eq!(parse_cmd_for_test(r#"{"cmd":"status"}"#).unwrap(), "status");
        assert_eq!(parse_cmd_for_test(r#"{"cmd":"stop"}"#).unwrap(), "stop");
    }

    #[test]
    fn parse_rejects_unknown_cmd_and_extra_fields() {
        assert!(parse_cmd_for_test(r#"{"cmd":"explode"}"#).is_err());
        assert!(parse_cmd_for_test(r#"{"cmd":"send","goal":"x","shell":"rm"}"#).is_err());
        assert!(parse_cmd_for_test("not-json").is_err());
        assert!(parse_cmd_for_test("{}").is_err());
    }

    #[test]
    fn parse_wire_event_accepts_typed_kinds() {
        let raw = r#"{"v":1,"kind":"assistant_delta","role":"assistant","text":"Hel","seq":0,"done":false}"#;
        let ev = parse_wire_event(raw).unwrap();
        match ev.body {
            EventKind::AssistantDelta { text, seq, done, .. } => {
                assert_eq!(text, "Hel");
                assert_eq!(seq, 0);
                assert!(!done);
            }
            other => panic!("wrong kind: {other:?}"),
        }
        let job = parse_wire_event(
            r#"{"v":1,"kind":"job","job_id":"pending","state":"running","summary":"planning"}"#,
        )
        .unwrap();
        match job.body {
            EventKind::Job { state, .. } => assert_eq!(state, "running"),
            other => panic!("{other:?}"),
        }
    }

    #[test]
    fn parse_wire_event_rejects_unknown_malformed() {
        assert!(parse_wire_event(r#"{"v":1,"kind":"shell","cmd":"rm"}"#)
            .unwrap_err()
            .contains("unknown event kind"));
        assert!(parse_wire_event("not json").is_err());
        assert!(parse_wire_event(r#"{"v":2,"kind":"user","role":"user","text":"x"}"#)
            .unwrap_err()
            .contains("schema version"));
        assert!(parse_wire_event(r#"{"v":1,"kind":"job","job_id":"x","state":"exploded","summary":"n"}"#)
            .unwrap_err()
            .contains("unknown job state"));
        assert!(parse_wire_event(r#"["not","an","object"]"#).is_err());
    }

    #[test]
    fn redact_value_strips_token_in_text() {
        let mut v = json!({
            "kind": "assistant",
            "text": "Authorization: Bearer sk-secretTEST99abc cookie=SESSIONID_SUPER_SECRET"
        });
        redact_value(&mut v);
        let s = v["text"].as_str().unwrap();
        assert!(!s.contains("sk-secretTEST99abc"), "{s}");
        assert!(!s.contains("SESSIONID_SUPER_SECRET"), "{s}");
        assert_eq!(v["kind"], "assistant");
    }

    #[test]
    fn redact_skips_pairing_ids() {
        let mut v = json!({
            "kind": "session",
            "pairing_id": "pair-abc",
            "pairing_code": "K7Q2MX",
            "text": "token=abc123SECRETVALUE"
        });
        redact_value(&mut v);
        assert_eq!(v["pairing_code"], "K7Q2MX");
        assert_eq!(v["pairing_id"], "pair-abc");
        let t = v["text"].as_str().unwrap();
        assert!(!t.contains("abc123SECRETVALUE"), "{t}");
    }

    #[test]
    fn snapshot_roundtrip() {
        let dir = std::env::temp_dir().join(format!(
            "cloakcli_snap_{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(crate::state::data_dir(&dir).join("teach")).unwrap();
        let mut session = ChatSession::default();
        session.push_user("click the link");
        session.push_assistant("click a → done ok");
        persist_snapshot(&dir, "demo", &session);
        let loaded = load_snapshot(&dir).unwrap();
        assert_eq!(loaded.profile, "demo");
        assert!(loaded.messages.iter().any(|m| m.role == "user"));
        assert!(loaded.messages.iter().any(|m| m.role == "assistant"));
        let _ = std::fs::remove_dir_all(&dir);
    }
}
