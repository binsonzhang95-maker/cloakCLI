//! Skill package publish, tar+SHA-256 digest, safe extract, and client install cache.
//!
//! Layout: `skills/<name>/skill.json` + optional `manifest.json` + optional `scripts/`.
//! Master stores releases under `data/skill_releases/`. Clients install into an
//! immutable `data/skill_cache/<skill_id>/<digest>/` and ACK the digest.
//!
//! Secrets: names/refs only — never values in packages or job JSON.

use anyhow::{bail, Context, Result};
use base64::{engine::general_purpose::STANDARD as B64, Engine as _};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::fs::{self, File};
use std::io::{Cursor, Read, Write};
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::process::Command;
use tokio::time::timeout;

use crate::skills;
use crate::state;
use crate::util;

pub const MAX_PACKAGE_BYTES: u64 = 8 * 1024 * 1024;
pub const MAX_PACKAGE_FILES: usize = 512;

const SKIP_DIR_NAMES: &[&str] = &["__pycache__", ".git", ".tmp"];
const SKIP_FILE_EXACT: &[&str] = &[
    "secrets.json",
    ".env",
    ".ds_store",
    "cookie.json",
    "cookies.json",
];

/// One declared terminal status for a skill. Unique `id` is the only wire value.
/// `success` / `retryable` / `label` are taken from this declaration, never from the runner.
/// `optional` is display-only and does not affect success counting.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct SkillStatusDecl {
    pub id: String,
    pub success: bool,
    pub retryable: bool,
    pub label: String,
    #[serde(default)]
    pub optional: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct SkillManifest {
    pub version: String,
    pub entry: SkillEntry,
    /// Secret *names* only (never values).
    #[serde(default)]
    pub secrets: Vec<String>,
    /// Absent field → legacy `ok|failed|cancelled`. Present empty/illegal array → reject.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub statuses: Option<Vec<SkillStatusDecl>>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct SkillEntry {
    pub kind: SkillEntryKind,
    /// Package-relative path; required when `kind == python_runner`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub path: Option<String>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum SkillEntryKind {
    SkillSteps,
    PythonRunner,
}

impl SkillEntryKind {
    pub fn as_str(self) -> &'static str {
        match self {
            SkillEntryKind::SkillSteps => "skill_steps",
            SkillEntryKind::PythonRunner => "python_runner",
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ReleaseRecord {
    pub skill_id: String,
    pub version: String,
    pub digest: String,
    /// Path relative to `data/skill_releases/`.
    pub path: String,
    pub published: bool,
    pub created_at: i64,
    pub entry: String,
    #[serde(default)]
    pub secret_names: Vec<String>,
    /// Snapshot of the packed manifest's `statuses` (None = legacy three-state).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub statuses: Option<Vec<SkillStatusDecl>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InstalledSkill {
    pub skill_id: String,
    pub version: String,
    pub digest: String,
    #[serde(default)]
    pub installed_at: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct Catalog {
    #[serde(default)]
    releases: Vec<ReleaseRecord>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
struct InstallIndex {
    #[serde(default)]
    skills: HashMap<String, InstalledSkill>,
}

pub fn sha256_hex(bytes: &[u8]) -> String {
    let mut h = Sha256::new();
    h.update(bytes);
    format!("{:x}", h.finalize())
}

pub fn normalize_digest(raw: &str) -> Result<String> {
    let s = raw.trim();
    let s = s
        .strip_prefix("sha256:")
        .or_else(|| s.strip_prefix("SHA256:"))
        .unwrap_or(s);
    let s = s.to_ascii_lowercase();
    if s.len() != 64 || !s.chars().all(|c| c.is_ascii_hexdigit()) {
        bail!("invalid SHA-256 digest (want 64 hex chars, optional sha256: prefix)");
    }
    Ok(s)
}

pub fn verify_digest(bytes: &[u8], digest: &str) -> Result<()> {
    let want = normalize_digest(digest)?;
    let got = sha256_hex(bytes);
    if got != want {
        bail!("package digest mismatch: got {got}, want {want}");
    }
    Ok(())
}

pub fn encode_package_b64(bytes: &[u8]) -> String {
    B64.encode(bytes)
}

pub fn decode_package_b64(s: &str) -> Result<Vec<u8>> {
    B64.decode(s.trim().as_bytes())
        .map_err(|e| anyhow::anyhow!("invalid package_b64: {e}"))
}

pub fn releases_dir(root: &Path) -> PathBuf {
    state::data_dir(root).join("skill_releases")
}

pub fn cache_root(root: &Path) -> PathBuf {
    state::data_dir(root).join("skill_cache")
}

pub fn cache_dir(root: &Path, skill_id: &str, digest: &str) -> PathBuf {
    cache_root(root).join(skill_id).join(digest)
}

fn catalog_path(root: &Path) -> PathBuf {
    releases_dir(root).join("catalog.json")
}

fn install_index_path(root: &Path) -> PathBuf {
    state::data_dir(root).join("skill_installs.json")
}

pub fn default_manifest(version: &str) -> SkillManifest {
    SkillManifest {
        version: version.to_string(),
        entry: SkillEntry {
            kind: SkillEntryKind::SkillSteps,
            path: None,
        },
        secrets: vec![],
        statuses: None,
    }
}

pub fn load_manifest_file(skill_dir: &Path) -> Result<Option<SkillManifest>> {
    let p = skill_dir.join("manifest.json");
    if !p.is_file() {
        return Ok(None);
    }
    let text = fs::read_to_string(&p).with_context(|| format!("read {}", p.display()))?;
    let m: SkillManifest = serde_json::from_str(&text)
        .with_context(|| format!("invalid manifest.json {}", p.display()))?;
    validate_manifest(&m, Some(skill_dir))?;
    Ok(Some(m))
}

pub fn validate_manifest(m: &SkillManifest, skill_dir: Option<&Path>) -> Result<()> {
    validate_version(&m.version)?;
    match m.entry.kind {
        SkillEntryKind::SkillSteps => {
            if let Some(p) = &m.entry.path {
                if !p.is_empty() {
                    validate_rel_path(p, "skill_steps path")?;
                }
            }
        }
        SkillEntryKind::PythonRunner => {
            let p = m
                .entry
                .path
                .as_deref()
                .map(str::trim)
                .filter(|s| !s.is_empty())
                .ok_or_else(|| anyhow::anyhow!("python_runner entry requires relative path"))?;
            validate_rel_path(p, "python_runner path")?;
            if !p.ends_with(".py") {
                bail!("python_runner path must be a .py file (got {p})");
            }
            if let Some(dir) = skill_dir {
                let full = dir.join(p);
                if !full.is_file() {
                    bail!("python_runner path does not exist: {p}");
                }
                let meta = fs::symlink_metadata(&full)?;
                if meta.file_type().is_symlink() {
                    bail!("python_runner path must not be a symlink: {p}");
                }
            }
        }
    }
    for name in &m.secrets {
        util::validate_name(name, "secret")?;
    }
    validate_status_decls(m.statuses.as_deref())?;
    Ok(())
}

/// Validate `manifest.statuses`. Missing (`None`) is legacy and allowed.
/// An empty array or illegal entries must reject publish/install — no silent fallback.
pub fn validate_status_decls(statuses: Option<&[SkillStatusDecl]>) -> Result<()> {
    let Some(list) = statuses else {
        return Ok(());
    };
    if list.is_empty() {
        bail!(
            "statuses must be a non-empty array (omit the field for legacy ok|failed|cancelled)"
        );
    }
    let mut seen = std::collections::HashSet::new();
    for s in list {
        let id = s.id.trim();
        if id.is_empty() || id != s.id {
            bail!("status id must be non-empty and must not have surrounding whitespace");
        }
        util::validate_name(id, "status id")?;
        if !seen.insert(id.to_string()) {
            bail!("duplicate status id '{id}'");
        }
        if s.label.trim().is_empty() {
            bail!("status '{id}' label must be non-empty");
        }
    }
    Ok(())
}

/// Look up the status declaration that was packed with this exact digest.
/// Never falls back to the latest published version or a local same-name skill.
pub fn statuses_for_digest(
    root: &Path,
    skill_id: &str,
    digest: &str,
) -> Result<Option<Vec<SkillStatusDecl>>> {
    util::validate_name(skill_id, "skill")?;
    let digest = normalize_digest(digest)?;
    let cat = load_catalog(root)?;
    let mut matches: Vec<&ReleaseRecord> = cat
        .releases
        .iter()
        .filter(|r| r.skill_id == skill_id && r.digest == digest)
        .collect();
    if matches.is_empty() {
        bail!("no release for skill '{skill_id}' digest {digest} (refusing latest/local fallback)");
    }
    matches.sort_by_key(|r| r.created_at);
    Ok(matches.last().unwrap().statuses.clone())
}

/// Built-in three-state used only when `manifest.statuses` is omitted.
pub fn legacy_status_decls() -> Vec<SkillStatusDecl> {
    vec![
        SkillStatusDecl {
            id: "ok".into(),
            success: true,
            retryable: false,
            label: "ok".into(),
            optional: false,
        },
        SkillStatusDecl {
            id: "failed".into(),
            success: false,
            retryable: false,
            label: "failed".into(),
            optional: false,
        },
        SkillStatusDecl {
            id: "cancelled".into(),
            success: false,
            retryable: false,
            label: "cancelled".into(),
            optional: false,
        },
    ]
}

pub fn validate_version(v: &str) -> Result<()> {
    util::validate_name(v, "skill version")
}

fn validate_rel_path(p: &str, kind: &str) -> Result<()> {
    if p.is_empty() {
        bail!("empty {kind}");
    }
    if p.starts_with('/') || p.starts_with('\\') {
        bail!("{kind} must be package-relative (got {p})");
    }
    let path = Path::new(p);
    if path.is_absolute() {
        bail!("{kind} must be package-relative (got {p})");
    }
    for c in path.components() {
        match c {
            Component::Normal(s) => {
                let s = s.to_string_lossy();
                if s == ".." || s.contains('\0') {
                    bail!("illegal component in {kind}: {p}");
                }
            }
            Component::CurDir => {}
            _ => bail!("illegal component in {kind}: {p}"),
        }
    }
    if p.contains("..") {
        bail!("{kind} must not contain '..' (got {p})");
    }
    Ok(())
}

fn skip_pack_name(name: &str) -> bool {
    let lower = name.to_ascii_lowercase();
    if SKIP_DIR_NAMES.iter().any(|s| lower == *s) {
        return true;
    }
    if SKIP_FILE_EXACT.iter().any(|s| lower == *s) {
        return true;
    }
    if lower.ends_with(".pyc")
        || lower.ends_with(".secret")
        || lower.ends_with(".env")
        || lower.starts_with(".tmp-")
    {
        return true;
    }
    false
}

fn collect_pack_files(dir: &Path, out: &mut Vec<PathBuf>) -> Result<()> {
    if out.len() >= MAX_PACKAGE_FILES {
        bail!("skill package has too many files (max {MAX_PACKAGE_FILES})");
    }
    for e in fs::read_dir(dir)? {
        let e = e?;
        let name = e.file_name();
        let name_s = name.to_string_lossy();
        if skip_pack_name(&name_s) {
            continue;
        }
        let ft = e.file_type()?;
        if ft.is_symlink() {
            bail!(
                "refusing symlink in skill package: {}",
                e.path().display()
            );
        }
        if ft.is_dir() {
            collect_pack_files(&e.path(), out)?;
        } else if ft.is_file() {
            out.push(e.path());
        } else {
            bail!("refusing special file in skill package: {}", e.path().display());
        }
        if out.len() > MAX_PACKAGE_FILES {
            bail!("skill package has too many files (max {MAX_PACKAGE_FILES})");
        }
    }
    Ok(())
}

fn load_catalog(root: &Path) -> Result<Catalog> {
    let p = catalog_path(root);
    if !p.exists() {
        return Ok(Catalog::default());
    }
    let text = fs::read_to_string(&p).with_context(|| format!("read {}", p.display()))?;
    Ok(serde_json::from_str(&text).unwrap_or_default())
}

fn save_catalog(root: &Path, cat: &Catalog) -> Result<()> {
    let dir = releases_dir(root);
    fs::create_dir_all(&dir)?;
    let p = catalog_path(root);
    fs::write(p, format!("{}\n", serde_json::to_string_pretty(cat)?))?;
    Ok(())
}

pub fn list_releases(root: &Path) -> Result<Vec<ReleaseRecord>> {
    Ok(load_catalog(root)?.releases)
}

pub fn resolve_published(
    root: &Path,
    skill_id: &str,
    version: Option<&str>,
    digest: Option<&str>,
) -> Result<ReleaseRecord> {
    util::validate_name(skill_id, "skill")?;
    let cat = load_catalog(root)?;
    let digest = match digest {
        Some(d) if !d.is_empty() => Some(normalize_digest(d)?),
        _ => None,
    };
    let mut matches: Vec<&ReleaseRecord> = cat
        .releases
        .iter()
        .filter(|r| r.skill_id == skill_id && r.published)
        .collect();
    if matches.is_empty() {
        bail!("no published release for skill '{skill_id}' (pack + publish first)");
    }
    if let Some(v) = version {
        validate_version(v)?;
        matches.retain(|r| r.version == v);
        if matches.is_empty() {
            bail!("no published release for skill '{skill_id}' version '{v}'");
        }
    }
    if let Some(d) = digest.as_deref() {
        matches.retain(|r| r.digest == d);
        if matches.is_empty() {
            bail!("digest does not match any published release of '{skill_id}'");
        }
    }
    matches.sort_by_key(|r| r.created_at);
    Ok((*matches.last().unwrap()).clone())
}

pub fn read_package_bytes(root: &Path, rec: &ReleaseRecord) -> Result<Vec<u8>> {
    let path = releases_dir(root).join(&rec.path);
    let bytes = fs::read(&path).with_context(|| format!("read {}", path.display()))?;
    if bytes.len() as u64 > MAX_PACKAGE_BYTES {
        bail!(
            "stored package exceeds size limit ({} > {MAX_PACKAGE_BYTES})",
            bytes.len()
        );
    }
    verify_digest(&bytes, &rec.digest)?;
    Ok(bytes)
}

pub fn pack_skill(
    root: &Path,
    name: &str,
    version_override: Option<&str>,
    publish: bool,
) -> Result<ReleaseRecord> {
    let skill = skills::get(root, name)?;
    let skill_dir = skill.path.clone();
    let sj = skill_dir.join("skill.json");
    if !sj.is_file() {
        bail!("missing skill.json in {}", skill_dir.display());
    }
    let skill_json: Value = serde_json::from_str(&fs::read_to_string(&sj)?)?;
    skills::assert_no_plaintext_secrets(&skill_json)?;

    let mut manifest = load_manifest_file(&skill_dir)?.unwrap_or_else(|| default_manifest("0.1.0"));
    if let Some(v) = version_override {
        validate_version(v)?;
        manifest.version = v.to_string();
    }
    validate_manifest(&manifest, Some(&skill_dir))?;

    let tar_bytes = pack_dir_to_tar(&skill_dir, &manifest)?;
    if tar_bytes.len() as u64 > MAX_PACKAGE_BYTES {
        bail!(
            "packed skill exceeds size limit ({} > {MAX_PACKAGE_BYTES})",
            tar_bytes.len()
        );
    }
    let digest = sha256_hex(&tar_bytes);

    let mut cat = load_catalog(root)?;
    if let Some(existing) = cat
        .releases
        .iter()
        .find(|r| r.skill_id == skill.name && r.version == manifest.version)
    {
        if existing.published && existing.digest != digest && publish {
            bail!(
                "version {} of '{}' already published with digest {}; bump --version",
                manifest.version,
                skill.name,
                existing.digest
            );
        }
        if existing.published && existing.digest == digest {
            return Ok(existing.clone());
        }
    }

    let rel = format!("{}/{}/package.tar", skill.name, manifest.version);
    let dest_dir = releases_dir(root)
        .join(&skill.name)
        .join(&manifest.version);
    fs::create_dir_all(&dest_dir)?;
    let dest = dest_dir.join("package.tar");
    fs::write(&dest, &tar_bytes)?;

    let rec = ReleaseRecord {
        skill_id: skill.name.clone(),
        version: manifest.version.clone(),
        digest,
        path: rel,
        published: publish,
        created_at: chrono::Utc::now().timestamp(),
        entry: manifest.entry.kind.as_str().to_string(),
        secret_names: manifest.secrets.clone(),
        statuses: manifest.statuses.clone(),
    };
    fs::write(
        dest_dir.join("release.json"),
        format!("{}\n", serde_json::to_string_pretty(&rec)?),
    )?;

    cat.releases
        .retain(|r| !(r.skill_id == rec.skill_id && r.version == rec.version));
    cat.releases.push(rec.clone());
    save_catalog(root, &cat)?;
    Ok(rec)
}

pub fn publish_skill(root: &Path, name: &str, version: Option<&str>) -> Result<ReleaseRecord> {
    util::validate_name(name, "skill")?;
    if let Some(v) = version {
        validate_version(v)?;
    }
    let mut cat = load_catalog(root)?;
    let idx = {
        let mut cands: Vec<usize> = cat
            .releases
            .iter()
            .enumerate()
            .filter(|(_, r)| r.skill_id == name)
            .filter(|(_, r)| version.map(|v| r.version == v).unwrap_or(true))
            .map(|(i, _)| i)
            .collect();
        if cands.is_empty() {
            bail!(
                "no packed release for skill '{name}'{}",
                version
                    .map(|v| format!(" version '{v}'"))
                    .unwrap_or_default()
            );
        }
        cands.sort_by_key(|i| cat.releases[*i].created_at);
        *cands.last().unwrap()
    };
    cat.releases[idx].published = true;
    let rec = cat.releases[idx].clone();
    let release_json = releases_dir(root)
        .join(&rec.skill_id)
        .join(&rec.version)
        .join("release.json");
    if release_json.parent().map(|p| p.exists()).unwrap_or(false) {
        let _ = fs::write(
            &release_json,
            format!("{}\n", serde_json::to_string_pretty(&rec)?),
        );
    }
    save_catalog(root, &cat)?;
    Ok(rec)
}

fn pack_dir_to_tar(skill_dir: &Path, manifest: &SkillManifest) -> Result<Vec<u8>> {
    let mut files = Vec::new();
    collect_pack_files(skill_dir, &mut files)?;
    files.sort();
    let mut builder = tar::Builder::new(Vec::new());
    builder.mode(tar::HeaderMode::Deterministic);

    let mut packed_manifest = false;
    for abs in &files {
        let rel = abs.strip_prefix(skill_dir).unwrap_or(abs);
        let rel_s = rel.to_string_lossy().replace('\\', "/");
        if rel_s == "manifest.json" {
            packed_manifest = true;
            continue; // always write canonical manifest below
        }
        validate_rel_path(&rel_s, "package member")?;
        let meta = fs::metadata(abs)?;
        if meta.len() > MAX_PACKAGE_BYTES {
            bail!("file {} exceeds package size limit", rel_s);
        }
        let mut f = File::open(abs).with_context(|| format!("open {}", abs.display()))?;
        let mut header = tar::Header::new_gnu();
        header.set_size(meta.len());
        header.set_mode(0o644);
        header.set_mtime(0);
        header.set_uid(0);
        header.set_gid(0);
        header.set_cksum();
        builder.append_data(&mut header, &rel_s, &mut f)?;
    }

    let manifest_bytes = format!("{}\n", serde_json::to_string_pretty(manifest)?).into_bytes();
    let mut header = tar::Header::new_gnu();
    header.set_size(manifest_bytes.len() as u64);
    header.set_mode(0o644);
    header.set_mtime(0);
    header.set_uid(0);
    header.set_gid(0);
    header.set_cksum();
    builder.append_data(
        &mut header,
        "manifest.json",
        Cursor::new(manifest_bytes),
    )?;
    let _ = packed_manifest;

    let bytes = builder.into_inner()?;
    Ok(bytes)
}

/// Safe extract: regular files + directories only. Rejects `..`, absolute paths, links.
pub fn unpack_tar_safe(bytes: &[u8], dest: &Path) -> Result<()> {
    if bytes.len() as u64 > MAX_PACKAGE_BYTES {
        bail!("package exceeds size limit ({} bytes)", bytes.len());
    }
    fs::create_dir_all(dest)?;
    let dest_canon = dest
        .canonicalize()
        .with_context(|| format!("canonicalize {}", dest.display()))?;

    let mut archive = tar::Archive::new(Cursor::new(bytes));
    archive.set_overwrite(false);
    archive.set_preserve_permissions(false);
    archive.set_preserve_mtime(false);

    let mut total: u64 = 0;
    let mut n_files: usize = 0;
    let mut saw_skill_json = false;

    for entry in archive.entries().context("tar entries")? {
        let mut entry = entry.context("tar entry")?;
        let kind = entry.header().entry_type();
        if kind.is_symlink() || kind.is_hard_link() {
            bail!("refusing link in skill package");
        }
        if !kind.is_file() && !kind.is_dir() {
            bail!("refusing tar entry type {kind:?} (only regular files/dirs allowed)");
        }
        let rel = entry.path().context("tar path")?.into_owned();
        let rel_s = rel.to_string_lossy();
        if rel_s.is_empty() || rel_s == "." {
            continue;
        }
        if rel.is_absolute() || rel_s.starts_with('/') || rel_s.starts_with('\\') {
            bail!("refusing absolute path in skill package: {rel_s}");
        }
        for c in rel.components() {
            match c {
                Component::Normal(s) => {
                    if s == ".." || s.to_string_lossy().contains('\0') {
                        bail!("refusing illegal path in skill package: {rel_s}");
                    }
                }
                Component::CurDir => {}
                _ => bail!("refusing illegal path in skill package: {rel_s}"),
            }
        }
        if rel_s.contains("..") {
            bail!("refusing path traversal in skill package: {rel_s}");
        }
        let size = entry.header().size().unwrap_or(0);
        total = total.saturating_add(size);
        if total > MAX_PACKAGE_BYTES {
            bail!("uncompressed package exceeds size limit ({total} > {MAX_PACKAGE_BYTES})");
        }
        if kind.is_file() {
            n_files += 1;
            if n_files > MAX_PACKAGE_FILES {
                bail!("too many files in package (max {MAX_PACKAGE_FILES})");
            }
        }
        let out = dest_canon.join(&rel);
        let parent = out.parent().unwrap_or(&dest_canon);
        fs::create_dir_all(parent)?;
        // After create, ensure we did not escape dest (symlink parents).
        let parent_canon = parent
            .canonicalize()
            .with_context(|| format!("canonicalize {}", parent.display()))?;
        if !parent_canon.starts_with(&dest_canon) {
            bail!("extract escaped install dir: {}", out.display());
        }
        if kind.is_dir() {
            fs::create_dir_all(&out)?;
            continue;
        }
        let lower_name = out
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or("")
            .to_ascii_lowercase();
        if SKIP_FILE_EXACT.contains(&lower_name.as_str()) || lower_name.ends_with(".secret") {
            bail!("refusing secret-like file in package: {rel_s}");
        }
        if rel_s == "skill.json" || rel_s.ends_with("/skill.json") {
            saw_skill_json = true;
        }
        let mut f = File::create(&out).with_context(|| format!("create {}", out.display()))?;
        let mut buf = Vec::new();
        entry.read_to_end(&mut buf)?;
        if buf.len() as u64 > MAX_PACKAGE_BYTES {
            bail!("file {rel_s} exceeds size limit");
        }
        f.write_all(&buf)?;
    }
    if !saw_skill_json {
        bail!("package missing skill.json");
    }
    Ok(())
}

pub fn list_installed(root: &Path) -> Result<Vec<InstalledSkill>> {
    let idx = load_install_index(root)?;
    let mut v: Vec<_> = idx.skills.into_values().collect();
    v.sort_by(|a, b| a.skill_id.cmp(&b.skill_id));
    Ok(v)
}

fn load_install_index(root: &Path) -> Result<InstallIndex> {
    let p = install_index_path(root);
    if !p.exists() {
        return Ok(InstallIndex::default());
    }
    let text = fs::read_to_string(&p).with_context(|| format!("read {}", p.display()))?;
    Ok(serde_json::from_str(&text).unwrap_or_default())
}

fn save_install_index(root: &Path, idx: &InstallIndex) -> Result<()> {
    fs::create_dir_all(state::data_dir(root))?;
    fs::write(
        install_index_path(root),
        format!("{}\n", serde_json::to_string_pretty(idx)?),
    )?;
    Ok(())
}

/// Stage → verify → atomic rename into content-addressed cache. Failure leaves previous install.
pub fn install_package(
    root: &Path,
    skill_id: &str,
    version: &str,
    digest: &str,
    tar_bytes: &[u8],
) -> Result<InstalledSkill> {
    util::validate_name(skill_id, "skill")?;
    validate_version(version)?;
    let digest = normalize_digest(digest)?;
    verify_digest(tar_bytes, &digest)?;
    if tar_bytes.len() as u64 > MAX_PACKAGE_BYTES {
        bail!("package exceeds size limit");
    }

    let dest = cache_dir(root, skill_id, &digest);
    if dest.join("skill.json").is_file() {
        // Idempotent retry: already installed this digest.
        let rec = InstalledSkill {
            skill_id: skill_id.to_string(),
            version: version.to_string(),
            digest: digest.clone(),
            installed_at: chrono::Utc::now().timestamp(),
        };
        write_current_pointer(root, &rec)?;
        return Ok(rec);
    }

    let cache_skill = cache_root(root).join(skill_id);
    fs::create_dir_all(&cache_skill)?;
    let staging = cache_skill.join(format!(".tmp-{}", uuid::Uuid::new_v4().simple()));
    fs::create_dir_all(&staging)?;
    struct StagingGuard(PathBuf);
    impl Drop for StagingGuard {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }
    let guard = StagingGuard(staging.clone());

    unpack_tar_safe(tar_bytes, &staging)?;
    let sj = staging.join("skill.json");
    if !sj.is_file() {
        bail!("extracted package missing skill.json");
    }
    let skill_json: Value = serde_json::from_str(&fs::read_to_string(&sj)?)?;
    skills::assert_no_plaintext_secrets(&skill_json)?;
    if let Some(m) = load_manifest_file(&staging)? {
        if m.version != version {
            bail!(
                "manifest version {} does not match sync version {version}",
                m.version
            );
        }
        validate_manifest(&m, Some(&staging))?;
    }

    fs::write(staging.join(".digest"), format!("{digest}\n"))?;
    fs::write(
        staging.join("release.json"),
        format!(
            "{}\n",
            serde_json::to_string_pretty(&json!({
                "skill_id": skill_id,
                "version": version,
                "digest": digest,
            }))?
        ),
    )?;

    if dest.exists() {
        bail!("cache dest exists but is incomplete: {}", dest.display());
    }
    fs::rename(&staging, &dest).with_context(|| {
        format!(
            "atomic install {} → {}",
            staging.display(),
            dest.display()
        )
    })?;
    std::mem::forget(guard);

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(&dest, fs::Permissions::from_mode(0o555));
    }

    let rec = InstalledSkill {
        skill_id: skill_id.to_string(),
        version: version.to_string(),
        digest,
        installed_at: chrono::Utc::now().timestamp(),
    };
    write_current_pointer(root, &rec)?;
    Ok(rec)
}

fn write_current_pointer(root: &Path, rec: &InstalledSkill) -> Result<()> {
    let mut idx = load_install_index(root)?;
    idx.skills.insert(rec.skill_id.clone(), rec.clone());
    save_install_index(root, &idx)
}

/// Resolve an installed digest cache. Does **not** fall back to `skills/<name>/`.
pub fn lookup_installed(root: &Path, skill_id: &str, digest: &str) -> Result<PathBuf> {
    util::validate_name(skill_id, "skill")?;
    let digest = normalize_digest(digest)?;
    let dir = cache_dir(root, skill_id, &digest);
    let sj = dir.join("skill.json");
    if !sj.is_file() {
        bail!(
            "skill '{skill_id}' digest {digest} is not installed on this client (no local same-name fallback)"
        );
    }
    let sidecar = dir.join(".digest");
    if sidecar.is_file() {
        let got = fs::read_to_string(&sidecar).unwrap_or_default();
        let got = normalize_digest(got.trim()).unwrap_or_default();
        if got != digest {
            bail!("installed cache digest sidecar mismatch for '{skill_id}'");
        }
    }
    Ok(dir)
}

pub fn installed_for_hello(root: &Path) -> Vec<Value> {
    match list_installed(root) {
        Ok(list) => list
            .into_iter()
            .map(|s| {
                json!({
                    "skill_id": s.skill_id,
                    "version": s.version,
                    "digest": s.digest,
                })
            })
            .collect(),
        Err(_) => vec![],
    }
}

pub fn parse_installed_list(v: &Value) -> HashMap<String, InstalledSkill> {
    let mut map = HashMap::new();
    let Some(arr) = v.as_array() else {
        return map;
    };
    for item in arr {
        let skill_id = item
            .get("skill_id")
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .to_string();
        let version = item
            .get("version")
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .to_string();
        let digest = item
            .get("digest")
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .to_string();
        if skill_id.is_empty() || digest.is_empty() {
            continue;
        }
        let Ok(digest) = normalize_digest(&digest) else {
            continue;
        };
        map.insert(
            skill_id.clone(),
            InstalledSkill {
                skill_id,
                version,
                digest,
                installed_at: 0,
            },
        );
    }
    map
}

pub fn python_interpreter() -> Result<String> {
    if let Ok(bin) = std::env::var("CLOAKCLI_PYTHON") {
        if !bin.is_empty() {
            return Ok(bin);
        }
    }
    for name in ["python3", "python"] {
        if which::which(name).is_ok() {
            return Ok(name.to_string());
        }
    }
    bail!("python3 not found; set CLOAKCLI_PYTHON");
}

fn strip_secret_env(cmd: &mut Command) {
    for k in [
        "CLOAKCLI_LLM_API_KEY",
        "OPENAI_API_KEY",
        "CLOAKCLI_MASTER_TOKEN",
        "CLOAKCLI_CLIENT_TOKEN",
        "ANTHROPIC_API_KEY",
    ] {
        cmd.env_remove(k);
    }
}

/// Run a published package-relative Python file. Argv array only — never shell.
pub async fn run_python_runner(
    pkg_dir: &Path,
    rel_path: &str,
    stdin_payload: &Value,
    cancel: Option<Arc<AtomicBool>>,
    run_timeout: Duration,
) -> Result<Value> {
    skills::assert_no_plaintext_secrets(stdin_payload)?;
    validate_rel_path(rel_path, "python_runner path")?;
    let script = pkg_dir.join(rel_path);
    let pkg_canon = pkg_dir
        .canonicalize()
        .with_context(|| format!("canonicalize {}", pkg_dir.display()))?;
    let script_canon = script
        .canonicalize()
        .with_context(|| format!("canonicalize runner {}", script.display()))?;
    if !script_canon.starts_with(&pkg_canon) {
        bail!("python_runner path escapes package dir");
    }
    let meta = fs::symlink_metadata(&script_canon)?;
    if meta.file_type().is_symlink() || !meta.file_type().is_file() {
        bail!("python_runner must be a regular file");
    }

    let python = python_interpreter()?;
    let mut cmd = Command::new(&python);
    cmd.arg(&script_canon)
        .current_dir(&pkg_canon)
        .stdin(std::process::Stdio::piped())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .kill_on_drop(true)
        .env_remove("PYTHONSTARTUP");
    strip_secret_env(&mut cmd);
    #[cfg(unix)]
    {
        cmd.process_group(0);
    }

    let mut child = cmd
        .spawn()
        .with_context(|| format!("spawn {python} {}", script_canon.display()))?;
    if let Some(mut stdin) = child.stdin.take() {
        let body = serde_json::to_vec(stdin_payload)?;
        stdin.write_all(&body).await?;
        stdin.write_all(b"\n").await?;
        stdin.shutdown().await.ok();
    }
    let pid = child.id();
    let mut stdout_pipe = child.stdout.take();
    let mut stderr_pipe = child.stderr.take();

    let wait_fut = async {
        loop {
            if let Some(flag) = &cancel {
                if flag.load(Ordering::SeqCst) {
                    if let Some(pid) = pid {
                        kill_tree(pid);
                    }
                    let _ = child.start_kill();
                    bail!("cancelled");
                }
            }
            match timeout(Duration::from_millis(100), child.wait()).await {
                Ok(Ok(status)) => {
                    let mut stdout = Vec::new();
                    let mut stderr = Vec::new();
                    if let Some(ref mut out) = stdout_pipe {
                        let _ = out.read_to_end(&mut stdout).await;
                    }
                    if let Some(ref mut err) = stderr_pipe {
                        let _ = err.read_to_end(&mut stderr).await;
                    }
                    return Ok((status, stdout, stderr));
                }
                Ok(Err(e)) => return Err(anyhow::anyhow!("wait python_runner: {e}")),
                Err(_) => continue,
            }
        }
    };

    let (status, stdout, stderr) = match timeout(run_timeout, wait_fut).await {
        Ok(r) => r?,
        Err(_) => {
            if let Some(pid) = pid {
                kill_tree(pid);
            }
            let _ = child.start_kill();
            bail!("python_runner timed out");
        }
    };

    let stdout_s = String::from_utf8_lossy(&stdout);
    let stderr_s = String::from_utf8_lossy(&stderr).trim().to_string();
    if !status.success() {
        let err = if stderr_s.is_empty() {
            stdout_s.trim().to_string()
        } else {
            stderr_s
        };
        bail!(
            "python_runner exited {}: {}",
            status.code().unwrap_or(-1),
            truncate(&err, 800)
        );
    }
    // Last non-empty stdout line must be a JSON object. Empty / non-JSON is not success.
    crate::skill_status::parse_stdout_report(&stdout_s)
        .map_err(|e| anyhow::anyhow!("{}", e.as_message()))
}

fn kill_tree(pid: u32) {
    let _ = std::process::Command::new("kill")
        .args(["-TERM", "--", &format!("-{pid}")])
        .status();
    std::thread::sleep(Duration::from_millis(200));
    let _ = std::process::Command::new("kill")
        .args(["-KILL", "--", &format!("-{pid}")])
        .status();
}

fn truncate(s: &str, n: usize) -> String {
    if s.len() <= n {
        s.to_string()
    } else {
        format!("{}…", &s[..n])
    }
}

pub fn entry_from_package(pkg_dir: &Path) -> Result<SkillManifest> {
    if let Some(m) = load_manifest_file(pkg_dir)? {
        return Ok(m);
    }
    Ok(default_manifest("0.0.0"))
}

pub fn release_to_json(rec: &ReleaseRecord) -> Value {
    json!({
        "skill_id": rec.skill_id,
        "version": rec.version,
        "digest": rec.digest,
        "path": rec.path,
        "published": rec.published,
        "created_at": rec.created_at,
        "entry": rec.entry,
        "secret_names": rec.secret_names,
        "statuses": rec.statuses,
    })
}

pub fn client_has_digest(
    installed: &HashMap<String, InstalledSkill>,
    skill_id: &str,
    digest: &str,
) -> bool {
    let Ok(d) = normalize_digest(digest) else {
        return false;
    };
    installed
        .get(skill_id)
        .map(|s| s.digest == d)
        .unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn tmp_root() -> PathBuf {
        let n = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let p = std::env::temp_dir().join(format!("cloakcli_pkg_{n}"));
        let _ = fs::remove_dir_all(&p);
        fs::create_dir_all(p.join("skills")).unwrap();
        fs::create_dir_all(p.join("data")).unwrap();
        p
    }

    fn write_echo_skill(root: &Path, name: &str) -> PathBuf {
        let dir = state::skills_dir(root).join(name);
        fs::create_dir_all(dir.join("scripts")).unwrap();
        fs::write(
            dir.join("skill.json"),
            format!(
                "{{\n  \"schema_version\": 1,\n  \"name\": \"{name}\",\n  \"description\": \"fixture\",\n  \"params\": [],\n  \"steps\": []\n}}\n"
            ),
        )
        .unwrap();
        fs::write(
            dir.join("manifest.json"),
            format!(
                "{{\n  \"version\": \"1.0.0\",\n  \"entry\": {{\"kind\": \"python_runner\", \"path\": \"scripts/echo.py\"}},\n  \"secrets\": []\n}}\n"
            ),
        )
        .unwrap();
        fs::write(
            dir.join("scripts").join("echo.py"),
            "import json,sys\np=json.loads(sys.stdin.read() or '{}')\nprint(json.dumps({'ok': True, 'echo': True, 'digest': p.get('digest')}))\n",
        )
        .unwrap();
        dir
    }

    /// Craft a ustar member without going through tar::Builder path checks.
    fn raw_tar_with_name(name: &str, typeflag: u8) -> Vec<u8> {
        let data = b"{\"schema_version\":1,\"name\":\"x\",\"steps\":[]}\n";
        let mut header = [0u8; 512];
        let nb = name.as_bytes();
        assert!(nb.len() < 100);
        header[..nb.len()].copy_from_slice(nb);
        header[100..108].copy_from_slice(b"0000644 ");
        header[108..116].copy_from_slice(b"0000000 ");
        header[116..124].copy_from_slice(b"0000000 ");
        let size = if typeflag == b'2' {
            format!("{:011o} ", 0)
        } else {
            format!("{:011o} ", data.len())
        };
        header[124..136].copy_from_slice(size.as_bytes());
        header[136..148].copy_from_slice(b"00000000000 ");
        header[148..156].copy_from_slice(b"        ");
        header[156] = typeflag;
        if typeflag == b'2' {
            let link = b"skill.json";
            header[157..157 + link.len()].copy_from_slice(link);
        }
        header[257..262].copy_from_slice(b"ustar");
        let sum: u32 = header.iter().map(|b| *b as u32).sum();
        let chk = format!("{:06o}\0 ", sum);
        header[148..156].copy_from_slice(chk.as_bytes());
        let mut out = header.to_vec();
        if typeflag != b'2' {
            out.extend_from_slice(data);
            let pad = (512 - (data.len() % 512)) % 512;
            out.extend(std::iter::repeat(0).take(pad));
        }
        out.extend_from_slice(&[0u8; 1024]);
        out
    }

    #[test]
    fn pack_is_deterministic_and_publish_flag() {
        let root = tmp_root();
        write_echo_skill(&root, "echo-runner");
        let a = pack_skill(&root, "echo-runner", Some("1.0.0"), false).unwrap();
        assert!(!a.published);
        let b = pack_skill(&root, "echo-runner", Some("1.0.0"), false).unwrap();
        assert_eq!(a.digest, b.digest);
        assert_eq!(a.digest.len(), 64);
        let pubd = publish_skill(&root, "echo-runner", Some("1.0.0")).unwrap();
        assert!(pubd.published);
        assert_eq!(list_releases(&root).unwrap().len(), 1);
        let got = resolve_published(&root, "echo-runner", None, None).unwrap();
        assert_eq!(got.digest, a.digest);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn resolve_published_rejects_wrong_digest() {
        let root = tmp_root();
        write_echo_skill(&root, "echo-runner");
        pack_skill(&root, "echo-runner", Some("1.0.0"), true).unwrap();
        let err = resolve_published(
            &root,
            "echo-runner",
            None,
            Some("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
        )
        .unwrap_err()
        .to_string();
        assert!(err.contains("digest"), "{err}");
        let err = resolve_published(&root, "echo-runner", None, Some("deadbeef")).unwrap_err();
        assert!(err.to_string().contains("invalid SHA-256"), "{err}");
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn unpack_rejects_dotdot_abs_and_links() {
        let dest = tmp_root().join("extract");
        fs::create_dir_all(&dest).unwrap();
        let err = unpack_tar_safe(&raw_tar_with_name("../evil.json", b'0'), &dest)
            .unwrap_err()
            .to_string();
        assert!(
            err.contains("illegal") || err.contains("traversal") || err.contains("refusing"),
            "{err}"
        );

        let err = unpack_tar_safe(&raw_tar_with_name("/tmp/evil.json", b'0'), &dest)
            .unwrap_err()
            .to_string();
        assert!(
            err.contains("absolute") || err.contains("illegal") || err.contains("refusing"),
            "{err}"
        );

        let err = unpack_tar_safe(&raw_tar_with_name("link", b'2'), &dest)
            .unwrap_err()
            .to_string();
        assert!(err.to_ascii_lowercase().contains("link"), "{err}");
        let _ = fs::remove_dir_all(dest.parent().unwrap());
    }

    #[test]
    fn install_then_lookup_no_local_fallback() {
        let root = tmp_root();
        write_echo_skill(&root, "echo-runner");
        // A local same-name skill that must NOT be used as fallback.
        fs::create_dir_all(state::skills_dir(&root).join("other")).unwrap();
        let rec = pack_skill(&root, "echo-runner", Some("1.0.0"), true).unwrap();
        let bytes = read_package_bytes(&root, &rec).unwrap();
        let client = tmp_root();
        fs::create_dir_all(state::skills_dir(&client).join("echo-runner")).unwrap();
        fs::write(
            state::skills_dir(&client).join("echo-runner").join("skill.json"),
            "{\"schema_version\":1,\"name\":\"echo-runner\",\"steps\":[]}\n",
        )
        .unwrap();
        let inst = install_package(&client, &rec.skill_id, &rec.version, &rec.digest, &bytes)
            .unwrap();
        assert_eq!(inst.digest, rec.digest);
        let dir = lookup_installed(&client, "echo-runner", &rec.digest).unwrap();
        assert!(dir.join("skill.json").is_file());
        assert!(dir.join("scripts").join("echo.py").is_file());
        let err = lookup_installed(
            &client,
            "echo-runner",
            "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        )
        .unwrap_err()
        .to_string();
        assert!(err.contains("no local same-name fallback"), "{err}");

        // Tampered bytes refuse.
        let mut bad = bytes.clone();
        if let Some(last) = bad.last_mut() {
            *last ^= 0xff;
        }
        let err = install_package(&client, &rec.skill_id, &rec.version, &rec.digest, &bad)
            .unwrap_err()
            .to_string();
        assert!(err.contains("digest mismatch"), "{err}");
        let _ = fs::remove_dir_all(&root);
        let _ = fs::remove_dir_all(&client);
    }

    #[test]
    fn failed_install_keeps_previous() {
        let root = tmp_root();
        write_echo_skill(&root, "echo-runner");
        let rec = pack_skill(&root, "echo-runner", Some("1.0.0"), true).unwrap();
        let bytes = read_package_bytes(&root, &rec).unwrap();
        install_package(&root, &rec.skill_id, &rec.version, &rec.digest, &bytes).unwrap();
        let prev = list_installed(&root).unwrap();
        assert_eq!(prev[0].digest, rec.digest);

        let err = install_package(
            &root,
            "echo-runner",
            "9.9.9",
            "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
            b"not-a-tar",
        )
        .unwrap_err()
        .to_string();
        assert!(!err.is_empty());
        let still = list_installed(&root).unwrap();
        assert_eq!(still[0].digest, rec.digest);
        let _ = fs::remove_dir_all(&root);
    }

    #[tokio::test]
    async fn python_runner_echoes_digest() {
        let root = tmp_root();
        let dir = write_echo_skill(&root, "echo-runner");
        let payload = json!({
            "skill_id": "echo-runner",
            "version": "1.0.0",
            "digest": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "profile": "noproxy",
            "vars": {}
        });
        let out = run_python_runner(
            &dir,
            "scripts/echo.py",
            &payload,
            None,
            Duration::from_secs(10),
        )
        .await
        .unwrap();
        assert_eq!(out["ok"], true);
        assert_eq!(out["echo"], true);
        let _ = fs::remove_dir_all(&root);
    }

    fn write_status_skill(root: &Path, name: &str, statuses_json: &str, script: &str) -> PathBuf {
        let dir = state::skills_dir(root).join(name);
        fs::create_dir_all(dir.join("scripts")).unwrap();
        fs::write(
            dir.join("skill.json"),
            format!(
                "{{\n  \"schema_version\": 1,\n  \"name\": \"{name}\",\n  \"description\": \"fixture\",\n  \"params\": [],\n  \"steps\": []\n}}\n"
            ),
        )
        .unwrap();
        fs::write(
            dir.join("manifest.json"),
            format!(
                "{{\n  \"version\": \"1.0.0\",\n  \"entry\": {{\"kind\": \"python_runner\", \"path\": \"scripts/run.py\"}},\n  \"secrets\": [],\n  \"statuses\": {statuses_json}\n}}\n"
            ),
        )
        .unwrap();
        fs::write(dir.join("scripts").join("run.py"), script).unwrap();
        dir
    }

    #[test]
    fn statuses_empty_or_illegal_reject_pack() {
        let root = tmp_root();
        write_status_skill(&root, "bad-empty", "[]", "print('x')\n");
        let err = pack_skill(&root, "bad-empty", Some("1.0.0"), true)
            .unwrap_err()
            .to_string();
        assert!(err.contains("non-empty"), "{err}");

        let root = tmp_root();
        write_status_skill(
            &root,
            "bad-dup",
            r#"[{"id":"ok","success":true,"retryable":false,"label":"A"},{"id":"ok","success":false,"retryable":false,"label":"B"}]"#,
            "print('x')\n",
        );
        let err = pack_skill(&root, "bad-dup", Some("1.0.0"), true)
            .unwrap_err()
            .to_string();
        assert!(err.contains("duplicate"), "{err}");

        let root = tmp_root();
        write_status_skill(
            &root,
            "bad-label",
            r#"[{"id":"ok","success":true,"retryable":false,"label":"  "}]"#,
            "print('x')\n",
        );
        let err = pack_skill(&root, "bad-label", Some("1.0.0"), true)
            .unwrap_err()
            .to_string();
        assert!(err.contains("label"), "{err}");

        let root = tmp_root();
        write_status_skill(
            &root,
            "bad-id",
            r#"[{"id":"","success":true,"retryable":false,"label":"x"}]"#,
            "print('x')\n",
        );
        let err = pack_skill(&root, "bad-id", Some("1.0.0"), true)
            .unwrap_err()
            .to_string();
        assert!(err.contains("id") || err.contains("empty"), "{err}");
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn missing_statuses_is_legacy_and_changing_statuses_changes_digest() {
        let root = tmp_root();
        write_echo_skill(&root, "echo-runner");
        let legacy = pack_skill(&root, "echo-runner", Some("1.0.0"), true).unwrap();
        assert!(legacy.statuses.is_none());

        write_status_skill(
            &root,
            "pin-reg",
            r#"[{"id":"logged_in","success":true,"retryable":false,"label":"已登录"},{"id":"email_confirmed","success":true,"retryable":false,"label":"邮箱已确认","optional":true},{"id":"oops_park","success":false,"retryable":true,"label":"风控先放"}]"#,
            "import json,sys\np=json.loads(sys.stdin.read() or '{}')\nprint(json.dumps({'skill_id':p.get('skill_id'),'version':p.get('version'),'digest':p.get('digest'),'status':(p.get('vars') or {}).get('status') or 'logged_in'}))\n",
        );
        let a = pack_skill(&root, "pin-reg", Some("1.0.0"), true).unwrap();
        assert_eq!(a.statuses.as_ref().unwrap().len(), 3);
        let got = statuses_for_digest(&root, "pin-reg", &a.digest).unwrap();
        assert_eq!(got.as_ref().unwrap()[0].id, "logged_in");

        // Bump version with a different set → new digest.
        let dir = state::skills_dir(&root).join("pin-reg");
        fs::write(
            dir.join("manifest.json"),
            r#"{"version":"1.1.0","entry":{"kind":"python_runner","path":"scripts/run.py"},"secrets":[],"statuses":[{"id":"logged_in","success":true,"retryable":false,"label":"Signed in"}]}"#,
        )
        .unwrap();
        let b = pack_skill(&root, "pin-reg", Some("1.1.0"), true).unwrap();
        assert_ne!(a.digest, b.digest);
        // Old digest still returns v1 labels.
        let old = statuses_for_digest(&root, "pin-reg", &a.digest).unwrap().unwrap();
        assert_eq!(old[0].label, "已登录");
        let new = statuses_for_digest(&root, "pin-reg", &b.digest).unwrap().unwrap();
        assert_eq!(new[0].label, "Signed in");
        assert_eq!(new.len(), 1);

        // Same published version with different statuses (digest change) is refused.
        fs::write(
            dir.join("manifest.json"),
            r#"{"version":"1.0.0","entry":{"kind":"python_runner","path":"scripts/run.py"},"secrets":[],"statuses":[{"id":"logged_in","success":true,"retryable":false,"label":"nope"}]}"#,
        )
        .unwrap();
        let err = pack_skill(&root, "pin-reg", Some("1.0.0"), true)
            .unwrap_err()
            .to_string();
        assert!(err.contains("already published") || err.contains("bump"), "{err}");
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn two_skills_different_status_sets_stored_per_digest() {
        let root = tmp_root();
        write_status_skill(
            &root,
            "pin-reg",
            r#"[{"id":"logged_in","success":true,"retryable":false,"label":"已登录"}]"#,
            "print('x')\n",
        );
        write_status_skill(
            &root,
            "ship-demo",
            r#"[{"id":"shipped","success":true,"retryable":false,"label":"Shipped"}]"#,
            "print('x')\n",
        );
        let pin = pack_skill(&root, "pin-reg", Some("1.0.0"), true).unwrap();
        let ship = pack_skill(&root, "ship-demo", Some("1.0.0"), true).unwrap();
        let pin_s = statuses_for_digest(&root, "pin-reg", &pin.digest).unwrap().unwrap();
        let ship_s = statuses_for_digest(&root, "ship-demo", &ship.digest).unwrap().unwrap();
        assert_eq!(pin_s[0].id, "logged_in");
        assert_eq!(ship_s[0].id, "shipped");
        let err = statuses_for_digest(&root, "pin-reg", &ship.digest)
            .unwrap_err()
            .to_string();
        assert!(err.contains("no release") || err.contains("digest"), "{err}");
        let _ = fs::remove_dir_all(&root);
    }

    #[tokio::test]
    async fn python_runner_empty_or_non_json_is_not_success() {
        let root = tmp_root();
        let dir = write_echo_skill(&root, "echo-runner");
        fs::write(dir.join("scripts").join("empty.py"), "import sys\nsys.exit(0)\n").unwrap();
        let err = run_python_runner(
            &dir,
            "scripts/empty.py",
            &json!({}),
            None,
            Duration::from_secs(5),
        )
        .await
        .unwrap_err()
        .to_string();
        assert!(err.contains("empty stdout") || err.contains("protocol"), "{err}");

        fs::write(
            dir.join("scripts").join("tail.py"),
            "print('{\"ok\": true}')\nprint('log after report')\n",
        )
        .unwrap();
        let err = run_python_runner(
            &dir,
            "scripts/tail.py",
            &json!({}),
            None,
            Duration::from_secs(5),
        )
        .await
        .unwrap_err()
        .to_string();
        assert!(
            err.contains("not a JSON object") || err.contains("protocol"),
            "{err}"
        );

        fs::write(
            dir.join("scripts").join("logs_then_json.py"),
            "print('starting')\nprint('{\"ok\": true, \"echo\": true}')\n",
        )
        .unwrap();
        let out = run_python_runner(
            &dir,
            "scripts/logs_then_json.py",
            &json!({}),
            None,
            Duration::from_secs(5),
        )
        .await
        .unwrap();
        assert_eq!(out["ok"], true);
        let _ = fs::remove_dir_all(&root);
    }

    #[tokio::test]
    async fn python_runner_rejects_path_escape() {
        let root = tmp_root();
        let dir = write_echo_skill(&root, "echo-runner");
        let err = run_python_runner(
            &dir,
            "../echo.py",
            &json!({}),
            None,
            Duration::from_secs(5),
        )
        .await
        .unwrap_err()
        .to_string();
        assert!(
            err.contains("..") || err.contains("illegal") || err.contains("relative"),
            "{err}"
        );
        let _ = fs::remove_dir_all(&root);
    }
}
