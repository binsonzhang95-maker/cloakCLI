"""Execute skill.json steps."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .browser import apply_cookie_file, get_page, launch_context
from .paths import ensure_under_root, get_root, set_root

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


class UndefinedVarError(ValueError):
    pass


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


def run_skill(
    *,
    skill_path: str,
    user_data_dir: str,
    headed: bool = False,
    proxy: str | None = None,
    vars: dict[str, Any] | None = None,
    root: str | None = None,
    cookie_file: str | None = None,
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

    ctx = launch_context(user_data_dir=str(user_data_safe), headed=headed, proxy=proxy)
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
    extracts: dict[str, Any] = {}

    skill_name = skill.get("name") or Path(skill_path_safe).parent.name
    if "/" in skill_name or "\\" in skill_name or ".." in skill_name:
        raise ValueError(f"unsafe skill name for artifacts: {skill_name}")

    artifacts_dir = ensure_under_root(
        project_root / "data" / "artifacts" / skill_name, project_root
    )
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    try:
        for i, raw_step in enumerate(skill.get("steps") or []):
            step = _subst(raw_step, {**variables, **extracts})
            action = (step.get("action") or "").strip().lower()
            if not action:
                raise ValueError(f"Step {i}: missing action")
            _run_step(page, step, action, extracts, artifacts_dir, variables, project_root)
    finally:
        try:
            ctx.close()
        except Exception:
            pass

    result = {
        "skill": skill_name,
        "extracts": extracts,
        "vars": variables,
        "artifacts_dir": str(artifacts_dir),
    }
    if cookie_meta:
        result["cookies"] = cookie_meta  # counts/domains only
    (artifacts_dir / "last_result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return result


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
        sel = step.get("selector") or step.get("css")
        if not sel:
            raise ValueError("click requires selector/css")
        page.click(sel, timeout=step.get("timeout", 30000))
        return

    if action in ("type", "fill"):
        sel = step.get("selector") or step.get("css")
        text = step.get("text", step.get("value", ""))
        if not sel:
            raise ValueError(f"{action} requires selector/css")
        if action == "fill":
            page.fill(sel, str(text), timeout=step.get("timeout", 30000))
        else:
            page.click(sel, timeout=step.get("timeout", 30000))
            page.keyboard.type(str(text), delay=step.get("delay", 20))
        return

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
        if attr:
            val = loc.get_attribute(attr)
        else:
            val = loc.inner_text()
        extracts[as_name] = val
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
