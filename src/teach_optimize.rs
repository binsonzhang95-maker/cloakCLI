//! TEACH PATH (not recover): one-shot export optimize and optional mid-record assist.
//!
//! Recording itself never calls the model per click/fill. After local post-process,
//! export may make **one** chat/completions call that returns a structured patch.
//! The patch is validated locally (action whitelist, selector syntax, step order)
//! before save. Fail-open: a model/network error keeps the locally mapped skill.
//!
//! Mid-record assist is capped at **2** LLM calls per teach session and is skipped
//! when the last call was slow (not cheap) or LLM is unconfigured.

use anyhow::{bail, Result};
use serde::Deserialize;
use serde_json::{json, Value};
use std::path::Path;
use std::time::Instant;

use crate::llm;
use crate::llm_client;
use crate::teach::MappedSkill;

const TEACH_ACTION_WHITELIST: &[&str] = &["goto", "click", "fill"];
const MAX_SELECTOR_LEN: usize = 500;
const MAX_GOAL_LEN: usize = 240;
const MAX_FIELD_NAME_LEN: usize = 64;
const OPTIMIZE_TIMEOUT_SEC: u64 = 20;
const ASSIST_TIMEOUT_SEC: u64 = 3;
/// Last assist slower than this is treated as "not cheap" → defer.
pub const ASSIST_CHEAP_MS: u64 = 2000;
pub const ASSIST_MAX_LLM_CALLS: u8 = 2;

const OPTIMIZE_SYSTEM: &str = r#"You optimize a recorded browser skill. TEACH PATH only (not stall-recovery).
Return a JSON object, schema_version 1, no markdown:
{
  "schema_version": 1,
  "goal": "optional improved goal",
  "drop_indices": [int],
  "merge": [{"from": int, "into": int}],
  "selectors": [{"index": int, "selector": "...", "selectors": ["..."]}],
  "field_names": [{"index": int, "name": "email"}]
}
Rules:
- Indices refer to the provided steps array (0-based).
- Only reinforce selectors, merge consecutive fills, drop noisy clicks, name fields, fix goal.
- Do not change fill `text` values (secrets stay as {{vars.NAME}}).
- Do not add steps, goto targets, or actions other than goto/click/fill.
- Prefer stable selectors: #id, [name], [autocomplete], [data-testid], role+name.
- merge.from and merge.into must be adjacent fill steps.
"#;

#[derive(Debug, Clone)]
pub struct OptimizeOutcome {
    pub mapped: MappedSkill,
    pub applied: bool,
    pub skipped: Option<String>,
    pub tokens: u32,
    pub latency_ms: u64,
}

fn skip_optimize(mapped: &MappedSkill, why: String) -> OptimizeOutcome {
    OptimizeOutcome {
        mapped: mapped.clone(),
        applied: false,
        skipped: Some(why),
        tokens: 0,
        latency_ms: 0,
    }
}

#[derive(Debug, Clone, Default)]
pub struct AssistOutcome {
    pub selectors: Vec<String>,
    pub deferred: bool,
    pub used_llm: bool,
    pub tokens: u32,
    pub latency_ms: u64,
    pub reason: String,
}

#[derive(Debug, Deserialize)]
struct OptimizePatch {
    #[serde(default)]
    schema_version: u32,
    #[serde(default)]
    goal: Option<String>,
    #[serde(default)]
    drop_indices: Vec<usize>,
    #[serde(default)]
    merge: Vec<MergeOp>,
    #[serde(default)]
    selectors: Vec<SelectorPatch>,
    #[serde(default)]
    field_names: Vec<FieldNamePatch>,
}

#[derive(Debug, Deserialize)]
struct MergeOp {
    from: usize,
    into: usize,
}

#[derive(Debug, Deserialize)]
struct SelectorPatch {
    index: usize,
    #[serde(default)]
    selector: Option<String>,
    #[serde(default)]
    selectors: Option<Vec<String>>,
}

#[derive(Debug, Deserialize)]
struct FieldNamePatch {
    index: usize,
    name: String,
}

/// One-shot LLM optimize. No-ops when LLM is off, unconfigured, or the toggle is false.
pub fn maybe_optimize(root: &Path, mapped: &MappedSkill, enabled: bool) -> Result<OptimizeOutcome> {
    if !enabled {
        return Ok(skip_optimize(mapped, "smart optimize off".into()));
    }
    let Some(cfg) = llm::load(root)? else {
        return Ok(skip_optimize(mapped, "no config/llm.json".into()));
    };
    if !cfg.enabled || !cfg.teach_smart_optimize {
        return Ok(skip_optimize(
            mapped,
            "llm disabled or teach_smart_optimize=false".into(),
        ));
    }
    if cfg.base_url.is_empty() || cfg.model.is_empty() {
        return Ok(skip_optimize(mapped, "llm incomplete".into()));
    }
    let Some(key) = llm::resolve_api_key_from_env(&cfg.api_key_env) else {
        return Ok(skip_optimize(mapped, format!("env {} unset", cfg.api_key_env)));
    };

    let user = json!({
        "name": mapped.name,
        "goal": mapped.goal,
        "description": mapped.description,
        "steps": mapped.steps,
        "params": mapped.params,
    });
    let messages = [
        json!({"role": "system", "content": OPTIMIZE_SYSTEM}),
        json!({"role": "user", "content": user.to_string()}),
    ];
    let t0 = Instant::now();
    let chat = match llm_client::chat_complete(
        &cfg.base_url,
        &key,
        &cfg.model,
        &messages,
        OPTIMIZE_TIMEOUT_SEC,
    ) {
        Ok(c) => c,
        Err(e) => {
            let msg = llm::redact_secrets(&e.to_string(), Some(&key));
            return Ok(OptimizeOutcome {
                mapped: mapped.clone(),
                applied: false,
                skipped: Some(format!("model error: {msg}")),
                tokens: 0,
                latency_ms: t0.elapsed().as_millis() as u64,
            });
        }
    };
    let latency_ms = t0.elapsed().as_millis() as u64;
    match apply_optimize_patch(mapped, &chat.text) {
        Ok(next) => Ok(OptimizeOutcome {
            mapped: next,
            applied: true,
            tokens: chat.tokens,
            latency_ms,
            skipped: None,
        }),
        Err(e) => Ok(OptimizeOutcome {
            mapped: mapped.clone(),
            applied: false,
            tokens: chat.tokens,
            latency_ms,
            skipped: Some(format!("patch rejected: {e}")),
        }),
    }
}

/// Local validation + apply. Rejects illegal actions, bad selectors, secret-looking text.
pub fn apply_optimize_patch(mapped: &MappedSkill, raw: &str) -> Result<MappedSkill> {
    let blob = extract_json_object(raw).ok_or_else(|| anyhow::anyhow!("patch is not JSON"))?;
    let patch: OptimizePatch = serde_json::from_value(blob)
        .map_err(|e| anyhow::anyhow!("patch schema: {e}"))?;
    if patch.schema_version != 0 && patch.schema_version != 1 {
        bail!("unsupported schema_version {}", patch.schema_version);
    }

    let mut steps = mapped.steps.clone();
    let n = steps.len();
    if n == 0 {
        bail!("empty steps");
    }

    for sp in &patch.selectors {
        if sp.index >= n {
            bail!("selector index {} out of range", sp.index);
        }
        let action = step_action(&steps[sp.index]);
        if action == "goto" {
            bail!("cannot patch selectors on goto");
        }
        if let Some(sel) = sp.selector.as_deref() {
            let sel = accept_selector(sel)?;
            steps[sp.index]["selector"] = json!(sel);
        }
        if let Some(chain) = &sp.selectors {
            let mut clean = Vec::new();
            for s in chain {
                if let Ok(s) = accept_selector(s) {
                    if !clean.iter().any(|x| x == &s) {
                        clean.push(s);
                    }
                }
            }
            if let Some(primary) = steps[sp.index]
                .get("selector")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string())
            {
                if !clean.iter().any(|x| x == &primary) {
                    clean.insert(0, primary);
                }
            }
            if clean.len() > 1 {
                steps[sp.index]["selectors"] = json!(clean);
            }
        }
    }

    for fp in &patch.field_names {
        if fp.index >= n {
            bail!("field_name index {} out of range", fp.index);
        }
        let action = step_action(&steps[fp.index]);
        if action != "fill" && action != "click" {
            continue;
        }
        if let Some(name) = accept_field_name(&fp.name) {
            steps[fp.index]["field_name"] = json!(name);
        }
    }

    let mut drop: Vec<bool> = vec![false; n];
    for idx in &patch.drop_indices {
        if *idx >= n {
            bail!("drop index {idx} out of range");
        }
        // Never drop the only goto; never drop a fill that holds vars (secrets).
        let action = step_action(&steps[*idx]);
        if action == "fill" && step_has_vars(&steps[*idx]) {
            continue;
        }
        drop[*idx] = true;
    }
    for m in &patch.merge {
        if m.from >= n || m.into >= n {
            bail!("merge index out of range");
        }
        if m.from == m.into {
            continue;
        }
        if m.from.abs_diff(m.into) != 1 {
            bail!("merge requires adjacent steps");
        }
        if step_action(&steps[m.from]) != "fill" || step_action(&steps[m.into]) != "fill" {
            bail!("merge only allowed for fill steps");
        }
        if step_has_vars(&steps[m.from]) && !step_has_vars(&steps[m.into]) {
            // Keep the secret-bearing step as `into`.
            continue;
        }
        drop[m.from] = true;
    }

    let mut kept: Vec<Value> = Vec::new();
    for (i, step) in steps.into_iter().enumerate() {
        if drop[i] {
            continue;
        }
        validate_teach_step(&step)?;
        kept.push(step);
    }
    if kept.is_empty() {
        bail!("patch would drop every step");
    }

    let mut out = mapped.clone();
    out.steps = kept;
    if let Some(g) = patch.goal.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
        if let Some(g) = accept_goal(g) {
            out.goal = Some(g.clone());
            out.description = format!("Taught skill: {g}");
        }
    }
    Ok(out)
}

/// Mid-record assist: suggest backup selectors. Caller enforces the 2-call cap.
pub fn assist_selectors(
    root: &Path,
    primary: &str,
    existing: &[String],
    field_json: Option<&Value>,
    allow_llm: bool,
) -> AssistOutcome {
    let mut selectors = local_backup_chain(primary, existing, field_json);
    if !allow_llm {
        return AssistOutcome {
            selectors,
            deferred: true,
            reason: "deferred (not cheap or cap)".into(),
            ..Default::default()
        };
    }
    let Ok(Some(cfg)) = llm::load(root) else {
        return AssistOutcome {
            selectors,
            deferred: true,
            reason: "no llm config".into(),
            ..Default::default()
        };
    };
    if !cfg.enabled || cfg.base_url.is_empty() || cfg.model.is_empty() {
        return AssistOutcome {
            selectors,
            deferred: true,
            reason: "llm disabled".into(),
            ..Default::default()
        };
    }
    let Some(key) = llm::resolve_api_key_from_env(&cfg.api_key_env) else {
        return AssistOutcome {
            selectors,
            deferred: true,
            reason: "no api key".into(),
            ..Default::default()
        };
    };

    let user = json!({
        "selector": primary,
        "selectors": existing,
        "field": field_json,
        "task": "Return JSON {\"schema_version\":1,\"selectors\":[\"...backup css...\"]} only. No markdown."
    });
    let messages = [
        json!({
            "role": "system",
            "content": "TEACH PATH assist (not recover). Suggest 1-4 stable CSS backup selectors. JSON only."
        }),
        json!({"role": "user", "content": user.to_string()}),
    ];
    let t0 = Instant::now();
    match llm_client::chat_complete(
        &cfg.base_url,
        &key,
        &cfg.model,
        &messages,
        ASSIST_TIMEOUT_SEC,
    ) {
        Ok(chat) => {
            if let Some(extra) = parse_assist_selectors(&chat.text) {
                for s in extra {
                    if accept_selector(&s).is_ok() && !selectors.iter().any(|x| x == &s) {
                        selectors.push(s);
                    }
                }
            }
            AssistOutcome {
                selectors,
                deferred: false,
                used_llm: true,
                tokens: chat.tokens,
                latency_ms: t0.elapsed().as_millis() as u64,
                reason: "ok".into(),
            }
        }
        Err(_) => AssistOutcome {
            selectors,
            deferred: true,
            used_llm: false,
            latency_ms: t0.elapsed().as_millis() as u64,
            reason: "model error".into(),
            ..Default::default()
        },
    }
}

pub fn local_backup_chain(
    primary: &str,
    existing: &[String],
    field_json: Option<&Value>,
) -> Vec<String> {
    let mut out = Vec::new();
    let mut push = |s: String| {
        let t = s.trim();
        if t.is_empty() {
            return;
        }
        if out.iter().any(|x| x == t) {
            return;
        }
        out.push(t.to_string());
    };
    if !primary.trim().is_empty() {
        push(primary.to_string());
    }
    for s in existing {
        push(s.clone());
    }
    if let Some(f) = field_json {
        if let Some(id) = f.get("id").and_then(|v| v.as_str()).filter(|s| is_safe_ident(s)) {
            push(format!("#{id}"));
        }
        let tag = f
            .get("tag")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .unwrap_or("input");
        if let Some(name) = f.get("name").and_then(|v| v.as_str()).filter(|s| !s.is_empty()) {
            push(format!("{}[name=\"{}\"]", tag, css_attr(name)));
        }
        if let Some(ac) = f
            .get("autocomplete")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty() && *s != "off")
        {
            push(format!("[autocomplete=\"{}\"]", css_attr(ac)));
        }
        if let Some(tid) = f
            .get("testid")
            .or_else(|| f.get("data-testid"))
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
        {
            push(format!("[data-testid=\"{}\"]", css_attr(tid)));
        }
        if let Some(ty) = f
            .get("type")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty() && *s != "text")
        {
            push(format!("input[type=\"{}\"]", css_attr(ty)));
        }
    }
    out
}

fn parse_assist_selectors(raw: &str) -> Option<Vec<String>> {
    let blob = extract_json_object(raw)?;
    let arr = blob.get("selectors")?.as_array()?;
    let mut out = Vec::new();
    for v in arr {
        if let Some(s) = v.as_str() {
            if accept_selector(s).is_ok() {
                out.push(s.to_string());
            }
        }
    }
    Some(out)
}

fn extract_json_object(text: &str) -> Option<Value> {
    let s = text.trim();
    let s = if let Some(rest) = s.strip_prefix("```json") {
        rest.trim_start()
            .strip_suffix("```")
            .unwrap_or(rest)
            .trim()
    } else if let Some(rest) = s.strip_prefix("```") {
        rest.trim_start()
            .strip_suffix("```")
            .unwrap_or(rest)
            .trim()
    } else {
        s
    };
    if let Ok(v) = serde_json::from_str::<Value>(s) {
        if v.is_object() {
            return Some(v);
        }
    }
    let start = s.find('{')?;
    let end = s.rfind('}')?;
    serde_json::from_str(&s[start..=end]).ok()
}

pub fn accept_selector(raw: &str) -> Result<String> {
    let s = raw.trim();
    if s.is_empty() {
        bail!("empty selector");
    }
    if s.len() > MAX_SELECTOR_LEN {
        bail!("selector too long");
    }
    if s.contains(';') || s.contains('{') || s.contains('}') {
        bail!("selector rejected");
    }
    if s.chars().any(|c| c.is_control()) {
        bail!("selector rejected");
    }
    Ok(s.to_string())
}

fn accept_field_name(raw: &str) -> Option<String> {
    let t = raw.trim();
    if t.is_empty() || t.chars().count() > MAX_FIELD_NAME_LEN {
        return None;
    }
    if t.chars().any(|c| c.is_control()) {
        return None;
    }
    let lower = t.to_ascii_lowercase();
    if lower.contains("password")
        || lower.contains("secret")
        || lower.contains("api_key")
        || lower.contains("token")
        || t.contains("sk-")
    {
        // Keep local secret handling; do not let the model rename a password field in logs.
        if lower == "password" || lower == "passwd" {
            return Some("password".into());
        }
        return None;
    }
    Some(t.to_string())
}

fn accept_goal(raw: &str) -> Option<String> {
    let t = raw.trim();
    if t.is_empty() || t.chars().count() > MAX_GOAL_LEN {
        return None;
    }
    if t.chars().any(|c| c.is_control()) {
        return None;
    }
    let lower = t.to_ascii_lowercase();
    if lower.contains("authorization") || lower.contains("bearer ") || t.contains("sk-") {
        return None;
    }
    Some(t.to_string())
}

fn step_action(step: &Value) -> String {
    step.get("action")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_ascii_lowercase()
}

fn step_has_vars(step: &Value) -> bool {
    step.get("text")
        .and_then(|v| v.as_str())
        .map(|s| s.contains("{{vars."))
        .unwrap_or(false)
}

fn validate_teach_step(step: &Value) -> Result<()> {
    let action = step_action(step);
    if !TEACH_ACTION_WHITELIST.iter().any(|a| *a == action) {
        bail!("action '{action}' not in teach whitelist");
    }
    match action.as_str() {
        "goto" => {
            let url = step
                .get("url")
                .and_then(|v| v.as_str())
                .ok_or_else(|| anyhow::anyhow!("goto missing url"))?;
            let _ = crate::teach::sanitize_url(url)?;
        }
        "click" | "fill" => {
            let sel = step
                .get("selector")
                .and_then(|v| v.as_str())
                .ok_or_else(|| anyhow::anyhow!("{action} missing selector"))?;
            accept_selector(sel)?;
        }
        _ => {}
    }
    Ok(())
}

fn is_safe_ident(s: &str) -> bool {
    let mut cs = s.chars();
    match cs.next() {
        Some(c) if c.is_ascii_alphabetic() => {}
        _ => return false,
    }
    cs.all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
}

fn css_attr(v: &str) -> String {
    v.replace('\\', "\\\\").replace('"', "\\\"")
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::teach::{map_events_to_skill, FieldHint, RecordedEvent};

    fn fill_skill() -> MappedSkill {
        let events = vec![
            RecordedEvent {
                kind: "navigation".into(),
                url: Some("https://example.com/form".into()),
                ..Default::default()
            },
            RecordedEvent {
                kind: "input".into(),
                selector: Some("#a".into()),
                value: Some("one".into()),
                field: Some(FieldHint {
                    input_type: Some("email".into()),
                    name: Some("email".into()),
                    id: Some("a".into()),
                    ..Default::default()
                }),
                ..Default::default()
            },
            RecordedEvent {
                kind: "click".into(),
                selector: Some("button.x".into()),
                ..Default::default()
            },
            RecordedEvent {
                kind: "click".into(),
                selector: Some("button.x".into()),
                ..Default::default()
            },
        ];
        map_events_to_skill("s", Some("submit form"), &events, false).unwrap()
    }

    #[test]
    fn patch_updates_goal_selectors_field_names() {
        let mapped = fill_skill();
        let n = mapped.steps.len();
        // after denoise, duplicate click collapsed so 3 steps: goto, fill, click
        assert!(n >= 2, "{n} {:?}", mapped.steps);
        let fill_idx = mapped
            .steps
            .iter()
            .position(|s| s["action"] == "fill")
            .unwrap();
        let patch = json!({
            "schema_version": 1,
            "goal": "Enter email and submit",
            "selectors": [{
                "index": fill_idx,
                "selector": "input[name=\"email\"]",
                "selectors": ["#a", "input[name=\"email\"]"]
            }],
            "field_names": [{"index": fill_idx, "name": "email"}]
        })
        .to_string();
        let next = apply_optimize_patch(&mapped, &patch).unwrap();
        assert_eq!(next.goal.as_deref(), Some("Enter email and submit"));
        assert_eq!(next.steps[fill_idx]["selector"], "input[name=\"email\"]");
        assert_eq!(next.steps[fill_idx]["field_name"], "email");
        let chain = next.steps[fill_idx]["selectors"].as_array().unwrap();
        assert!(chain.iter().any(|v| v == "input[name=\"email\"]"));
    }

    #[test]
    fn patch_rejects_shell_and_file_goto() {
        let mapped = fill_skill();
        let err = apply_optimize_patch(
            &mapped,
            r#"{"schema_version":1,"selectors":[{"index":0,"selector":"x; rm"}]}"#,
        )
        .unwrap_err()
        .to_string();
        assert!(err.contains("rejected") || err.contains("goto"), "{err}");
    }

    #[test]
    fn patch_rejects_dropping_everything() {
        let mapped = fill_skill();
        let idxs: Vec<String> = (0..mapped.steps.len()).map(|i| i.to_string()).collect();
        let raw = format!(
            r#"{{"schema_version":1,"drop_indices":[{}]}}"#,
            idxs.join(",")
        );
        let err = apply_optimize_patch(&mapped, &raw).unwrap_err().to_string();
        assert!(err.contains("every step") || err.contains("drop"), "{err}");
    }

    #[test]
    fn patch_merge_adjacent_fills() {
        let events = vec![
            RecordedEvent {
                kind: "input".into(),
                selector: Some("#a".into()),
                value: Some("1".into()),
                ..Default::default()
            },
            RecordedEvent {
                kind: "input".into(),
                selector: Some("#b".into()),
                value: Some("2".into()),
                ..Default::default()
            },
        ];
        let mapped = map_events_to_skill("m", None, &events, false).unwrap();
        assert_eq!(mapped.steps.len(), 2);
        let next = apply_optimize_patch(
            &mapped,
            r#"{"schema_version":1,"merge":[{"from":1,"into":0}]}"#,
        )
        .unwrap();
        assert_eq!(next.steps.len(), 1);
        assert_eq!(next.steps[0]["selector"], "#a");
    }

    #[test]
    fn local_chain_from_field() {
        let field = json!({"id":"user","name":"username","tag":"input","autocomplete":"username"});
        let chain = local_backup_chain("#user", &[], Some(&field));
        assert!(chain.contains(&"#user".into()));
        assert!(chain.iter().any(|s| s.contains("name=\"username\"")));
        assert!(chain.iter().any(|s| s.contains("autocomplete=\"username\"")));
    }

    #[test]
    fn accept_selector_blocks_css_injection() {
        assert!(accept_selector("a;color:red").is_err());
        assert!(accept_selector("#ok").is_ok());
    }
}
