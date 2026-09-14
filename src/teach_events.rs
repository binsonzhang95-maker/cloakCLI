//! JSONL Teach Chat protocol for the desktop adapter.
//!
//! `cloakcli teach chat --events` speaks this instead of the ratatui UI.
//! stdin: one JSON object per line (`cmd` = send|cancel|confirm|status|stop).
//! stdout: one JSON object per line (`kind` = session|status|user|assistant|system|tool|job|error|closed).
//! All free-text is redacted before write. Playwright / LLM stay in this process.

use anyhow::Result;
use serde::Deserialize;
use serde_json::{json, Value};
use std::io::{self, BufRead, Write};
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::Mutex;
use uuid::Uuid;

use crate::teach_chat::{
    apply_plan, execute_planned, live_llm_from_root, mock_llm_from_env, plan_turn,
    run_llm_turn_ctx, ChatSession, MockLlm, PlannedTurn, TurnContext,
};
use crate::teach_hub::TeachHubHandle;
use crate::teach_protocol::{redact_for_log, TeachMachine};

#[derive(Debug, Clone)]
pub struct EventsOpts {
    pub profile: String,
    pub mock_json: Option<String>,
    pub spawn_browser: bool,
}

#[derive(Debug, Deserialize)]
struct InLine {
    cmd: String,
    #[serde(default)]
    goal: Option<String>,
    #[serde(default)]
    yes: Option<bool>,
    #[serde(default)]
    profile: Option<String>,
    #[serde(default)]
    skill: Option<String>,
}

struct Shared {
    session: Mutex<ChatSession>,
    pending: Mutex<Option<PlannedTurn>>,
    busy: AtomicBool,
    profile: String,
}

pub async fn run(root: &Path, hub: TeachHubHandle, opts: EventsOpts) -> Result<()> {
    let hub = Arc::new(hub);
    let mut session = ChatSession::default();
    session.hub_connected = true;
    session.push_system(
        "Teach Chat events — send a JSONL goal. LLM / Playwright stay in cloakcli; the UI only displays.",
    );

    let shared = Arc::new(Shared {
        session: Mutex::new(session),
        pending: Mutex::new(None),
        busy: AtomicBool::new(false),
        profile: opts.profile.clone(),
    });

    emit(json!({
        "kind": "session",
        "session_id": hub.session_id(),
        "pairing_id": hub.pairing_id(),
        "pairing_code": hub.pairing_code(),
        "hub_url": format!("ws://127.0.0.1:{}", hub.port()),
        "profile": opts.profile,
        "spawn_browser": opts.spawn_browser,
    }));

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
                        let parsed: InLine = match serde_json::from_str(raw) {
                            Ok(v) => v,
                            Err(e) => {
                                emit(json!({
                                    "kind": "error",
                                    "code": "bad_cmd",
                                    "message": format!("invalid command JSON: {e}"),
                                }));
                                continue;
                            }
                        };
                        match parsed.cmd.as_str() {
                            "stop" | "quit" => {
                                cancel_inflight(&hub, &shared).await;
                                if let Some(h) = inflight.take() {
                                    let _ = tokio::time::timeout(Duration::from_secs(2), h).await;
                                }
                                break;
                            }
                            "status" => emit_status(&hub, &shared).await,
                            "cancel" => {
                                cancel_inflight(&hub, &shared).await;
                            }
                            "confirm" => {
                                let yes = parsed.yes.unwrap_or(false);
                                if inflight.is_some() || shared.busy.load(Ordering::SeqCst) {
                                    emit(json!({
                                        "kind": "error",
                                        "code": "busy",
                                        "message": "a turn is already running",
                                    }));
                                    continue;
                                }
                                shared.busy.store(true, Ordering::SeqCst);
                                let hub2 = hub.clone();
                                let shared2 = shared.clone();
                                inflight = Some(tokio::spawn(async move {
                                    handle_confirm(hub2, shared2, yes).await;
                                }));
                            }
                            "send" => {
                                let goal = parsed.goal.unwrap_or_default();
                                if goal.trim().is_empty() {
                                    emit(json!({
                                        "kind": "error",
                                        "code": "bad_cmd",
                                        "message": "send requires goal",
                                    }));
                                    continue;
                                }
                                if let Some(p) = parsed.profile.as_deref() {
                                    if !p.is_empty() && p != shared.profile {
                                        emit(json!({
                                            "kind": "error",
                                            "code": "profile_mismatch",
                                            "message": format!(
                                                "session profile is {}; restart Teach Chat to use {p}",
                                                shared.profile
                                            ),
                                        }));
                                        continue;
                                    }
                                }
                                if inflight.is_some() || shared.busy.load(Ordering::SeqCst) {
                                    emit(json!({
                                        "kind": "error",
                                        "code": "busy",
                                        "message": "a turn is already running; cancel first",
                                    }));
                                    continue;
                                }
                                shared.busy.store(true, Ordering::SeqCst);
                                let hub2 = hub.clone();
                                let shared2 = shared.clone();
                                let mock2 = mock.clone();
                                let live2 = live.clone();
                                let skill = parsed.skill.filter(|s| !s.is_empty());
                                inflight = Some(tokio::spawn(async move {
                                    handle_send(hub2, shared2, mock2, live2, goal, skill).await;
                                }));
                            }
                            other => {
                                emit(json!({
                                    "kind": "error",
                                    "code": "bad_cmd",
                                    "message": format!("unknown cmd: {other}"),
                                }));
                            }
                        }
                    }
                    None => break,
                }
            }
        }
    }

    emit(json!({
        "kind": "closed",
        "reason": "stop",
        "profile": opts.profile,
    }));
    hub.abort();
    Ok(())
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
    emit(json!({
        "kind": "job",
        "job_id": id.unwrap_or_else(|| "none".into()),
        "state": "cancelled",
        "summary": "cancelled",
    }));
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
    let tools: Vec<Value> = s
        .tools
        .iter()
        .map(|t| json!({"summary": t.summary, "status": t.status}))
        .collect();
    emit(json!({
        "kind": "status",
        "phase": s.phase.as_str(),
        "status": s.status,
        "hub": s.hub_connected,
        "worker": s.worker_connected,
        "extension": s.extension_connected,
        "page_url": s.page_url,
        "page_origin": s.page_origin,
        "page_title": s.page_title,
        "busy": shared.busy.load(Ordering::SeqCst),
        "mode": s.mode,
        "profile": shared.profile,
        "tools": tools,
        "last_request_id": s.last_request_id,
    }));
}

fn emit_session_messages(session: &ChatSession) {
    if let Some(m) = session.messages.last() {
        emit(json!({
            "kind": m.role,
            "role": m.role,
            "text": m.text,
        }));
    }
    if !session.tools.is_empty() {
        let tools: Vec<Value> = session
            .tools
            .iter()
            .map(|t| json!({"summary": t.summary, "status": t.status}))
            .collect();
        emit(json!({ "kind": "tool", "tools": tools }));
    }
}

async fn handle_send(
    hub: Arc<TeachHubHandle>,
    shared: Arc<Shared>,
    mock: Arc<Option<MockLlm>>,
    live: Arc<Option<crate::teach_chat::LiveLlm>>,
    goal: String,
    skill: Option<String>,
) {
    {
        let mut s = shared.session.lock().await;
        s.cancel.store(false, Ordering::SeqCst);
        s.push_user(&goal);
        s.phase = TeachMachine::AgentActing;
        s.status = "planning".into();
        emit(json!({
            "kind": "user",
            "role": "user",
            "text": s.messages.last().map(|m| m.text.clone()).unwrap_or_default(),
        }));
        emit(json!({
            "kind": "job",
            "job_id": "pending",
            "state": "running",
            "summary": "planning",
        }));
    }

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

    let planned = if let Some(m) = mock.as_ref() {
        let current = if page_origin.is_empty() {
            None
        } else {
            Some(page_origin.as_str())
        };
        plan_turn(&m.text, &allow, current)
    } else if let Some(llm) = live.as_ref() {
        let ctx = TurnContext {
            human_summary: human.as_deref(),
            profile: Some(shared.profile.as_str()),
            skill: skill.as_deref(),
        };
        match run_llm_turn_ctx(llm, &goal, page.as_ref(), &allow, ctx).await {
            Ok(p) => p,
            Err(e) => {
                let mut s = shared.session.lock().await;
                s.phase = TeachMachine::Error;
                s.status = e.to_string();
                s.push_system(&format!("llm: {e}"));
                emit(json!({
                    "kind": "error",
                    "code": "llm",
                    "message": e.to_string(),
                }));
                emit(json!({
                    "kind": "job",
                    "job_id": "pending",
                    "state": "failed",
                    "summary": "llm failed",
                    "error": e.to_string(),
                }));
                return;
            }
        }
    } else {
        let mut s = shared.session.lock().await;
        s.phase = TeachMachine::Chat;
        s.status = "no LLM".into();
        s.push_system("no LLM configured and no --mock-json / CLOAKCLI_TEACH_CHAT_MOCK");
        emit(json!({
            "kind": "error",
            "code": "no_llm",
            "message": "no LLM configured and no mock JSON",
        }));
        emit(json!({
            "kind": "job",
            "job_id": "pending",
            "state": "failed",
            "summary": "no LLM",
            "error": "no LLM configured",
        }));
        return;
    };

    if shared
        .session
        .lock()
        .await
        .cancel
        .load(Ordering::SeqCst)
    {
        let mut s = shared.session.lock().await;
        s.phase = TeachMachine::Chat;
        s.status = "cancelled".into();
        emit(json!({
            "kind": "job",
            "job_id": "pending",
            "state": "cancelled",
            "summary": "cancelled during planning",
        }));
        return;
    }

    {
        let mut s = shared.session.lock().await;
        apply_plan(&mut s, &planned);
        emit(json!({
            "kind": "assistant",
            "role": "assistant",
            "text": s.messages.last().map(|m| m.text.clone()).unwrap_or_else(|| planned.assistant_text.clone()),
        }));
        let tools: Vec<Value> = s
            .tools
            .iter()
            .map(|t| json!({"summary": t.summary, "status": t.status}))
            .collect();
        emit(json!({ "kind": "tool", "tools": tools }));
    }

    if planned.needs_confirm.is_some() {
        *shared.pending.lock().await = Some(planned);
        emit(json!({
            "kind": "job",
            "job_id": "confirm",
            "state": "needs_confirm",
            "summary": "navigation needs confirmation",
        }));
        return;
    }

    if !planned.errors.is_empty() {
        emit(json!({
            "kind": "job",
            "job_id": "pending",
            "state": "failed",
            "summary": "validate failed",
            "error": planned.errors.join("; "),
        }));
        return;
    }

    if planned.actions.is_empty() {
        emit(json!({
            "kind": "job",
            "job_id": "pending",
            "state": "done",
            "summary": "no actions",
        }));
        return;
    }

    dispatch_planned(hub, shared, planned, false).await;
}

async fn handle_confirm(hub: Arc<TeachHubHandle>, shared: Arc<Shared>, yes: bool) {
    let pending = shared.pending.lock().await.take();
    let Some(planned) = pending else {
        emit(json!({
            "kind": "error",
            "code": "bad_cmd",
            "message": "no pending confirmation",
        }));
        return;
    };
    {
        let mut s = shared.session.lock().await;
        s.confirm = None;
        if !yes {
            s.phase = TeachMachine::Chat;
            s.status = "navigation rejected".into();
            s.push_system("high-risk navigation rejected");
            emit(json!({
                "kind": "system",
                "role": "system",
                "text": "high-risk navigation rejected",
            }));
            emit(json!({
                "kind": "job",
                "job_id": "confirm",
                "state": "cancelled",
                "summary": "confirmation rejected",
            }));
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
        emit(json!({
            "kind": "job",
            "job_id": request_id,
            "state": "running",
            "summary": "executing",
        }));
        let tools: Vec<Value> = s
            .tools
            .iter()
            .map(|t| json!({"summary": t.summary, "status": t.status}))
            .collect();
        emit(json!({ "kind": "tool", "tools": tools }));
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
                emit(json!({
                    "kind": "job",
                    "job_id": request_id,
                    "state": "cancelled",
                    "summary": "cancelled",
                }));
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
                let tools: Vec<Value> = s
                    .tools
                    .iter()
                    .map(|t| json!({"summary": t.summary, "status": t.status}))
                    .collect();
                emit(json!({ "kind": "tool", "tools": tools }));
                emit(json!({
                    "kind": "system",
                    "role": "system",
                    "text": s.messages.last().map(|m| m.text.clone()).unwrap_or_else(|| "results".into()),
                }));
                emit(json!({
                    "kind": "job",
                    "job_id": request_id,
                    "state": if ok { "done" } else { "failed" },
                    "summary": if ok { "executed" } else { "execute failed" },
                    "error": if err.is_empty() { Value::Null } else { json!(err) },
                    "ok": ok,
                }));
            }
        }
        Err(e) => {
            let mut s = shared.session.lock().await;
            s.phase = TeachMachine::Error;
            s.status = e.to_string();
            s.push_system(&format!("execute: {e}"));
            emit(json!({
                "kind": "error",
                "code": "execute",
                "message": e.to_string(),
            }));
            emit(json!({
                "kind": "job",
                "job_id": request_id,
                "state": "failed",
                "summary": "execute failed",
                "error": e.to_string(),
            }));
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

fn emit(mut v: Value) {
    redact_value(&mut v);
    if v.get("v").is_none() {
        v["v"] = json!(1);
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
    let v: InLine = serde_json::from_str(raw).map_err(|e| e.to_string())?;
    Ok(v.cmd)
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
}
