//! Typed JSONL event DTOs for `cloakcli teach chat --events`.
//!
//! The desktop crate does not path-dep on `cloakcli`. This schema must match
//! `src/teach_events.rs` (v=1). Unknown kinds and malformed JSON are rejected
//! and never forwarded to the WebView.

use serde::de::Error;
use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const EVENT_SCHEMA_V: u32 = 1;

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

const JOB_STATES: &[&str] = &["running", "done", "failed", "cancelled", "needs_confirm"];

/// Envelope for JSONL events. `flatten` + `deny_unknown_fields` cannot be
/// combined on this struct (`kind` would be treated as unknown); unknown keys
/// are rejected on `EventKind` and nested DTOs. `v` is required and must be 1.
#[derive(Debug, Clone, Serialize, PartialEq)]
pub struct WireEvent {
    pub v: u32,
    #[serde(flatten)]
    pub body: EventKind,
}

impl<'de> Deserialize<'de> for WireEvent {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        #[derive(Deserialize)]
        struct Helper {
            v: u32,
            #[serde(flatten)]
            body: EventKind,
        }
        let helper = Helper::deserialize(deserializer)?;
        if helper.v != EVENT_SCHEMA_V {
            return Err(D::Error::custom(format!(
                "unsupported event schema version {}",
                helper.v
            )));
        }
        Ok(WireEvent {
            v: helper.v,
            body: helper.body,
        })
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
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
        messages: Vec<ChatLineDto>,
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
#[serde(deny_unknown_fields)]
pub struct ToolStatusDto {
    pub summary: String,
    pub status: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct ChatLineDto {
    pub role: String,
    pub text: String,
}

/// Parse one JSONL event from the cloakcli child. Rejects unknown kinds,
/// extra fields, missing or wrong schema `v`, unknown job states, and
/// malformed JSON. `v` must be present and equal to 1 (no default).
pub fn parse_event_line(line: &str) -> Result<Value, String> {
    let v: Value = serde_json::from_str(line).map_err(|e| format!("malformed JSON: {e}"))?;
    let obj = v
        .as_object()
        .ok_or_else(|| "event must be a JSON object".to_string())?;
    let kind = obj
        .get("kind")
        .and_then(|k| k.as_str())
        .ok_or_else(|| "event missing kind".to_string())?;
    if !EVENT_KINDS.contains(&kind) {
        return Err(format!("unknown event kind: {kind}"));
    }
    let ver = match obj.get("v") {
        None => return Err("event missing schema version v".into()),
        Some(val) => val
            .as_u64()
            .ok_or_else(|| "event schema version v must be an integer".to_string())?,
    };
    if ver != EVENT_SCHEMA_V as u64 {
        return Err(format!("unsupported event schema version {ver}"));
    }
    if kind == "job" {
        if let Some(state) = obj.get("state").and_then(|s| s.as_str()) {
            if !JOB_STATES.contains(&state) {
                return Err(format!("unknown job state: {state}"));
            }
        } else {
            return Err("job missing state".into());
        }
        if obj.get("job_id").and_then(|s| s.as_str()).is_none() {
            return Err("job missing job_id".into());
        }
    }
    let _typed: WireEvent =
        serde_json::from_value(v.clone()).map_err(|e| format!("malformed {kind} event: {e}"))?;
    Ok(v)
}

pub fn bad_event_payload(message: &str) -> Value {
    serde_json::json!({
        "v": EVENT_SCHEMA_V,
        "kind": "error",
        "code": "bad_event",
        "message": message,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn accepts_known_kinds() {
        let session = r#"{"v":1,"kind":"session","session_id":"s","pairing_id":"p","pairing_code":"K7Q2MX","hub_url":"ws://127.0.0.1:1","profile":"demo","spawn_browser":false}"#;
        assert!(parse_event_line(session).is_ok());
        let delta = r#"{"v":1,"kind":"assistant_delta","role":"assistant","text":"He","seq":0,"done":false}"#;
        assert!(parse_event_line(delta).is_ok());
        let resume = r#"{"v":1,"kind":"resume","profile":"demo","messages":[{"role":"user","text":"hi"}],"tools":[],"phase":"chat","status":"restored","hub_resume":"new_hub","note":"n"}"#;
        assert!(parse_event_line(resume).is_ok());
    }

    #[test]
    fn rejects_unknown_and_malformed() {
        let err = parse_event_line(r#"{"v":1,"kind":"shell","cmd":"rm -rf"}"#).unwrap_err();
        assert!(err.contains("unknown event kind"), "{err}");
        assert!(parse_event_line("not-json").is_err());
        assert!(parse_event_line(r#"{"v":2,"kind":"user","role":"user","text":"x"}"#).is_err());
        assert!(
            parse_event_line(r#"{"v":1,"kind":"job","job_id":"x","state":"exploded","summary":"n"}"#)
                .is_err()
        );
        assert!(parse_event_line(r#"{"v":1,"kind":"user"}"#).is_err());
    }

    #[test]
    fn parse_event_line_strict_v1_schema() {
        let ok = parse_event_line(r#"{"v":1,"kind":"user","role":"user","text":"hello"}"#)
            .expect("valid v=1 must be accepted");
        assert_eq!(ok["v"], 1);
        assert_eq!(ok["kind"], "user");
        assert_eq!(ok["text"], "hello");

        let extra = parse_event_line(
            r#"{"v":1,"kind":"user","role":"user","text":"hello","extra":true}"#,
        )
        .unwrap_err();
        assert!(
            extra.contains("unknown field") || extra.contains("malformed"),
            "unknown field must be rejected: {extra}"
        );

        let missing = parse_event_line(r#"{"kind":"user","role":"user","text":"hello"}"#)
            .unwrap_err();
        assert!(
            missing.contains("missing") && missing.contains('v'),
            "missing v must be rejected: {missing}"
        );

        let wrong = parse_event_line(r#"{"v":2,"kind":"user","role":"user","text":"hello"}"#)
            .unwrap_err();
        assert!(
            wrong.contains("schema version"),
            "wrong v must be rejected: {wrong}"
        );
        let zero = parse_event_line(r#"{"v":0,"kind":"user","role":"user","text":"hello"}"#)
            .unwrap_err();
        assert!(
            zero.contains("schema version"),
            "v=0 must be rejected: {zero}"
        );
    }
}
