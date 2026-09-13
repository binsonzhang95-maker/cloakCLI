use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};

use crate::state;
use crate::teach_chat::{validate_action_with_source, validate_human_action};
use crate::teach_protocol::{
    is_raw_dom_event, is_var_placeholder, sanitize_page_url, selector_from_value,
};
use crate::util;

/// Unified Playwright actions that may appear in a Teach Chat skill draft.
pub const TEACH_EXPORT_ACTIONS: &[&str] = &[
    "goto", "click", "fill", "type", "wait", "scroll", "press", "select",
];

const TEACH_SKIP_ACTIONS: &[&str] = &["done", "fail", "ask_human"];

const SECRET_FIELD_MARKERS: &[&str] = &[
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
];

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Skill {
    #[serde(default = "default_schema")]
    pub schema_version: u32,
    pub name: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub params: Vec<Value>,
    /// Skill-level stall policy: `fail` (default) or `recover`. Steps inherit unless overridden.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub on_stall: Option<String>,
    /// Optional skill-level goal used when a step omits `goal`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub goal: Option<String>,
    #[serde(default)]
    pub steps: Vec<Value>,
    /// Absolute path to the skill directory (not in skill.json)
    #[serde(skip)]
    pub path: PathBuf,
}

fn default_schema() -> u32 {
    1
}

#[derive(Debug, Clone)]
pub struct SkillListResult {
    pub skills: Vec<Skill>,
    pub invalid: Vec<(PathBuf, String)>,
}

pub fn list_with_errors(root: &Path) -> Result<SkillListResult> {
    let dir = state::skills_dir(root);
    if !dir.exists() {
        return Ok(SkillListResult {
            skills: vec![],
            invalid: vec![],
        });
    }
    let mut found = Vec::new();
    let mut invalid = Vec::new();
    collect_skills(&dir, &mut found, &mut invalid)?;
    let mut by_name = std::collections::HashMap::new();
    for s in found {
        by_name.entry(s.name.clone()).or_insert(s);
    }
    let mut out: Vec<_> = by_name.into_values().collect();
    out.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(SkillListResult {
        skills: out,
        invalid,
    })
}

pub fn list(root: &Path) -> Result<Vec<Skill>> {
    Ok(list_with_errors(root)?.skills)
}

fn collect_skills(
    dir: &Path,
    out: &mut Vec<Skill>,
    invalid: &mut Vec<(PathBuf, String)>,
) -> Result<()> {
    if !dir.is_dir() {
        return Ok(());
    }
    for e in fs::read_dir(dir)? {
        let e = e?;
        let p = e.path();
        if p.is_dir() {
            let sj = p.join("skill.json");
            if sj.is_file() {
                match load_dir(&p) {
                    Ok(s) => out.push(s),
                    Err(err) => invalid.push((sj, err.to_string())),
                }
            } else {
                collect_skills(&p, out, invalid)?;
            }
        }
    }
    Ok(())
}

pub fn get(root: &Path, name: &str) -> Result<Skill> {
    util::validate_name(name, "skill")?;
    for s in list(root)? {
        if s.name == name {
            return Ok(s);
        }
    }
    bail!("Skill not found: {name}");
}

fn load_dir(path: &Path) -> Result<Skill> {
    let sj = path.join("skill.json");
    let text = fs::read_to_string(&sj).with_context(|| format!("read {}", sj.display()))?;
    let mut skill: Skill = serde_json::from_str(&text)
        .with_context(|| format!("invalid skill JSON {}", sj.display()))?;
    if skill.name.is_empty() {
        skill.name = path
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or("unnamed")
            .to_string();
    }
    util::validate_name(&skill.name, "skill")?;
    skill.path = path.to_path_buf();
    Ok(skill)
}

pub fn import(root: &Path, src: &Path, name: Option<&str>) -> Result<Skill> {
    let src = src
        .canonicalize()
        .with_context(|| format!("resolve {}", src.display()))?;
    let skills = state::skills_dir(root);
    fs::create_dir_all(&skills)?;
    let skills_canon = util::ensure_under_root(root, &skills)?;

    if src.is_file() {
        let data: Value = serde_json::from_str(&fs::read_to_string(&src)?)?;
        let skill_name = resolve_import_name(name, &data, &src, true)?;
        util::validate_name(&skill_name, "skill")?;
        let dest = skills.join(&skill_name);
        let dest_check = util::ensure_under_root(root, &dest)?;
        if !dest_check.starts_with(&skills_canon) {
            bail!("import destination escapes skills/");
        }
        if dest.exists() {
            bail!("Skill already exists: {skill_name}");
        }
        fs::create_dir_all(&dest)?;
        let mut data = data;
        if let Some(obj) = data.as_object_mut() {
            obj.insert("name".into(), Value::String(skill_name.clone()));
            obj.entry("schema_version").or_insert(Value::from(1));
        }
        fs::write(
            dest.join("skill.json"),
            format!("{}\n", serde_json::to_string_pretty(&data)?),
        )?;
        return load_dir(&dest);
    }

    let sj = src.join("skill.json");
    if !sj.is_file() {
        bail!("No skill.json in {}", src.display());
    }
    let data: Value = serde_json::from_str(&fs::read_to_string(&sj)?)?;
    let skill_name = resolve_import_name(name, &data, &src, false)?;
    util::validate_name(&skill_name, "skill")?;
    let dest = skills.join(&skill_name);
    let dest_check = util::ensure_under_root(root, &dest)?;
    if !dest_check.starts_with(&skills_canon) {
        bail!("import destination escapes skills/");
    }
    if dest.exists() {
        bail!("Skill already exists: {skill_name}");
    }
    copy_dir(&src, &dest)?;
    let mut data = serde_json::from_str::<Value>(&fs::read_to_string(dest.join("skill.json"))?)?;
    if let Some(obj) = data.as_object_mut() {
        obj.insert("name".into(), Value::String(skill_name));
    }
    fs::write(
        dest.join("skill.json"),
        format!("{}\n", serde_json::to_string_pretty(&data)?),
    )?;
    load_dir(&dest)
}

/// Outcome of classifying one timeline action for a Teach Chat skill draft.
#[derive(Debug, Clone)]
pub enum ExportStepClass {
    Keep(Value),
    Skip { reason: String },
    Reject { reason: String },
}

#[derive(Debug, Clone, Default)]
pub struct ExportPrepare {
    pub steps: Vec<Value>,
    pub vars: Vec<String>,
    pub skipped: Vec<String>,
    pub n_agent: usize,
    pub n_human: usize,
}

/// Classify a single action for export. Raw DOM and dangerous actions are
/// rejected; `done`/`fail`/`ask_human` and coordinate-only clicks are skipped.
pub fn classify_teach_export_step(step: &Value, default_source: &str) -> ExportStepClass {
    if is_raw_dom_event(step) {
        return ExportStepClass::Reject {
            reason: "raw DOM event is not an exportable skill step".into(),
        };
    }
    let raw_type = step
        .get("action")
        .or_else(|| step.get("type"))
        .or_else(|| step.get("name"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_ascii_lowercase();
    if raw_type.is_empty() {
        return ExportStepClass::Reject {
            reason: "missing action type".into(),
        };
    }
    if TEACH_SKIP_ACTIONS.contains(&raw_type.as_str()) {
        return ExportStepClass::Skip {
            reason: format!("skipped control action {raw_type}"),
        };
    }
    let source_in = step
        .get("source")
        .and_then(|v| v.as_str())
        .unwrap_or(default_source)
        .trim()
        .to_ascii_lowercase();
    let source = if source_in == "human" { "human" } else { "agent" };
    let validated = if source == "human" {
        validate_human_action(step)
    } else {
        validate_action_with_source(step, "llm")
    };
    let a = match validated {
        Ok(a) => a,
        Err(e) => {
            return ExportStepClass::Reject { reason: e.message };
        }
    };
    if !TEACH_EXPORT_ACTIONS.contains(&a.action.as_str()) {
        return ExportStepClass::Reject {
            reason: format!("non-exportable action: {}", a.action),
        };
    }
    if a.action == "click" && a.selector.is_none() {
        return ExportStepClass::Skip {
            reason: "coordinate click is not exportable without a selector".into(),
        };
    }
    match canonicalize_export_step(step, &a.action, source) {
        Ok(v) => ExportStepClass::Keep(v),
        Err(reason) => ExportStepClass::Reject { reason },
    }
}

/// Validate one draft step against the unified Teach Chat action schema.
#[allow(dead_code)]
pub fn validate_teach_draft_step(step: &Value) -> Result<Value> {
    match classify_teach_export_step(step, "agent") {
        ExportStepClass::Keep(v) => Ok(v),
        ExportStepClass::Skip { reason } | ExportStepClass::Reject { reason } => {
            bail!("{reason}")
        }
    }
}

/// Turn merged timeline actions into canonical Playwright skill steps.
/// Secrets become `{{vars.NAME}}`. `source` is `human` or `agent` (never `llm`).
pub fn prepare_teach_export_steps(
    steps: &[Value],
    default_source: &str,
) -> Result<ExportPrepare> {
    let mut out = ExportPrepare::default();
    for (i, step) in steps.iter().enumerate() {
        match classify_teach_export_step(step, default_source) {
            ExportStepClass::Keep(v) => {
                if let Some(t) = v.get("text").and_then(|x| x.as_str()) {
                    if let Some(name) = var_name_from_placeholder(t) {
                        if !out.vars.iter().any(|x| x == &name) {
                            out.vars.push(name);
                        }
                    }
                }
                match v.get("source").and_then(|s| s.as_str()) {
                    Some("human") => out.n_human += 1,
                    _ => out.n_agent += 1,
                }
                out.steps.push(v);
            }
            ExportStepClass::Skip { reason } => {
                out.skipped.push(format!("steps[{i}]: {reason}"));
            }
            ExportStepClass::Reject { reason } => {
                bail!("steps[{i}]: {reason}");
            }
        }
    }
    // Second pass: collect placeholders that canonicalize already set.
    for s in &out.steps {
        if let Some(t) = s.get("text").and_then(|x| x.as_str()) {
            if let Some(name) = var_name_from_placeholder(t) {
                if !out.vars.iter().any(|x| x == &name) {
                    out.vars.push(name);
                }
            }
        }
    }
    assert_no_plaintext_secrets(&Value::Array(out.steps.clone()))?;
    Ok(out)
}

fn canonicalize_export_step(raw: &Value, action: &str, source: &str) -> Result<Value, String> {
    let mut step = serde_json::Map::new();
    step.insert("action".into(), json!(action));
    step.insert("source".into(), json!(source));

    let selector = selector_from_value(raw);
    if let Some(sel) = selector {
        if sel.to_ascii_lowercase().contains("javascript:")
            || sel.to_ascii_lowercase().contains("data:")
        {
            return Err("css/selector rejected".into());
        }
        step.insert("selector".into(), json!(sel));
    }

    match action {
        "goto" => {
            let url = raw
                .get("url")
                .and_then(|v| v.as_str())
                .ok_or_else(|| "goto requires url".to_string())?;
            let clean = sanitize_page_url(url).map_err(|e| e.message.clone())?;
            step.insert("url".into(), json!(clean));
        }
        "fill" | "type" => {
            let text = raw
                .get("text")
                .or_else(|| raw.get("value"))
                .and_then(|v| {
                    if v.is_string() {
                        v.as_str().map(|s| s.to_string())
                    } else {
                        Some(v.to_string())
                    }
                })
                .unwrap_or_default();
            let parameterized = parameterize_fill_text(action, &text, raw);
            step.insert("text".into(), json!(parameterized));
        }
        "wait" => {
            let ms = raw
                .get("ms")
                .or_else(|| raw.get("timeout"))
                .and_then(|v| v.as_u64())
                .unwrap_or(500);
            step.insert("ms".into(), json!(ms.min(30_000)));
        }
        "scroll" => {
            let dx = raw
                .get("delta_x")
                .or_else(|| raw.get("dx"))
                .and_then(|v| v.as_i64())
                .unwrap_or(0);
            let dy = raw
                .get("delta_y")
                .or_else(|| raw.get("dy"))
                .and_then(|v| v.as_i64())
                .unwrap_or(0);
            step.insert("delta_x".into(), json!(dx));
            step.insert("delta_y".into(), json!(dy));
        }
        "press" => {
            if let Some(k) = raw.get("key").and_then(|v| v.as_str()) {
                step.insert("key".into(), json!(k));
            }
        }
        "select" => {
            if let Some(v) = raw.get("value") {
                if looks_secret_step(raw) {
                    return Err("select value looks like a secret; refusing plaintext".into());
                }
                step.insert("value".into(), v.clone());
            }
        }
        "click" => {}
        _ => {}
    }

    if let Some(fnm) = raw.get("field_name").and_then(|v| v.as_str()) {
        if !fnm.is_empty() {
            step.insert("field_name".into(), json!(fnm));
        }
    }
    if let Some(sels) = raw.get("selectors").and_then(|v| v.as_array()) {
        let cleaned: Vec<Value> = sels
            .iter()
            .filter_map(|x| x.as_str())
            .filter(|s| !s.is_empty())
            .map(|s| json!(s))
            .collect();
        if !cleaned.is_empty() {
            step.insert("selectors".into(), Value::Array(cleaned));
        }
    }

    let v = Value::Object(step);
    if let Err(e) = assert_no_plaintext_secrets(&v) {
        return Err(e.to_string());
    }
    Ok(v)
}

fn parameterize_fill_text(action: &str, text: &str, step: &Value) -> String {
    let _ = action;
    if is_var_placeholder(text) {
        return text.trim().to_string();
    }
    if looks_secret_step(step) {
        let name = var_name_from_step(step);
        return format!("{{{{vars.{name}}}}}");
    }
    if text == "[REDACTED]" {
        let name = var_name_from_step(step);
        return format!("{{{{vars.{name}}}}}");
    }
    strip_secretish_value(text)
}

fn looks_secret_step(step: &Value) -> bool {
    let field_name = step
        .get("field_name")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let selector = step
        .get("selector")
        .or_else(|| step.get("css"))
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let hay = format!(
        "{field_name} {selector} {} {} {} {} {}",
        step.pointer("/field/type").and_then(|v| v.as_str()).unwrap_or(""),
        step.pointer("/field/name").and_then(|v| v.as_str()).unwrap_or(""),
        step.pointer("/field/id").and_then(|v| v.as_str()).unwrap_or(""),
        step.pointer("/field/autocomplete")
            .and_then(|v| v.as_str())
            .unwrap_or(""),
        step.get("autocomplete").and_then(|v| v.as_str()).unwrap_or(""),
    )
    .to_ascii_lowercase();
    if hay.contains("type=password") || hay.contains("input[type=\"password\"]") {
        return true;
    }
    if hay.contains("pass") {
        return true;
    }
    SECRET_FIELD_MARKERS.iter().any(|m| hay.contains(m))
}

fn var_name_from_step(step: &Value) -> String {
    let hay = format!(
        "{} {} {} {}",
        step.get("field_name").and_then(|v| v.as_str()).unwrap_or(""),
        step.get("selector").and_then(|v| v.as_str()).unwrap_or(""),
        step.pointer("/field/name").and_then(|v| v.as_str()).unwrap_or(""),
        step.pointer("/field/id").and_then(|v| v.as_str()).unwrap_or(""),
    )
    .to_ascii_lowercase();
    if hay.contains("token") || hay.contains("jwt") {
        return "TOKEN".into();
    }
    if hay.contains("pass") {
        return "PASSWORD".into();
    }
    if hay.contains("cookie") {
        return "COOKIE".into();
    }
    if hay.contains("auth") || hay.contains("secret") {
        return "SECRET".into();
    }
    if hay.contains("api") && hay.contains("key") {
        return "API_KEY".into();
    }
    if let Some(fnm) = step.get("field_name").and_then(|v| v.as_str()) {
        if let Some(n) = sanitize_var_ident(fnm) {
            return n;
        }
    }
    if let Some(sel) = step.get("selector").and_then(|v| v.as_str()) {
        let id = sel.trim().trim_start_matches('#');
        if let Some(n) = sanitize_var_ident(id) {
            return n;
        }
    }
    "SECRET".into()
}

fn sanitize_var_ident(raw: &str) -> Option<String> {
    let mut s: String = raw
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() {
                c.to_ascii_uppercase()
            } else {
                '_'
            }
        })
        .collect();
    while s.contains("__") {
        s = s.replace("__", "_");
    }
    s = s.trim_matches('_').to_string();
    if s.is_empty() {
        return None;
    }
    if s.chars().next()?.is_ascii_digit() {
        s.insert(0, 'V');
    }
    if s.len() > 32 {
        s.truncate(32);
    }
    Some(s)
}

pub fn var_name_from_placeholder(text: &str) -> Option<String> {
    let t = text.trim();
    if !is_var_placeholder(t) {
        return None;
    }
    let inner = t
        .trim_start_matches("{{vars.")
        .trim_end_matches("}}")
        .trim();
    if inner.is_empty() || inner.len() > 32 {
        return None;
    }
    if inner
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '_')
    {
        Some(inner.to_string())
    } else {
        None
    }
}

fn strip_secretish_value(v: &str) -> String {
    let lower = v.to_ascii_lowercase();
    if lower.contains("bearer ")
        || lower.contains("authorization")
        || lower.contains("cookie=")
        || v.contains("sk-")
    {
        return "{{vars.REDACTED}}".into();
    }
    v.to_string()
}

/// Refuse to serialize password/token/cookie/Authorization plaintext.
pub fn assert_no_plaintext_secrets(v: &Value) -> Result<()> {
    fn walk(v: &Value) -> Result<()> {
        match v {
            Value::Object(map) => {
                let action = map
                    .get("action")
                    .and_then(|x| x.as_str())
                    .unwrap_or("")
                    .to_ascii_lowercase();
                for (k, child) in map {
                    let lk = k.to_ascii_lowercase();
                    if matches!(
                        lk.as_str(),
                        "password"
                            | "token"
                            | "cookie"
                            | "cookies"
                            | "authorization"
                            | "api_key"
                            | "apikey"
                            | "secret"
                            | "session_token"
                            | "pairing_code"
                            | "pairing_id"
                    ) {
                        if let Some(s) = child.as_str() {
                            if !s.is_empty() && !is_var_placeholder(s) && s != "[REDACTED]" {
                                bail!("refusing plaintext secret field {k}");
                            }
                        }
                    }
                    if matches!(action.as_str(), "fill" | "type")
                        && (lk == "text" || lk == "value")
                    {
                        if let Some(s) = child.as_str() {
                            if looks_like_secret_literal(s) {
                                bail!("refusing plaintext secret in {action}.{k}");
                            }
                        }
                    }
                    walk(child)?;
                }
                Ok(())
            }
            Value::Array(arr) => {
                for c in arr {
                    walk(c)?;
                }
                Ok(())
            }
            Value::String(s) => {
                if looks_like_secret_literal(s) && !is_var_placeholder(s) {
                    // Allow short non-secret strings; flag bearer/cookie dumps.
                    if s.to_ascii_lowercase().contains("bearer ")
                        || s.to_ascii_lowercase().contains("cookie=")
                    {
                        bail!("refusing secret-like string in skill draft");
                    }
                }
                Ok(())
            }
            _ => Ok(()),
        }
    }
    walk(v)
}

fn looks_like_secret_literal(s: &str) -> bool {
    if is_var_placeholder(s) || s == "[REDACTED]" {
        return false;
    }
    let low = s.to_ascii_lowercase();
    low.contains("bearer ")
        || low.contains("authorization:")
        || low.contains("cookie=")
        || s.contains("sk-")
}

/// Root-bound draft write used by Teach Chat. Never overwrites skill.json
/// unless `overwrite` is true. Failed validation writes nothing.
#[allow(dead_code)]
pub fn write_teach_draft(
    root: &Path,
    name: &str,
    skill: &Value,
    overwrite: bool,
    audit: Option<&str>,
) -> Result<PathBuf> {
    util::validate_name(name, "skill")?;
    let skills = state::skills_dir(root);
    fs::create_dir_all(&skills)?;
    let skills_canon = util::ensure_under_root(root, &skills)?;
    if name.contains("..") || name.contains('/') || name.contains('\\') {
        bail!("invalid skill name '{name}'");
    }
    let dest = skills.join(name);
    let dest_check = util::ensure_under_root(root, &dest)?;
    if !dest_check.starts_with(&skills_canon) {
        bail!("export destination escapes skills/");
    }
    let sj_existing = dest.join("skill.json");
    if sj_existing.is_file() && !overwrite {
        bail!("Skill already exists: {name} (export refused; pick another name or confirm overwrite)");
    }
    if dest.exists() {
        let dest_canon = dest
            .canonicalize()
            .with_context(|| format!("canonicalize {}", dest.display()))?;
        if !dest_canon.starts_with(&skills_canon) {
            bail!("export destination escapes skills/ (symlink)");
        }
    }
    assert_no_plaintext_secrets(skill)?;

    let created_dir = !dest.exists();
    fs::create_dir_all(&dest)?;
    let dest_canon = dest
        .canonicalize()
        .with_context(|| format!("canonicalize created {}", dest.display()))?;
    if !dest_canon.starts_with(&skills_canon) {
        if created_dir {
            let _ = fs::remove_dir_all(&dest);
        }
        bail!("export destination escaped skills/; refused");
    }

    let sj = dest_canon.join("skill.json");
    let _ = util::ensure_under_root(root, &sj)?;
    let tmp = dest_canon.join(".skill.json.tmp");
    let _ = util::ensure_under_root(root, &tmp)?;
    let body = format!("{}\n", serde_json::to_string_pretty(skill)?);
    if let Err(e) = fs::write(&tmp, &body) {
        let _ = fs::remove_file(&tmp);
        if created_dir {
            let _ = fs::remove_dir_all(&dest);
        }
        return Err(e.into());
    }
    if let Err(e) = fs::rename(&tmp, &sj) {
        let _ = fs::remove_file(&tmp);
        if created_dir && !sj_existing.is_file() {
            let _ = fs::remove_dir_all(&dest);
        }
        return Err(e.into());
    }

    if let Some(text) = audit {
        let ap = dest_canon.join("AUDIT.md");
        let _ = util::ensure_under_root(root, &ap)?;
        let _ = fs::write(&ap, text);
    }
    Ok(sj)
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
        let p = std::env::temp_dir().join(format!("cloakcli_skill_test_{n}"));
        let _ = fs::remove_dir_all(&p);
        fs::create_dir_all(p.join("skills")).unwrap();
        fs::write(
            p.join("Cargo.toml"),
            "[package]\nname=\"t\"\nversion=\"0.0.0\"\n",
        )
        .unwrap();
        p
    }

    #[test]
    fn parses_goal_and_on_stall() {
        let root = tmp_root();
        let dir = state::skills_dir(&root).join("recover-demo");
        fs::create_dir_all(&dir).unwrap();
        fs::write(
            dir.join("skill.json"),
            r##"{
              "schema_version": 1,
              "name": "recover-demo",
              "on_stall": "recover",
              "goal": "finish the page",
              "steps": [
                {"action":"click","selector":"#missing","goal":"Click more info","on_stall":"recover"}
              ]
            }"##,
        )
        .unwrap();
        let s = get(&root, "recover-demo").unwrap();
        assert_eq!(s.on_stall.as_deref(), Some("recover"));
        assert_eq!(s.goal.as_deref(), Some("finish the page"));
        let step = &s.steps[0];
        assert_eq!(step["goal"], "Click more info");
        assert_eq!(step["on_stall"], "recover");
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn teach_draft_rejects_raw_dom_and_danger() {
        let raw = json!({"kind": "click", "selector": "#x"});
        match classify_teach_export_step(&raw, "human") {
            ExportStepClass::Reject { reason } => {
                assert!(reason.contains("raw DOM"), "{reason}");
            }
            other => panic!("expected reject, got {other:?}"),
        }
        let shell = json!({"action": "shell", "cmd": "id"});
        match classify_teach_export_step(&shell, "agent") {
            ExportStepClass::Reject { reason } => {
                assert!(reason.contains("forbidden") || reason.contains("unknown"), "{reason}");
            }
            other => panic!("expected reject, got {other:?}"),
        }
        let js = json!({"action": "goto", "url": "javascript:alert(1)"});
        match classify_teach_export_step(&js, "agent") {
            ExportStepClass::Reject { reason } => {
                assert!(
                    reason.contains("javascript") || reason.contains("blocked"),
                    "{reason}"
                );
            }
            other => panic!("expected reject, got {other:?}"),
        }
    }

    #[test]
    fn teach_draft_parameterizes_password_and_maps_source() {
        let steps = vec![
            json!({"action":"goto","url":"https://example.com/login?token=leakme&next=/app","source":"llm"}),
            json!({"action":"fill","selector":"#user","text":"alice","field_name":"username","source":"human"}),
            json!({"action":"fill","selector":"#pass","text":"hunter2","field_name":"password","source":"human"}),
            json!({"action":"click","selector":"button.submit","source":"human"}),
            json!({"action":"done","reason":"ok","source":"llm"}),
        ];
        let prep = prepare_teach_export_steps(&steps, "agent").unwrap();
        assert_eq!(prep.steps.len(), 4);
        assert_eq!(prep.steps[0]["source"], "agent");
        assert_eq!(prep.steps[1]["source"], "human");
        assert_eq!(prep.steps[2]["text"], "{{vars.PASSWORD}}");
        assert_eq!(prep.steps[1]["text"], "alice");
        assert!(!prep.steps[0]["url"].as_str().unwrap().contains("leakme"));
        assert!(prep.vars.iter().any(|v| v == "PASSWORD"));
        assert!(prep.skipped.iter().any(|s| s.contains("done")));
        let blob = serde_json::to_string(&prep.steps).unwrap();
        assert!(!blob.contains("hunter2"));
        assert!(!blob.contains("leakme"));
    }

    #[test]
    fn write_teach_draft_refuses_overwrite_and_path_escape() {
        let root = tmp_root();
        let skill = json!({
            "schema_version": 1,
            "name": "demo-draft",
            "steps": [{"action":"goto","url":"https://example.com/","source":"agent"}]
        });
        let p = write_teach_draft(&root, "demo-draft", &skill, false, Some("# audit\n")).unwrap();
        assert!(p.ends_with("skill.json"));
        let err = write_teach_draft(&root, "demo-draft", &skill, false, None)
            .unwrap_err()
            .to_string();
        assert!(err.contains("already exists"), "{err}");
        let original = fs::read_to_string(&p).unwrap();
        let err = write_teach_draft(&root, "../etc", &skill, false, None)
            .unwrap_err()
            .to_string();
        assert!(err.contains("Invalid") || err.contains("invalid"), "{err}");
        assert_eq!(fs::read_to_string(&p).unwrap(), original);
        write_teach_draft(&root, "demo-draft", &skill, true, None).unwrap();
        let _ = fs::remove_dir_all(&root);
    }
}

fn resolve_import_name(
    name: Option<&str>,
    data: &Value,
    src: &Path,
    is_file: bool,
) -> Result<String> {
    if let Some(n) = name {
        util::validate_name(n, "skill")?;
        return Ok(n.to_string());
    }
    if let Some(s) = data.get("name").and_then(|v| v.as_str()) {
        util::validate_name(s, "skill")?;
        return Ok(s.to_string());
    }
    let fallback = if is_file {
        src.parent()
            .and_then(|p| p.file_name())
            .and_then(|s| s.to_str())
            .unwrap_or("imported")
    } else {
        src.file_name()
            .and_then(|s| s.to_str())
            .unwrap_or("imported")
    };
    util::validate_name(fallback, "skill")?;
    Ok(fallback.to_string())
}

fn copy_dir(src: &Path, dest: &Path) -> Result<()> {
    fs::create_dir_all(dest)?;
    for e in fs::read_dir(src)? {
        let e = e?;
        let from = e.path();
        let to = dest.join(e.file_name());
        if from.is_dir() {
            copy_dir(&from, &to)?;
        } else {
            fs::copy(&from, &to)?;
        }
    }
    Ok(())
}
