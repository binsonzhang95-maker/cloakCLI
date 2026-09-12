//! Master-side fleet registry file (desired clients / defaults).
//! Live online state comes from the master hub (outbound client connections).

use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};

use crate::state;
use crate::util;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FleetClient {
    pub name: String,
    /// Master address clients should dial, or a note URL for operators.
    #[serde(default)]
    pub url: String,
    #[serde(default)]
    pub token: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub notes: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FleetConfig {
    #[serde(default = "default_concurrency")]
    pub default_concurrency: usize,
    #[serde(default)]
    pub default_headed: bool,
    #[serde(default)]
    pub clients: Vec<FleetClient>,
}

fn default_concurrency() -> usize {
    2
}

impl Default for FleetConfig {
    fn default() -> Self {
        Self {
            default_concurrency: 2,
            default_headed: false,
            clients: vec![FleetClient {
                name: "local".into(),
                url: "127.0.0.1:7750".into(),
                token: "dev-token".into(),
                notes: Some("expected client_id=local (outbound to master)".into()),
            }],
        }
    }
}

pub fn fleet_path(root: &Path) -> PathBuf {
    state::data_dir(root).join("fleet.json")
}

pub fn load(root: &Path) -> Result<FleetConfig> {
    let path = fleet_path(root);
    if !path.exists() {
        let cfg = FleetConfig::default();
        save(root, &cfg)?;
        return Ok(cfg);
    }
    let text = fs::read_to_string(&path).with_context(|| format!("read {}", path.display()))?;
    Ok(serde_json::from_str(&text).context("parse fleet.json")?)
}

pub fn save(root: &Path, cfg: &FleetConfig) -> Result<()> {
    fs::create_dir_all(state::data_dir(root))?;
    let path = fleet_path(root);
    fs::write(&path, format!("{}\n", serde_json::to_string_pretty(cfg)?))?;
    Ok(())
}

pub fn add_client(root: &Path, client: FleetClient) -> Result<FleetConfig> {
    util::validate_name(&client.name, "client")?;
    let mut cfg = load(root)?;
    if cfg.clients.iter().any(|c| c.name == client.name) {
        bail!("client already exists: {}", client.name);
    }
    cfg.clients.push(client);
    save(root, &cfg)?;
    Ok(cfg)
}

pub fn remove_client(root: &Path, name: &str) -> Result<FleetConfig> {
    let mut cfg = load(root)?;
    let before = cfg.clients.len();
    cfg.clients.retain(|c| c.name != name);
    if cfg.clients.len() == before {
        bail!("client not found: {name}");
    }
    save(root, &cfg)?;
    Ok(cfg)
}

pub fn update_defaults(
    root: &Path,
    concurrency: Option<usize>,
    headed: Option<bool>,
) -> Result<FleetConfig> {
    let mut cfg = load(root)?;
    if let Some(c) = concurrency {
        cfg.default_concurrency = c.max(1);
    }
    if let Some(h) = headed {
        cfg.default_headed = h;
    }
    save(root, &cfg)?;
    Ok(cfg)
}
