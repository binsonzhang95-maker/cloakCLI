use anyhow::{bail, Context, Result};
use chrono::Utc;
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};

use crate::state;
use crate::util::{self, redact_proxy};


/// CloakBrowser fingerprint seed range (inclusive), same as cloakbrowser.
const FINGERPRINT_SEED_MIN: u32 = 10000;
const FINGERPRINT_SEED_MAX: u32 = 99999;

/// Mint a seed in 10000..=99999 using UUID entropy (no extra rand crate).
fn mint_fingerprint_seed() -> u32 {
    let n = uuid::Uuid::new_v4().as_u128();
    let span = (FINGERPRINT_SEED_MAX - FINGERPRINT_SEED_MIN + 1) as u128;
    FINGERPRINT_SEED_MIN + (n % span) as u32
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Profile {
    pub name: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub proxy: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub notes: Option<String>,
    pub user_data_dir: String,
    pub created_at: String,
    /// Persistent CloakBrowser `--fingerprint=<seed>` (10000..=99999).
    /// Locked per profile so reopen keeps the same UA/GPU fingerprint.
    /// Old JSON without this field deserializes as None; Python
    /// `ensure_fingerprint_seed` / `--fresh-profile` (regenerate=True) backfills
    /// or voids it. There is no Rust wipe/fresh path — regenerate via Python.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fingerprint_seed: Option<u32>,
}

/// New layout: profiles/<name>/
pub fn profile_dir(root: &Path, name: &str) -> PathBuf {
    state::profiles_dir(root).join(name)
}

/// New layout metadata: profiles/<name>/profile.json
pub fn profile_json_path(root: &Path, name: &str) -> PathBuf {
    profile_dir(root, name).join("profile.json")
}

/// Legacy flat: profiles/<name>.json
fn flat_path(root: &Path, name: &str) -> PathBuf {
    state::profiles_dir(root).join(format!("{name}.json"))
}

/// Resolve existing metadata path (dir layout preferred, then flat).
fn meta_path_existing(root: &Path, name: &str) -> Option<PathBuf> {
    let dir_meta = profile_json_path(root, name);
    if dir_meta.is_file() {
        return Some(dir_meta);
    }
    let flat = flat_path(root, name);
    if flat.is_file() {
        return Some(flat);
    }
    None
}

/// Migrate flat profiles/<name>.json → profiles/<name>/profile.json on first write/cookie op.
/// Choice (documented in COOKIE-MVP.md): migrate on ensure_dir_layout (create/update,
/// all cookie ops, and before open), not eagerly on every list/get read.
pub fn ensure_dir_layout(root: &Path, name: &str) -> Result<PathBuf> {
    util::validate_name(name, "profile")?;
    let dir = profile_dir(root, name);
    let dest = profile_json_path(root, name);
    let flat = flat_path(root, name);

    if dest.is_file() {
        return Ok(dest);
    }
    if flat.is_file() {
        fs::create_dir_all(&dir)?;
        // Prefer rename (atomic-ish); fall back to copy+remove
        if fs::rename(&flat, &dest).is_err() {
            fs::copy(&flat, &dest)
                .with_context(|| format!("migrate copy {} → {}", flat.display(), dest.display()))?;
            fs::remove_file(&flat)?;
        }
        return Ok(dest);
    }
    // New empty dir for callers that will write
    fs::create_dir_all(&dir)?;
    Ok(dest)
}

fn write_profile(root: &Path, profile: &Profile) -> Result<()> {
    let path = ensure_dir_layout(root, &profile.name)?;
    let json = serde_json::to_string_pretty(profile)?;
    fs::write(&path, format!("{json}\n"))?;
    Ok(())
}

pub fn create(root: &Path, name: &str, proxy: Option<String>, notes: Option<String>) -> Result<Profile> {
    util::validate_name(name, "profile")?;
    let dir = state::profiles_dir(root);
    fs::create_dir_all(&dir)?;
    if meta_path_existing(root, name).is_some() {
        bail!("Profile already exists: {name}");
    }
    let udir = state::profile_user_data_dir(root, name);
    let _ = util::ensure_under_root(root, &udir)?;
    fs::create_dir_all(&udir)?;
    let profile = Profile {
        name: name.to_string(),
        proxy,
        notes,
        user_data_dir: udir.to_string_lossy().to_string(),
        created_at: Utc::now().to_rfc3339(),
        fingerprint_seed: Some(mint_fingerprint_seed()),
    };
    write_profile(root, &profile)?;
    Ok(profile)
}

/// Update proxy/notes on an existing profile (name immutable).
pub fn update(
    root: &Path,
    name: &str,
    proxy: Option<Option<String>>,
    notes: Option<Option<String>>,
) -> Result<Profile> {
    util::validate_name(name, "profile")?;
    let mut p = get(root, name)?;
    if let Some(px) = proxy {
        p.proxy = px;
    }
    if let Some(n) = notes {
        p.notes = n;
    }
    write_profile(root, &p)?;
    Ok(p)
}

pub fn list(root: &Path) -> Result<Vec<Profile>> {
    let dir = state::profiles_dir(root);
    if !dir.exists() {
        return Ok(vec![]);
    }
    let mut out = Vec::new();
    let mut seen = std::collections::HashSet::new();
    let mut entries: Vec<_> = fs::read_dir(&dir)?.filter_map(|e| e.ok()).collect();
    entries.sort_by_key(|e| e.file_name());
    for e in entries {
        let p = e.path();
        if p.is_dir() {
            let meta = p.join("profile.json");
            if meta.is_file() {
                match load_path(&meta) {
                    Ok(prof) => {
                        seen.insert(prof.name.clone());
                        out.push(prof);
                    }
                    Err(_) => continue,
                }
            }
            continue;
        }
        if p.extension().and_then(|x| x.to_str()) != Some("json") {
            continue;
        }
        // Skip accidental cookie.json at top level
        if p.file_name().and_then(|x| x.to_str()) == Some("cookie.json") {
            continue;
        }
        match load_path(&p) {
            Ok(prof) => {
                if seen.insert(prof.name.clone()) {
                    out.push(prof);
                }
            }
            Err(_) => continue,
        }
    }
    out.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(out)
}

pub fn get(root: &Path, name: &str) -> Result<Profile> {
    util::validate_name(name, "profile")?;
    let path = meta_path_existing(root, name).ok_or_else(|| anyhow::anyhow!("Profile not found: {name}"))?;
    load_path(&path)
}

pub fn delete(root: &Path, name: &str) -> Result<()> {
    util::validate_name(name, "profile")?;
    let dir_meta = profile_json_path(root, name);
    let flat = flat_path(root, name);
    let mut removed = false;
    if dir_meta.is_file() || profile_dir(root, name).is_dir() {
        let dir = profile_dir(root, name);
        if dir.is_dir() {
            fs::remove_dir_all(&dir)?;
            removed = true;
        }
    }
    if flat.is_file() {
        fs::remove_file(&flat)?;
        removed = true;
    }
    if !removed {
        bail!("Profile not found: {name}");
    }
    Ok(())
}

/// Profile for display/logs with proxy credentials redacted.
pub fn redacted_view(p: &Profile) -> serde_json::Value {
    serde_json::json!({
        "name": p.name,
        "proxy": p.proxy.as_ref().map(|s| redact_proxy(s)),
        "notes": p.notes,
        "user_data_dir": p.user_data_dir,
        "created_at": p.created_at,
        "fingerprint_seed": p.fingerprint_seed,
    })
}

pub fn display_proxy(p: &Profile) -> String {
    p.proxy
        .as_ref()
        .map(|s| redact_proxy(s))
        .unwrap_or_else(|| "-".into())
}

fn load_path(path: &Path) -> Result<Profile> {
    let text = fs::read_to_string(path).with_context(|| format!("read {}", path.display()))?;
    let mut profile: Profile = serde_json::from_str(&text)?;
    if profile.user_data_dir.is_empty() {
        // Infer root: .../profiles/<name>/profile.json → parent^2, or .../profiles/<name>.json → parent^1
        let root = if path.file_name().and_then(|s| s.to_str()) == Some("profile.json") {
            path.parent()
                .and_then(|p| p.parent())
                .and_then(|p| p.parent())
        } else {
            path.parent().and_then(|p| p.parent())
        };
        if let Some(root) = root {
            profile.user_data_dir = state::profile_user_data_dir(root, &profile.name)
                .to_string_lossy()
                .to_string();
        }
    }
    Ok(profile)
}
