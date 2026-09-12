//! LLM stall-recovery config: `config/llm.json` (mode 0600).
//! API keys are referenced by environment variable name only — never stored.

use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use std::env;
use std::fs;
use std::path::Path;

use crate::state;
use crate::util;

pub const DEFAULT_RECOVER_TIMEOUT_SEC: u64 = 300;
pub const DEFAULT_MAX_ACTIONS: u32 = 120;
pub const DEFAULT_MAX_LOOPS: u32 = 60;
pub const DEFAULT_MAX_TOKENS: u32 = 200_000;
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
            api_key_env: "OPENAI_API_KEY".into(),
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
    cfg.base_url = cfg.base_url.trim().trim_end_matches('/').to_string();
    cfg.model = cfg.model.trim().to_string();
    cfg.api_key_env = cfg.api_key_env.trim().to_string();
    if !cfg.api_key_env.is_empty() {
        validate_env_name(&cfg.api_key_env)?;
    }
    if !cfg.base_url.is_empty() {
        validate_base_url(&cfg.base_url)?;
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
    let ok = !name.is_empty()
        && name.len() <= 128
        && name
            .chars()
            .enumerate()
            .all(|(i, c)| {
                if i == 0 {
                    c.is_ascii_alphabetic() || c == '_'
                } else {
                    c.is_ascii_alphanumeric() || c == '_'
                }
            });
    if !ok {
        bail!("invalid api_key_env '{name}': use an environment variable name, not a raw key");
    }
    // Reject values that look like keys (sk-..., Bearer, long secrets)
    if name.contains('-') || name.len() > 64 {
        bail!("invalid api_key_env '{name}': looks like a secret, not an env var name");
    }
    Ok(())
}

pub fn validate_base_url(url: &str) -> Result<()> {
    let lower = url.to_ascii_lowercase();
    if !(lower.starts_with("https://") || lower.starts_with("http://")) {
        bail!("base_url must be http(s):// (got scheme-less or blocked URL)");
    }
    if lower.starts_with("file:") || lower.starts_with("javascript:") || lower.starts_with("data:") {
        bail!("base_url rejects file:/javascript:/data:");
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

    if let Some(u) = args.base_url {
        validate_base_url(&u)?;
        cfg.base_url = u.trim().trim_end_matches('/').to_string();
    }
    if let Some(m) = args.model {
        let m = m.trim().to_string();
        if m.is_empty() {
            bail!("model must not be empty");
        }
        cfg.model = m;
    }
    if let Some(e) = args.api_key_env {
        validate_env_name(&e)?;
        cfg.api_key_env = e;
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
        anyhow::anyhow!("no config/llm.json — run: cloakcli llm set --base-url … --model … --api-key-env …")
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
            key_present: env_is_set(&c.api_key_env),
            recover_timeout_sec: c.recover_timeout_sec,
            allow_hosts: c.allow_hosts.clone(),
            max_actions: c.max_actions,
            max_loops: c.max_loops,
            path,
        },
    }
}

fn env_is_set(name: &str) -> bool {
    if name.is_empty() {
        return false;
    }
    match env::var(name) {
        Ok(v) => !v.is_empty(),
        Err(_) => false,
    }
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
            "llm: not configured\n  path: {}\n  hint: cloakcli llm set --base-url URL --model MODEL --api-key-env VAR\n",
            view.path
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

    #[test]
    fn save_mode_0600_and_no_raw_key() {
        let root = tmp_root();
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
        let err = apply_set(
            &root,
            LlmSetArgs {
                base_url: Some("https://api.openai.com/v1".into()),
                model: Some("gpt-4o".into()),
                api_key_env: Some("sk-thisisarealsecretkeyvalue".into()),
                ..Default::default()
            },
        )
        .unwrap_err();
        assert!(err.to_string().contains("env"));
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
        assert!(err.to_string().contains("http"));
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
}
