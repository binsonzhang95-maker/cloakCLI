//! Legacy inbound HTTP agent — deferred (prefer outbound `client connect`).
use anyhow::{bail, Result};
use std::path::PathBuf;

pub struct AgentConfig {
    pub root: PathBuf,
    pub bind: String,
    pub token: String,
}

pub fn serve(_cfg: AgentConfig) -> Result<()> {
    bail!(
        "HTTP client agent deferred in this build (rustc 1.85 / dep pins). \
         Use outbound mode instead:\n  cloakcli client connect --master 127.0.0.1:7750 --id box1"
    )
}
