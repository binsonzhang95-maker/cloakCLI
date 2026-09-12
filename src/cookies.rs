//! Profile cookie import/export — Playwright storage_state compatible.
//! Never log or display cookie values.

use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeSet;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

#[cfg(unix)]
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};

use crate::profiles;
use crate::util;

/// Playwright-compatible storage state on disk.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StorageState {
    pub cookies: Vec<Value>,
    #[serde(default)]
    pub origins: Vec<Value>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CookieFormat {
    Auto,
    StorageState,
    CookiesJson,
}

impl CookieFormat {
    pub fn parse(s: &str) -> Result<Self> {
        match s.trim().to_ascii_lowercase().as_str() {
            "auto" => Ok(Self::Auto),
            "storage-state" | "storage_state" | "storagestate" => Ok(Self::StorageState),
            "cookies-json" | "cookies_json" | "cookiesjson" | "cookies" => Ok(Self::CookiesJson),
            other => bail!("unknown cookie format '{other}' (use auto|storage-state|cookies-json)"),
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct CookieStatus {
    pub profile: String,
    pub present: bool,
    pub cookie_count: usize,
    /// Cookies that are session (-1/omitted) or not yet expired.
    pub valid_count: usize,
    /// Cookies with expires unix timestamp in the past.
    pub expired_count: usize,
    pub origin_count: usize,
    pub domains: Vec<String>,
    pub path: String,
}

/// Path to profiles/<name>/cookie.json (after dir layout ensured).
pub fn cookie_path(root: &Path, name: &str) -> PathBuf {
    profiles::profile_dir(root, name).join("cookie.json")
}

/// Resolve cookie.json for worker IPC.
///
/// - Ensures dir layout (flat→directory migrate).
/// - Returns `Ok(None)` when the file is absent.
/// - On present path: must be a **regular file** (no symlinks), canonicalized under
///   that profile directory, and schema-valid — otherwise `Err` with `INVALID_COOKIE`
///   (open must fail clearly, not silently skip).
pub fn cookie_file_for_open(root: &Path, name: &str) -> Result<Option<String>> {
    util::validate_name(name, "profile")?;
    let _ = profiles::get(root, name)?;
    profiles::ensure_dir_layout(root, name)?;

    let p = cookie_path(root, name);
    if !p.exists() {
        return Ok(None);
    }

    let meta = fs::symlink_metadata(&p).with_context(|| {
        format!("INVALID_COOKIE: cannot stat cookie file {}", p.display())
    })?;

    if meta.file_type().is_symlink() {
        bail!("INVALID_COOKIE: cookie.json is a symlink (rejected)");
    }
    if !meta.file_type().is_file() {
        bail!("INVALID_COOKIE: cookie.json is not a regular file");
    }

    let profile_dir = profiles::profile_dir(root, name);
    let profile_canon = profile_dir.canonicalize().with_context(|| {
        format!(
            "INVALID_COOKIE: cannot canonicalize profile dir {}",
            profile_dir.display()
        )
    })?;
    let canon = p.canonicalize().with_context(|| {
        format!(
            "INVALID_COOKIE: cannot canonicalize cookie file {}",
            p.display()
        )
    })?;

    if !canon.starts_with(&profile_canon) {
        bail!("INVALID_COOKIE: cookie path escapes profile directory");
    }
    if canon.file_name().and_then(|s| s.to_str()) != Some("cookie.json") {
        bail!("INVALID_COOKIE: unexpected cookie file name after resolve");
    }

    // Fail open clearly if corrupt / unreadable (do not silent-skip).
    let state = load_storage_state(&canon).map_err(|e| {
        anyhow::anyhow!("INVALID_COOKIE: cookie file unreadable or corrupt: {e}")
    })?;
    validate_cookie_entries(&state)?;

    Ok(Some(canon.to_string_lossy().to_string()))
}

pub fn status(root: &Path, name: &str) -> Result<CookieStatus> {
    util::validate_name(name, "profile")?;
    let _ = profiles::get(root, name)?;
    profiles::ensure_dir_layout(root, name)?;
    let path = cookie_path(root, name);
    if !path.is_file() {
        return Ok(CookieStatus {
            profile: name.to_string(),
            present: false,
            cookie_count: 0,
            valid_count: 0,
            expired_count: 0,
            origin_count: 0,
            domains: vec![],
            path: path.to_string_lossy().to_string(),
        });
    }
    let state = load_storage_state(&path)?;
    validate_cookie_entries(&state)?;
    Ok(status_from_state(name, &path, &state))
}

fn status_from_state(name: &str, path: &Path, state: &StorageState) -> CookieStatus {
    let mut domains: BTreeSet<String> = BTreeSet::new();
    let mut expired_count = 0usize;
    let mut valid_count = 0usize;
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0);

    for c in &state.cookies {
        if let Some(d) = c.get("domain").and_then(|v| v.as_str()) {
            if !d.is_empty() {
                domains.insert(d.to_string());
            }
        }
        if cookie_is_expired(c, now) {
            expired_count += 1;
        } else {
            valid_count += 1;
        }
    }
    CookieStatus {
        profile: name.to_string(),
        present: true,
        cookie_count: state.cookies.len(),
        valid_count,
        expired_count,
        origin_count: state.origins.len(),
        domains: domains.into_iter().collect(),
        path: path.to_string_lossy().to_string(),
    }
}

fn cookie_is_expired(c: &Value, now_unix: i64) -> bool {
    match c.get("expires") {
        None => false, // session / unspecified → treat as valid
        Some(Value::Number(n)) => {
            if let Some(exp) = n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)) {
                // Playwright: -1 = session cookie
                if exp < 0 {
                    return false;
                }
                exp > 0 && exp < now_unix
            } else {
                false
            }
        }
        Some(Value::Null) => false,
        _ => false,
    }
}

/// Human summary for TUI — never includes values.
pub fn status_summary(root: &Path, name: &str) -> String {
    match status(root, name) {
        Ok(s) if !s.present => "none".into(),
        Ok(s) => {
            let dom = if s.domains.is_empty() {
                "-".into()
            } else if s.domains.len() <= 3 {
                s.domains.join(",")
            } else {
                format!(
                    "{},…(+{})",
                    s.domains.iter().take(2).cloned().collect::<Vec<_>>().join(","),
                    s.domains.len() - 2
                )
            };
            if s.expired_count > 0 {
                format!(
                    "{} valid/{} exp [{}]",
                    s.valid_count, s.expired_count, dom
                )
            } else {
                format!("{} [{}]", s.cookie_count, dom)
            }
        }
        Err(_) => "err".into(),
    }
}

pub fn import(
    root: &Path,
    name: &str,
    file: &Path,
    format: CookieFormat,
) -> Result<CookieStatus> {
    util::validate_name(name, "profile")?;
    let _ = profiles::get(root, name)?;
    profiles::ensure_dir_layout(root, name)?;

    let text = fs::read_to_string(file)
        .with_context(|| format!("read cookie file {}", file.display()))?;
    let state = parse_import(&text, format)?;
    validate_storage_state(&state)?;

    let dest = cookie_path(root, name);
    write_storage_state_atomic(&dest, &state)?;
    Ok(status_from_state(name, &dest, &state))
}

pub fn export(root: &Path, name: &str, out: Option<&Path>) -> Result<CookieStatus> {
    util::validate_name(name, "profile")?;
    let _ = profiles::get(root, name)?;
    profiles::ensure_dir_layout(root, name)?;
    let path = cookie_path(root, name);
    if !path.is_file() {
        bail!("no cookie.json for profile {name} (import first)");
    }
    let state = load_storage_state(&path)?;
    validate_cookie_entries(&state)?;
    let json = serde_json::to_string_pretty(&state)?;
    let body = format!("{json}\n");
    match out {
        None => {
            print!("{body}");
        }
        Some(p) => {
            let dest = validate_export_out(root, p)?;
            write_storage_state_atomic(&dest, &state)?;
        }
    }
    Ok(status_from_state(name, &path, &state))
}

/// Export `--out` policy (Astra review):
/// - Refuse directory targets.
/// - Refuse writing into project `data/` or `artifacts/` (public/runtime dirs).
/// - Allowed: absolute paths outside those dirs, or relative paths that resolve
///   outside them (e.g. `/tmp/foo.json`, `./my-export.json` under repo root but
///   not under `data/`/`artifacts/`). Mode `0600` always applied (incl. existing).
pub fn validate_export_out(root: &Path, out: &Path) -> Result<PathBuf> {
    if out.as_os_str().is_empty() {
        bail!("export --out path is empty");
    }

    let candidate = if out.is_absolute() {
        out.to_path_buf()
    } else {
        // Relative to process cwd (CLI convention), not forced under root.
        std::env::current_dir()
            .unwrap_or_else(|_| root.to_path_buf())
            .join(out)
    };

    if candidate.exists() && candidate.is_dir() {
        bail!(
            "export --out refuses directory targets: {}",
            candidate.display()
        );
    }

    // Build a path we can prefix-check even if the file does not exist yet.
    let check = if candidate.exists() {
        candidate
            .canonicalize()
            .with_context(|| format!("canonicalize export target {}", candidate.display()))?
    } else {
        let parent = candidate.parent().unwrap_or_else(|| Path::new("."));
        if parent.exists() {
            let parent_c = parent.canonicalize().with_context(|| {
                format!("canonicalize export parent {}", parent.display())
            })?;
            let fname = candidate.file_name().context("export --out has no file name")?;
            parent_c.join(fname)
        } else {
            candidate.clone()
        }
    };

    for (label, sub) in [("data", "data"), ("artifacts", "artifacts")] {
        let forbidden = root.join(sub);
        let hits = if let Ok(fc) = forbidden.canonicalize() {
            check.starts_with(&fc)
        } else {
            // data/ may not exist yet — still block intended path under root/data
            check.starts_with(&forbidden) || candidate.starts_with(&forbidden)
        };
        if hits {
            bail!(
                "export --out refuses writing into project {label}/ (public/runtime dir): {}",
                check.display()
            );
        }
    }

    Ok(candidate)
}

/// Write export body to a string (for tests / non-stdout).
pub fn export_string(root: &Path, name: &str) -> Result<(String, CookieStatus)> {
    util::validate_name(name, "profile")?;
    let _ = profiles::get(root, name)?;
    profiles::ensure_dir_layout(root, name)?;
    let path = cookie_path(root, name);
    if !path.is_file() {
        bail!("no cookie.json for profile {name}");
    }
    let state = load_storage_state(&path)?;
    validate_cookie_entries(&state)?;
    let json = serde_json::to_string_pretty(&state)?;
    Ok((format!("{json}\n"), status_from_state(name, &path, &state)))
}

pub fn clear(root: &Path, name: &str) -> Result<bool> {
    util::validate_name(name, "profile")?;
    let _ = profiles::get(root, name)?;
    profiles::ensure_dir_layout(root, name)?;
    let path = cookie_path(root, name);
    if path.is_file() {
        fs::remove_file(&path)?;
        Ok(true)
    } else {
        Ok(false)
    }
}

pub fn parse_import(text: &str, format: CookieFormat) -> Result<StorageState> {
    let trimmed = text.trim();
    if trimmed.is_empty() {
        bail!("empty cookie file");
    }
    let value: Value = serde_json::from_str(trimmed).context("cookie file is not valid JSON")?;
    match format {
        CookieFormat::StorageState => parse_storage_state_value(value),
        CookieFormat::CookiesJson => parse_cookies_json_value(value),
        CookieFormat::Auto => detect_and_parse(value),
    }
}

fn detect_and_parse(value: Value) -> Result<StorageState> {
    match &value {
        Value::Array(_) => parse_cookies_json_value(value),
        Value::Object(map) => {
            if map.contains_key("cookies") || map.contains_key("origins") {
                parse_storage_state_value(value)
            } else {
                bail!("auto-detect failed: expected storage_state object or cookies array");
            }
        }
        _ => bail!("auto-detect failed: expected JSON object or array"),
    }
}

fn parse_storage_state_value(value: Value) -> Result<StorageState> {
    let obj = value
        .as_object()
        .context("storage-state must be a JSON object")?;
    let cookies = match obj.get("cookies") {
        None => Vec::new(),
        Some(Value::Array(a)) => a.clone(),
        Some(_) => bail!("storage-state.cookies must be an array"),
    };
    let origins = match obj.get("origins") {
        None => Vec::new(),
        Some(Value::Array(a)) => a.clone(),
        Some(_) => bail!("storage-state.origins must be an array"),
    };
    Ok(StorageState { cookies, origins })
}

fn parse_cookies_json_value(value: Value) -> Result<StorageState> {
    let cookies = match value {
        Value::Array(a) => a,
        Value::Object(map) => match map.get("cookies") {
            Some(Value::Array(a)) => a.clone(),
            Some(_) => bail!("cookies-json.cookies must be an array"),
            None => bail!("cookies-json object must have a cookies array (or pass a bare array)"),
        },
        _ => bail!("cookies-json must be an array or {{cookies:[...]}}"),
    };
    Ok(StorageState {
        cookies,
        origins: vec![],
    })
}

/// Full import validation: non-empty + per-cookie schema.
fn validate_storage_state(state: &StorageState) -> Result<()> {
    if state.cookies.is_empty() && state.origins.is_empty() {
        bail!("INVALID_COOKIE: no cookies or origins to import");
    }
    validate_cookie_entries(state)
}

/// Per-cookie schema (also used on open/status/export load). Never trusts file blindly.
fn validate_cookie_entries(state: &StorageState) -> Result<()> {
    for (i, c) in state.cookies.iter().enumerate() {
        let obj = c.as_object().with_context(|| {
            format!("INVALID_COOKIE: cookie[{i}] must be an object")
        })?;
        let name = obj
            .get("name")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty());
        let value = obj.get("value");
        if name.is_none() {
            bail!("INVALID_COOKIE: cookie[{i}] missing name");
        }
        if value.is_none() {
            bail!("INVALID_COOKIE: cookie[{i}] missing value");
        }
        // Playwright needs domain or url
        let has_domain = obj
            .get("domain")
            .and_then(|v| v.as_str())
            .map(|s| !s.is_empty())
            .unwrap_or(false);
        let has_url = obj
            .get("url")
            .and_then(|v| v.as_str())
            .map(|s| !s.is_empty())
            .unwrap_or(false);
        if !has_domain && !has_url {
            bail!("INVALID_COOKIE: cookie[{i}] needs domain or url");
        }
    }
    for (i, o) in state.origins.iter().enumerate() {
        if !o.is_object() {
            bail!("INVALID_COOKIE: origins[{i}] must be an object");
        }
    }
    Ok(())
}

fn load_storage_state(path: &Path) -> Result<StorageState> {
    let text = fs::read_to_string(path).with_context(|| format!("read {}", path.display()))?;
    let value: Value =
        serde_json::from_str(&text).with_context(|| format!("parse {}", path.display()))?;
    // Accept only object root (storage_state shape); reject arrays/scalars.
    let state = parse_storage_state_value(value)
        .with_context(|| format!("INVALID_COOKIE: bad storage_state in {}", path.display()))?;
    Ok(state)
}

pub fn write_storage_state_atomic(path: &Path, state: &StorageState) -> Result<()> {
    let json = serde_json::to_string_pretty(state)?;
    let body = format!("{json}\n");
    atomic_write_0600(path, body.as_bytes())
}

/// Atomic write + mode 0600 (unix). Temp file in same directory then rename.
/// Always re-applies 0600 after rename (covers overwriting an existing 0644 target).
pub fn atomic_write_0600(path: &Path, data: &[u8]) -> Result<()> {
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .map(|p| p.to_path_buf())
        .unwrap_or_else(|| PathBuf::from("."));
    fs::create_dir_all(&parent)
        .with_context(|| format!("create parent {}", parent.display()))?;

    let fname = path
        .file_name()
        .and_then(|s| s.to_str())
        .unwrap_or("cookie.json");
    let tmp = parent.join(format!(
        ".{fname}.tmp.{}",
        uuid::Uuid::new_v4().simple()
    ));

    {
        let mut opts = OpenOptions::new();
        opts.write(true).create(true).truncate(true);
        #[cfg(unix)]
        opts.mode(0o600);
        let mut f = opts
            .open(&tmp)
            .with_context(|| format!("open temp {}", tmp.display()))?;
        f.write_all(data)
            .with_context(|| format!("write temp {}", tmp.display()))?;
        f.sync_all().ok();
    }

    fs::rename(&tmp, path).with_context(|| {
        let _ = fs::remove_file(&tmp);
        format!("rename {} → {}", tmp.display(), path.display())
    })?;

    #[cfg(unix)]
    {
        let mut perms = fs::metadata(path)?.permissions();
        perms.set_mode(0o600);
        fs::set_permissions(path, perms)?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp_root() -> PathBuf {
        let n = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let p = std::env::temp_dir().join(format!("cloakcli_cookie_test_{n}"));
        let _ = fs::remove_dir_all(&p);
        fs::create_dir_all(p.join("profiles")).unwrap();
        fs::create_dir_all(p.join("skills")).unwrap();
        fs::create_dir_all(p.join("data")).unwrap();
        fs::write(p.join("Cargo.toml"), "[package]\nname=\"t\"\nversion=\"0.0.0\"\n").unwrap();
        p
    }

    fn sample_cookie_json() -> &'static str {
        r#"{"cookies":[{"name":"sid","value":"secret","domain":".example.com","path":"/"}],"origins":[]}"#
    }

    #[test]
    fn parse_storage_state_and_cookies_json() {
        let ss = parse_import(
            r#"{"cookies":[{"name":"a","value":"1","domain":".ex.com","path":"/"}],"origins":[]}"#,
            CookieFormat::StorageState,
        )
        .unwrap();
        assert_eq!(ss.cookies.len(), 1);

        let arr = parse_import(
            r#"[{"name":"b","value":"2","domain":".y.com","path":"/"}]"#,
            CookieFormat::CookiesJson,
        )
        .unwrap();
        assert_eq!(arr.cookies.len(), 1);

        let auto = parse_import(
            r#"{"cookies":[{"name":"c","value":"3","url":"https://z.com/"}]}"#,
            CookieFormat::Auto,
        )
        .unwrap();
        assert_eq!(auto.cookies.len(), 1);
    }

    #[test]
    fn rejects_missing_domain() {
        let err = parse_import(
            r#"[{"name":"x","value":"y"}]"#,
            CookieFormat::CookiesJson,
        )
        .and_then(|s| {
            validate_storage_state(&s)?;
            Ok(s)
        });
        assert!(err.is_err());
        let msg = format!("{}", err.unwrap_err());
        assert!(msg.contains("INVALID_COOKIE"));
        assert!(msg.contains("domain") || msg.contains("url"));
    }

    #[test]
    fn import_export_clear_roundtrip() {
        let root = tmp_root();
        profiles::create(&root, "p1", None, None).unwrap();
        let fixture = root.join("fixture.json");
        fs::write(&fixture, sample_cookie_json()).unwrap();

        let st = import(&root, "p1", &fixture, CookieFormat::Auto).unwrap();
        assert!(st.present);
        assert_eq!(st.cookie_count, 1);
        assert_eq!(st.valid_count, 1);
        assert_eq!(st.expired_count, 0);
        assert!(st.domains.iter().any(|d| d.contains("example.com")));

        let cookie = cookie_path(&root, "p1");
        assert!(cookie.is_file());
        #[cfg(unix)]
        {
            let mode = fs::metadata(&cookie).unwrap().permissions().mode() & 0o777;
            assert_eq!(mode, 0o600);
        }

        let (exported, _) = export_string(&root, "p1").unwrap();
        assert!(exported.contains("\"sid\""));
        assert!(exported.contains("secret")); // file content has values; status must not
        let st_status = status(&root, "p1").unwrap();
        let status_json = serde_json::to_string(&st_status).unwrap();
        assert!(!status_json.contains("secret"));
        let summary = status_summary(&root, "p1");
        assert!(!summary.contains("secret"));

        assert!(clear(&root, "p1").unwrap());
        assert!(!cookie_path(&root, "p1").is_file());
        let st2 = status(&root, "p1").unwrap();
        assert!(!st2.present);

        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn migrates_flat_profile_on_cookie_import() {
        let root = tmp_root();
        let flat = root.join("profiles").join("legacy.json");
        fs::write(
            &flat,
            r#"{"name":"legacy","user_data_dir":"","created_at":"2026-01-01T00:00:00Z"}"#,
        )
        .unwrap();
        assert!(profiles::get(&root, "legacy").is_ok());

        let fixture = root.join("c.json");
        fs::write(
            &fixture,
            r#"[{"name":"t","value":"v","domain":".t.com","path":"/"}]"#,
        )
        .unwrap();
        import(&root, "legacy", &fixture, CookieFormat::Auto).unwrap();

        assert!(!flat.exists());
        assert!(root.join("profiles/legacy/profile.json").is_file());
        assert!(root.join("profiles/legacy/cookie.json").is_file());
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn migrates_flat_on_status_export_clear() {
        let root = tmp_root();
        let flat = root.join("profiles").join("flat2.json");
        fs::write(
            &flat,
            r#"{"name":"flat2","user_data_dir":"","created_at":"2026-01-01T00:00:00Z"}"#,
        )
        .unwrap();

        // status alone must migrate
        let _ = status(&root, "flat2").unwrap();
        assert!(!flat.exists());
        assert!(root.join("profiles/flat2/profile.json").is_file());

        // re-create flat for export/clear path
        let flat3 = root.join("profiles").join("flat3.json");
        fs::write(
            &flat3,
            r#"{"name":"flat3","user_data_dir":"","created_at":"2026-01-01T00:00:00Z"}"#,
        )
        .unwrap();
        profiles::ensure_dir_layout(&root, "flat3").unwrap();
        // put cookie then remake as... actually after ensure it's dir. Test clear migrates:
        let flat4 = root.join("profiles").join("flat4.json");
        fs::write(
            &flat4,
            r#"{"name":"flat4","user_data_dir":"","created_at":"2026-01-01T00:00:00Z"}"#,
        )
        .unwrap();
        assert!(!clear(&root, "flat4").unwrap());
        assert!(root.join("profiles/flat4/profile.json").is_file());
        assert!(!flat4.exists());

        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn atomic_write_resets_0600_on_existing_target() {
        let root = tmp_root();
        let path = root.join("existing-export.json");
        fs::write(&path, b"old\n").unwrap();
        #[cfg(unix)]
        {
            let mut perms = fs::metadata(&path).unwrap().permissions();
            perms.set_mode(0o644);
            fs::set_permissions(&path, perms).unwrap();
            let before = fs::metadata(&path).unwrap().permissions().mode() & 0o777;
            assert_eq!(before, 0o644);
        }
        atomic_write_0600(&path, sample_cookie_json().as_bytes()).unwrap();
        #[cfg(unix)]
        {
            let mode = fs::metadata(&path).unwrap().permissions().mode() & 0o777;
            assert_eq!(mode, 0o600);
        }
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn export_rejects_data_and_directory_targets() {
        let root = tmp_root();
        profiles::create(&root, "p1", None, None).unwrap();
        let fixture = root.join("fixture.json");
        fs::write(&fixture, sample_cookie_json()).unwrap();
        import(&root, "p1", &fixture, CookieFormat::Auto).unwrap();

        let data_out = root.join("data").join("cookie-export.json");
        let err = export(&root, "p1", Some(&data_out));
        assert!(err.is_err(), "expected reject data/");
        let msg = format!("{}", err.unwrap_err());
        assert!(msg.contains("data/") || msg.contains("refuses"));

        let dir_target = root.join("skills");
        let err2 = export(&root, "p1", Some(&dir_target));
        assert!(err2.is_err(), "expected reject directory");

        // Allowed: under /tmp-style path outside data/
        let ok_out = root.join("my-cookies-export.json");
        export(&root, "p1", Some(&ok_out)).unwrap();
        assert!(ok_out.is_file());
        #[cfg(unix)]
        {
            let mode = fs::metadata(&ok_out).unwrap().permissions().mode() & 0o777;
            assert_eq!(mode, 0o600);
        }

        let _ = fs::remove_dir_all(&root);
    }

    #[cfg(unix)]
    #[test]
    fn cookie_file_for_open_rejects_symlink() {
        let root = tmp_root();
        profiles::create(&root, "p1", None, None).unwrap();
        profiles::ensure_dir_layout(&root, "p1").unwrap();

        let outside = root.join("outside-secret.json");
        fs::write(&outside, sample_cookie_json()).unwrap();
        let cookie = cookie_path(&root, "p1");
        std::os::unix::fs::symlink(&outside, &cookie).unwrap();

        let err = cookie_file_for_open(&root, "p1");
        assert!(err.is_err());
        let msg = format!("{}", err.unwrap_err());
        assert!(msg.contains("INVALID_COOKIE"));
        assert!(msg.contains("symlink") || msg.contains("escapes"));
        assert!(!msg.contains("secret"));

        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn cookie_file_for_open_rejects_corrupt_and_skips_missing() {
        let root = tmp_root();
        profiles::create(&root, "p1", None, None).unwrap();
        assert!(cookie_file_for_open(&root, "p1").unwrap().is_none());

        profiles::ensure_dir_layout(&root, "p1").unwrap();
        let cookie = cookie_path(&root, "p1");
        fs::write(&cookie, b"not-json{\n").unwrap();
        let err = cookie_file_for_open(&root, "p1");
        assert!(err.is_err());
        let msg = format!("{}", err.unwrap_err());
        assert!(msg.contains("INVALID_COOKIE"));

        // Bad schema (missing domain)
        fs::write(
            &cookie,
            r#"{"cookies":[{"name":"a","value":"LEAK_ME"}],"origins":[]}"#,
        )
        .unwrap();
        let err2 = cookie_file_for_open(&root, "p1");
        assert!(err2.is_err());
        let msg2 = format!("{}", err2.unwrap_err());
        assert!(msg2.contains("INVALID_COOKIE"));
        assert!(!msg2.contains("LEAK_ME"));

        // Valid file → Some(path)
        fs::write(&cookie, sample_cookie_json()).unwrap();
        let ok = cookie_file_for_open(&root, "p1").unwrap();
        assert!(ok.is_some());
        assert!(ok.unwrap().ends_with("cookie.json"));

        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn status_counts_expired() {
        let root = tmp_root();
        profiles::create(&root, "p1", None, None).unwrap();
        let fixture = root.join("fixture.json");
        // expires=1 → definitely expired; -1 → session valid
        fs::write(
            &fixture,
            r#"{"cookies":[
                {"name":"old","value":"x","domain":".ex.com","path":"/","expires":1},
                {"name":"sess","value":"y","domain":".ex.com","path":"/","expires":-1}
            ],"origins":[]}"#,
        )
        .unwrap();
        import(&root, "p1", &fixture, CookieFormat::Auto).unwrap();
        let st = status(&root, "p1").unwrap();
        assert_eq!(st.cookie_count, 2);
        assert_eq!(st.expired_count, 1);
        assert_eq!(st.valid_count, 1);
        let sj = serde_json::to_string(&st).unwrap();
        assert!(!sj.contains("\"x\"") && !sj.contains("\"y\""));
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn values_never_in_status_summary_or_errors() {
        let root = tmp_root();
        profiles::create(&root, "p1", None, None).unwrap();
        let fixture = root.join("fixture.json");
        let secret = "SUPER_SECRET_COOKIE_VALUE_XYZ";
        fs::write(
            &fixture,
            format!(
                r#"{{"cookies":[{{"name":"sid","value":"{secret}","domain":".example.com","path":"/"}}],"origins":[]}}"#
            ),
        )
        .unwrap();
        import(&root, "p1", &fixture, CookieFormat::Auto).unwrap();
        let st = status(&root, "p1").unwrap();
        let status_json = serde_json::to_string_pretty(&st).unwrap();
        assert!(!status_json.contains(secret));
        assert!(!status_summary(&root, "p1").contains(secret));

        // Corrupt with secret still must not leak in error
        let cookie = cookie_path(&root, "p1");
        fs::write(
            &cookie,
            format!(r#"{{"cookies":[{{"name":"a","value":"{secret}"}}],"origins":[]}}"#),
        )
        .unwrap();
        let err = cookie_file_for_open(&root, "p1").unwrap_err();
        assert!(!format!("{err}").contains(secret));

        let _ = fs::remove_dir_all(&root);
    }
}
