//! Versioned JSON messages for master ↔ client (outbound long-lived TCP).
//! MVP stub — TCP JSONL now; schema ready for WebSocket/TLS later.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

pub const PROTOCOL_VERSION: u32 = 1;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Envelope {
    pub v: u32,
    #[serde(rename = "type")]
    pub msg_type: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub request_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub client_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ts: Option<i64>,
    #[serde(default)]
    pub data: Value,
}

impl Envelope {
    pub fn new(msg_type: &str) -> Self {
        Self {
            v: PROTOCOL_VERSION,
            msg_type: msg_type.into(),
            request_id: None,
            client_id: None,
            ts: Some(chrono::Utc::now().timestamp()),
            data: json!({}),
        }
    }

    pub fn with_client(mut self, id: &str) -> Self {
        self.client_id = Some(id.into());
        self
    }

    pub fn with_request(mut self, id: &str) -> Self {
        self.request_id = Some(id.into());
        self
    }

    pub fn with_data(mut self, data: Value) -> Self {
        self.data = data;
        self
    }
}

/// Desired fleet config revision (master) vs observed (client).
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ConfigRevision {
    pub revision: u64,
    #[serde(default)]
    pub concurrency: usize,
    #[serde(default)]
    pub headed: bool,
    #[serde(default)]
    pub labels: Vec<String>,
}
