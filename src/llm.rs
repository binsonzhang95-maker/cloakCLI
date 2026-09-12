//! LLM stall-recovery config: `config/llm.json` (mode 0600).
//!
//! Key strategy A: store `api_key_env` only (default `CLOAKCLI_LLM_API_KEY`).
//! Never persist a raw key in llm.json, cache, logs, `show`, or artifacts.
//! Reject CLI `--api-key` (shell history / argv). Accept TTY hidden prompt,
//! `--stdin-key`, or an already-set env (`OPENAI_API_KEY` as documented fallback).

use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use std::env;
use std::fs;
use std::io::{self, BufRead, IsTerminal, Write};
use std::path::{Path, PathBuf};

use crate::state;
use crate::util;
use url::Url;

pub const DEFAULT_RECOVER_TIMEOUT_SEC: u64 = 300;
pub const DEFAULT_MAX_ACTIONS: u32 = 120;
pub const DEFAULT_MAX_LOOPS: u32 = 60;
pub const DEFAULT_MAX_TOKENS: u32 = 200_000;
pub const DEFAULT_API_KEY_ENV: &str = "CLOAKCLI_LLM_API_KEY";
pub const COMPAT_API_KEY_ENV: &str = "OPENAI_API_KEY";
pub const DEFAULT_BASE_URL: &str = "https://api.openai.com/v1";
pub const MAX_MODELS_BODY: usize = 1_048_576;
pub const MAX_MODELS: usize = 256;
pub const MAX_MODEL_ID_LEN: usize = 128;
pub const MAX_MODELS_PAGES: u32 = 5;
pub const MODELS_CONNECT_TIMEOUT_SEC: u64 = 10;
pub const MODELS_READ_TIMEOUT_SEC: u64 = 20;
const SCHEMA_VERSION: u32 = 1;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LlmConfig {
    #[serde(default = "default_schema")]
    pub schema_version: u32,
    #[serde(default)]
    pub enabled: bool,
    #[serde(default)]
    pub base_url: String,
    #[serde(default)]
    pub model: String,
    /// Name of the environment variable that holds the API key. Never a raw key.
    #[serde(default)]
    pub api_key_env: String,
    #[serde(default = "default_recover_timeout")]
    pub recover_timeout_sec: u64,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub allow_hosts: Vec<String>,
    #[serde(default = "default_max_actions")]
    pub max_actions: u32,
    #[serde(default = "default_max_loops")]
    pub max_loops: u32,
    #[serde(default = "default_max_tokens")]
    pub max_tokens_per_recover: u32,
}

fn default_schema() -> u32 {
    SCHEMA_VERSION
}
fn default_recover_timeout() -> u64 {
    DEFAULT_RECOVER_TIMEOUT_SEC
}
fn default_max_actions() -> u32 {
    DEFAULT_MAX_ACTIONS
}
fn default_max_loops() -> u32 {
    DEFAULT_MAX_LOOPS
}
fn default_max_tokens() -> u32 {
    DEFAULT_MAX_TOKENS
}

impl Default for LlmConfig {
    fn default() -> Self {
        Self {
            schema_version: SCHEMA_VERSION,
            enabled: false,
            base_url: String::new(),
            model: String::new(),
            api_key_env: DEFAULT_API_KEY_ENV.into(),
            recover_timeout_sec: DEFAULT_RECOVER_TIMEOUT_SEC,
            allow_hosts: Vec::new(),
            max_actions: DEFAULT_MAX_ACTIONS,
            max_loops: DEFAULT_MAX_LOOPS,
            max_tokens_per_recover: DEFAULT_MAX_TOKENS,
        }
    }
}

/// Redacted view for TUI / `llm show`. Never includes secret values.
#[derive(Debug, Clone, Default)]
pub struct LlmView {
    pub configured: bool,
    pub enabled: bool,
    pub model: String,
    pub base_url: String,
    pub api_key_env: String,
    pub key_present: bool,
    pub recover_timeout_sec: u64,
    pub allow_hosts: Vec<String>,
    pub max_actions: u32,
    pub max_loops: u32,
    pub path: String,
}

#[derive(Debug, Clone, Default)]
pub struct LlmSetArgs {
    pub base_url: Option<String>,
    pub model: Option<String>,
    pub api_key_env: Option<String>,
    pub enabled: Option<bool>,
    pub recover_timeout_sec: Option<u64>,
    pub allow_hosts: Option<Vec<String>>,
    pub max_actions: Option<u32>,
    pub max_loops: Option<u32>,
    pub max_tokens_per_recover: Option<u32>,
}

pub fn config_path(root: &Path) -> std::path::PathBuf {
    state::llm_config_path(root)
}

pub fn load(root: &Path) -> Result<Option<LlmConfig>> {
    let path = config_path(root);
    if !path.is_file() {
        return Ok(None);
    }
    let text = fs::read_to_string(&path)
        .with_context(|| format!("read {}", path.display()))?;
    let mut cfg: LlmConfig = serde_json::from_str(&text)
        .with_context(|| format!("invalid llm.json {}", path.display()))?;
    sanitize_loaded(&mut cfg)?;
    Ok(Some(cfg))
}

pub fn save(root: &Path, cfg: &LlmConfig) -> Result<()> {
    let mut cfg = cfg.clone();
    sanitize_loaded(&mut cfg)?;
    let path = config_path(root);
    let json = serde_json::to_string_pretty(&cfg)?;
    util::write_mode_0600(&path, format!("{json}\n").as_bytes())?;
    Ok(())
}

fn sanitize_loaded(cfg: &mut LlmConfig) -> Result<()> {
    cfg.schema_version = SCHEMA_VERSION;
    cfg.model = cfg.model.trim().to_string();
    cfg.api_key_env = cfg.api_key_env.trim().to_string();
    if cfg.api_key_env.is_empty() {
        cfg.api_key_env = DEFAULT_API_KEY_ENV.into();
    }
    validate_env_name(&cfg.api_key_env)?;
    if !cfg.model.is_empty() {
        let key = resolve_api_key_from_env(&cfg.api_key_env);
        match accept_model_id(&cfg.model, key.as_deref()) {
            Ok(m) => cfg.model = m,
            Err(_) => cfg.model.clear(),
        }
    }
    if !cfg.base_url.is_empty() {
        cfg.base_url = normalize_base_url(&cfg.base_url)?;
    }
    if cfg.recover_timeout_sec < 5 {
        cfg.recover_timeout_sec = 5;
    }
    if cfg.recover_timeout_sec > 3600 {
        cfg.recover_timeout_sec = 3600;
    }
    if cfg.max_actions == 0 {
        cfg.max_actions = DEFAULT_MAX_ACTIONS;
    }
    if cfg.max_loops == 0 {
        cfg.max_loops = DEFAULT_MAX_LOOPS;
    }
    cfg.max_actions = cfg.max_actions.min(500);
    cfg.max_loops = cfg.max_loops.min(200);
    if cfg.max_tokens_per_recover == 0 {
        cfg.max_tokens_per_recover = DEFAULT_MAX_TOKENS;
    }
    let mut hosts = Vec::new();
    for h in cfg.allow_hosts.drain(..) {
        if let Ok(n) = normalize_host(&h) {
            if !hosts.contains(&n) {
                hosts.push(n);
            }
        }
    }
    cfg.allow_hosts = hosts;
    Ok(())
}

pub fn validate_env_name(name: &str) -> Result<()> {
    // Never interpolate `name` into the error: a pasted API key must not be echoed.
    let ok = !name.is_empty()
        && name.len() <= 128
        && name.chars().enumerate().all(|(i, c)| {
            if i == 0 {
                c.is_ascii_alphabetic() || c == '_'
            } else {
                c.is_ascii_alphanumeric() || c == '_'
            }
        });
    if !ok {
        bail!("invalid api_key_env: use an environment variable name, not a raw key");
    }
    // Reject values that look like keys (sk-..., Bearer, long secrets)
    if name.contains('-') || name.len() > 64 {
        bail!("invalid api_key_env: looks like a secret, not an env var name");
    }
    Ok(())
}

/// Parse an http(s) URL.
///
/// Base URLs (`allow_query = false`) reject credentials, query, and fragment.
/// Endpoint / pagination URLs (`allow_query = true`) still reject credentials
/// and fragment, but may carry a query string.
pub fn parse_http_url(raw: &str, allow_query: bool) -> Result<Url> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        bail!("base_url must be http(s):// (got scheme-less or blocked URL)");
    }
    let lower = trimmed.to_ascii_lowercase();
    if lower.starts_with("file:") || lower.starts_with("javascript:") || lower.starts_with("data:")
    {
        bail!("base_url rejects file:/javascript:/data:");
    }
    if trimmed.chars().any(|c| c.is_whitespace() || c.is_control()) {
        bail!("base_url must not contain whitespace");
    }
    // `https:///v1` has an empty authority; do not let the parser promote `v1` to host.
    if trimmed
        .splitn(2, "://")
        .nth(1)
        .map(|rest| rest.starts_with('/'))
        .unwrap_or(false)
    {
        bail!("base_url missing host");
    }
    let parsed = Url::parse(trimmed).map_err(|_| {
        anyhow::anyhow!("base_url must be http(s):// (got scheme-less or blocked URL)")
    })?;
    if parsed.scheme() != "http" && parsed.scheme() != "https" {
        bail!("base_url must be http(s):// (got scheme-less or blocked URL)");
    }
    if !parsed.username().is_empty() || parsed.password().is_some() {
        bail!("base_url must not contain credentials");
    }
    if parsed.host_str().map(|h| h.is_empty()).unwrap_or(true) {
        bail!("base_url missing host");
    }
    if parsed.query().is_some() && !allow_query {
        bail!("base_url must not contain a query string");
    }
    if parsed.fragment().is_some() {
        bail!("base_url must not contain a fragment");
    }
    Ok(parsed)
}

pub fn validate_base_url(url: &str) -> Result<()> {
    let _ = parse_http_url(url, false)?;
    Ok(())
}

fn path_segments_of(url: &Url) -> Vec<String> {
    url.path_segments()
        .map(|s| {
            s.filter(|p| !p.is_empty())
                .map(|p| p.to_string())
                .collect()
        })
        .unwrap_or_default()
}

fn drop_endpoint_suffixes(mut segs: Vec<String>) -> Vec<String> {
    loop {
        let n = segs.len();
        if n >= 2
            && segs[n - 2].eq_ignore_ascii_case("chat")
            && segs[n - 1].eq_ignore_ascii_case("completions")
        {
            segs.pop();
            segs.pop();
            continue;
        }
        if n >= 1 {
            let last = segs[n - 1].to_ascii_lowercase();
            if last == "models" || last == "completions" {
                segs.pop();
                continue;
            }
        }
        break;
    }
    segs
}

/// Collapse consecutive `/v1` path *segments* only (`/v1/v1` → `/v1`).
/// `/v1/v10` is a different last segment and must be left intact.
fn collapse_duplicate_v1(segs: Vec<String>) -> Vec<String> {
    let mut out = Vec::with_capacity(segs.len());
    for s in segs {
        if s.eq_ignore_ascii_case("v1")
            && out
                .last()
                .map(|p: &String| p.eq_ignore_ascii_case("v1"))
                .unwrap_or(false)
        {
            continue;
        }
        out.push(s);
    }
    out
}

fn url_with_segments(mut url: Url, segs: &[String]) -> Result<Url> {
    {
        let mut p = url
            .path_segments_mut()
            .map_err(|_| anyhow::anyhow!("base_url missing host"))?;
        p.clear();
        for s in segs {
            p.push(s);
        }
    }
    url.set_query(None);
    url.set_fragment(None);
    Ok(url)
}

fn serialize_url(url: &Url) -> String {
    url.as_str().trim_end_matches('/').to_string()
}

/// Strip trailing slashes, drop accidental `/models` or `/chat/completions`
/// suffixes, collapse consecutive `/v1` path segments. http(s) only.
pub fn normalize_base_url(raw: &str) -> Result<String> {
    validate_base_url(raw)?;
    let parsed = parse_http_url(raw, false)?;
    let segs = collapse_duplicate_v1(drop_endpoint_suffixes(path_segments_of(&parsed)));
    let url = url_with_segments(parsed, &segs)?;
    let s = serialize_url(&url);
    let _ = parse_http_url(&s, false)?;
    Ok(s)
}

/// Join `{base}/{path}` without duplicating a trailing `/v1` path segment.
pub fn join_openai_path(base: &str, path: &str) -> Result<String> {
    let base = normalize_base_url(base)?;
    let path = path.trim().trim_start_matches('/');
    if path.is_empty() {
        return Ok(base);
    }
    let parsed = parse_http_url(&base, false)?;
    let mut segs = path_segments_of(&parsed);
    let mut add: Vec<String> = path
        .split('/')
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string())
        .collect();
    if segs
        .last()
        .map(|s| s.eq_ignore_ascii_case("v1"))
        .unwrap_or(false)
        && add
            .first()
            .map(|s| s.eq_ignore_ascii_case("v1"))
            .unwrap_or(false)
    {
        add.remove(0);
    }
    segs.extend(add);
    let url = url_with_segments(parsed, &segs)?;
    Ok(serialize_url(&url))
}

/// Reject empty / control-character / overlong ids, and ids that contain the
/// current API key. Errors never include the raw id (it may be the secret).
pub fn accept_model_id(id: &str, api_key: Option<&str>) -> Result<String> {
    let id = id.trim();
    if id.is_empty() {
        bail!("model must not be empty");
    }
    if id.chars().any(|c| c.is_control()) {
        bail!("model id contains control characters");
    }
    if id.len() > MAX_MODEL_ID_LEN {
        bail!("model id exceeds {MAX_MODEL_ID_LEN} characters");
    }
    if let Some(k) = api_key {
        if k.len() >= 4 && id.contains(k) {
            bail!("model id rejected");
        }
    }
    Ok(id.to_string())
}

/// A fetched/cached model list may be saved only for the base it was requested with.
pub fn models_bound_to_request_base(list_base: &str, request_base: &str) -> bool {
    if list_base.is_empty() || request_base.is_empty() {
        return false;
    }
    match (
        normalize_base_url(list_base),
        normalize_base_url(request_base),
    ) {
        (Ok(a), Ok(b)) => a == b,
        _ => false,
    }
}

pub fn models_url(base: &str) -> Result<String> {
    join_openai_path(base, "models")
}

pub fn chat_completions_url(base: &str) -> Result<String> {
    join_openai_path(base, "chat/completions")
}

/// Reject `--api-key` / `--api-key=` in argv so clap never interpolates the value.
pub fn reject_api_key_argv<I, S>(args: I) -> Result<()>
where
    I: IntoIterator<Item = S>,
    S: AsRef<str>,
{
    for a in args {
        let a = a.as_ref();
        if a == "--api-key" || a.starts_with("--api-key=") {
            bail!(
                "--api-key is rejected (appears in shell history and process argv). \
                 Set {} (or {}), use a TTY hidden prompt, \
                 or pipe the key with --stdin-key. llm.json stores api_key_env only.",
                DEFAULT_API_KEY_ENV,
                COMPAT_API_KEY_ENV
            );
        }
    }
    Ok(())
}

pub fn normalize_host(raw: &str) -> Result<String> {
    let s = raw.trim().to_ascii_lowercase();
    if s.is_empty() {
        bail!("empty host");
    }
    let host = if let Some(rest) = s.split_once("://") {
        let after = rest.1;
        let no_path = after.split('/').next().unwrap_or(after);
        let no_at = no_path.rsplit('@').next().unwrap_or(no_path);
        no_at.split(':').next().unwrap_or(no_at).to_string()
    } else {
        s.trim_end_matches('/').to_string()
    };
    let host = host.trim_matches('.').to_string();
    if host.is_empty()
        || host.contains('/')
        || host.contains(' ')
        || host.contains('\\')
        || host.contains('\n')
    {
        bail!("invalid allow_hosts entry '{raw}'");
    }
    Ok(host)
}

pub fn apply_set(root: &Path, args: LlmSetArgs) -> Result<LlmConfig> {
    let mut cfg = load(root)?.unwrap_or_default();
    let creating = config_path(root).is_file() == false
        && cfg.base_url.is_empty()
        && cfg.model.is_empty();

    if let Some(e) = args.api_key_env {
        validate_env_name(&e)?;
        cfg.api_key_env = e;
    } else if cfg.api_key_env.is_empty() {
        cfg.api_key_env = DEFAULT_API_KEY_ENV.into();
    }
    if let Some(u) = args.base_url {
        cfg.base_url = normalize_base_url(&u)?;
    }
    if let Some(m) = args.model {
        let key = resolve_api_key_from_env(&cfg.api_key_env);
        let m = accept_model_id(&m, key.as_deref())?;
        validate_model_against_cache(root, &cfg.base_url, &m)?;
        cfg.model = m;
    }
    if let Some(en) = args.enabled {
        cfg.enabled = en;
    } else if creating {
        cfg.enabled = true;
    }
    if let Some(t) = args.recover_timeout_sec {
        if !(5..=3600).contains(&t) {
            bail!("recover_timeout_sec must be 5..=3600 (got {t})");
        }
        cfg.recover_timeout_sec = t;
    }
    if let Some(hosts) = args.allow_hosts {
        let mut out = Vec::new();
        for h in hosts {
            out.push(normalize_host(&h)?);
        }
        cfg.allow_hosts = out;
    }
    if let Some(n) = args.max_actions {
        if n == 0 || n > 500 {
            bail!("max_actions must be 1..=500");
        }
        cfg.max_actions = n;
    }
    if let Some(n) = args.max_loops {
        if n == 0 || n > 200 {
            bail!("max_loops must be 1..=200");
        }
        cfg.max_loops = n;
    }
    if let Some(n) = args.max_tokens_per_recover {
        cfg.max_tokens_per_recover = n.max(1000);
    }

    if cfg.base_url.is_empty() || cfg.model.is_empty() || cfg.api_key_env.is_empty() {
        bail!("llm set requires --base-url, --model, and --api-key-env (env var name only)");
    }
    save(root, &cfg)?;
    Ok(cfg)
}

pub fn toggle_enabled(root: &Path) -> Result<LlmView> {
    let mut cfg = load(root)?.ok_or_else(|| {
        anyhow::anyhow!("no config/llm.json — run: cloakcli llm configure")
    })?;
    cfg.enabled = !cfg.enabled;
    save(root, &cfg)?;
    Ok(view_of(root, Some(&cfg)))
}

pub fn view(root: &Path) -> LlmView {
    match load(root) {
        Ok(cfg) => view_of(root, cfg.as_ref()),
        Err(_) => LlmView {
            configured: false,
            path: config_path(root).display().to_string(),
            ..Default::default()
        },
    }
}

fn view_of(root: &Path, cfg: Option<&LlmConfig>) -> LlmView {
    let path = config_path(root).display().to_string();
    match cfg {
        None => LlmView {
            configured: false,
            recover_timeout_sec: DEFAULT_RECOVER_TIMEOUT_SEC,
            path,
            ..Default::default()
        },
        Some(c) => LlmView {
            configured: true,
            enabled: c.enabled,
            model: c.model.clone(),
            base_url: c.base_url.clone(),
            api_key_env: c.api_key_env.clone(),
            key_present: resolve_api_key_from_env(&c.api_key_env).is_some(),
            recover_timeout_sec: c.recover_timeout_sec,
            allow_hosts: c.allow_hosts.clone(),
            max_actions: c.max_actions,
            max_loops: c.max_loops,
            path,
        },
    }
}

fn env_nonempty(name: &str) -> Option<String> {
    if name.is_empty() {
        return None;
    }
    match env::var(name) {
        Ok(v) if !v.is_empty() => Some(v),
        _ => None,
    }
}

/// Look up the API key for `env_name`. If the name is the default
/// `CLOAKCLI_LLM_API_KEY` and unset, fall back to `OPENAI_API_KEY`.
pub fn resolve_api_key_from_env(env_name: &str) -> Option<String> {
    if let Some(v) = env_nonempty(env_name) {
        return Some(v);
    }
    if env_name == DEFAULT_API_KEY_ENV {
        return env_nonempty(COMPAT_API_KEY_ENV);
    }
    None
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum KeySource {
    Env,
    CompatEnv,
    Stdin,
    Tty,
}

/// Resolve the key without accepting `--api-key` argv.
/// `stdin_key` must be explicit (`--stdin-key`).
pub fn resolve_api_key(
    env_name: &str,
    stdin_key: bool,
    interactive: bool,
) -> Result<(String, KeySource)> {
    if let Some(v) = env_nonempty(env_name) {
        return Ok((v, KeySource::Env));
    }
    if env_name == DEFAULT_API_KEY_ENV {
        if let Some(v) = env_nonempty(COMPAT_API_KEY_ENV) {
            return Ok((v, KeySource::CompatEnv));
        }
    }
    if stdin_key {
        let key = read_key_from_stdin()?;
        env::set_var(env_name, &key);
        return Ok((key, KeySource::Stdin));
    }
    if interactive && io::stdin().is_terminal() {
        let key = read_hidden_tty_key()?;
        env::set_var(env_name, &key);
        return Ok((key, KeySource::Tty));
    }
    bail!(
        "API key not found. Set {env_name} (or {}), use a TTY hidden prompt, \
         or pipe the key with --stdin-key. Do not pass --api-key (shell history).",
        COMPAT_API_KEY_ENV
    );
}

fn read_key_from_stdin() -> Result<String> {
    let mut s = String::new();
    io::stdin()
        .lock()
        .read_line(&mut s)
        .context("read API key from stdin")?;
    let key = s.trim_end_matches(['\n', '\r']).to_string();
    if key.is_empty() {
        bail!("stdin API key was empty");
    }
    Ok(key)
}

fn read_hidden_tty_key() -> Result<String> {
    use crossterm::event::{self, Event, KeyCode, KeyEventKind, KeyModifiers};
    use crossterm::terminal::{disable_raw_mode, enable_raw_mode};

    eprint!("API key (hidden, not saved to disk): ");
    let _ = io::stderr().flush();
    enable_raw_mode().context("enable raw mode for hidden key")?;
    struct RawGuard;
    impl Drop for RawGuard {
        fn drop(&mut self) {
            let _ = disable_raw_mode();
        }
    }
    let _guard = RawGuard;
    let mut buf = String::new();
    loop {
        match event::read() {
            Ok(Event::Key(k)) => {
                if k.kind != KeyEventKind::Press {
                    continue;
                }
                match k.code {
                    KeyCode::Enter => break,
                    KeyCode::Esc => {
                        eprintln!();
                        bail!("cancelled");
                    }
                    KeyCode::Backspace => {
                        buf.pop();
                    }
                    KeyCode::Char('c') if k.modifiers.contains(KeyModifiers::CONTROL) => {
                        eprintln!();
                        bail!("cancelled");
                    }
                    KeyCode::Char(c) => buf.push(c),
                    _ => {}
                }
            }
            Ok(_) => {}
            Err(e) => bail!("read hidden key: {e}"),
        }
    }
    drop(_guard);
    eprintln!();
    if buf.is_empty() {
        bail!("API key was empty");
    }
    Ok(buf)
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct ModelsCache {
    pub base_url: String,
    #[serde(default)]
    pub ids: Vec<String>,
    #[serde(default)]
    pub truncated: bool,
}

pub fn models_cache_path(root: &Path) -> PathBuf {
    state::config_dir(root).join("llm_models_cache.json")
}

fn peek_resolved_api_key(root: &Path) -> Option<String> {
    let env_name = fs::read_to_string(config_path(root))
        .ok()
        .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok())
        .and_then(|v| {
            v.get("api_key_env")
                .and_then(|x| x.as_str())
                .map(|s| s.trim().to_string())
        })
        .filter(|s| validate_env_name(s).is_ok())
        .unwrap_or_else(|| DEFAULT_API_KEY_ENV.to_string());
    resolve_api_key_from_env(&env_name)
}

fn filter_model_ids(ids: &mut Vec<String>, api_key: Option<&str>) {
    let mut out = Vec::with_capacity(ids.len());
    for id in ids.drain(..) {
        if let Ok(id) = accept_model_id(&id, api_key) {
            if !out.iter().any(|x| x == &id) {
                out.push(id);
            }
        }
    }
    if out.len() > MAX_MODELS {
        out.truncate(MAX_MODELS);
    }
    *ids = out;
}

pub fn load_models_cache(root: &Path) -> Option<ModelsCache> {
    let path = models_cache_path(root);
    let text = fs::read_to_string(path).ok()?;
    let mut cache: ModelsCache = serde_json::from_str(&text).ok()?;
    cache.base_url = normalize_base_url(&cache.base_url).ok()?;
    let key = peek_resolved_api_key(root);
    filter_model_ids(&mut cache.ids, key.as_deref());
    if cache.ids.len() > MAX_MODELS {
        cache.ids.truncate(MAX_MODELS);
        cache.truncated = true;
    }
    Some(cache)
}

pub fn save_models_cache(root: &Path, cache: &ModelsCache) -> Result<()> {
    let mut cache = cache.clone();
    cache.base_url = normalize_base_url(&cache.base_url)?;
    let key = peek_resolved_api_key(root);
    filter_model_ids(&mut cache.ids, key.as_deref());
    if cache.ids.len() > MAX_MODELS {
        cache.ids.truncate(MAX_MODELS);
        cache.truncated = true;
    }
    let path = models_cache_path(root);
    let json = serde_json::to_string_pretty(&cache)?;
    // Cache is ids only — never raw /models JSON or keys.
    util::write_mode_0600(&path, format!("{json}\n").as_bytes())?;
    Ok(())
}

pub fn validate_model_against_cache(root: &Path, base_url: &str, model: &str) -> Result<()> {
    let Some(cache) = load_models_cache(root) else {
        bail!(
            "no fetched model list for this base_url; run: cloakcli llm models  or  cloakcli llm configure"
        );
    };
    if cache.ids.is_empty() || cache.base_url.is_empty() {
        bail!("fetched model list is missing or corrupt; re-run: cloakcli llm models");
    }
    if base_url.is_empty() {
        bail!("base_url required to validate model against fetched list");
    }
    let base = normalize_base_url(base_url)?;
    if !models_bound_to_request_base(&cache.base_url, &base) {
        bail!("model list is bound to a different base_url; refetch models for the current base");
    }
    if cache.ids.iter().any(|id| id == model) {
        return Ok(());
    }
    // Do not interpolate `model` — it may be a pasted secret.
    bail!(
        "model is not in the last fetched list for this base_url ({} ids{}). \
         Re-run: cloakcli llm models   or   cloakcli llm configure",
        cache.ids.len(),
        if cache.truncated { ", truncated" } else { "" }
    );
}

pub fn format_models_list(list: &crate::llm_client::ModelsList) -> String {
    let mut out = format!(
        "models: {}  truncated={}  pages={}  GET {}\n",
        list.ids.len(),
        list.truncated,
        list.pages,
        list.url
    );
    for (i, id) in list.ids.iter().enumerate() {
        if accept_model_id(id, None).is_err() {
            continue;
        }
        out.push_str(&format!("  {:>3}. {id}\n", i + 1));
    }
    if list.truncated {
        out.push_str(&format!(
            "note: list truncated (caps: body {MAX_MODELS_BODY}B, count {MAX_MODELS}, id {MAX_MODEL_ID_LEN}, pages {MAX_MODELS_PAGES})\n"
        ));
    }
    out
}

fn prompt_line(prompt: &str, default: &str) -> Result<String> {
    eprint!("{prompt}");
    if !default.is_empty() {
        eprint!(" [{default}]");
    }
    eprint!(": ");
    io::stderr().flush().ok();
    let mut s = String::new();
    io::stdin()
        .lock()
        .read_line(&mut s)
        .context("read prompt")?;
    let t = s.trim().to_string();
    if t.is_empty() {
        Ok(default.to_string())
    } else {
        Ok(t)
    }
}

fn pick_from_list(
    ids: &[String],
    model: Option<String>,
    pick: Option<usize>,
    interactive: bool,
) -> Result<String> {
    if let Some(m) = model {
        let m = m.trim().to_string();
        if ids.iter().any(|id| id == &m) {
            return Ok(m);
        }
        bail!(
            "model is not in the fetched list ({} ids). Pass an id from `cloakcli llm models` or --pick N.",
            ids.len()
        );
    }
    if let Some(n) = pick {
        if n == 0 || n > ids.len() {
            bail!("--pick {n} out of range (1..={})", ids.len());
        }
        return Ok(ids[n - 1].clone());
    }
    if interactive && io::stdin().is_terminal() {
        eprint!("select model 1-{} (or id): ", ids.len());
        io::stderr().flush().ok();
        let mut s = String::new();
        io::stdin()
            .lock()
            .read_line(&mut s)
            .context("read model pick")?;
        let t = s.trim();
        if t.is_empty() {
            bail!("no model selected");
        }
        if let Ok(n) = t.parse::<usize>() {
            if n == 0 || n > ids.len() {
                bail!("pick {n} out of range (1..={})", ids.len());
            }
            return Ok(ids[n - 1].clone());
        }
        if ids.iter().any(|id| id == t) {
            return Ok(t.to_string());
        }
        bail!("input is not in the fetched model list");
    }
    bail!("non-interactive configure requires --model ID or --pick N (1-based)");
}

#[derive(Debug, Clone, Default)]
pub struct ConfigureArgs {
    pub base_url: Option<String>,
    pub model: Option<String>,
    pub pick: Option<usize>,
    pub api_key_env: Option<String>,
    pub stdin_key: bool,
    pub enabled: Option<bool>,
    pub recover_timeout_sec: Option<u64>,
    pub allow_hosts: Option<Vec<String>>,
}

pub fn run_configure(root: &Path, args: ConfigureArgs) -> Result<LlmConfig> {
    let interactive = io::stdin().is_terminal() && !args.stdin_key;
    let existing = load(root)?;

    let env_name = match args.api_key_env.as_deref() {
        Some(e) => {
            validate_env_name(e)?;
            e.to_string()
        }
        None => existing
            .as_ref()
            .map(|c| c.api_key_env.clone())
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| DEFAULT_API_KEY_ENV.to_string()),
    };

    let base_url = match args.base_url.as_deref() {
        Some(u) => normalize_base_url(u)?,
        None if interactive => {
            let def = existing
                .as_ref()
                .map(|c| c.base_url.clone())
                .filter(|s| !s.is_empty())
                .unwrap_or_else(|| DEFAULT_BASE_URL.to_string());
            let entered = prompt_line("base_url (OpenAI-compatible /v1)", &def)?;
            normalize_base_url(&entered)?
        }
        None => {
            let from_cfg = existing
                .as_ref()
                .map(|c| c.base_url.clone())
                .filter(|s| !s.is_empty());
            match from_cfg {
                Some(u) => normalize_base_url(&u)?,
                None => bail!("configure requires --base-url (or a TTY to prompt)"),
            }
        }
    };

    let (key, source) = resolve_api_key(&env_name, args.stdin_key, interactive)?;
    let _ = source;
    let list = crate::llm_client::fetch_models(&base_url, &key).map_err(|e| {
        anyhow::anyhow!("{}", redact_secrets(&e.to_string(), Some(&key)))
    })?;
    save_models_cache(
        root,
        &ModelsCache {
            base_url: normalize_base_url(&base_url)?,
            ids: list.ids.clone(),
            truncated: list.truncated,
        },
    )?;
    print!("{}", format_models_list(&list));

    let model = pick_from_list(&list.ids, args.model, args.pick, interactive)?;

    let cfg = apply_set(
        root,
        LlmSetArgs {
            base_url: Some(base_url),
            model: Some(model),
            api_key_env: Some(env_name.clone()),
            enabled: args.enabled,
            recover_timeout_sec: args.recover_timeout_sec,
            allow_hosts: args.allow_hosts,
            ..Default::default()
        },
    )?;
    eprintln!(
        "key: stored api_key_env={env_name} only (not the secret). \
         export {env_name} before worker/recover if it is not already in the environment."
    );
    Ok(cfg)
}

pub fn run_models(
    root: &Path,
    base_url: Option<String>,
    api_key_env: Option<String>,
    stdin_key: bool,
) -> Result<crate::llm_client::ModelsList> {
    let interactive = io::stdin().is_terminal() && !stdin_key;
    let existing = load(root)?;
    let env_name = match api_key_env.as_deref() {
        Some(e) => {
            validate_env_name(e)?;
            e.to_string()
        }
        None => existing
            .as_ref()
            .map(|c| c.api_key_env.clone())
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| DEFAULT_API_KEY_ENV.to_string()),
    };
    let base = match base_url.as_deref() {
        Some(u) => normalize_base_url(u)?,
        None => {
            let u = existing
                .as_ref()
                .map(|c| c.base_url.clone())
                .filter(|s| !s.is_empty())
                .ok_or_else(|| {
                    anyhow::anyhow!("no base_url — pass --base-url or run cloakcli llm configure")
                })?;
            normalize_base_url(&u)?
        }
    };
    let (key, _) = resolve_api_key(&env_name, stdin_key, interactive)?;
    let list = crate::llm_client::fetch_models(&base, &key)
        .map_err(|e| anyhow::anyhow!("{}", redact_secrets(&e.to_string(), Some(&key))))?;
    save_models_cache(
        root,
        &ModelsCache {
            base_url: base,
            ids: list.ids.clone(),
            truncated: list.truncated,
        },
    )?;
    Ok(list)
}

/// IPC wait for a worker cmd. `run_skill` must outlive recover_timeout_sec.
pub fn ipc_timeout_secs_for(root: &Path, cmd: &str) -> u64 {
    let base = state::ipc_timeout_secs();
    if cmd != "run_skill" {
        return base;
    }
    let extra = load(root)
        .ok()
        .flatten()
        .filter(|c| c.enabled)
        .map(|c| c.recover_timeout_sec.saturating_add(60))
        .unwrap_or(0);
    base.max(extra)
}

/// Redact API keys, bearer tokens, cookies, Authorization, proxy userinfo.
pub fn redact_secrets(text: &str, extra: Option<&str>) -> String {
    let mut s = text.to_string();
    if let Some(v) = extra {
        if v.len() >= 4 {
            s = s.replace(v, "***");
        }
    }
    s = redact_proxy_userinfo(&s);
    s = replace_ci_prefix_value(&s, "authorization:", "***");
    s = replace_ci_prefix_value(&s, "authorization=", "***");
    s = regex_like_bearer(&s);
    s = replace_ci_prefix_value(&s, "cookie:", "***");
    s = replace_ci_prefix_value(&s, "cookie=", "***");
    s = strip_json_string_field(&s, "api_key");
    s = strip_json_string_field(&s, "apiKey");
    s
}

fn redact_proxy_userinfo(s: &str) -> String {
    // scheme://user:pass@host → scheme://***:***@host
    let mut out = String::with_capacity(s.len());
    let bytes = s.as_bytes();
    let mut i = 0;
    while i < bytes.len() {
        if let Some(rel) = find_subslice(&bytes[i..], b"://") {
            out.push_str(&s[i..i + rel + 3]);
            i += rel + 3;
            if let Some(at) = bytes[i..].iter().position(|&b| b == b'@') {
                let userinfo = &s[i..i + at];
                if userinfo.contains(':') || !userinfo.is_empty() {
                    // don't treat emails in prose: require no whitespace/newline in userinfo
                    if !userinfo.chars().any(|c| c.is_whitespace()) {
                        out.push_str("***:***");
                        i += at;
                        continue;
                    }
                }
            }
        } else {
            out.push_str(&s[i..]);
            break;
        }
    }
    if out.is_empty() {
        s.to_string()
    } else {
        out
    }
}

fn find_subslice(hay: &[u8], needle: &[u8]) -> Option<usize> {
    hay.windows(needle.len()).position(|w| w == needle)
}

fn replace_ci_prefix_value(s: &str, prefix: &str, replacement: &str) -> String {
    let lower = s.to_ascii_lowercase();
    let p = prefix.to_ascii_lowercase();
    let mut out = String::new();
    let mut last = 0;
    let mut search_from = 0;
    while let Some(pos) = lower[search_from..].find(&p) {
        let abs = search_from + pos;
        out.push_str(&s[last..abs]);
        out.push_str(&s[abs..abs + prefix.len()]);
        let rest = abs + prefix.len();
        let after = s[rest..].trim_start_start();
        let skip_ws = s[rest..].len() - after.len();
        out.push_str(&s[rest..rest + skip_ws]);
        out.push_str(replacement);
        let val_start = rest + skip_ws;
        let val_end = s[val_start..]
            .find(|c: char| c.is_whitespace() || c == '"' || c == ',' || c == '}')
            .map(|n| val_start + n)
            .unwrap_or(s.len());
        last = val_end;
        search_from = val_end;
    }
    out.push_str(&s[last..]);
    out
}

trait TrimStartStart {
    fn trim_start_start(&self) -> &str;
}
impl TrimStartStart for str {
    fn trim_start_start(&self) -> &str {
        self.trim_start()
    }
}

fn regex_like_bearer(s: &str) -> String {
    let lower = s.to_ascii_lowercase();
    let mut out = String::new();
    let mut last = 0;
    let mut search = 0;
    while let Some(pos) = lower[search..].find("bearer ") {
        let abs = search + pos;
        out.push_str(&s[last..abs]);
        out.push_str("Bearer ***");
        let rest = abs + "bearer ".len();
        let val_end = s[rest..]
            .find(|c: char| c.is_whitespace() || c == '"' || c == ',' || c == '}')
            .map(|n| rest + n)
            .unwrap_or(s.len());
        last = val_end;
        search = val_end;
    }
    out.push_str(&s[last..]);
    out
}

fn strip_json_string_field(s: &str, field: &str) -> String {
    let needle = format!("\"{field}\"");
    let mut out = String::new();
    let mut last = 0;
    let mut search = 0;
    while let Some(pos) = s[search..].find(&needle) {
        let abs = search + pos;
        let after_key = abs + needle.len();
        let rest = s[after_key..].trim_start();
        if let Some(colon) = rest.strip_prefix(':') {
            let val = colon.trim_start();
            if let Some(stripped) = val.strip_prefix('"') {
                if let Some(end) = stripped.find('"') {
                    out.push_str(&s[last..after_key]);
                    let colon_off = s[after_key..].find(':').unwrap_or(0);
                    let colon_abs = after_key + colon_off;
                    let after_colon = &s[colon_abs + 1..];
                    let quote_rel = after_colon.find('"').unwrap_or(0);
                    let open = colon_abs + 1 + quote_rel;
                    out.push_str(&s[after_key..open + 1]);
                    out.push_str("***");
                    last = open + 1 + end + 1;
                    search = last;
                    continue;
                }
            }
        }
        search = abs + needle.len();
    }
    out.push_str(&s[last..]);
    out
}

pub fn format_show(view: &LlmView) -> String {
    if !view.configured {
        return format!(
            "llm: not configured\n  path: {}\n  hint: cloakcli llm configure --base-url URL\n  key:  export {}  (or {}; never --api-key)\n",
            view.path, DEFAULT_API_KEY_ENV, COMPAT_API_KEY_ENV
        );
    }
    let key = if view.key_present { "set" } else { "missing" };
    let hosts = if view.allow_hosts.is_empty() {
        "(none — same-origin goto only)".into()
    } else {
        view.allow_hosts.join(", ")
    };
    format!(
        "enabled:              {}\n\
         model:                {}\n\
         base_url:             {}\n\
         api_key_env:          {} ({key})\n\
         recover_timeout_sec:  {}\n\
         allow_hosts:          {hosts}\n\
         max_actions:          {}\n\
         max_loops:            {}\n\
         config:               {}\n",
        view.enabled,
        view.model,
        view.base_url,
        view.api_key_env,
        view.recover_timeout_sec,
        view.max_actions,
        view.max_loops,
        view.path
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn tmp_root() -> std::path::PathBuf {
        let n = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let p = std::env::temp_dir().join(format!("cloakcli_llm_test_{n}"));
        let _ = fs::remove_dir_all(&p);
        fs::create_dir_all(p.join("skills")).unwrap();
        fs::write(p.join("Cargo.toml"), "[package]\nname=\"t\"\nversion=\"0.0.0\"\n").unwrap();
        p
    }

    fn seed_cache(root: &std::path::Path, base: &str, ids: &[&str]) {
        save_models_cache(
            root,
            &ModelsCache {
                base_url: normalize_base_url(base).unwrap(),
                ids: ids.iter().map(|s| (*s).to_string()).collect(),
                truncated: false,
            },
        )
        .unwrap();
    }

    #[test]
    fn save_mode_0600_and_no_raw_key() {
        let root = tmp_root();
        seed_cache(&root, "https://api.openai.com/v1", &["gpt-4o"]);
        let cfg = apply_set(
            &root,
            LlmSetArgs {
                base_url: Some("https://api.openai.com/v1".into()),
                model: Some("gpt-4o".into()),
                api_key_env: Some("OPENAI_API_KEY".into()),
                enabled: Some(true),
                recover_timeout_sec: Some(300),
                ..Default::default()
            },
        )
        .unwrap();
        assert_eq!(cfg.recover_timeout_sec, 300);
        let path = config_path(&root);
        let meta = fs::metadata(&path).unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            assert_eq!(meta.permissions().mode() & 0o777, 0o600);
        }
        let text = fs::read_to_string(&path).unwrap();
        assert!(!text.contains("sk-"));
        assert!(!text.to_ascii_lowercase().contains("api_key\":"));
        assert!(text.contains("api_key_env"));
        assert!(text.contains("OPENAI_API_KEY"));
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn rejects_raw_key_as_env_name() {
        let root = tmp_root();
        let secret = "sk-thisisarealsecretkeyvalue";
        let err = apply_set(
            &root,
            LlmSetArgs {
                base_url: Some("https://api.openai.com/v1".into()),
                model: Some("gpt-4o".into()),
                api_key_env: Some(secret.into()),
                ..Default::default()
            },
        )
        .unwrap_err()
        .to_string();
        assert!(err.contains("env"), "{err}");
        assert!(!err.contains(secret), "echoed pasted key: {err}");
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn rejects_file_base_url() {
        let root = tmp_root();
        let err = apply_set(
            &root,
            LlmSetArgs {
                base_url: Some("file:///etc/passwd".into()),
                model: Some("x".into()),
                api_key_env: Some("OPENAI_API_KEY".into()),
                ..Default::default()
            },
        )
        .unwrap_err();
        assert!(
            err.to_string().contains("http") || err.to_string().contains("rejects"),
            "{err}"
        );
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn default_timeout_is_300() {
        assert_eq!(DEFAULT_RECOVER_TIMEOUT_SEC, 300);
        let c = LlmConfig::default();
        assert_eq!(c.recover_timeout_sec, 300);
    }

    #[test]
    fn redact_strips_authorization_and_proxy() {
        let s = redact_secrets(
            "Authorization: Bearer sk-secretTEST99 cookie=abc proxy=http://user:pw@host:1",
            Some("sk-secretTEST99"),
        );
        assert!(!s.contains("sk-secretTEST99"), "{s}");
        assert!(!s.contains("user:pw"), "{s}");
        assert!(s.contains("***"));
    }

    #[test]
    fn ipc_timeout_extends_when_recover_enabled() {
        let root = tmp_root();
        seed_cache(&root, "https://example.com/v1", &["m"]);
        apply_set(
            &root,
            LlmSetArgs {
                base_url: Some("https://example.com/v1".into()),
                model: Some("m".into()),
                api_key_env: Some("OPENAI_API_KEY".into()),
                enabled: Some(true),
                recover_timeout_sec: Some(300),
                ..Default::default()
            },
        )
        .unwrap();
        let t = ipc_timeout_secs_for(&root, "run_skill");
        assert!(t >= 360, "got {t}");
        let ping = ipc_timeout_secs_for(&root, "ping");
        assert_eq!(ping, state::ipc_timeout_secs());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn view_never_includes_env_value() {
        let root = tmp_root();
        seed_cache(&root, "https://api.example.com/v1", &["vision"]);
        apply_set(
            &root,
            LlmSetArgs {
                base_url: Some("https://api.example.com/v1".into()),
                model: Some("vision".into()),
                api_key_env: Some("CLOAKCLI_LLM_TEST_KEY".into()),
                enabled: Some(true),
                ..Default::default()
            },
        )
        .unwrap();
        env::set_var("CLOAKCLI_LLM_TEST_KEY", "super-secret-value-xyz");
        let v = view(&root);
        let shown = format_show(&v);
        assert!(!shown.contains("super-secret-value-xyz"));
        assert!(shown.contains("CLOAKCLI_LLM_TEST_KEY"));
        assert!(shown.contains("(set)"));
        env::remove_var("CLOAKCLI_LLM_TEST_KEY");
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn normalize_host_from_url() {
        assert_eq!(
            normalize_host("https://Example.COM:443/foo").unwrap(),
            "example.com"
        );
        assert_eq!(normalize_host("*.example.com").unwrap(), "*.example.com");
    }

    #[test]
    fn default_api_key_env_is_cloakcli() {
        assert_eq!(LlmConfig::default().api_key_env, DEFAULT_API_KEY_ENV);
        assert_eq!(DEFAULT_API_KEY_ENV, "CLOAKCLI_LLM_API_KEY");
    }

    #[test]
    fn normalize_base_url_strips_slash_and_endpoints() {
        assert_eq!(
            normalize_base_url("https://api.openai.com/v1/").unwrap(),
            "https://api.openai.com/v1"
        );
        assert_eq!(
            normalize_base_url("https://api.openai.com/v1/models").unwrap(),
            "https://api.openai.com/v1"
        );
        assert_eq!(
            normalize_base_url("https://api.openai.com/v1/chat/completions").unwrap(),
            "https://api.openai.com/v1"
        );
        assert_eq!(
            normalize_base_url("https://api.openai.com/v1/v1").unwrap(),
            "https://api.openai.com/v1"
        );
    }

    #[test]
    fn join_does_not_duplicate_v1() {
        let base = "https://api.openai.com/v1";
        assert_eq!(
            join_openai_path(base, "models").unwrap(),
            "https://api.openai.com/v1/models"
        );
        assert_eq!(
            join_openai_path(base, "/v1/models").unwrap(),
            "https://api.openai.com/v1/models"
        );
        assert_eq!(
            join_openai_path(base, "chat/completions").unwrap(),
            "https://api.openai.com/v1/chat/completions"
        );
        assert_eq!(
            join_openai_path(base, "v1/chat/completions").unwrap(),
            "https://api.openai.com/v1/chat/completions"
        );
        assert_eq!(
            models_url(base).unwrap(),
            "https://api.openai.com/v1/models"
        );
        assert_eq!(
            chat_completions_url(base).unwrap(),
            "https://api.openai.com/v1/chat/completions"
        );
    }

    #[test]
    fn rejects_javascript_and_data_urls() {
        for u in ["javascript:alert(1)", "data:text/plain,hi", "file:///etc/passwd"] {
            let err = validate_base_url(u).unwrap_err().to_string();
            assert!(
                err.contains("http") || err.contains("rejects"),
                "{u}: {err}"
            );
        }
    }

    #[test]
    fn reject_api_key_argv_does_not_echo_value() {
        let err = reject_api_key_argv(["cloakcli", "llm", "configure", "--api-key", "sk-must-not-echo"])
            .unwrap_err()
            .to_string();
        assert!(err.contains("--api-key"));
        assert!(err.contains("shell history"));
        assert!(!err.contains("sk-must-not-echo"));
        reject_api_key_argv(["cloakcli", "llm", "set", "--api-key-env", "CLOAKCLI_LLM_API_KEY"])
            .unwrap();
        let err2 = reject_api_key_argv(["--api-key=sk-equals-form-secret"])
            .unwrap_err()
            .to_string();
        assert!(!err2.contains("sk-equals-form-secret"));
    }

    #[test]
    fn set_model_validates_against_last_fetch() {
        let root = tmp_root();
        seed_cache(
            &root,
            "https://api.example.com/v1",
            &["vision", "other"],
        );
        apply_set(
            &root,
            LlmSetArgs {
                base_url: Some("https://api.example.com/v1".into()),
                model: Some("vision".into()),
                api_key_env: Some("CLOAKCLI_LLM_API_KEY".into()),
                enabled: Some(true),
                ..Default::default()
            },
        )
        .unwrap();
        apply_set(
            &root,
            LlmSetArgs {
                model: Some("other".into()),
                ..Default::default()
            },
        )
        .unwrap();
        let err = apply_set(
            &root,
            LlmSetArgs {
                model: Some("not-in-list".into()),
                ..Default::default()
            },
        )
        .unwrap_err()
        .to_string();
        assert!(err.contains("last fetched"), "{err}");
        assert!(!err.contains("not-in-list"), "must not echo rejected id: {err}");
        assert!(!err.contains("sk-"));
        let cache_text = fs::read_to_string(models_cache_path(&root)).unwrap();
        assert!(!cache_text.contains("api_key"));
        assert!(!cache_text.to_ascii_lowercase().contains("bearer"));
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn set_model_rejects_cache_miss_corrupt_and_base_mismatch() {
        let root = tmp_root();
        let miss = apply_set(
            &root,
            LlmSetArgs {
                base_url: Some("https://api.example.com/v1".into()),
                model: Some("vision".into()),
                api_key_env: Some("CLOAKCLI_LLM_API_KEY".into()),
                ..Default::default()
            },
        )
        .unwrap_err()
        .to_string();
        assert!(miss.contains("no fetched model list"), "{miss}");
        assert!(!miss.contains("vision"), "{miss}");

        fs::create_dir_all(state::config_dir(&root)).unwrap();
        fs::write(models_cache_path(&root), "{not-json").unwrap();
        let corrupt = apply_set(
            &root,
            LlmSetArgs {
                base_url: Some("https://api.example.com/v1".into()),
                model: Some("vision".into()),
                api_key_env: Some("CLOAKCLI_LLM_API_KEY".into()),
                ..Default::default()
            },
        )
        .unwrap_err()
        .to_string();
        assert!(
            corrupt.contains("no fetched") || corrupt.contains("corrupt"),
            "{corrupt}"
        );

        seed_cache(&root, "https://api.example.com/v1", &["vision"]);
        let mismatch = apply_set(
            &root,
            LlmSetArgs {
                base_url: Some("https://other.example.com/v1".into()),
                model: Some("vision".into()),
                api_key_env: Some("CLOAKCLI_LLM_API_KEY".into()),
                ..Default::default()
            },
        )
        .unwrap_err()
        .to_string();
        assert!(mismatch.contains("different base_url"), "{mismatch}");
        assert!(!mismatch.contains("vision"), "{mismatch}");
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn show_never_prints_env_value_or_raw_key_field() {
        let root = tmp_root();
        seed_cache(&root, "https://api.example.com/v1", &["vision"]);
        apply_set(
            &root,
            LlmSetArgs {
                base_url: Some("https://api.example.com/v1".into()),
                model: Some("vision".into()),
                api_key_env: Some(DEFAULT_API_KEY_ENV.into()),
                ..Default::default()
            },
        )
        .unwrap();
        env::set_var(DEFAULT_API_KEY_ENV, "sk-super-secret-show-test");
        let shown = format_show(&view(&root));
        assert!(!shown.contains("sk-super-secret-show-test"));
        assert!(!shown.to_ascii_lowercase().contains("sk-super"));
        assert!(shown.contains(DEFAULT_API_KEY_ENV));
        env::remove_var(DEFAULT_API_KEY_ENV);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn resolve_key_falls_back_to_openai_compat() {
        env::remove_var(DEFAULT_API_KEY_ENV);
        env::set_var(COMPAT_API_KEY_ENV, "sk-compat-only");
        let v = resolve_api_key_from_env(DEFAULT_API_KEY_ENV).unwrap();
        assert_eq!(v, "sk-compat-only");
        env::remove_var(COMPAT_API_KEY_ENV);
    }

    #[test]
    fn normalize_rejects_query_fragment_credentials_and_keeps_v10() {
        for u in [
            "https://api.openai.com/v1?x=1",
            "https://api.openai.com/v1#frag",
            "https://user:pass@api.openai.com/v1",
            "https://user@api.openai.com/v1",
        ] {
            let err = normalize_base_url(u).unwrap_err().to_string();
            assert!(
                err.contains("query")
                    || err.contains("fragment")
                    || err.contains("credentials"),
                "{u}: {err}"
            );
            assert!(!err.contains("user:pass"), "{err}");
            assert!(!err.contains("pass@"), "{err}");
        }
        assert_eq!(
            normalize_base_url("https://api.openai.com/v1/v10").unwrap(),
            "https://api.openai.com/v1/v10"
        );
        assert_eq!(
            join_openai_path("https://api.openai.com/v1/v10", "models").unwrap(),
            "https://api.openai.com/v1/v10/models"
        );
        let miss = normalize_base_url("https:///v1").unwrap_err().to_string();
        assert!(miss.contains("host") || miss.contains("http"), "{miss}");
    }

    #[test]
    fn accept_model_id_rejects_key_and_control_chars_without_echo() {
        let key = "sk-live-secret-abc";
        let err = accept_model_id(key, Some(key)).unwrap_err().to_string();
        assert!(err.contains("rejected"), "{err}");
        assert!(!err.contains(key), "{err}");
        let embedded = format!("model-{key}-id");
        let err2 = accept_model_id(&embedded, Some(key))
            .unwrap_err()
            .to_string();
        assert!(!err2.contains(key), "{err2}");
        let ctrl = "gpt-4o\u{0007}bell";
        let err3 = accept_model_id(ctrl, None).unwrap_err().to_string();
        assert!(err3.contains("control"), "{err3}");
        assert!(!err3.contains("gpt-4o"), "{err3}");
        assert_eq!(accept_model_id("gpt-4o", Some(key)).unwrap(), "gpt-4o");
    }

    #[test]
    fn models_list_is_bound_to_request_base() {
        assert!(models_bound_to_request_base(
            "https://api.example.com/v1",
            "https://api.example.com/v1/"
        ));
        assert!(!models_bound_to_request_base(
            "https://api.example.com/v1",
            "https://other.example.com/v1"
        ));
        assert!(!models_bound_to_request_base(
            "",
            "https://api.example.com/v1"
        ));
        assert!(!models_bound_to_request_base(
            "https://api.example.com/v1?x=1",
            "https://api.example.com/v1"
        ));
    }

    #[test]
    fn cache_drops_ids_that_contain_the_api_key() {
        let root = tmp_root();
        let key = "sk-cache-secret-xyz";
        env::set_var(DEFAULT_API_KEY_ENV, key);
        fs::create_dir_all(state::config_dir(&root)).unwrap();
        fs::write(
            models_cache_path(&root),
            format!(
                "{{\"base_url\":\"https://api.example.com/v1\",\"ids\":[\"ok\",\"{key}\"],\"truncated\":false}}\n"
            ),
        )
        .unwrap();
        let cache = load_models_cache(&root).expect("cache");
        assert_eq!(cache.ids, vec!["ok"]);
        assert!(!cache.ids.iter().any(|id| id.contains(key)));
        env::remove_var(DEFAULT_API_KEY_ENV);
        let _ = fs::remove_dir_all(&root);
    }
}
