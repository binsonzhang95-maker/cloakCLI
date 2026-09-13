//! Teach Hub protocol (M1): versioned Envelope and message types.
//!
//! Separate from `protocol.rs` (Master/Fleet). Do not mix the two.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

pub const TEACH_PROTOCOL_VERSION: u32 = 1;
pub const MAX_MESSAGE_BYTES: usize = 256 * 1024;
pub const MAX_SELECTOR_LEN: usize = 500;
pub const MAX_TITLE_LEN: usize = 200;
pub const MAX_CLICKABLE: usize = 40;
pub const MAX_NONCE_LEN: usize = 128;
pub const MAX_PAIRING_FAILURES: u32 = 5;

pub const TYPE_PAIRING_OFFER: &str = "pairing_offer";
pub const TYPE_PAIRING_ACCEPT: &str = "pairing_accept";
pub const TYPE_PAIRING_RESULT: &str = "pairing_result";
pub const TYPE_PAGE_STATE: &str = "page_state";
pub const TYPE_HEARTBEAT: &str = "heartbeat";
pub const TYPE_ERROR: &str = "error";
pub const TYPE_CANCEL: &str = "cancel";
pub const TYPE_ALLOWLIST_UPDATE: &str = "allowlist_update";
pub const TYPE_CHAT_MESSAGE: &str = "chat_message";
pub const TYPE_LLM_STREAM: &str = "llm_stream";
pub const TYPE_ACTION_REQUEST: &str = "action_request";
pub const TYPE_ACTION_RESULT: &str = "action_result";
pub const TYPE_TAKEOVER_START: &str = "takeover_start";
pub const TYPE_TAKEOVER_EVENT: &str = "takeover_event";
pub const TYPE_TAKEOVER_STOP: &str = "takeover_stop";
pub const TYPE_NORMALIZE_RESULT: &str = "normalize_result";
pub const TYPE_HUMAN_CONFIRM: &str = "human_confirm";
pub const TYPE_RESUME: &str = "resume";
pub const TYPE_EXPORT: &str = "export";
pub const TYPE_EXPORT_RESULT: &str = "export_result";

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

const SECRET_LOG_KEYS: &[&str] = &[
    "token",
    "session_token",
    "cookie",
    "cookies",
    "password",
    "passwd",
    "secret",
    "authorization",
    "api_key",
    "apikey",
];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MsgType {
    PairingOffer,
    PairingAccept,
    PairingResult,
    PageState,
    Heartbeat,
    Error,
    Cancel,
    AllowlistUpdate,
    ChatMessage,
    LlmStream,
    ActionRequest,
    ActionResult,
    TakeoverStart,
    TakeoverEvent,
    TakeoverStop,
    NormalizeResult,
    HumanConfirm,
    Resume,
    Export,
    ExportResult,
}

impl MsgType {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::PairingOffer => TYPE_PAIRING_OFFER,
            Self::PairingAccept => TYPE_PAIRING_ACCEPT,
            Self::PairingResult => TYPE_PAIRING_RESULT,
            Self::PageState => TYPE_PAGE_STATE,
            Self::Heartbeat => TYPE_HEARTBEAT,
            Self::Error => TYPE_ERROR,
            Self::Cancel => TYPE_CANCEL,
            Self::AllowlistUpdate => TYPE_ALLOWLIST_UPDATE,
            Self::ChatMessage => TYPE_CHAT_MESSAGE,
            Self::LlmStream => TYPE_LLM_STREAM,
            Self::ActionRequest => TYPE_ACTION_REQUEST,
            Self::ActionResult => TYPE_ACTION_RESULT,
            Self::TakeoverStart => TYPE_TAKEOVER_START,
            Self::TakeoverEvent => TYPE_TAKEOVER_EVENT,
            Self::TakeoverStop => TYPE_TAKEOVER_STOP,
            Self::NormalizeResult => TYPE_NORMALIZE_RESULT,
            Self::HumanConfirm => TYPE_HUMAN_CONFIRM,
            Self::Resume => TYPE_RESUME,
            Self::Export => TYPE_EXPORT,
            Self::ExportResult => TYPE_EXPORT_RESULT,
        }
    }

    pub fn parse(s: &str) -> Option<Self> {
        Some(match s {
            TYPE_PAIRING_OFFER => Self::PairingOffer,
            TYPE_PAIRING_ACCEPT => Self::PairingAccept,
            TYPE_PAIRING_RESULT => Self::PairingResult,
            TYPE_PAGE_STATE => Self::PageState,
            TYPE_HEARTBEAT => Self::Heartbeat,
            TYPE_ERROR => Self::Error,
            TYPE_CANCEL => Self::Cancel,
            TYPE_ALLOWLIST_UPDATE => Self::AllowlistUpdate,
            TYPE_CHAT_MESSAGE => Self::ChatMessage,
            TYPE_LLM_STREAM => Self::LlmStream,
            TYPE_ACTION_REQUEST => Self::ActionRequest,
            TYPE_ACTION_RESULT => Self::ActionResult,
            TYPE_TAKEOVER_START => Self::TakeoverStart,
            TYPE_TAKEOVER_EVENT => Self::TakeoverEvent,
            TYPE_TAKEOVER_STOP => Self::TakeoverStop,
            TYPE_NORMALIZE_RESULT => Self::NormalizeResult,
            TYPE_HUMAN_CONFIRM => Self::HumanConfirm,
            TYPE_RESUME => Self::Resume,
            TYPE_EXPORT => Self::Export,
            TYPE_EXPORT_RESULT => Self::ExportResult,
            _ => return None,
        })
    }

    pub fn is_m1(self) -> bool {
        matches!(
            self,
            Self::PairingOffer
                | Self::PairingAccept
                | Self::PairingResult
                | Self::PageState
                | Self::Heartbeat
                | Self::Error
                | Self::Cancel
                | Self::AllowlistUpdate
        )
    }

    pub fn is_m2(self) -> bool {
        matches!(
            self,
            Self::ChatMessage
                | Self::LlmStream
                | Self::ActionRequest
                | Self::ActionResult
                | Self::HumanConfirm
        )
    }

    #[allow(dead_code)]
    pub fn is_m3(self) -> bool {
        matches!(
            self,
            Self::TakeoverStart | Self::TakeoverEvent | Self::TakeoverStop | Self::NormalizeResult | Self::Resume
        )
    }

    #[allow(dead_code)]
    pub fn is_m4(self) -> bool {
        matches!(self, Self::Export | Self::ExportResult)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TeachMachine {
    Chat,
    AgentActing,
    AwaitingConfirm,
    HumanTakeover,
    Resume,
    Cancel,
    Error,
}

impl TeachMachine {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Chat => "chat",
            Self::AgentActing => "agent_acting",
            Self::AwaitingConfirm => "awaiting_confirm",
            Self::HumanTakeover => "human_takeover",
            Self::Resume => "resume",
            Self::Cancel => "cancel",
            Self::Error => "error",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ClientRole {
    Extension,
    Worker,
}

impl ClientRole {
    pub fn parse(s: &str) -> Option<Self> {
        match s.trim().to_ascii_lowercase().as_str() {
            "extension" => Some(Self::Extension),
            "worker" => Some(Self::Worker),
            _ => None,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Extension => "extension",
            Self::Worker => "worker",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Envelope {
    pub v: u32,
    #[serde(rename = "type")]
    pub msg_type: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub session_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub request_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub seq: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ts: Option<String>,
    #[serde(default)]
    pub data: Value,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProtocolError {
    pub code: &'static str,
    pub message: String,
    pub retryable: bool,
}

impl std::fmt::Display for ProtocolError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.code, self.message)
    }
}

impl std::error::Error for ProtocolError {}

impl ProtocolError {
    pub fn new(code: &'static str, message: impl Into<String>, retryable: bool) -> Self {
        Self {
            code,
            message: message.into(),
            retryable,
        }
    }

    pub fn to_envelope(&self, session_id: Option<&str>, request_id: Option<&str>) -> Envelope {
        let mut env = Envelope::new(TYPE_ERROR);
        env.session_id = session_id.map(|s| s.to_string());
        env.request_id = request_id.map(|s| s.to_string());
        env.data = json!({
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        });
        env
    }
}

impl Envelope {
    pub fn new(msg_type: &str) -> Self {
        Self {
            v: TEACH_PROTOCOL_VERSION,
            msg_type: msg_type.into(),
            session_id: None,
            request_id: None,
            seq: None,
            ts: Some(now_ts()),
            data: json!({}),
        }
    }

    pub fn with_session(mut self, id: &str) -> Self {
        self.session_id = Some(id.into());
        self
    }

    pub fn with_request(mut self, id: &str) -> Self {
        self.request_id = Some(id.into());
        self
    }

    pub fn with_seq(mut self, seq: u64) -> Self {
        self.seq = Some(seq);
        self
    }

    pub fn with_data(mut self, data: Value) -> Self {
        self.data = data;
        self
    }

    pub fn typed(&self) -> Option<MsgType> {
        MsgType::parse(&self.msg_type)
    }

    pub fn to_vec(&self) -> Result<Vec<u8>, ProtocolError> {
        let bytes = serde_json::to_vec(self).map_err(|e| {
            ProtocolError::new("encode", format!("encode failed: {e}"), false)
        })?;
        if bytes.len() > MAX_MESSAGE_BYTES {
            return Err(ProtocolError::new(
                "message_too_large",
                "encoded message exceeds limit",
                false,
            ));
        }
        Ok(bytes)
    }

    pub fn to_jsonl(&self) -> Result<Vec<u8>, ProtocolError> {
        let mut bytes = self.to_vec()?;
        bytes.push(b'\n');
        Ok(bytes)
    }
}

pub fn now_ts() -> String {
    chrono::Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Secs, true)
}

/// Parse one JSON object. Rejects oversize, bad version, and non-object data.
pub fn parse_envelope(bytes: &[u8]) -> Result<Envelope, ProtocolError> {
    if bytes.len() > MAX_MESSAGE_BYTES {
        return Err(ProtocolError::new(
            "message_too_large",
            "message exceeds limit",
            false,
        ));
    }
    let trimmed = strip_line_ending(bytes);
    if trimmed.is_empty() {
        return Err(ProtocolError::new("invalid_json", "empty message", false));
    }
    let env: Envelope = serde_json::from_slice(trimmed).map_err(|e| {
        ProtocolError::new("invalid_json", format!("invalid json: {e}"), false)
    })?;
    validate_envelope(&env)?;
    Ok(env)
}

pub fn validate_envelope(env: &Envelope) -> Result<(), ProtocolError> {
    if env.v != TEACH_PROTOCOL_VERSION {
        return Err(ProtocolError::new(
            "protocol_version",
            format!("unsupported version {}", env.v),
            false,
        ));
    }
    if env.msg_type.trim().is_empty() {
        return Err(ProtocolError::new(
            "invalid_type",
            "missing message type",
            false,
        ));
    }
    if !env.data.is_null() && !env.data.is_object() {
        return Err(ProtocolError::new(
            "invalid_data",
            "data must be an object",
            false,
        ));
    }
    Ok(())
}

fn strip_line_ending(bytes: &[u8]) -> &[u8] {
    let mut s = bytes;
    if s.ends_with(b"\r\n") {
        s = &s[..s.len() - 2];
    } else if s.ends_with(b"\n") {
        s = &s[..s.len() - 1];
    }
    s
}

/// Canonical field is `selector`. Legacy Recover `css` is accepted when reading.
pub fn selector_from_value(v: &Value) -> Option<String> {
    let raw = v
        .get("selector")
        .or_else(|| v.get("css"))
        .and_then(|x| x.as_str())?;
    let s = raw.trim();
    if s.is_empty() || s.len() > MAX_SELECTOR_LEN {
        return None;
    }
    if s.contains(';') || s.contains('{') || s.contains('}') {
        return None;
    }
    Some(s.to_string())
}

pub fn is_http_origin(origin: &str) -> bool {
    let origin = origin.trim();
    match origin_of(origin) {
        Some(o) => o == origin || o == origin.trim_end_matches('/'),
        None => false,
    }
}

pub fn origin_of(url: &str) -> Option<String> {
    let u = url::Url::parse(url.trim()).ok()?;
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

pub fn origin_allowed(origin: &str, allowlist: &[String]) -> bool {
    if !is_http_origin(origin) {
        return false;
    }
    allowlist.iter().any(|o| o == origin)
}

/// HTTP(S) only; strip userinfo, fragment, and secret query keys.
pub fn sanitize_page_url(raw: &str) -> Result<String, ProtocolError> {
    let raw = raw.trim();
    if raw.is_empty() {
        return Err(ProtocolError::new("invalid_url", "empty url", false));
    }
    let mut u = url::Url::parse(raw)
        .map_err(|_| ProtocolError::new("invalid_url", "invalid url", false))?;
    if u.scheme() != "http" && u.scheme() != "https" {
        return Err(ProtocolError::new(
            "invalid_url",
            "refusing non-http(s) URL",
            false,
        ));
    }
    if u.cannot_be_a_base() {
        return Err(ProtocolError::new(
            "invalid_url",
            "refusing non-base URL",
            false,
        ));
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
            .map(|(k, v)| format!("{k}={v}"))
            .collect::<Vec<_>>()
            .join("&");
        u.set_query(Some(&q));
    }
    Ok(u.to_string())
}

fn is_secret_query_key(k: &str) -> bool {
    let k = k.trim().to_ascii_lowercase();
    SECRET_QUERY_KEYS
        .iter()
        .any(|s| k == *s || k.contains(s))
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct Viewport {
    #[serde(default)]
    pub width: u32,
    #[serde(default)]
    pub height: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct Clickable {
    #[serde(default)]
    pub tag: String,
    #[serde(default)]
    pub role: String,
    #[serde(default)]
    pub text: String,
    /// Canonical selector. Use [`selector_from_value`] when reading raw JSON.
    #[serde(default)]
    pub selector: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PageState {
    pub url: String,
    pub origin: String,
    #[serde(default)]
    pub title: String,
    #[serde(default)]
    pub viewport: Viewport,
    pub observation_id: String,
    #[serde(default)]
    pub clickable: Vec<Clickable>,
}

impl PageState {
    pub fn from_data(data: &Value, allowlist: &[String]) -> Result<Self, ProtocolError> {
        let url_raw = data
            .get("url")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim();
        let url = sanitize_page_url(url_raw)?;
        let origin = data
            .get("origin")
            .and_then(|v| v.as_str())
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(|s| s.to_string())
            .or_else(|| origin_of(&url))
            .ok_or_else(|| ProtocolError::new("invalid_origin", "missing origin", false))?;
        if !is_http_origin(&origin) {
            return Err(ProtocolError::new(
                "invalid_origin",
                "origin must be http(s)",
                false,
            ));
        }
        if origin_of(&url).as_deref() != Some(origin.as_str()) {
            return Err(ProtocolError::new(
                "invalid_origin",
                "origin does not match url",
                false,
            ));
        }
        if !origin_allowed(&origin, allowlist) {
            return Err(ProtocolError::new(
                "origin_not_allowed",
                "origin is not on the allowlist",
                false,
            ));
        }
        let observation_id = data
            .get("observation_id")
            .and_then(|v| v.as_str())
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .ok_or_else(|| {
                ProtocolError::new("invalid_page_state", "missing observation_id", false)
            })?
            .to_string();
        if observation_id.len() > 80 {
            return Err(ProtocolError::new(
                "invalid_page_state",
                "observation_id too long",
                false,
            ));
        }
        let mut title = data
            .get("title")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_string();
        if title.len() > MAX_TITLE_LEN {
            title.truncate(MAX_TITLE_LEN);
        }
        let viewport = Viewport {
            width: data
                .pointer("/viewport/width")
                .and_then(|v| v.as_u64())
                .unwrap_or(0) as u32,
            height: data
                .pointer("/viewport/height")
                .and_then(|v| v.as_u64())
                .unwrap_or(0) as u32,
        };
        let mut clickable = Vec::new();
        if let Some(arr) = data.get("clickable").and_then(|v| v.as_array()) {
            for item in arr.iter().take(MAX_CLICKABLE) {
                let mut text = item
                    .get("text")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .trim()
                    .to_string();
                if looks_secret_text(&text) {
                    text = "[REDACTED]".into();
                }
                if text.len() > 80 {
                    text.truncate(80);
                }
                clickable.push(Clickable {
                    tag: item
                        .get("tag")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_ascii_lowercase(),
                    role: item
                        .get("role")
                        .and_then(|v| v.as_str())
                        .unwrap_or("")
                        .to_string(),
                    text,
                    selector: selector_from_value(item),
                });
            }
        }
        Ok(Self {
            url,
            origin,
            title,
            viewport,
            observation_id,
            clickable,
        })
    }
}

fn looks_secret_text(s: &str) -> bool {
    let lower = s.to_ascii_lowercase();
    lower.contains("bearer ")
        || lower.contains("authorization")
        || lower.contains("cookie=")
        || s.contains("sk-")
}

/// Redact tokens/cookies/passwords from a log line. Never a substitute for
/// not putting secrets in the string in the first place.
pub fn redact_for_log(s: &str) -> String {
    let mut out = s.to_string();
    for pat in [
        r#"(?i)("(?:session_token|token|cookie|password|secret|authorization)"\s*:\s*")[^"]*"#,
        r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)(\S+)",
        r"(?i)(cookie\s*[:=]\s*)(\S+)",
        r"(?i)(bearer\s+)([A-Za-z0-9._\-+/=]+)",
    ] {
        if let Ok(re) = regex_replace(pat, &out) {
            out = re;
        }
    }
    out
}

fn regex_replace(pattern: &str, text: &str) -> Result<String, ()> {
    // Tiny replacements without adding the `regex` crate: handle the JSON
    // secret-key case and a few literal prefixes.
    let _ = pattern;
    Ok(redact_secret_keys(text))
}

fn redact_secret_keys(text: &str) -> String {
    let mut s = text.to_string();
    for key in SECRET_LOG_KEYS {
        s = redact_json_key(&s, key);
    }
    s = redact_prefix_value(&s, "Bearer ");
    s = redact_prefix_value(&s, "bearer ");
    s = redact_prefix_ci(&s, "authorization:");
    s = redact_prefix_ci(&s, "cookie:");
    s = redact_prefix_ci(&s, "password=");
    s
}

fn redact_json_key(s: &str, key: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let lower = s.to_ascii_lowercase();
    let needle = format!("\"{key}\"");
    let mut i = 0;
    while let Some(pos) = lower[i..].find(&needle) {
        let abs = i + pos;
        out.push_str(&s[i..abs]);
        out.push_str(&s[abs..abs + needle.len()]);
        let rest = &s[abs + needle.len()..];
        let trimmed = rest.trim_start();
        let skipped = rest.len() - trimmed.len();
        out.push_str(&rest[..skipped]);
        if let Some(stripped) = trimmed.strip_prefix(':') {
            out.push(':');
            let after = stripped.trim_start();
            out.push_str(&stripped[..stripped.len() - after.len()]);
            if let Some(inner) = after.strip_prefix('"') {
                out.push_str("\"[REDACTED]\"");
                if let Some(end) = inner.find('"') {
                    i = abs + needle.len() + skipped + 1 + (stripped.len() - after.len()) + 1 + end
                        + 1;
                    continue;
                }
            } else {
                let take = after
                    .find(|c: char| c.is_whitespace() || c == ',' || c == '}' || c == '\n')
                    .unwrap_or(after.len());
                out.push_str("[REDACTED]");
                i = abs
                    + needle.len()
                    + skipped
                    + 1
                    + (stripped.len() - after.len())
                    + take;
                continue;
            }
        }
        i = abs + needle.len();
    }
    out.push_str(&s[i..]);
    out
}

fn redact_prefix_value(s: &str, prefix: &str) -> String {
    let mut out = String::with_capacity(s.len());
    let mut rest = s;
    while let Some(pos) = rest.find(prefix) {
        out.push_str(&rest[..pos]);
        out.push_str(prefix);
        out.push_str("[REDACTED]");
        let after = &rest[pos + prefix.len()..];
        let skip = after
            .find(|c: char| c.is_whitespace() || c == '"' || c == ',' || c == '}')
            .unwrap_or(after.len());
        rest = &after[skip..];
    }
    out.push_str(rest);
    out
}

fn redact_prefix_ci(s: &str, prefix: &str) -> String {
    let lower = s.to_ascii_lowercase();
    let p = prefix.to_ascii_lowercase();
    let mut out = String::with_capacity(s.len());
    let mut i = 0;
    while let Some(pos) = lower[i..].find(&p) {
        let abs = i + pos;
        out.push_str(&s[i..abs]);
        out.push_str(&s[abs..abs + prefix.len()]);
        out.push_str("[REDACTED]");
        let after = &s[abs + prefix.len()..];
        let skip = after
            .find(|c: char| c.is_whitespace() || c == '"' || c == ',' || c == '}')
            .unwrap_or(after.len());
        i = abs + prefix.len() + skip;
    }
    out.push_str(&s[i..]);
    out
}

/// Structured redaction for action events written to the hub timeline / TUI.
/// Fill/type plaintext is replaced with `[REDACTED]` plus `text_len`, unless the
/// value is already a `{{vars.NAME}}` placeholder. Canonical field is `selector`;
/// `css` is dropped from objects that have an `action` key (read-compat only).
pub fn is_var_placeholder(s: &str) -> bool {
    let t = s.trim();
    t.starts_with("{{vars.") && t.ends_with("}}") && t.len() <= 80 && !t.contains('\n')
}

pub fn redact_action_payload(data: &Value) -> Value {
    let mut v = data.clone();
    redact_action_payload_in_place(&mut v);
    v
}

fn redact_secret_field(map: &mut serde_json::Map<String, Value>, key: &str) {
    let Some(Value::String(s)) = map.get(key) else {
        return;
    };
    let s = s.clone();
    if is_var_placeholder(&s) {
        return;
    }
    let len = s.len();
    map.insert(key.to_string(), json!("[REDACTED]"));
    if key == "text" {
        map.entry("text_len".to_string()).or_insert(json!(len));
    }
}

fn redact_action_payload_in_place(v: &mut Value) {
    match v {
        Value::Object(map) => {
            let atype = map
                .get("action")
                .and_then(|x| x.as_str())
                .unwrap_or("")
                .to_ascii_lowercase();
            if atype == "fill" || atype == "type" {
                redact_secret_field(map, "text");
                redact_secret_field(map, "value");
            }
            if atype == "select" {
                redact_secret_field(map, "value");
                redact_secret_field(map, "text");
            }
            if !atype.is_empty() {
                if map.contains_key("selector") {
                    map.remove("css");
                } else if let Some(css) = map.remove("css") {
                    map.insert("selector".into(), css);
                }
            }
            for (_, child) in map.iter_mut() {
                redact_action_payload_in_place(child);
            }
        }
        Value::Array(arr) => {
            for child in arr {
                redact_action_payload_in_place(child);
            }
        }
        _ => {}
    }
}

/// Strip password/token plaintext from a temporary takeover DOM event.
/// Never a substitute for the extension redacting first.
pub fn redact_takeover_event(data: &Value) -> Value {
    let mut v = data.clone();
    let field_type = v
        .pointer("/field/type")
        .and_then(|x| x.as_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    let hay = format!(
        "{field_type} {} {} {}",
        v.get("label").and_then(|x| x.as_str()).unwrap_or(""),
        v.pointer("/field/name")
            .and_then(|x| x.as_str())
            .unwrap_or(""),
        v.pointer("/field/id").and_then(|x| x.as_str()).unwrap_or("")
    )
    .to_ascii_lowercase();
    let secret = v.get("redacted").and_then(|x| x.as_bool()).unwrap_or(false)
        || field_type == "password"
        || hay.contains("password")
        || hay.contains("passwd")
        || hay.contains("secret")
        || hay.contains("token")
        || hay.contains("authorization")
        || hay.contains("cookie");
    let Some(map) = v.as_object_mut() else {
        return v;
    };
    if secret {
        if let Some(Value::String(s)) = map.get("value") {
            let len = s.len();
            map.entry("value_len".to_string()).or_insert(json!(len));
        }
        map.insert("value".into(), json!(""));
        map.insert("redacted".into(), json!(true));
        if let Some(Value::String(t)) = map.get("text") {
            let low = t.to_ascii_lowercase();
            if low.contains("password") || low.contains("token") || low.contains("secret") {
                map.insert("text".into(), json!("[REDACTED]"));
            }
        }
    }
    if let Some(url) = map.get("url").and_then(|x| x.as_str()).map(|s| s.to_string()) {
        if let Ok(clean) = sanitize_page_url(&url) {
            map.insert("url".into(), json!(clean));
        }
    }
    v
}

pub fn is_raw_dom_event(v: &Value) -> bool {
    if v.get("action").and_then(|x| x.as_str()).is_some() {
        return false;
    }
    matches!(
        v.get("kind").and_then(|x| x.as_str()).unwrap_or(""),
        "click"
            | "input"
            | "fill"
            | "type"
            | "select"
            | "navigation"
            | "nav"
            | "goto"
            | "keypress"
            | "keydown"
            | "press"
            | "change"
    )
}

pub fn pairing_offer_data(pairing_id: &str, code: &str, expires_at: &str) -> Value {
    json!({
        "pairing_id": pairing_id,
        "code": code,
        "expires_at": expires_at,
        "capabilities": {
            "page_state": true,
            "pairing": true,
            "takeover": true,
            "actions": true,
            "normalize": true,
        }
    })
}

pub fn pairing_accept_from_data(data: &Value) -> Result<PairingAccept, ProtocolError> {
    let pairing_id = data
        .get("pairing_id")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string());
    let code = data
        .get("code")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string());
    let nonce = data
        .get("nonce")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .unwrap_or("")
        .to_string();
    if nonce.is_empty() || nonce.len() > MAX_NONCE_LEN {
        return Err(ProtocolError::new(
            "invalid_pairing",
            "nonce required",
            false,
        ));
    }
    let role = data
        .get("role")
        .and_then(|v| v.as_str())
        .and_then(ClientRole::parse)
        .ok_or_else(|| ProtocolError::new("invalid_pairing", "role must be extension or worker", false))?;
    let session_token = data
        .get("session_token")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string());
    let resume_from = data.get("resume_from").and_then(|v| v.as_u64());
    if session_token.is_none() && (pairing_id.is_none() || code.is_none()) {
        return Err(ProtocolError::new(
            "invalid_pairing",
            "pairing_id+code or session_token required",
            false,
        ));
    }
    Ok(PairingAccept {
        pairing_id,
        code,
        nonce,
        role,
        session_token,
        resume_from,
    })
}

#[derive(Debug, Clone)]
pub struct PairingAccept {
    pub pairing_id: Option<String>,
    pub code: Option<String>,
    pub nonce: String,
    pub role: ClientRole,
    pub session_token: Option<String>,
    pub resume_from: Option<u64>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn envelope_round_trip() {
        let env = Envelope::new(TYPE_PAGE_STATE)
            .with_session("sess-1")
            .with_request("req-1")
            .with_seq(3)
            .with_data(json!({"url": "https://example.com/"}));
        let bytes = env.to_jsonl().unwrap();
        let parsed = parse_envelope(&bytes).unwrap();
        assert_eq!(parsed.v, 1);
        assert_eq!(parsed.msg_type, TYPE_PAGE_STATE);
        assert_eq!(parsed.session_id.as_deref(), Some("sess-1"));
        assert_eq!(parsed.seq, Some(3));
        assert_eq!(parsed.typed(), Some(MsgType::PageState));
    }

    #[test]
    fn rejects_wrong_version() {
        let raw = br#"{"v":2,"type":"heartbeat","data":{}}"#;
        let err = parse_envelope(raw).unwrap_err();
        assert_eq!(err.code, "protocol_version");
    }

    #[test]
    fn rejects_oversize() {
        let err = parse_envelope(&vec![b'x'; MAX_MESSAGE_BYTES + 1]).unwrap_err();
        assert_eq!(err.code, "message_too_large");
    }

    #[test]
    fn selector_prefers_canonical_and_accepts_css_alias() {
        assert_eq!(
            selector_from_value(&json!({"selector": "#ok", "css": "#legacy"})).as_deref(),
            Some("#ok")
        );
        assert_eq!(
            selector_from_value(&json!({"css": "button.submit"})).as_deref(),
            Some("button.submit")
        );
        assert!(selector_from_value(&json!({"css": "div{color:red}"})).is_none());
        assert!(selector_from_value(&json!({"selector": ""})).is_none());
    }

    #[test]
    fn origin_helpers_reject_dangerous_schemes() {
        assert!(origin_of("file:///etc/passwd").is_none());
        assert!(origin_of("javascript:alert(1)").is_none());
        assert!(origin_of("data:text/html,hi").is_none());
        assert_eq!(
            origin_of("https://Example.COM/app?q=1").as_deref(),
            Some("https://example.com")
        );
        assert!(!origin_allowed(
            "https://evil.example",
            &["https://example.com".into()]
        ));
        assert!(origin_allowed(
            "https://example.com",
            &["https://example.com".into()]
        ));
    }

    #[test]
    fn sanitize_url_strips_secrets() {
        let u = sanitize_page_url("https://user:secret@example.com/p?token=abc&q=1#frag").unwrap();
        assert!(!u.contains("secret"));
        assert!(!u.contains("token=abc"));
        assert!(u.contains("q=1"));
        assert!(sanitize_page_url("javascript:alert(1)").is_err());
        assert!(sanitize_page_url("file:///etc/passwd").is_err());
    }

    #[test]
    fn redact_action_payload_strips_fill_text_and_css() {
        let raw = json!({
            "actions": [{
                "action": "fill",
                "selector": "#pw",
                "css": "#pw",
                "text": "super-secret-password"
            }]
        });
        let red = redact_action_payload(&raw);
        let a = &red["actions"][0];
        assert_eq!(a["text"], "[REDACTED]");
        assert_eq!(a["text_len"], 21);
        assert_eq!(a["selector"], "#pw");
        assert!(a.get("css").is_none(), "{a}");
        let blob = red.to_string();
        assert!(!blob.contains("super-secret-password"), "{blob}");

        let placeholder = json!({"action":"fill","selector":"#pw","text":"{{vars.PASSWORD}}"});
        let p = redact_action_payload(&placeholder);
        assert_eq!(p["text"], "{{vars.PASSWORD}}");
    }

    #[test]
    fn page_state_requires_allowlist_and_observation_id() {
        let allow = vec!["https://example.com".into()];
        let ok = PageState::from_data(
            &json!({
                "url": "https://example.com/app?token=leakme",
                "origin": "https://example.com",
                "title": "Dash",
                "viewport": {"width": 1280, "height": 720},
                "observation_id": "obs-1",
                "clickable": [{"tag": "button", "css": "#go", "text": "Go"}]
            }),
            &allow,
        )
        .unwrap();
        assert_eq!(ok.origin, "https://example.com");
        assert!(!ok.url.contains("leakme"));
        assert_eq!(ok.observation_id, "obs-1");
        assert_eq!(ok.clickable[0].selector.as_deref(), Some("#go"));

        let denied = PageState::from_data(
            &json!({
                "url": "https://evil.example/",
                "origin": "https://evil.example",
                "observation_id": "obs-2"
            }),
            &allow,
        )
        .unwrap_err();
        assert_eq!(denied.code, "origin_not_allowed");
    }

    #[test]
    fn redact_for_log_hides_token_cookie_password() {
        let s = redact_for_log(
            r#"pairing session_token="abcTOKEN" {"token":"sekrit","password":"hunter2","cookie":"sid=1"} Authorization: Bearer abcdef"#,
        );
        assert!(!s.contains("sekrit"), "{s}");
        assert!(!s.contains("hunter2"), "{s}");
        assert!(!s.contains("sid=1"), "{s}");
        assert!(s.contains("[REDACTED]"), "{s}");
    }

    #[test]
    fn redact_takeover_event_strips_password() {
        let raw = json!({
            "kind": "input",
            "selector": "#pw",
            "value": "hunter2-secret",
            "field": {"type": "password", "name": "password"}
        });
        let red = redact_takeover_event(&raw);
        assert_eq!(red["value"], "");
        assert_eq!(red["redacted"], true);
        assert!(!red.to_string().contains("hunter2-secret"));
        assert!(!is_raw_dom_event(&json!({"action":"click","selector":"#x"})));
        assert!(is_raw_dom_event(&json!({"kind":"click","selector":"#x"})));
    }

    #[test]
    fn pairing_accept_needs_nonce_and_role() {
        let err = pairing_accept_from_data(&json!({
            "pairing_id": "p",
            "code": "ABC",
            "role": "extension"
        }))
        .unwrap_err();
        assert_eq!(err.code, "invalid_pairing");

        let ok = pairing_accept_from_data(&json!({
            "session_token": "tok",
            "nonce": "n1",
            "role": "worker",
            "resume_from": 4
        }))
        .unwrap();
        assert_eq!(ok.role, ClientRole::Worker);
        assert_eq!(ok.resume_from, Some(4));
    }

    #[test]
    fn m2_types_parse_but_are_not_m1() {
        assert_eq!(
            MsgType::parse(TYPE_ACTION_REQUEST),
            Some(MsgType::ActionRequest)
        );
        assert!(!MsgType::ActionRequest.is_m1());
        assert!(MsgType::ActionRequest.is_m2());
        assert!(MsgType::PageState.is_m1());
        assert!(!MsgType::TakeoverStart.is_m1());
        assert!(MsgType::TakeoverStart.is_m3());
        assert!(!MsgType::Export.is_m1());
    }
}
