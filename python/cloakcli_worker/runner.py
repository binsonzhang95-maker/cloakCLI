"""Execute skill.json steps, with optional vision stall-recovery on the same page."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .browser import apply_cookie_file, get_page, launch_context
from .llm_config import load_llm_config
from .paths import PathTrustError, ensure_under_root, get_root, set_root
from .redact import looks_secret_key, redact_any, redact_text
from .recover.loop import AskHumanError, RecoverFailed, RecoverResult, run_recover
from .recover.origin import origin_of

# {{NAME}} and {{vars.NAME}} (teach export uses the vars. prefix).
_VAR_RE = re.compile(r"\{\{\s*(?:vars\.)?([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


class UndefinedVarError(ValueError):
    pass


class SkillRunError(Exception):
    """Worker-visible skill failure with structured data (trajectory, status)."""

    def __init__(self, message: str, *, status: str, data: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.data = data or {}


def _subst(value: Any, variables: dict[str, Any], *, allow_missing: bool = False) -> Any:
    if isinstance(value, str):
        missing: list[str] = []

        def repl(m: re.Match[str]) -> str:
            key = m.group(1)
            if key not in variables:
                missing.append(key)
                return m.group(0)
            return str(variables[key])

        out = _VAR_RE.sub(repl, value)
        if missing and not allow_missing:
            raise UndefinedVarError(
                f"undefined variable(s) in skill: {', '.join(sorted(set(missing)))}"
            )
        return out
    if isinstance(value, list):
        return [_subst(v, variables, allow_missing=allow_missing) for v in value]
    if isinstance(value, dict):
        return {k: _subst(v, variables, allow_missing=allow_missing) for k, v in value.items()}
    return value


def load_skill(skill_path: str | Path) -> dict[str, Any]:
    path = Path(skill_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    return data


def _validate_params(skill: dict[str, Any], variables: dict[str, Any]) -> None:
    """Validate required params from skill.params definitions."""
    params = skill.get("params") or []
    if not isinstance(params, list):
        return
    missing_required: list[str] = []
    for p in params:
        if isinstance(p, str):
            continue
        if not isinstance(p, dict):
            continue
        name = p.get("name")
        if not name:
            continue
        required = bool(p.get("required", False))
        if name not in variables and "default" in p:
            variables[name] = p["default"]
        if required and name not in variables:
            missing_required.append(str(name))
    if missing_required:
        raise ValueError(
            f"missing required skill param(s): {', '.join(missing_required)}"
        )


def resolve_on_stall(step: dict[str, Any], skill: dict[str, Any]) -> str:
    raw = step.get("on_stall", skill.get("on_stall", "fail"))
    v = str(raw or "fail").strip().lower()
    if v in ("recover", "fail"):
        return v
    return "fail"


def step_goal(step: dict[str, Any], skill: dict[str, Any], action: str) -> str:
    g = step.get("goal") or skill.get("goal")
    if isinstance(g, str) and g.strip():
        return g.strip()
    sel = step.get("selector") or step.get("css") or ""
    return f"Complete step {action}" + (f" ({sel})" if sel else "")


def _is_recoverable(exc: BaseException) -> bool:
    if isinstance(
        exc,
        (PathTrustError, UndefinedVarError, SkillRunError, KeyboardInterrupt, SystemExit),
    ):
        return False
    if isinstance(exc, ValueError):
        msg = str(exc).lower()
        if "unknown action" in msg or "undefined variable" in msg or "missing required" in msg:
            return False
    return True


def run_skill(
    *,
    skill_path: str,
    user_data_dir: str,
    headed: bool = False,
    proxy: str | None = None,
    vars: dict[str, Any] | None = None,
    root: str | None = None,
    cookie_file: str | None = None,
    cancel_check: Any | None = None,
    provider: Any | None = None,
) -> dict[str, Any]:
    if root:
        set_root(root)
    project_root = get_root()

    skill_path_safe = ensure_under_root(skill_path, project_root)
    user_data_safe = ensure_under_root(user_data_dir, project_root)
    user_data_safe.mkdir(parents=True, exist_ok=True)

    skill = load_skill(skill_path_safe)
    variables: dict[str, Any] = {}
    if isinstance(skill.get("vars"), dict):
        variables.update(skill["vars"])
    if vars:
        variables.update(vars)

    _validate_params(skill, variables)

    # Persist fingerprint_seed from profiles/*/profile.json when resolvable
    # (by user_data_dir match). No seed → cloakbrowser random for this launch only.
    profiles_root = project_root / "profiles"
    profile_name = None
    if isinstance(variables.get("profile"), str) and variables["profile"].strip():
        profile_name = variables["profile"].strip()
    meta_path = None
    if profile_name:
        candidate = profiles_root / profile_name / "profile.json"
        if candidate.is_file():
            meta_path = candidate
    ctx = launch_context(
        user_data_dir=str(user_data_safe),
        headed=headed,
        proxy=proxy,
        profile_meta_path=meta_path,
        profiles_root=profiles_root,
    )
    cookie_meta = None
    if cookie_file:
        cookie_safe = ensure_under_root(cookie_file, project_root)
        try:
            cookie_meta = apply_cookie_file(ctx, str(cookie_safe))
        except Exception:
            try:
                ctx.close()
            except Exception:
                pass
            raise
    page = get_page(ctx)

    skill_name = skill.get("name") or Path(skill_path_safe).parent.name
    if "/" in skill_name or "\\" in skill_name or ".." in skill_name:
        try:
            ctx.close()
        except Exception:
            pass
        raise ValueError(f"unsafe skill name for artifacts: {skill_name}")

    artifacts_dir = ensure_under_root(
        project_root / "data" / "artifacts" / skill_name, project_root
    )
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    try:
        result = execute_skill(
            page=page,
            skill=skill,
            skill_name=skill_name,
            variables=variables,
            artifacts_dir=artifacts_dir,
            project_root=project_root,
            cookie_meta=cookie_meta,
            cancel_check=cancel_check,
            provider=provider,
        )
        return result
    finally:
        try:
            ctx.close()
        except Exception:
            pass


def execute_skill(
    *,
    page: Any,
    skill: dict[str, Any],
    skill_name: str,
    variables: dict[str, Any],
    artifacts_dir: Path,
    project_root: Path,
    cookie_meta: dict[str, Any] | None = None,
    cancel_check: Any | None = None,
    provider: Any | None = None,
) -> dict[str, Any]:
    extracts: dict[str, Any] = {}
    recover_runs: list[dict[str, Any]] = []
    task_origin: str | None = None
    llm_cfg = load_llm_config(project_root)

    def _write(status: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        result = {
            "skill": skill_name,
            "status": status,
            "extracts": extracts,
            "vars": redact_any(_public_vars(variables)),
            "artifacts_dir": str(artifacts_dir),
        }
        if cookie_meta:
            result["cookies"] = cookie_meta
        if recover_runs:
            result["recover"] = recover_runs
        if extra:
            result.update(extra)
        (artifacts_dir / "last_result.json").write_text(
            json.dumps(redact_any(result), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return result

    try:
        for i, raw_step in enumerate(skill.get("steps") or []):
            step = _subst(raw_step, {**variables, **extracts})
            if not isinstance(step, dict):
                raise ValueError(f"Step {i}: must be an object")
            action = (step.get("action") or "").strip().lower()
            if not action:
                raise ValueError(f"Step {i}: missing action")
            try:
                _run_step(
                    page, step, action, extracts, artifacts_dir, variables, project_root
                )
                if action == "goto":
                    task_origin = task_origin or origin_of(getattr(page, "url", None))
                else:
                    task_origin = task_origin or origin_of(getattr(page, "url", None))
            except Exception as e:
                if not _is_recoverable(e) or resolve_on_stall(step, skill) != "recover":
                    raise
                if not llm_cfg or not llm_cfg.enabled:
                    raise SkillRunError(
                        f"{e}; recover skipped (llm disabled or unconfigured)",
                        status="failed",
                        data={"step": i, "action": action},
                    ) from e
                stall = _stall_payload(i, action, step, e)
                goal = step_goal(step, skill, action)
                outcome: RecoverResult = run_recover(
                    page=page,
                    goal=goal,
                    stall=stall,
                    artifacts_dir=artifacts_dir,
                    skill_name=skill_name,
                    task_origin=task_origin or origin_of(getattr(page, "url", None)),
                    cfg=llm_cfg,
                    root=project_root,
                    provider=provider,
                    cancel_check=cancel_check,
                )
                recover_runs.append(outcome.public_dict())
                if outcome.status == "done":
                    task_origin = task_origin or origin_of(getattr(page, "url", None))
                    continue
                data = {
                    "status": "paused" if outcome.status == "ask_human" else outcome.status,
                    "recover": outcome.public_dict(),
                    "trajectory": outcome.trajectory_path,
                    "step": i,
                }
                _write(
                    "paused" if outcome.status == "ask_human" else "failed",
                    {"recover_last": outcome.public_dict()},
                )
                if outcome.status == "ask_human":
                    raise SkillRunError(
                        f"ASK_HUMAN: {outcome.reason}",
                        status="paused",
                        data=data,
                    ) from e
                if outcome.status == "timeout":
                    raise SkillRunError(
                        f"RECOVER_TIMEOUT: {outcome.reason}",
                        status="timeout",
                        data=data,
                    ) from e
                if outcome.status == "cancelled":
                    raise SkillRunError(
                        f"RECOVER_CANCELLED: {outcome.reason}",
                        status="cancelled",
                        data=data,
                    ) from e
                raise SkillRunError(
                    f"RECOVER_FAILED: {outcome.reason}",
                    status="failed",
                    data=data,
                ) from e
    except SkillRunError:
        raise
    except AskHumanError as e:
        recover_runs.append(e.result.public_dict())
        _write("paused", {"recover_last": e.result.public_dict()})
        raise SkillRunError(
            f"ASK_HUMAN: {e.result.reason}",
            status="paused",
            data={"status": "paused", "recover": e.result.public_dict()},
        ) from e
    except RecoverFailed as e:
        recover_runs.append(e.result.public_dict())
        _write("failed", {"recover_last": e.result.public_dict()})
        raise SkillRunError(
            str(e),
            status=e.result.status,
            data={"status": e.result.status, "recover": e.result.public_dict()},
        ) from e

    return _write("succeeded")


def _public_vars(variables: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in variables.items():
        if looks_secret_key(str(k)):
            out[k] = "***"
        else:
            out[k] = v
    return out


_DUMPED_SELECTOR_MARKERS = ("api_key", "apikey", "authorization", "cookie")
_DUMPED_VALUE_RE = re.compile(
    r"(?i)(authorization\s*[:=]|bearer\s+[A-Za-z0-9._\-+/=]{8,}|"
    r"\bsk-(?:proj-)?[A-Za-z0-9]{8,}\b|(?:set-)?cookie\s*[:=])"
)


def _selector_chain(step: dict[str, Any]) -> list[str]:
    out: list[str] = []
    primary = step.get("selector") or step.get("css")
    if isinstance(primary, str) and primary.strip():
        out.append(primary.strip())
    alts = step.get("selectors") or []
    if isinstance(alts, list):
        for s in alts:
            if isinstance(s, str) and s.strip() and s.strip() not in out:
                out.append(s.strip())
    return out


def _stall_payload(index: int, action: str, step: dict[str, Any], exc: BaseException) -> dict[str, Any]:
    sel = step.get("selector") or step.get("css")
    payload: dict[str, Any] = {
        "step_index": index,
        "action": action,
        "error_type": type(exc).__name__,
        "error": redact_text(str(exc)),
    }
    if sel:
        payload["selector"] = sel
    chain = _selector_chain(step)
    if chain:
        payload["selectors"] = chain
    if step.get("field_name"):
        payload["field_name"] = step.get("field_name")
    intended = step.get("text", step.get("value"))
    if intended is not None:
        # Username/password form values may be needed so recover can finish login.
        # Never pass API keys, Authorization headers, or cookie dumps.
        payload["intended_text"] = _intended_text_for_stall(str(intended), str(sel or ""), step)
    return payload


def _intended_text_for_stall(text: str, sel: str, step: dict[str, Any]) -> str:
    haystacks = [sel.lower()]
    for key in ("name", "as", "var"):
        v = step.get(key)
        if isinstance(v, str):
            haystacks.append(v.lower())
    if any(any(m in h for m in _DUMPED_SELECTOR_MARKERS) for h in haystacks):
        return "(redacted)"
    if _DUMPED_VALUE_RE.search(text):
        return "(redacted)"
    return redact_text(text)[:80]


def _run_step(
    page: Any,
    step: dict[str, Any],
    action: str,
    extracts: dict[str, Any],
    artifacts_dir: Path,
    variables: dict[str, Any],
    root: Path,
) -> None:
    if action == "goto":
        url = step.get("url")
        if not url:
            raise ValueError("goto requires url")
        page.goto(
            url,
            wait_until=step.get("wait_until", "domcontentloaded"),
            timeout=step.get("timeout", 60000),
        )
        return

    if action == "click":
        sels = _selector_chain(step)
        if not sels:
            raise ValueError("click requires selector/css")
        timeout = step.get("timeout", 30000)
        last = None
        for i, sel in enumerate(sels):
            t = timeout if i == 0 else min(3000, int(timeout) if timeout else 3000)
            try:
                page.click(sel, timeout=t)
                return
            except Exception as e:
                last = e
        if last:
            raise last
        raise ValueError("click requires selector/css")

    if action in ("type", "fill"):
        sels = _selector_chain(step)
        text = step.get("text", step.get("value", ""))
        if not sels:
            raise ValueError(f"{action} requires selector/css")
        timeout = step.get("timeout", 30000)
        last = None
        for i, sel in enumerate(sels):
            t = timeout if i == 0 else min(3000, int(timeout) if timeout else 3000)
            try:
                if action == "fill":
                    page.fill(sel, str(text), timeout=t)
                else:
                    page.click(sel, timeout=t)
                    page.keyboard.type(str(text), delay=step.get("delay", 20))
                return
            except Exception as e:
                last = e
        if last:
            raise last
        raise ValueError(f"{action} requires selector/css")

    if action == "wait":
        ms = int(step.get("ms", step.get("timeout", 1000)))
        page.wait_for_timeout(ms)
        return

    if action in ("extract_text", "extract"):
        sel = step.get("css") or step.get("selector")
        as_name = step.get("as") or "value"
        attr = step.get("attr")
        if not sel:
            raise ValueError("extract_text requires css/selector")
        loc = page.locator(sel).first
        timeout = step.get("timeout", 30000)
        wait_for = getattr(loc, "wait_for", None)
        if callable(wait_for):
            wait_for(state="visible", timeout=timeout)
        if attr:
            val = loc.get_attribute(attr)
        else:
            inner = loc.inner_text
            val = inner(timeout=timeout) if callable(inner) else inner
        extracts[as_name] = val
        return

    if action in ("assert", "assert_visible"):
        sel = step.get("selector") or step.get("css")
        if not sel:
            raise ValueError("assert requires selector/css")
        loc = page.locator(sel).first
        timeout = step.get("timeout", 10000)
        wait_for = getattr(loc, "wait_for", None)
        if callable(wait_for):
            wait_for(state="visible", timeout=timeout)
        contains = step.get("contains") or step.get("text")
        if contains:
            inner = loc.inner_text
            text = inner(timeout=timeout) if callable(inner) else str(inner)
            if str(contains) not in str(text):
                raise AssertionError(
                    f"assert text {contains!r} not in {str(text)[:80]!r}"
                )
        return

    if action == "screenshot":
        name = step.get("path") or step.get("name") or "screenshot.png"
        path = Path(name)
        if path.is_absolute():
            path = ensure_under_root(path, root)
        else:
            if str(path).startswith("artifacts/"):
                path = ensure_under_root(root / path, root)
            else:
                path = ensure_under_root(artifacts_dir / path.name, root)
        path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path), full_page=bool(step.get("full_page", False)))
        extracts.setdefault("_screenshots", []).append(str(path))
        return

    raise ValueError(f"Unknown action: {action}")
