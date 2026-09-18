//! Per-skill terminal statuses: final-report adapter, master validate, idempotent finalize.
//!
//! Scheduler `state` stays running/succeeded/failed/cancelled(/paused).
//! Business `result` is derived from the job's digest-bound declaration — never from
//! the latest package, a local same-name skill, or reporter-supplied success/label.

use anyhow::{bail, Result};
use serde_json::{json, Value};

use crate::jobs::{self, JobBusinessResult, JobRecord};
use crate::ledger;
use crate::skill_pkg::{self, SkillStatusDecl};

#[derive(Debug, Clone)]
pub struct ReportIdentity {
    pub skill_id: String,
    pub version: String,
    pub digest: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ProtocolError {
    EmptyStdout,
    MalformedFinalLine,
    MissingFields(&'static str),
    UnknownStatus(String),
    IdentityMismatch,
    DispatchMismatch,
    NotAnObject,
}

impl ProtocolError {
    pub fn as_message(&self) -> String {
        match self {
            ProtocolError::EmptyStdout => {
                "protocol: empty stdout (final report required)".into()
            }
            ProtocolError::MalformedFinalLine => {
                "protocol: last non-empty stdout line is not a JSON object".into()
            }
            ProtocolError::MissingFields(f) => {
                format!("protocol: final report missing {f}")
            }
            ProtocolError::UnknownStatus(s) => {
                format!("protocol: unknown status '{s}' (not in digest-bound declaration)")
            }
            ProtocolError::IdentityMismatch => {
                "protocol: skill_id/version/digest does not match job binding".into()
            }
            ProtocolError::DispatchMismatch => {
                "protocol: report client_id does not match dispatched node".into()
            }
            ProtocolError::NotAnObject => "protocol: final report is not a JSON object".into(),
        }
    }
}

/// Last non-empty line of stdout. Does **not** search backward for valid JSON.
pub fn last_nonempty_line(stdout: &str) -> Option<&str> {
    stdout
        .lines()
        .rev()
        .map(str::trim)
        .find(|l| !l.is_empty())
}

/// New protocol: last non-empty stdout line must be a JSON object.
pub fn parse_stdout_report(stdout: &str) -> Result<Value, ProtocolError> {
    let line = last_nonempty_line(stdout).ok_or(ProtocolError::EmptyStdout)?;
    let v: Value = serde_json::from_str(line).map_err(|_| ProtocolError::MalformedFinalLine)?;
    if !v.is_object() {
        return Err(ProtocolError::MalformedFinalLine);
    }
    Ok(v)
}

/// python_runner: new protocol requires identity in the last-line JSON;
/// legacy (`decls == None`) maps `ok` / `status` and only checks identity when present.
pub fn adapt_python_report(
    report: &Value,
    identity: &ReportIdentity,
    decls: Option<&[SkillStatusDecl]>,
) -> Result<JobBusinessResult, ProtocolError> {
    adapt_report(report, identity, decls, decls.is_some())
}

/// skill_steps: worker JSON is the report body; missing identity is filled from the job.
pub fn adapt_skill_steps_report(
    worker_data: &Value,
    identity: &ReportIdentity,
    decls: Option<&[SkillStatusDecl]>,
) -> Result<JobBusinessResult, ProtocolError> {
    adapt_report(worker_data, identity, decls, false)
}

pub fn adapt_report(
    report: &Value,
    identity: &ReportIdentity,
    decls: Option<&[SkillStatusDecl]>,
    require_identity: bool,
) -> Result<JobBusinessResult, ProtocolError> {
    if !report.is_object() {
        return Err(ProtocolError::NotAnObject);
    }
    check_identity(report, identity, require_identity)?;

    match decls {
        None => adapt_legacy(report, identity),
        Some(list) => {
            let status = report
                .get("status")
                .and_then(|v| v.as_str())
                .ok_or(ProtocolError::MissingFields("status"))?;
            let def = list
                .iter()
                .find(|s| s.id == status)
                .ok_or_else(|| ProtocolError::UnknownStatus(status.to_string()))?;
            Ok(result_from_decl(identity, def))
        }
    }
}

fn check_identity(
    report: &Value,
    identity: &ReportIdentity,
    require_identity: bool,
) -> Result<(), ProtocolError> {
    let sid = report.get("skill_id").and_then(|v| v.as_str());
    let ver = report.get("version").and_then(|v| v.as_str());
    let dig = report.get("digest").and_then(|v| v.as_str());
    if require_identity {
        if sid.is_none() {
            return Err(ProtocolError::MissingFields("skill_id"));
        }
        if ver.is_none() {
            return Err(ProtocolError::MissingFields("version"));
        }
        if dig.is_none() {
            return Err(ProtocolError::MissingFields("digest"));
        }
        let status = report.get("status").and_then(|v| v.as_str());
        if status.is_none() && report.get("ok").and_then(|v| v.as_bool()).is_none() {
            // New protocol requires `status`. Legacy python may send `ok` instead;
            // that path is selected when decls is None. When decls is Some, status
            // is checked later.
        }
    }
    if let Some(s) = sid {
        if s != identity.skill_id {
            return Err(ProtocolError::IdentityMismatch);
        }
    }
    if let Some(v) = ver {
        if v != identity.version {
            return Err(ProtocolError::IdentityMismatch);
        }
    }
    if let Some(d) = dig {
        let nd = skill_pkg::normalize_digest(d).map_err(|_| ProtocolError::IdentityMismatch)?;
        if nd != identity.digest {
            return Err(ProtocolError::IdentityMismatch);
        }
    }
    Ok(())
}

fn result_from_decl(identity: &ReportIdentity, def: &SkillStatusDecl) -> JobBusinessResult {
    JobBusinessResult {
        skill_id: identity.skill_id.clone(),
        version: identity.version.clone(),
        digest: if identity.digest.is_empty() {
            None
        } else {
            Some(identity.digest.clone())
        },
        status: def.id.clone(),
        success: def.success,
        retryable: def.retryable,
        label: def.label.clone(),
        optional: def.optional,
    }
}

fn adapt_legacy(
    report: &Value,
    identity: &ReportIdentity,
) -> Result<JobBusinessResult, ProtocolError> {
    let id = legacy_status_id(report)?;
    let decls = skill_pkg::legacy_status_decls();
    let def = decls
        .iter()
        .find(|s| s.id == id)
        .ok_or_else(|| ProtocolError::UnknownStatus(id.clone()))?;
    Ok(result_from_decl(identity, def))
}

fn legacy_status_id(report: &Value) -> Result<String, ProtocolError> {
    if let Some(s) = report.get("status").and_then(|v| v.as_str()) {
        return match s {
            "ok" | "succeeded" | "success" => Ok("ok".into()),
            "failed" | "error" => Ok("failed".into()),
            "cancelled" | "canceled" => Ok("cancelled".into()),
            other => Err(ProtocolError::UnknownStatus(other.to_string())),
        };
    }
    match report.get("ok").and_then(|v| v.as_bool()) {
        Some(true) => Ok("ok".into()),
        Some(false) => Ok("failed".into()),
        None => Err(ProtocolError::MissingFields("status or ok")),
    }
}

/// Client-side: paused is a scheduler state, not a business status.
pub fn is_paused_report(report: &Value) -> bool {
    matches!(
        report.get("status").and_then(|v| v.as_str()),
        Some("paused" | "ask_human")
    )
}

#[derive(Debug, Clone)]
pub struct ClientJobUpdate {
    pub job_id: String,
    pub client_id: String,
    pub reported_state: String,
    pub error: Option<String>,
    pub protocol_error: Option<String>,
    pub data: Option<Value>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FinalizeOutcome {
    Applied,
    Duplicate,
    Conflict,
    Protocol,
    SchedulerOnly,
}

/// Ingest a client `job_state` on the master: validate identity, dispatch node,
/// and digest-bound status. Protocol errors end scheduling without forging a
/// skill `failed` business result. Duplicate finals are idempotent; conflicts
/// do not overwrite.
pub fn ingest_client_update(root: &std::path::Path, upd: ClientJobUpdate) -> Result<JobRecord> {
    let mut rec = jobs::load(root, &upd.job_id)?.unwrap_or(JobRecord {
        job_id: upd.job_id.clone(),
        client_id: upd.client_id.clone(),
        skill: String::new(),
        profile: String::new(),
        headed: false,
        state: "queued".into(),
        error: None,
        data: None,
        updated_at: 0,
        skill_version: None,
        skill_digest: None,
        account_id: None,
        geo: None,
        result: None,
        protocol_error: None,
        success_counted: false,
    });

    let outcome = apply_update(&mut rec, &upd, root)?;
    jobs::save(root, &rec)?;
    if outcome == FinalizeOutcome::Applied {
        if let Some(res) = &rec.result {
            ledger::record(root, res, &rec.job_id, rec.success_counted)?;
        }
    }
    Ok(rec)
}

fn apply_update(
    rec: &mut JobRecord,
    upd: &ClientJobUpdate,
    root: &std::path::Path,
) -> Result<FinalizeOutcome> {
    rec.updated_at = chrono::Utc::now().timestamp();
    if !upd.client_id.is_empty() && rec.client_id.is_empty() {
        rec.client_id = upd.client_id.clone();
    }
    if upd.data.is_some() {
        rec.data = upd.data.clone();
    }

    let reported = upd.reported_state.as_str();

    if reported == "running" || reported == "queued" || reported == "cancelling" {
        if rec.result.is_some() || jobs::is_terminal(&rec.state) {
            return Ok(FinalizeOutcome::SchedulerOnly);
        }
        rec.state = reported.to_string();
        return Ok(FinalizeOutcome::SchedulerOnly);
    }

    if reported == "paused" {
        if rec.result.is_some() {
            return Ok(FinalizeOutcome::Duplicate);
        }
        rec.state = "paused".into();
        rec.error = upd.error.clone();
        rec.protocol_error = None;
        return Ok(FinalizeOutcome::SchedulerOnly);
    }

    if reported == "cancelled" || reported == "canceled" {
        return finalize_cancel(rec, upd);
    }

    // Dispatch node: reports for a bound job must come from the client we sent to.
    if !rec.client_id.is_empty()
        && !upd.client_id.is_empty()
        && rec.client_id != upd.client_id
    {
        return fail_protocol(rec, ProtocolError::DispatchMismatch.as_message());
    }

    let Some(data) = upd.data.as_ref() else {
        if let Some(pe) = &upd.protocol_error {
            if !pe.is_empty() {
                return fail_protocol(rec, pe.clone());
            }
        }
        if reported == "failed" {
            if rec.result.is_some() {
                return Ok(FinalizeOutcome::Duplicate);
            }
            rec.state = "failed".into();
            rec.error = upd.error.clone();
            return Ok(FinalizeOutcome::SchedulerOnly);
        }
        return fail_protocol(rec, ProtocolError::EmptyStdout.as_message());
    };

    if is_paused_report(data) {
        if rec.result.is_some() {
            return Ok(FinalizeOutcome::Duplicate);
        }
        rec.state = "paused".into();
        rec.error = upd.error.clone();
        return Ok(FinalizeOutcome::SchedulerOnly);
    }

    let (identity, decls) = match bind_identity_and_decls(rec, root) {
        Ok(v) => v,
        Err(e) => return fail_protocol(rec, e),
    };

    let require_identity = decls.is_some();
    let adapted = match adapt_report(data, &identity, decls.as_deref(), require_identity) {
        Ok(r) => r,
        Err(pe) => return fail_protocol(rec, pe.as_message()),
    };

    finalize_result(rec, adapted)
}

fn bind_identity_and_decls(
    rec: &JobRecord,
    root: &std::path::Path,
) -> Result<(ReportIdentity, Option<Vec<SkillStatusDecl>>), String> {
    let skill_id = rec.skill.clone();
    let version = rec.skill_version.clone().unwrap_or_default();
    let digest = rec.skill_digest.clone().unwrap_or_default();

    if digest.is_empty() {
        // Historical record without digest: legacy adapter, do not forge a digest.
        if skill_id.is_empty() {
            return Err(ProtocolError::MissingFields("skill").as_message());
        }
        return Ok((
            ReportIdentity {
                skill_id,
                version,
                digest: String::new(),
            },
            None,
        ));
    }

    let digest = skill_pkg::normalize_digest(&digest).map_err(|e| e.to_string())?;
    let decls = skill_pkg::statuses_for_digest(root, &skill_id, &digest)
        .map_err(|e| e.to_string())?;
    Ok((
        ReportIdentity {
            skill_id,
            version,
            digest,
        },
        decls,
    ))
}

fn finalize_cancel(rec: &mut JobRecord, upd: &ClientJobUpdate) -> Result<FinalizeOutcome> {
    if rec.result.is_some() {
        // Already finalized — late cancel does not wipe a counted result.
        return Ok(FinalizeOutcome::Duplicate);
    }
    rec.state = "cancelled".into();
    rec.error = upd.error.clone();
    rec.protocol_error = None;
    rec.result = None;
    Ok(FinalizeOutcome::SchedulerOnly)
}

fn fail_protocol(rec: &mut JobRecord, msg: String) -> Result<FinalizeOutcome> {
    if rec.result.is_some() {
        return Ok(FinalizeOutcome::Conflict);
    }
    rec.state = "failed".into();
    rec.protocol_error = Some(msg.clone());
    rec.error = Some(msg);
    rec.result = None;
    Ok(FinalizeOutcome::Protocol)
}

fn finalize_result(
    rec: &mut JobRecord,
    incoming: JobBusinessResult,
) -> Result<FinalizeOutcome> {
    if let Some(existing) = &rec.result {
        if existing.status == incoming.status
            && existing.digest == incoming.digest
            && existing.skill_id == incoming.skill_id
        {
            return Ok(FinalizeOutcome::Duplicate);
        }
        return Ok(FinalizeOutcome::Conflict);
    }
    rec.result = Some(incoming.clone());
    rec.protocol_error = None;
    rec.error = None;
    rec.state = if incoming.success {
        "succeeded".into()
    } else {
        "failed".into()
    };
    if incoming.success && !rec.success_counted {
        rec.success_counted = true;
    }
    Ok(FinalizeOutcome::Applied)
}

/// Build a python_runner / skill_steps identity from the job binding.
pub fn identity_from_job(skill_id: &str, version: &str, digest: &str) -> Result<ReportIdentity> {
    if skill_id.is_empty() {
        bail!("skill_id required");
    }
    let digest = if digest.is_empty() {
        String::new()
    } else {
        skill_pkg::normalize_digest(digest)?
    };
    Ok(ReportIdentity {
        skill_id: skill_id.to_string(),
        version: version.to_string(),
        digest,
    })
}

pub fn decls_from_manifest(m: &skill_pkg::SkillManifest) -> Option<Vec<SkillStatusDecl>> {
    m.statuses.clone()
}

/// Client helper: map an adapted result to scheduler state (does not count).
pub fn scheduler_state_for(result: &JobBusinessResult) -> &'static str {
    if result.success {
        "succeeded"
    } else {
        "failed"
    }
}

pub fn result_to_json(r: &JobBusinessResult) -> Value {
    json!({
        "skill_id": r.skill_id,
        "version": r.version,
        "digest": r.digest,
        "status": r.status,
        "success": r.success,
        "retryable": r.retryable,
        "label": r.label,
        "optional": r.optional,
    })
}

/// Convenience for tests and CLI: count successes in a skill partition.
#[allow(dead_code)]
pub fn success_count(root: &std::path::Path, skill_id: &str) -> Result<u64> {
    ledger::success_count(root, skill_id)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::skill_pkg::SkillStatusDecl;
    use crate::state;
    use serde_json::json;
    use std::fs;
    use std::path::PathBuf;
    use std::time::{SystemTime, UNIX_EPOCH};

    fn tmp() -> PathBuf {
        let n = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let p = std::env::temp_dir().join(format!("cloakcli_status_{n}"));
        let _ = fs::remove_dir_all(&p);
        fs::create_dir_all(p.join("skills")).unwrap();
        fs::create_dir_all(p.join("data")).unwrap();
        p
    }

    fn pin_decls() -> Vec<SkillStatusDecl> {
        vec![
            SkillStatusDecl {
                id: "logged_in".into(),
                success: true,
                retryable: false,
                label: "已登录".into(),
                optional: false,
            },
            SkillStatusDecl {
                id: "email_confirmed".into(),
                success: true,
                retryable: false,
                label: "邮箱已确认".into(),
                optional: true,
            },
            SkillStatusDecl {
                id: "oops_park".into(),
                success: false,
                retryable: true,
                label: "风控先放".into(),
                optional: false,
            },
        ]
    }

    fn ship_decls() -> Vec<SkillStatusDecl> {
        vec![
            SkillStatusDecl {
                id: "shipped".into(),
                success: true,
                retryable: false,
                label: "Shipped".into(),
                optional: false,
            },
            SkillStatusDecl {
                id: "returned".into(),
                success: false,
                retryable: true,
                label: "Returned".into(),
                optional: false,
            },
        ]
    }

    fn digest_a() -> String {
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa".into()
    }
    fn digest_b() -> String {
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb".into()
    }

    fn ident(skill: &str, digest: &str) -> ReportIdentity {
        ReportIdentity {
            skill_id: skill.into(),
            version: "1.0.0".into(),
            digest: digest.into(),
        }
    }

    fn report(skill: &str, digest: &str, status: &str) -> Value {
        json!({
            "skill_id": skill,
            "version": "1.0.0",
            "digest": digest,
            "status": status,
        })
    }

    fn seed_release(
        root: &std::path::Path,
        skill: &str,
        version: &str,
        digest: &str,
        statuses: Option<Vec<SkillStatusDecl>>,
    ) {
        let dir = state::data_dir(root).join("skill_releases");
        fs::create_dir_all(&dir).unwrap();
        fs::write(
            dir.join("catalog.json"),
            format!(
                "{}\n",
                serde_json::to_string_pretty(&json!({
                    "releases": [{
                        "skill_id": skill,
                        "version": version,
                        "digest": digest,
                        "path": format!("{skill}/{version}/package.tar"),
                        "published": true,
                        "created_at": 1,
                        "entry": "python_runner",
                        "secret_names": [],
                        "statuses": statuses,
                    }]
                }))
                .unwrap()
            ),
        )
        .unwrap();
    }

    fn bind_job(
        root: &std::path::Path,
        job_id: &str,
        skill: &str,
        digest: &str,
        client: &str,
    ) -> JobRecord {
        let rec = jobs::upsert_state(
            root,
            job_id,
            client,
            skill,
            "noproxy",
            false,
            "running",
            None,
            None,
        )
        .unwrap();
        jobs::set_binding(root, job_id, Some("1.0.0"), Some(digest), None, None).unwrap();
        let _ = rec;
        jobs::load(root, job_id).unwrap().unwrap()
    }

    #[test]
    fn stdout_empty_and_malformed_rejected() {
        assert!(matches!(
            parse_stdout_report(""),
            Err(ProtocolError::EmptyStdout)
        ));
        assert!(matches!(
            parse_stdout_report("   \n  \n"),
            Err(ProtocolError::EmptyStdout)
        ));
        assert!(matches!(
            parse_stdout_report("not json\n"),
            Err(ProtocolError::MalformedFinalLine)
        ));
        assert!(matches!(
            parse_stdout_report("{\"status\":\"logged_in\"}\nthis is a log line\n"),
            Err(ProtocolError::MalformedFinalLine)
        ));
        // Does not walk backward to the previous JSON line.
        let ok = parse_stdout_report("log: start\n{\"a\":1}\n").unwrap();
        assert_eq!(ok["a"], 1);
        let arr = parse_stdout_report("[1,2]\n");
        assert!(matches!(arr, Err(ProtocolError::MalformedFinalLine)));
    }

    #[test]
    fn two_skills_cross_reject_unknown_and_wrong_triple() {
        let pin = ident("pin-reg", &digest_a());
        let ship = ident("ship-demo", &digest_b());
        let pin_d = pin_decls();
        let ship_d = ship_decls();

        let ok = adapt_python_report(
            &report("pin-reg", &digest_a(), "logged_in"),
            &pin,
            Some(&pin_d),
        )
        .unwrap();
        assert_eq!(ok.status, "logged_in");
        assert!(ok.success);
        assert_eq!(ok.label, "已登录");

        let shipped = adapt_python_report(
            &report("ship-demo", &digest_b(), "shipped"),
            &ship,
            Some(&ship_d),
        )
        .unwrap();
        assert_eq!(shipped.status, "shipped");

        // Cross-use: pin-reg reporting shipped.
        let err = adapt_python_report(
            &report("pin-reg", &digest_a(), "shipped"),
            &pin,
            Some(&pin_d),
        )
        .unwrap_err();
        assert!(matches!(err, ProtocolError::UnknownStatus(s) if s == "shipped"));

        let err = adapt_python_report(
            &report("ship-demo", &digest_b(), "logged_in"),
            &ship,
            Some(&ship_d),
        )
        .unwrap_err();
        assert!(matches!(err, ProtocolError::UnknownStatus(_)));

        // Wrong triple.
        let err = adapt_python_report(
            &report("pin-reg", &digest_b(), "logged_in"),
            &pin,
            Some(&pin_d),
        )
        .unwrap_err();
        assert_eq!(err, ProtocolError::IdentityMismatch);

        let err = adapt_python_report(
            &report("other", &digest_a(), "logged_in"),
            &pin,
            Some(&pin_d),
        )
        .unwrap_err();
        assert_eq!(err, ProtocolError::IdentityMismatch);

        let mut bad_ver = report("pin-reg", &digest_a(), "logged_in");
        bad_ver["version"] = json!("9.9.9");
        let err = adapt_python_report(&bad_ver, &pin, Some(&pin_d)).unwrap_err();
        assert_eq!(err, ProtocolError::IdentityMismatch);

        // Reporter-supplied success/label ignored.
        let mut forged = report("pin-reg", &digest_a(), "oops_park");
        forged["success"] = json!(true);
        forged["label"] = json!("totally fine");
        let got = adapt_python_report(&forged, &pin, Some(&pin_d)).unwrap();
        assert!(!got.success);
        assert_eq!(got.label, "风控先放");
        assert!(got.retryable);
    }

    #[test]
    fn ingest_counts_success_once_and_keeps_login_on_confirm_fail() {
        let root = tmp();
        seed_release(&root, "pin-reg", "1.0.0", &digest_a(), Some(pin_decls()));

        // logged_in counts once
        bind_job(&root, "j-login", "pin-reg", &digest_a(), "box1");
        let rec = ingest_client_update(
            &root,
            ClientJobUpdate {
                job_id: "j-login".into(),
                client_id: "box1".into(),
                reported_state: "succeeded".into(),
                error: None,
                protocol_error: None,
                data: Some(report("pin-reg", &digest_a(), "logged_in")),
            },
        )
        .unwrap();
        assert_eq!(rec.state, "succeeded");
        assert_eq!(rec.result.as_ref().unwrap().status, "logged_in");
        assert!(rec.success_counted);
        assert_eq!(success_count(&root, "pin-reg").unwrap(), 1);

        // Duplicate same result: no double count.
        let rec2 = ingest_client_update(
            &root,
            ClientJobUpdate {
                job_id: "j-login".into(),
                client_id: "box1".into(),
                reported_state: "succeeded".into(),
                error: None,
                protocol_error: None,
                data: Some(report("pin-reg", &digest_a(), "logged_in")),
            },
        )
        .unwrap();
        assert_eq!(rec2.result.as_ref().unwrap().status, "logged_in");
        assert_eq!(success_count(&root, "pin-reg").unwrap(), 1);

        // Conflict: email_confirmed must not overwrite logged_in.
        let rec3 = ingest_client_update(
            &root,
            ClientJobUpdate {
                job_id: "j-login".into(),
                client_id: "box1".into(),
                reported_state: "succeeded".into(),
                error: None,
                protocol_error: None,
                data: Some(report("pin-reg", &digest_a(), "email_confirmed")),
            },
        )
        .unwrap();
        assert_eq!(rec3.result.as_ref().unwrap().status, "logged_in");
        assert_eq!(success_count(&root, "pin-reg").unwrap(), 1);

        // Separate job: email_confirmed (implies login by skill contract) counts once.
        bind_job(&root, "j-confirm", "pin-reg", &digest_a(), "box1");
        let rec = ingest_client_update(
            &root,
            ClientJobUpdate {
                job_id: "j-confirm".into(),
                client_id: "box1".into(),
                reported_state: "succeeded".into(),
                error: None,
                protocol_error: None,
                data: Some(report("pin-reg", &digest_a(), "email_confirmed")),
            },
        )
        .unwrap();
        assert!(rec.result.as_ref().unwrap().success);
        assert!(rec.result.as_ref().unwrap().optional);
        assert_eq!(success_count(&root, "pin-reg").unwrap(), 2);

        // oops_park does not count.
        bind_job(&root, "j-oops", "pin-reg", &digest_a(), "box1");
        let rec = ingest_client_update(
            &root,
            ClientJobUpdate {
                job_id: "j-oops".into(),
                client_id: "box1".into(),
                reported_state: "succeeded".into(),
                error: None,
                protocol_error: None,
                data: Some(report("pin-reg", &digest_a(), "oops_park")),
            },
        )
        .unwrap();
        assert_eq!(rec.state, "failed");
        assert_eq!(rec.result.as_ref().unwrap().status, "oops_park");
        assert!(!rec.result.as_ref().unwrap().success);
        assert!(!rec.success_counted);
        assert_eq!(success_count(&root, "pin-reg").unwrap(), 2);

        // Confirm step failed: skill reports logged_in (does not wipe login).
        bind_job(&root, "j-login2", "pin-reg", &digest_a(), "box1");
        let rec = ingest_client_update(
            &root,
            ClientJobUpdate {
                job_id: "j-login2".into(),
                client_id: "box1".into(),
                reported_state: "succeeded".into(),
                error: None,
                protocol_error: None,
                data: Some(report("pin-reg", &digest_a(), "logged_in")),
            },
        )
        .unwrap();
        assert_eq!(rec.result.as_ref().unwrap().status, "logged_in");
        assert_eq!(success_count(&root, "pin-reg").unwrap(), 3);

        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn protocol_errors_do_not_forge_skill_failed_or_count() {
        let root = tmp();
        seed_release(&root, "pin-reg", "1.0.0", &digest_a(), Some(pin_decls()));
        bind_job(&root, "j1", "pin-reg", &digest_a(), "box1");

        let rec = ingest_client_update(
            &root,
            ClientJobUpdate {
                job_id: "j1".into(),
                client_id: "box1".into(),
                reported_state: "succeeded".into(),
                error: None,
                protocol_error: None,
                data: Some(json!({"not": "a report"})),
            },
        )
        .unwrap();
        assert_eq!(rec.state, "failed");
        assert!(rec.result.is_none(), "must not forge skill failed");
        assert!(rec.protocol_error.is_some());
        assert_eq!(success_count(&root, "pin-reg").unwrap(), 0);

        bind_job(&root, "j2", "pin-reg", &digest_a(), "box1");
        let rec = ingest_client_update(
            &root,
            ClientJobUpdate {
                job_id: "j2".into(),
                client_id: "box1".into(),
                reported_state: "succeeded".into(),
                error: None,
                protocol_error: None,
                data: Some(report("pin-reg", &digest_a(), "nope")),
            },
        )
        .unwrap();
        assert!(rec.result.is_none());
        assert!(rec
            .protocol_error
            .as_deref()
            .unwrap()
            .contains("unknown status"));

        bind_job(&root, "j3", "pin-reg", &digest_a(), "box1");
        let rec = ingest_client_update(
            &root,
            ClientJobUpdate {
                job_id: "j3".into(),
                client_id: "other-box".into(),
                reported_state: "succeeded".into(),
                error: None,
                protocol_error: None,
                data: Some(report("pin-reg", &digest_a(), "logged_in")),
            },
        )
        .unwrap();
        assert!(rec.result.is_none());
        assert!(rec
            .protocol_error
            .as_deref()
            .unwrap()
            .contains("client_id"));

        // Crash / timeout: no result from missing data.
        bind_job(&root, "j4", "pin-reg", &digest_a(), "box1");
        let rec = ingest_client_update(
            &root,
            ClientJobUpdate {
                job_id: "j4".into(),
                client_id: "box1".into(),
                reported_state: "failed".into(),
                error: Some("python_runner timed out".into()),
                protocol_error: None,
                data: None,
            },
        )
        .unwrap();
        assert_eq!(rec.state, "failed");
        assert!(rec.result.is_none());
        assert!(rec.protocol_error.is_none());
        assert_eq!(success_count(&root, "pin-reg").unwrap(), 0);

        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn old_digest_labels_survive_status_definition_change() {
        let root = tmp();
        // v1 declaration
        seed_release(&root, "pin-reg", "1.0.0", &digest_a(), Some(pin_decls()));
        bind_job(&root, "old", "pin-reg", &digest_a(), "box1");
        ingest_client_update(
            &root,
            ClientJobUpdate {
                job_id: "old".into(),
                client_id: "box1".into(),
                reported_state: "succeeded".into(),
                error: None,
                protocol_error: None,
                data: Some(report("pin-reg", &digest_a(), "logged_in")),
            },
        )
        .unwrap();

        // Publish v2 with a different label for logged_in and a new digest.
        let mut v2 = pin_decls();
        v2[0].label = "Signed in (v2)".into();
        let dir = state::data_dir(&root).join("skill_releases");
        fs::write(
            dir.join("catalog.json"),
            serde_json::to_string_pretty(&json!({
                "releases": [
                    {
                        "skill_id": "pin-reg",
                        "version": "1.0.0",
                        "digest": digest_a(),
                        "path": "pin-reg/1.0.0/package.tar",
                        "published": true,
                        "created_at": 1,
                        "entry": "python_runner",
                        "secret_names": [],
                        "statuses": pin_decls(),
                    },
                    {
                        "skill_id": "pin-reg",
                        "version": "2.0.0",
                        "digest": digest_b(),
                        "path": "pin-reg/2.0.0/package.tar",
                        "published": true,
                        "created_at": 2,
                        "entry": "python_runner",
                        "secret_names": [],
                        "statuses": v2,
                    }
                ]
            }))
            .unwrap(),
        )
        .unwrap();

        let old = jobs::load(&root, "old").unwrap().unwrap();
        assert_eq!(old.result.as_ref().unwrap().label, "已登录");
        assert_eq!(old.result.as_ref().unwrap().digest.as_deref(), Some(digest_a().as_str()));

        // In-flight v1 job still validates against v1 decls (shipped still unknown).
        bind_job(&root, "inflight", "pin-reg", &digest_a(), "box1");
        let rec = ingest_client_update(
            &root,
            ClientJobUpdate {
                job_id: "inflight".into(),
                client_id: "box1".into(),
                reported_state: "succeeded".into(),
                error: None,
                protocol_error: None,
                data: Some(report("pin-reg", &digest_a(), "shipped")),
            },
        )
        .unwrap();
        assert!(rec.result.is_none());

        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn skill_steps_and_legacy_adapter() {
        let id = ident("hello", &digest_a());
        // skill_steps fills identity; worker only has status.
        let got = adapt_skill_steps_report(&json!({"status": "succeeded", "extracts": {}}), &id, None)
            .unwrap();
        assert_eq!(got.status, "ok");
        assert!(got.success);

        let got = adapt_skill_steps_report(&json!({"ok": true}), &id, None).unwrap();
        assert_eq!(got.status, "ok");

        let got = adapt_python_report(&json!({"ok": false, "error": "nope"}), &id, None).unwrap();
        assert_eq!(got.status, "failed");
        assert!(!got.success);

        let err = adapt_python_report(&json!({"echo": true}), &id, None).unwrap_err();
        assert!(matches!(err, ProtocolError::MissingFields(_)));

        // New protocol skill_steps with custom status, identity filled.
        let pin = ident("pin-reg", &digest_a());
        let got = adapt_skill_steps_report(
            &json!({"status": "logged_in", "extracts": {"x": 1}}),
            &pin,
            Some(&pin_decls()),
        )
        .unwrap();
        assert_eq!(got.status, "logged_in");
        assert_eq!(got.label, "已登录");

        // If worker *does* include a wrong triple, reject.
        let err = adapt_skill_steps_report(
            &json!({
                "skill_id": "nope",
                "version": "1.0.0",
                "digest": digest_a(),
                "status": "logged_in"
            }),
            &pin,
            Some(&pin_decls()),
        )
        .unwrap_err();
        assert_eq!(err, ProtocolError::IdentityMismatch);
    }

    #[test]
    fn cancel_does_not_count_and_retryable_does_not_resubmit() {
        let root = tmp();
        seed_release(&root, "pin-reg", "1.0.0", &digest_a(), Some(pin_decls()));
        bind_job(&root, "c1", "pin-reg", &digest_a(), "box1");
        let rec = ingest_client_update(
            &root,
            ClientJobUpdate {
                job_id: "c1".into(),
                client_id: "box1".into(),
                reported_state: "cancelled".into(),
                error: Some("cancelled".into()),
                protocol_error: None,
                data: Some(report("pin-reg", &digest_a(), "logged_in")),
            },
        )
        .unwrap();
        assert_eq!(rec.state, "cancelled");
        assert!(rec.result.is_none());
        assert_eq!(success_count(&root, "pin-reg").unwrap(), 0);

        // retryable oops_park is stored; ingest does not create another job.
        bind_job(&root, "r1", "pin-reg", &digest_a(), "box1");
        ingest_client_update(
            &root,
            ClientJobUpdate {
                job_id: "r1".into(),
                client_id: "box1".into(),
                reported_state: "succeeded".into(),
                error: None,
                protocol_error: None,
                data: Some(report("pin-reg", &digest_a(), "oops_park")),
            },
        )
        .unwrap();
        let jobs_dir = jobs::jobs_dir(&root);
        let n = fs::read_dir(&jobs_dir).unwrap().count();
        assert_eq!(n, 2, "retryable must not auto-register a new job");

        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn historical_without_digest_is_not_forged() {
        let root = tmp();
        let rec = JobRecord {
            job_id: "legacy-1".into(),
            client_id: "box1".into(),
            skill: "hello".into(),
            profile: "noproxy".into(),
            headed: false,
            state: "running".into(),
            error: None,
            data: None,
            updated_at: 0,
            skill_version: None,
            skill_digest: None,
            account_id: None,
            geo: None,
            result: None,
            protocol_error: None,
            success_counted: false,
        };
        jobs::save(&root, &rec).unwrap();
        let rec = ingest_client_update(
            &root,
            ClientJobUpdate {
                job_id: "legacy-1".into(),
                client_id: "box1".into(),
                reported_state: "succeeded".into(),
                error: None,
                protocol_error: None,
                data: Some(json!({"ok": true})),
            },
        )
        .unwrap();
        assert_eq!(rec.result.as_ref().unwrap().status, "ok");
        assert!(rec.result.as_ref().unwrap().digest.is_none());
        assert_eq!(success_count(&root, "hello").unwrap(), 1);
        let _ = fs::remove_dir_all(&root);
    }

    #[test]
    fn ledger_is_partitioned_by_skill() {
        let root = tmp();
        seed_release(&root, "pin-reg", "1.0.0", &digest_a(), Some(pin_decls()));
        seed_release(&root, "ship-demo", "1.0.0", &digest_b(), Some(ship_decls()));
        // Overwrite catalog with both.
        let dir = state::data_dir(&root).join("skill_releases");
        fs::write(
            dir.join("catalog.json"),
            serde_json::to_string_pretty(&json!({
                "releases": [
                    {
                        "skill_id": "pin-reg",
                        "version": "1.0.0",
                        "digest": digest_a(),
                        "path": "pin-reg/1.0.0/package.tar",
                        "published": true,
                        "created_at": 1,
                        "entry": "python_runner",
                        "secret_names": [],
                        "statuses": pin_decls(),
                    },
                    {
                        "skill_id": "ship-demo",
                        "version": "1.0.0",
                        "digest": digest_b(),
                        "path": "ship-demo/1.0.0/package.tar",
                        "published": true,
                        "created_at": 1,
                        "entry": "python_runner",
                        "secret_names": [],
                        "statuses": ship_decls(),
                    }
                ]
            }))
            .unwrap(),
        )
        .unwrap();

        bind_job(&root, "p1", "pin-reg", &digest_a(), "box1");
        ingest_client_update(
            &root,
            ClientJobUpdate {
                job_id: "p1".into(),
                client_id: "box1".into(),
                reported_state: "succeeded".into(),
                error: None,
                protocol_error: None,
                data: Some(report("pin-reg", &digest_a(), "logged_in")),
            },
        )
        .unwrap();
        bind_job(&root, "s1", "ship-demo", &digest_b(), "box1");
        ingest_client_update(
            &root,
            ClientJobUpdate {
                job_id: "s1".into(),
                client_id: "box1".into(),
                reported_state: "succeeded".into(),
                error: None,
                protocol_error: None,
                data: Some(report("ship-demo", &digest_b(), "shipped")),
            },
        )
        .unwrap();
        assert_eq!(success_count(&root, "pin-reg").unwrap(), 1);
        assert_eq!(success_count(&root, "ship-demo").unwrap(), 1);
        let pin = ledger::load_partition(&root, "pin-reg").unwrap();
        assert!(pin.entries.iter().all(|e| e.skill_id == "pin-reg"));
        assert!(!serde_json::to_string(&pin).unwrap().contains("email_confirmed")
            || pin.entries.iter().any(|e| e.status == "logged_in"));
        // Partition JSON has no global email_confirmed column — only per-entry status.
        let v = serde_json::to_value(&pin).unwrap();
        assert!(v.get("email_confirmed").is_none());
        let _ = fs::remove_dir_all(&root);
    }
}
