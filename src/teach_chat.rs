//! Teach Chat M2: LLM turn orchestration over the Teach Hub.
//!
//! One user goal → parse/validate → at most 3 schema actions → hub → worker.
//! Raw model text is never executed. Takeover/export are M3/M4 stubs.

use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use crate::llm;
use crate::llm_client;
use crate::teach_hub::TeachHubHandle;
use crate::teach_protocol::{
    origin_of, redact_action_payload, redact_for_log, sanitize_page_url, selector_from_value,
    TeachMachine,
};

pub const MAX_ACTIONS_PER_TURN: usize = 3;
pub const SCHEMA_VERSION: u32 = 1;
pub const MAX_TEXT_LEN: usize = 4000;
pub const MAX_WAIT_MS: u64 = 30_000;
pub const MAX_SCROLL_DELTA: i64 = 800;
pub const MAX_URL_LEN: usize = 2048;
pub const MAX_REASON_LEN: usize = 500;

const UNIFIED: &[&str] = &[
    "goto", "click", "fill", "type", "scroll", "wait", "done", "fail", "ask_human",
];

const FORBIDDEN: &[&str] = &[
    "shell",
    "exec",
    "eval",
    "evaluate",
    "python",
    "read_file",
    "write_file",
    "open",
    "download",
    "run",
    "bash",
    "cmd",
    "powershell",
    "import",
    "screenshot",
    "javascript",
    "js",
    "file",
    "system",
    "popen",
    "subprocess",
];

pub const SYSTEM_PROMPT: &str = r##"You are CloakCLI Teach Chat. Output JSON only, schema_version 1.
At most 3 actions per turn. Canonical field is "selector" (legacy "css" is accepted).
{"schema_version":1,"actions":[{"action":"click","selector":"#ok"}]}
Allowed actions: goto, click, fill, type, scroll, wait, done, fail, ask_human.
goto.url must be http(s). Any http(s) URL is allowed (no allowlist / no confirm gate).
Never file:/javascript:/data: or similar non-http schemes.
You CANNOT run shell, eval, evaluate, Python, file I/O, or arbitrary JavaScript.
fill/type text for passwords should use {{vars.PASSWORD}} when the value is unknown.
done/fail/ask_human require reason.
"##;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ActionError {
    pub message: String,
}

impl std::fmt::Display for ActionError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.message)
    }
}

impl std::error::Error for ActionError {}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TeachAction {
    pub schema_version: u32,
    pub action: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub selector: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub text: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub url: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub delta_x: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub delta_y: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ms: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub x: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub y: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub observation_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source: Option<String>,
}

impl TeachAction {
    /// Wire payload for the worker (includes fill/type plaintext so it can execute).
    pub fn to_value(&self) -> Value {
        serde_json::to_value(self).unwrap_or_else(|_| json!({"action": self.action}))
    }

    /// Timeline / TUI / log payload: fill/type text redacted; selector canonical.
    pub fn to_event_value(&self) -> Value {
        redact_action_payload(&self.to_value())
    }

    pub fn summary(&self) -> String {
        match self.action.as_str() {
            "goto" => format!(
                "goto {}",
                self.url.as_deref().map(redact_for_log).unwrap_or_default()
            ),
            "click" => format!(
                "click {}",
                self.selector.as_deref().unwrap_or("(coords)")
            ),
            "fill" | "type" => format!(
                "{} {} [REDACTED]",
                self.action,
                self.selector.as_deref().unwrap_or("")
            ),
            "scroll" => format!(
                "scroll dx={} dy={}",
                self.delta_x.unwrap_or(0),
                self.delta_y.unwrap_or(0)
            ),
            "wait" => format!("wait {}ms", self.ms.unwrap_or(0)),
            other => {
                let r = self.reason.as_deref().unwrap_or("");
                if r.is_empty() {
                    other.to_string()
                } else {
                    format!("{other} {r}")
                }
            }
        }
    }
}

#[derive(Debug, Clone)]
pub struct ParseResult {
    pub actions: Vec<TeachAction>,
    pub errors: Vec<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GotoRisk {
    Ok,
    Reject,
    /// Kept for non-goto high-risk flows (M3+). http(s) goto never uses this.
    #[allow(dead_code)]
    NeedsConfirm,
}

#[derive(Debug, Clone)]
pub struct NavConfirm {
    pub request_id: String,
    pub from_origin: String,
    pub to_url: String,
    pub to_origin: String,
    pub actions: Vec<TeachAction>,
}

#[derive(Debug, Clone)]
pub struct ToolLine {
    pub summary: String,
    pub status: String, // pending|validated|running|ok|fail|rejected|cancelled|needs_confirm
}

#[derive(Debug, Clone)]
pub struct ChatLine {
    pub role: String, // user|assistant|system
    pub text: String,
}

#[derive(Debug, Clone)]
pub struct ChatSession {
    pub phase: TeachMachine,
    pub messages: Vec<ChatLine>,
    pub tools: Vec<ToolLine>,
    pub input: String,
    pub stream: String,
    pub confirm: Option<NavConfirm>,
    pub page_url: String,
    pub page_origin: String,
    pub page_title: String,
    pub hub_connected: bool,
    pub worker_connected: bool,
    pub extension_connected: bool,
    pub mode: String,
    pub status: String,
    pub scroll: u16,
    pub ctrl_c_armed: bool,
    pub last_request_id: Option<String>,
    pub cancel: Arc<AtomicBool>,
}

impl Default for ChatSession {
    fn default() -> Self {
        Self {
            phase: TeachMachine::Chat,
            messages: vec![ChatLine {
                role: "system".into(),
                text: "Teach Chat M2 — send a goal with Enter. Ctrl-C cancels. Ctrl-T/R/E are M3/M4."
                    .into(),
            }],
            tools: Vec::new(),
            input: String::new(),
            stream: String::new(),
            confirm: None,
            page_url: String::new(),
            page_origin: String::new(),
            page_title: String::new(),
            hub_connected: false,
            worker_connected: false,
            extension_connected: false,
            mode: "LLM".into(),
            status: "chat".into(),
            scroll: 0,
            ctrl_c_armed: false,
            last_request_id: None,
            cancel: Arc::new(AtomicBool::new(false)),
        }
    }
}

impl ChatSession {
    pub fn push_user(&mut self, text: &str) {
        self.messages.push(ChatLine {
            role: "user".into(),
            text: redact_for_log(text),
        });
    }

    pub fn push_assistant(&mut self, text: &str) {
        self.messages.push(ChatLine {
            role: "assistant".into(),
            text: redact_for_log(text),
        });
    }

    pub fn push_system(&mut self, text: &str) {
        self.messages.push(ChatLine {
            role: "system".into(),
            text: redact_for_log(text),
        });
    }

    pub fn set_tools_from_actions(&mut self, actions: &[TeachAction], status: &str) {
        self.tools = actions
            .iter()
            .map(|a| ToolLine {
                summary: a.summary(),
                status: status.into(),
            })
            .collect();
    }
}

pub fn parse_model_output(text: &str) -> ParseResult {
    let Some(blob) = extract_json(text) else {
        return ParseResult {
            actions: Vec::new(),
            errors: vec!["model output is not valid JSON".into()],
        };
    };
    parse_json_blob(&blob)
}

pub fn parse_json_blob(blob: &Value) -> ParseResult {
    let mut errors = Vec::new();
    let schema_version = blob
        .get("schema_version")
        .and_then(|v| v.as_u64())
        .unwrap_or(SCHEMA_VERSION as u64) as u32;
    if blob.is_object() && schema_version != SCHEMA_VERSION {
        return ParseResult {
            actions: Vec::new(),
            errors: vec![format!("unsupported schema_version {schema_version}")],
        };
    }
    let items: Vec<Value> = if let Some(arr) = blob.as_array() {
        arr.clone()
    } else if let Some(arr) = blob.get("actions").and_then(|a| a.as_array()) {
        arr.clone()
    } else if blob.get("action").is_some() {
        vec![blob.clone()]
    } else {
        return ParseResult {
            actions: Vec::new(),
            errors: vec!["JSON missing action/actions".into()],
        };
    };
    if items.len() > MAX_ACTIONS_PER_TURN {
        return ParseResult {
            actions: Vec::new(),
            errors: vec![format!(
                "too many actions ({}); max {MAX_ACTIONS_PER_TURN} per turn",
                items.len()
            )],
        };
    }
    let mut actions = Vec::new();
    for (i, item) in items.iter().enumerate() {
        match validate_action(item) {
            Ok(a) => actions.push(a),
            Err(e) => errors.push(format!("actions[{i}]: {e}")),
        }
    }
    ParseResult { actions, errors }
}

pub fn validate_action(item: &Value) -> Result<TeachAction, ActionError> {
    let obj = item
        .as_object()
        .ok_or_else(|| ActionError {
            message: "action must be an object".into(),
        })?;
    let raw_type = obj
        .get("action")
        .or_else(|| obj.get("type"))
        .or_else(|| obj.get("name"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_ascii_lowercase();
    if raw_type.is_empty() {
        return Err(ActionError {
            message: "missing action type".into(),
        });
    }
    if FORBIDDEN.iter().any(|f| *f == raw_type) {
        return Err(ActionError {
            message: format!("forbidden action: {raw_type}"),
        });
    }
    if !UNIFIED.iter().any(|f| *f == raw_type) {
        return Err(ActionError {
            message: format!("unknown action: {raw_type}"),
        });
    }

    let selector = selector_from_value(item);
    if let Some(sel) = selector.as_deref() {
        let low = sel.to_ascii_lowercase();
        if low.contains("javascript:") || low.contains("data:") {
            return Err(ActionError {
                message: "css/selector rejected".into(),
            });
        }
    } else if obj.get("selector").is_some() || obj.get("css").is_some() {
        return Err(ActionError {
            message: "css/selector rejected".into(),
        });
    }

    let text = obj
        .get("text")
        .and_then(|v| {
            if v.is_string() {
                v.as_str().map(|s| s.to_string())
            } else {
                Some(v.to_string())
            }
        })
        .or_else(|| {
            if raw_type != "select" {
                obj.get("value").and_then(|v| {
                    if v.is_string() {
                        v.as_str().map(|s| s.to_string())
                    } else {
                        Some(v.to_string())
                    }
                })
            } else {
                None
            }
        });
    if let Some(ref t) = text {
        if t.len() > MAX_TEXT_LEN {
            return Err(ActionError {
                message: format!("text exceeds {MAX_TEXT_LEN} chars"),
            });
        }
        if t.to_ascii_lowercase().contains("javascript:") {
            return Err(ActionError {
                message: "text contains javascript:".into(),
            });
        }
    }

    let dx = obj
        .get("delta_x")
        .or_else(|| obj.get("dx"))
        .and_then(|v| v.as_i64())
        .unwrap_or(0);
    let dy = obj
        .get("delta_y")
        .or_else(|| obj.get("dy"))
        .and_then(|v| v.as_i64())
        .unwrap_or(0);
    if dx.abs() > MAX_SCROLL_DELTA || dy.abs() > MAX_SCROLL_DELTA {
        return Err(ActionError {
            message: "scroll delta out of range".into(),
        });
    }
    let mut ms = obj
        .get("ms")
        .or_else(|| obj.get("timeout"))
        .and_then(|v| v.as_u64())
        .unwrap_or(0);
    if ms > MAX_WAIT_MS {
        ms = MAX_WAIT_MS;
    }

    let url = obj.get("url").and_then(|v| v.as_str()).map(|s| s.to_string());
    if let Some(ref u) = url {
        reject_dangerous_url(u)?;
    }

    let reason = obj
        .get("reason")
        .or_else(|| obj.get("message"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .chars()
        .take(MAX_REASON_LEN)
        .collect::<String>();

    let x = obj.get("x").and_then(|v| v.as_i64());
    let y = obj.get("y").and_then(|v| v.as_i64());
    let observation_id = obj
        .get("observation_id")
        .or_else(|| obj.get("screenshot_id"))
        .and_then(|v| v.as_str())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty());

    match raw_type.as_str() {
        "click" => {
            if selector.is_none() && (x.is_none() || y.is_none()) {
                return Err(ActionError {
                    message: "click requires selector/css or x/y".into(),
                });
            }
            if selector.is_none() && observation_id.is_none() {
                return Err(ActionError {
                    message: "coordinate click requires screenshot_id matching current observation"
                        .into(),
                });
            }
        }
        "type" | "fill" => {
            if text.is_none() {
                return Err(ActionError {
                    message: format!("{raw_type} requires text"),
                });
            }
        }
        "scroll" => {
            if dx == 0 && dy == 0 && selector.is_none() {
                return Err(ActionError {
                    message: "scroll requires delta or selector".into(),
                });
            }
        }
        "wait" => {
            if ms == 0 {
                ms = 500;
            }
        }
        "goto" => {
            if url.as_deref().unwrap_or("").is_empty() {
                return Err(ActionError {
                    message: "goto requires url".into(),
                });
            }
        }
        "done" | "fail" | "ask_human" => {}
        _ => {}
    }

    let reason = if matches!(raw_type.as_str(), "done" | "fail" | "ask_human") && reason.is_empty()
    {
        Some(raw_type.clone())
    } else if reason.is_empty() {
        None
    } else {
        Some(reason)
    };

    Ok(TeachAction {
        schema_version: SCHEMA_VERSION,
        action: raw_type,
        selector,
        text,
        url,
        delta_x: if dx != 0 { Some(dx) } else { None },
        delta_y: if dy != 0 { Some(dy) } else { None },
        ms: if ms != 0 { Some(ms) } else { None },
        reason,
        x,
        y,
        observation_id,
        source: Some("llm".into()),
    })
}

pub fn reject_dangerous_url(url: &str) -> Result<(), ActionError> {
    let raw = url.trim();
    let low = raw.to_ascii_lowercase();
    if low.starts_with("javascript:") || low.contains("javascript:") {
        return Err(ActionError {
            message: "blocked scheme: javascript".into(),
        });
    }
    if low.starts_with("data:") {
        return Err(ActionError {
            message: "blocked scheme: data".into(),
        });
    }
    if low.starts_with("file:") {
        return Err(ActionError {
            message: "blocked scheme: file".into(),
        });
    }
    let parsed = url::Url::parse(raw).map_err(|_| ActionError {
        message: "invalid url".into(),
    })?;
    if parsed.scheme() != "http" && parsed.scheme() != "https" {
        return Err(ActionError {
            message: format!("blocked scheme: {}", parsed.scheme()),
        });
    }
    if !parsed.username().is_empty() || parsed.password().is_some() {
        return Err(ActionError {
            message: "url must not contain credentials".into(),
        });
    }
    if parsed.host_str().is_none() {
        return Err(ActionError {
            message: "url missing host".into(),
        });
    }
    if raw.len() > MAX_URL_LEN {
        return Err(ActionError {
            message: "url too long".into(),
        });
    }
    Ok(())
}

pub fn goto_risk(
    url: &str,
    allow_origins: &[String],
    current_origin: Option<&str>,
) -> (GotoRisk, String) {
    if let Err(e) = reject_dangerous_url(url) {
        return (GotoRisk::Reject, e.message);
    }
    let Some(dest) = origin_of(url) else {
        return (GotoRisk::Reject, "could not parse origin".into());
    };
    // User policy: any http(s) goto is allowed. Origin allowlist must not
    // reject or force confirm. Soft-log only (callers may record dest).
    let _ = (allow_origins, current_origin, dest);
    (GotoRisk::Ok, "http(s)".into())
}

fn extract_json(text: &str) -> Option<Value> {
    let mut s = text.trim().to_string();
    if let Some(start) = s.find("```") {
        let rest = &s[start + 3..];
        let rest = rest.strip_prefix("json").unwrap_or(rest);
        if let Some(end) = rest.find("```") {
            s = rest[..end].trim().to_string();
        }
    }
    if let Ok(v) = serde_json::from_str::<Value>(&s) {
        return Some(v);
    }
    if let Some(start) = s.find('{') {
        if let Some(end) = s.rfind('}') {
            if end > start {
                if let Ok(v) = serde_json::from_str::<Value>(&s[start..=end]) {
                    return Some(v);
                }
            }
        }
    }
    if let Some(start) = s.find('[') {
        if let Some(end) = s.rfind(']') {
            if end > start {
                if let Ok(v) = serde_json::from_str::<Value>(&s[start..=end]) {
                    return Some(v);
                }
            }
        }
    }
    None
}

pub fn build_messages(user: &str, page: Option<&PageBrief>, allow_origins: &[String]) -> Vec<Value> {
    let page_val = match page {
        Some(p) => json!({
            "url": p.url,
            "origin": p.origin,
            "title": p.title,
        }),
        None => Value::Null,
    };
    let user_obj = json!({
        "goal": redact_for_log(user),
        "page": page_val,
        "allow_origins": allow_origins,
        "max_actions": MAX_ACTIONS_PER_TURN,
    });
    vec![
        json!({"role": "system", "content": SYSTEM_PROMPT}),
        json!({"role": "user", "content": user_obj.to_string()}),
    ]
}

#[derive(Debug, Clone, Default)]
pub struct PageBrief {
    pub url: String,
    pub origin: String,
    pub title: String,
}

pub trait TeachLlm: Send + Sync {
    fn complete(&self, messages: &[Value]) -> Result<String>;
}

pub struct LiveLlm {
    pub base_url: String,
    pub api_key: String,
    pub model: String,
    pub timeout_sec: u64,
}

impl TeachLlm for LiveLlm {
    fn complete(&self, messages: &[Value]) -> Result<String> {
        let out = llm_client::chat_complete(
            &self.base_url,
            &self.api_key,
            &self.model,
            messages,
            self.timeout_sec,
        )?;
        Ok(out.text)
    }
}

pub struct MockLlm {
    pub text: String,
}

impl TeachLlm for MockLlm {
    fn complete(&self, _messages: &[Value]) -> Result<String> {
        Ok(self.text.clone())
    }
}

pub fn live_llm_from_root(root: &Path) -> Result<LiveLlm> {
    let cfg = llm::load(root)?.context("no config/llm.json — run: cloakcli llm configure")?;
    if cfg.model.is_empty() || cfg.base_url.is_empty() {
        bail!("llm model/base_url not configured");
    }
    let key = llm::resolve_api_key_from_env(&cfg.api_key_env)
        .context("llm API key missing (session env / api_key_env)")?;
    Ok(LiveLlm {
        base_url: cfg.base_url,
        api_key: key,
        model: cfg.model,
        timeout_sec: cfg.recover_timeout_sec.clamp(5, 60),
    })
}

#[derive(Debug, Clone)]
pub struct PlannedTurn {
    pub assistant_text: String,
    pub actions: Vec<TeachAction>,
    pub errors: Vec<String>,
    pub needs_confirm: Option<NavConfirm>,
}

/// Parse + validate a model (or mock) response. Does not execute anything.
pub fn plan_turn(
    llm_text: &str,
    allow_origins: &[String],
    current_origin: Option<&str>,
) -> PlannedTurn {
    let parsed = parse_model_output(llm_text);
    let mut needs_confirm = None;
    if parsed.errors.is_empty() {
        for a in &parsed.actions {
            if a.action == "goto" {
                if let Some(url) = a.url.as_deref() {
                    let (risk, reason) = goto_risk(url, allow_origins, current_origin);
                    match risk {
                        GotoRisk::Reject => {
                            return PlannedTurn {
                                assistant_text: redact_for_log(llm_text),
                                actions: Vec::new(),
                                errors: vec![format!("goto rejected: {reason}")],
                                needs_confirm: None,
                            };
                        }
                        GotoRisk::NeedsConfirm => {
                            let dest = origin_of(url).unwrap_or_default();
                            needs_confirm = Some(NavConfirm {
                                request_id: format!("req-{}", uuid::Uuid::new_v4()),
                                from_origin: current_origin.unwrap_or("").to_string(),
                                to_url: sanitize_page_url(url).unwrap_or_else(|_| dest.clone()),
                                to_origin: dest,
                                actions: parsed.actions.clone(),
                            });
                            break;
                        }
                        GotoRisk::Ok => {}
                    }
                }
            }
        }
    }
    PlannedTurn {
        assistant_text: redact_for_log(llm_text),
        actions: parsed.actions,
        errors: parsed.errors,
        needs_confirm,
    }
}

pub async fn run_llm_turn(
    llm: &dyn TeachLlm,
    user: &str,
    page: Option<&PageBrief>,
    allow_origins: &[String],
) -> Result<PlannedTurn> {
    let messages = build_messages(user, page, allow_origins);
    let text = llm.complete(&messages)?;
    let current = page.map(|p| p.origin.as_str()).filter(|s| !s.is_empty());
    Ok(plan_turn(&text, allow_origins, current))
}

/// Dispatch validated actions to the worker via the hub and wait for results.
pub async fn execute_planned(
    hub: &TeachHubHandle,
    planned: &PlannedTurn,
    cancel: &AtomicBool,
    confirmed: bool,
    request_id: Option<&str>,
) -> Result<Value> {
    if !planned.errors.is_empty() {
        bail!("{}", planned.errors.join("; "));
    }
    if planned.actions.is_empty() {
        bail!("no validated actions");
    }
    if planned.needs_confirm.is_some() && !confirmed {
        bail!("high-risk navigation requires confirmation");
    }
    let request_id = request_id
        .map(|s| s.to_string())
        .or_else(|| planned.needs_confirm.as_ref().map(|c| c.request_id.clone()))
        .unwrap_or_else(|| format!("req-{}", uuid::Uuid::new_v4()));
    hub.set_machine(TeachMachine::AgentActing).await;
    let actions: Vec<Value> = planned.actions.iter().map(|a| a.to_value()).collect();
    let rx = hub
        .dispatch_action_request(&request_id, actions, confirmed)
        .await?;
    let result = tokio::select! {
        r = hub.wait_action_result(rx, Duration::from_secs(45)) => r,
        _ = wait_cancel(cancel) => {
            hub.cancel_request(Some(&request_id)).await?;
            hub.set_machine(TeachMachine::Cancel).await;
            return Ok(json!({
                "ok": false,
                "cancelled": true,
                "request_id": request_id,
            }));
        }
    };
    hub.set_machine(TeachMachine::Chat).await;
    match result {
        Ok(env) => Ok(json!({
            "ok": env.data.get("ok").and_then(|v| v.as_bool()).unwrap_or(false),
            "request_id": request_id,
            "data": env.data,
        })),
        Err(e) => {
            hub.set_machine(TeachMachine::Error).await;
            Err(e)
        }
    }
}

async fn wait_cancel(flag: &AtomicBool) {
    loop {
        if flag.load(Ordering::SeqCst) {
            return;
        }
        tokio::time::sleep(Duration::from_millis(40)).await;
    }
}

/// Poll `poll_cancel` while `fut` runs. On a true poll, sets `cancel` so
/// in-flight hub/worker work can abort; still waits for `fut` to finish.
pub async fn await_cancellable<T, Fut, P>(cancel: &AtomicBool, fut: Fut, mut poll_cancel: P) -> T
where
    Fut: std::future::Future<Output = T>,
    P: FnMut() -> bool,
{
    tokio::pin!(fut);
    loop {
        tokio::select! {
            r = &mut fut => return r,
            _ = tokio::time::sleep(Duration::from_millis(25)) => {
                if cancel.load(Ordering::SeqCst) {
                    continue;
                }
                if poll_cancel() {
                    cancel.store(true, Ordering::SeqCst);
                }
            }
        }
    }
}

pub fn stub_shortcut(key: char) -> &'static str {
    match key {
        't' | 'T' => "takeover is M3 (not implemented)",
        'r' | 'R' => "resume is M3 (not implemented)",
        'e' | 'E' => "export is M4 (not implemented)",
        _ => "not implemented",
    }
}

/// Apply a planned (or executed) turn onto the TUI session.
pub fn apply_plan(session: &mut ChatSession, planned: &PlannedTurn) {
    session.stream.clear();
    if !planned.errors.is_empty() {
        session.phase = TeachMachine::Error;
        session.status = planned.errors.join("; ");
        session.push_system(&format!("validate failed: {}", session.status));
        session.set_tools_from_actions(&planned.actions, "rejected");
        return;
    }
    session.push_assistant(&summarize_actions(&planned.actions));
    if let Some(c) = planned.needs_confirm.clone() {
        session.phase = TeachMachine::AwaitingConfirm;
        session.status = format!("confirm navigation to {}", c.to_origin);
        session.confirm = Some(c);
        session.set_tools_from_actions(&planned.actions, "needs_confirm");
        session.push_system(
            "High-risk navigation: press Y to confirm (Enter does not confirm). N or Esc to reject.",
        );
        return;
    }
    session.set_tools_from_actions(&planned.actions, "validated");
    session.phase = TeachMachine::Chat;
    session.status = format!("{} action(s) validated", planned.actions.len());
}

fn summarize_actions(actions: &[TeachAction]) -> String {
    if actions.is_empty() {
        return "(no actions)".into();
    }
    actions
        .iter()
        .map(|a| a.summary())
        .collect::<Vec<_>>()
        .join(" → ")
}

pub fn mock_llm_from_env() -> Option<MockLlm> {
    let raw = std::env::var("CLOAKCLI_TEACH_CHAT_MOCK").ok()?;
    let text = raw.trim();
    if text.is_empty() {
        return None;
    }
    Some(MockLlm {
        text: text.to_string(),
    })
}

/// CLI helper: plan a turn from --mock-json or a live LLM. Never executes raw text.
pub fn plan_from_mock_or_llm(root: &Path, goal: &str, mock_json: Option<&str>) -> Result<PlannedTurn> {
    let allow = Vec::new();
    if let Some(raw) = mock_json {
        return Ok(plan_turn(raw, &allow, None));
    }
    if let Some(mock) = mock_llm_from_env() {
        return Ok(plan_turn(&mock.text, &allow, None));
    }
    let llm = live_llm_from_root(root)?;
    let messages = build_messages(goal, None, &allow);
    let text = llm.complete(&messages)?;
    Ok(plan_turn(&text, &allow, None))
}

pub fn format_plan(planned: &PlannedTurn) -> String {
    let mut out = String::new();
    if !planned.errors.is_empty() {
        out.push_str("VALIDATE_FAIL\n");
        for e in &planned.errors {
            out.push_str(&format!("  - {e}\n"));
        }
        return out;
    }
    out.push_str(&format!(
        "VALIDATE_OK actions={}\n",
        planned.actions.len()
    ));
    for (i, a) in planned.actions.iter().enumerate() {
        out.push_str(&format!("  [{i}] {}\n", a.summary()));
    }
    if let Some(c) = &planned.needs_confirm {
        out.push_str(&format!(
            "NEEDS_CONFIRM from={} to={}\n",
            c.from_origin, c.to_origin
        ));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_rejects_raw_prose() {
        let r = parse_model_output("please click submit then run shell");
        assert!(r.actions.is_empty());
        assert!(!r.errors.is_empty());
    }

    #[test]
    fn parse_caps_at_three() {
        let r = parse_model_output(
            r#"{"schema_version":1,"actions":[
                {"action":"wait","ms":1},
                {"action":"wait","ms":1},
                {"action":"wait","ms":1},
                {"action":"done","reason":"x"}
            ]}"#,
        );
        assert!(r.actions.is_empty());
        assert!(r.errors.iter().any(|e| e.contains("too many")));
    }

    #[test]
    fn parse_three_ok_and_selector_alias() {
        let r = parse_model_output(
            r##"{"schema_version":1,"actions":[
                {"action":"click","css":"a"},
                {"action":"fill","selector":"#u","text":"alice"},
                {"action":"done","reason":"ok"}
            ]}"##,
        );
        assert_eq!(r.errors.len(), 0);
        assert_eq!(r.actions.len(), 3);
        assert_eq!(r.actions[0].selector.as_deref(), Some("a"));
        assert_eq!(r.actions[1].selector.as_deref(), Some("#u"));
    }

    #[test]
    fn forbidden_and_javascript_rejected() {
        for name in ["shell", "eval", "evaluate", "read_file", "javascript"] {
            let v = json!({"action": name, "cmd": "id"});
            assert!(validate_action(&v).is_err(), "{name}");
        }
        assert!(validate_action(&json!({"action":"goto","url":"javascript:alert(1)"})).is_err());
        assert!(validate_action(&json!({"action":"goto","url":"file:///etc/passwd"})).is_err());
        assert!(validate_action(&json!({"action":"click","selector":"javascript:x"})).is_err());
    }

    #[test]
    fn https_goto_any_origin_ok_no_allowlist_or_confirm() {
        let allow = vec!["https://example.com".into()];
        let (risk, _) = goto_risk(
            "https://other.example/login",
            &allow,
            Some("https://example.com"),
        );
        assert_eq!(risk, GotoRisk::Ok);
        let (risk, _) = goto_risk(
            "https://evil.example/",
            &allow,
            Some("https://example.com"),
        );
        assert_eq!(risk, GotoRisk::Ok);
        let (risk, _) = goto_risk("https://paste.example/x", &[], None);
        assert_eq!(risk, GotoRisk::Ok);
        let (risk, reason) = goto_risk("javascript:alert(1)", &allow, None);
        assert_eq!(risk, GotoRisk::Reject);
        assert!(reason.contains("javascript"));
        let (risk, _) = goto_risk("file:///etc/passwd", &allow, None);
        assert_eq!(risk, GotoRisk::Reject);
        let (risk, _) = goto_risk("data:text/html,hi", &allow, None);
        assert_eq!(risk, GotoRisk::Reject);
    }

    #[test]
    fn plan_turn_http_goto_skips_confirm() {
        let p = plan_turn(
            r#"{"schema_version":1,"actions":[{"action":"goto","url":"https://other.example/login"}]}"#,
            &["https://example.com".into()],
            Some("https://example.com"),
        );
        assert!(p.errors.is_empty(), "{:?}", p.errors);
        assert!(p.needs_confirm.is_none());
        assert_eq!(p.actions[0].action, "goto");
    }

    #[test]
    fn fill_event_value_redacts_text() {
        let a = validate_action(&json!({
            "action": "fill",
            "selector": "#pw",
            "text": "super-secret-password"
        }))
        .unwrap();
        let ev = a.to_event_value();
        assert_eq!(ev["text"], "[REDACTED]");
        assert_eq!(ev["selector"], "#pw");
        assert!(ev.get("css").is_none());
        assert!(!ev.to_string().contains("super-secret-password"));
        assert!(a.summary().contains("[REDACTED]"));
        assert!(!a.summary().contains("super-secret-password"));
        // Wire value still has plaintext for the worker.
        assert_eq!(a.to_value()["text"], "super-secret-password");
    }

    #[test]
    fn plan_turn_never_executes_raw_text() {
        let p = plan_turn("click #submit now", &[], None);
        assert!(p.actions.is_empty());
        assert!(!p.errors.is_empty());
    }

    #[test]
    fn stubs_point_at_later_milestones() {
        assert!(stub_shortcut('t').contains("M3"));
        assert!(stub_shortcut('e').contains("M4"));
    }

    #[tokio::test]
    async fn cancel_during_wait_polls_ctrl_c() {
        use std::sync::atomic::AtomicUsize;
        let cancel = AtomicBool::new(false);
        let ticks = AtomicUsize::new(0);
        let start = std::time::Instant::now();
        let out = await_cancellable(
            &cancel,
            async {
                loop {
                    if cancel.load(Ordering::SeqCst) {
                        return "cancelled";
                    }
                    tokio::time::sleep(Duration::from_millis(10)).await;
                }
            },
            || {
                let n = ticks.fetch_add(1, Ordering::SeqCst) + 1;
                n >= 3
            },
        )
        .await;
        assert_eq!(out, "cancelled");
        assert!(cancel.load(Ordering::SeqCst));
        assert!(start.elapsed() < Duration::from_secs(2));
    }
}
