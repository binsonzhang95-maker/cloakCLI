use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::fs;
use std::path::{Path, PathBuf};

use crate::state;
use crate::util;

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
