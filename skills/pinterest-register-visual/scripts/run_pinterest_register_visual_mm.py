#!/usr/bin/env python3
"""Pinterest visual register — PRODUCT multimodal loop (0.2.0).

screenshot → OpenAI-compatible vision (chat/completions + image_url) → JSON
action → CloakBrowser execute → same-session nurture BEFORE ctx.close.

This is the product path. Bot / operators must call this runner.
Free-form computerUse is not the product path.

Never launches system Chrome. Never changes CloakBrowser fingerprint knobs.
API keys are never accepted as --api-key (shell history) and never printed.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import random
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

VERSION = "0.2.0"
SKILL_ID = "pinterest-register-visual"
SESSION_OK_NAME = ".cloak_session_ok"
SIGNUP_URL = "https://www.pinterest.com/signup/"
HOME_URL = "https://www.pinterest.com/"
DEFAULT_LLM_MODEL = "grok-4.6"
# Independent login-state selectors (same family as nurture 0.1.7+).
UNAUTH_SELS = (
    '[data-test-id="unauth-header"], '
    '[data-test-id="simple-login-button"], '
    '[data-test-id="simple-signup-button"]'
)
ACCT_SELS = (
    '[data-test-id="header-accounts-options-button"], '
    '[data-test-id="header-profile"]'
)
PIN_LINK = 'a[href*="/pin/"]'

ALLOWED_ACTIONS = frozenset(
    {
        "click",
        "type",
        "press",
        "wait",
        "scroll",
        "imap_fetch_code",
        "nurture",
        "done",
        "fail",
    }
)
# Recover-schema alias; executed as type.
ACTION_ALIASES = {"fill": "type"}
FORBIDDEN_ACTIONS = frozenset(
    {
        "shell",
        "exec",
        "eval",
        "evaluate",
        "python",
        "read_file",
        "write_file",
        "open",
        "download",
        "run",
        "bash",
        "cmd",
        "powershell",
        "import",
        "javascript",
        "js",
        "file",
        "system",
        "popen",
        "subprocess",
        "goto",
        "screenshot",
    }
)
ALLOWED_STATUSES = frozenset(
    {
        "registered_ok",
        "browsed_ok",
        "oops_blocked",
        "verify_soft_fail",
        "account_deactivated",
        "not_logged_in",
        "visual_stuck",
    }
)
ALLOWED_PRESS_KEYS = frozenset(
    {
        "enter",
        "tab",
        "escape",
        "esc",
        "space",
        "backspace",
        "arrowup",
        "arrowdown",
        "arrowleft",
        "arrowright",
        "home",
        "end",
    }
)
PRESS_KEY_MAP = {
    "esc": "Escape",
    "escape": "Escape",
    "enter": "Enter",
    "tab": "Tab",
    "space": "Space",
    "backspace": "Backspace",
    "arrowup": "ArrowUp",
    "arrowdown": "ArrowDown",
    "arrowleft": "ArrowLeft",
    "arrowright": "ArrowRight",
    "home": "Home",
    "end": "End",
}
FIELD_PLACEHOLDERS = {
    "email": "EMAIL",
    "password": "PASSWORD",
    "birthday": "BIRTHDAY",
    "birthdate": "BIRTHDAY",
    "name": "DISPLAY_NAME",
    "display_name": "DISPLAY_NAME",
    "code": "CODE",
}
REQUIRED_SECRET_KEYS = (
    "PINTEREST_EMAIL",
    "PINTEREST_PASSWORD",
    "OUTLOOK_EMAIL",
    "OUTLOOK_CLIENT_ID",
    "OUTLOOK_REFRESH_TOKEN",
)
MAX_TEXT_LEN = 4000
MAX_WAIT_MS = 30_000
MAX_SCROLL_DELTA = 800
MAX_SELECTOR_LEN = 500
MAX_REASON_LEN = 500
MAX_SCREENSHOT_BYTES = 180_000
JPEG_QUALITY = 50
_CLICKABLE_JS = """() => {
  const sels = 'a, button, input, textarea, select, [role="button"], [role="link"]';
  const els = Array.from(document.querySelectorAll(sels)).slice(0, 24);
  return {
    title: (document.title || '').slice(0, 120),
    url: location.origin + location.pathname,
    items: els.map(el => {
      const r = el.getBoundingClientRect();
      const text = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0, 80);
      let css = el.tagName.toLowerCase();
      if (el.id) css = '#' + el.id;
      return {tag: el.tagName.toLowerCase(), text, css, bbox: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)}};
    }).filter(it => it.bbox.w > 0 && it.bbox.h > 0)
  };
}"""
DEFAULT_MAX_STEPS = 40
DEFAULT_TIMEOUT_SEC = 900
DRY_RUN_MAX_STEPS = 20

_SELECTOR_OK = re.compile(r"^[^;{}]{1,500}$")
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def _looks_like_root(p: Path) -> bool:
    return (p / "scripts" / "outlook_imap_pinterest_code.py").is_file() and (
        (p / "python" / "cloakcli_worker").is_dir() or (p / "profiles").is_dir()
    )


def resolve_root() -> Path:
    env = (os.environ.get("CLOAKCLI_ROOT") or "").strip()
    if env:
        cand = Path(env).expanduser().resolve()
        if _looks_like_root(cand):
            return cand
    here = Path(__file__).resolve()
    cwd = Path.cwd().resolve()
    for start in (here.parent, cwd):
        for cand in (start, *start.parents):
            if _looks_like_root(cand):
                return cand
    if here.parent.name == "scripts" and here.parents[1].name == "pinterest-register-visual":
        return here.parents[3]
    return here.parents[1]


ROOT = resolve_root()
_PY = str(ROOT / "python")
if _PY not in sys.path:
    sys.path.insert(0, _PY)

try:
    from cloakcli_worker.llm_config import (
        COMPAT_API_KEY_ENV,
        DEFAULT_API_KEY_ENV,
        LlmConfig,
        chat_completions_url,
        load_llm_config,
        normalize_base_url,
        parse_llm_config,
        resolve_api_key,
    )
    from cloakcli_worker.recover.provider import OpenAICompatProvider, ProviderError
    from cloakcli_worker.redact import redact_text
except ImportError:  # pragma: no cover - operator path always has worker
    COMPAT_API_KEY_ENV = "OPENAI_API_KEY"
    DEFAULT_API_KEY_ENV = "CLOAKCLI_LLM_API_KEY"
    LlmConfig = None  # type: ignore[misc, assignment]
    chat_completions_url = None  # type: ignore[assignment]
    load_llm_config = None  # type: ignore[assignment]
    normalize_base_url = None  # type: ignore[assignment]
    parse_llm_config = None  # type: ignore[assignment]
    resolve_api_key = None  # type: ignore[assignment]
    OpenAICompatProvider = None  # type: ignore[misc, assignment]
    ProviderError = RuntimeError  # type: ignore[misc, assignment]

    def redact_text(text: str, extra: list[str] | None = None) -> str:  # type: ignore[misc]
        s = text or ""
        for v in extra or []:
            if v and len(v) >= 4:
                s = s.replace(v, "***")
        return s


SYSTEM_PROMPT = """You drive Pinterest signup on an EXISTING CloakBrowser page (product multimodal loop).
You receive ONE compressed viewport screenshot (never a full-page original) plus a short DOM hint.
Return JSON only, schema_version 1, ONE action per turn:

{"schema_version":1,"action":"click|type|press|wait|scroll|imap_fetch_code|nurture|done|fail"}

click: {"selector":"css"} OR {"x":int,"y":int,"screenshot_id":"<current screenshot_id>"}
  Coordinate clicks REQUIRE screenshot_id equal to this observation. Coords are CSS pixels.
type: {"selector":"css","text":"{{EMAIL}}|{{PASSWORD}}|{{BIRTHDAY}}|{{DISPLAY_NAME}}|{{CODE}}"}
  Or {"field":"email|password|birthday|name|code"} (runner substitutes secrets). Never invent credentials.
press: {"key":"Enter|Tab|Escape|ArrowDown|..."}
scroll: {"delta_y":int} small only (|delta|<=800) or {"selector":"css"}
wait: {"ms":int}  (runner also applies human pacing; do not spam)
imap_fetch_code: {}  fetch Outlook IMAP 6-digit; then type {{CODE}}
nurture: {}  same-session feed browse; runner runs this BEFORE close ONLY after independent login check
done: {"status":"registered_ok","path":"code_ui|settings_confirm|already_logged_in","reason":"..."}
  registered_ok is a HINT. The runner confirms account menu / pin feed / no unauth Log in+Sign up CTA
  before writing .cloak_session_ok or chaining nurture. Do not claim success on the signup form.
fail: {"status":"oops_blocked|verify_soft_fail|account_deactivated|not_logged_in|visual_stuck","reason":"..."}

Goal: sign up with email/password/birthday (age 25–35) / name, verify 6-digit code if shown,
or finish onboarding + settings Confirm Email. Avoid Google OAuth. Prefer the primary Continue,
not "Continue with Google".
If full-page Oops (no code UI) → fail oops_blocked (park, do not re-Continue).
If logged-in (account menu / pin feed, no unauth Log in+Sign up CTA) → nurture or done registered_ok.
The runner always chains nurture before ctx.close on confirmed registered_ok unless the operator skipped it.
Do not request another screenshot. Do not output markdown. JSON object only.
"""


def log(obj: dict[str, Any]) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v
    return out


def random_birthday(age_min: int = 25, age_max: int = 35) -> tuple[str, int]:
    age = random.randint(age_min, age_max)
    b = date(date.today().year - age, random.randint(1, 12), random.randint(1, 28))
    return b.isoformat(), age


def random_display_name() -> str:
    first = [
        "James", "Oliver", "Noah", "Liam", "Ethan", "Mason", "Logan", "Lucas",
        "Emma", "Olivia", "Ava", "Sophia", "Mia", "Harper", "Amelia", "Evelyn",
        "Marcus", "Elena", "Nathan", "Claire", "Owen", "Grace", "Caleb", "Nora",
    ]
    return random.choice(first)


def extract_json(text: str) -> Any | None:
    if not text or not isinstance(text, str):
        return None
    s = text.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", s)
    if fence:
        s = fence.group(1).strip()
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start = s.find(opener)
        end = s.rfind(closer)
        if start >= 0 and end > start:
            try:
                return json.loads(s[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None


class ActionError(ValueError):
    pass


def parse_mm_action(text: str) -> dict[str, Any]:
    """Parse one product-loop action. Raises ActionError. Never executes prose."""
    blob = extract_json(text)
    if blob is None:
        raise ActionError("model output is not valid JSON")
    if isinstance(blob, list):
        if not blob:
            raise ActionError("empty actions array")
        blob = blob[0]
    if not isinstance(blob, dict):
        raise ActionError("JSON must be an object")
    if "actions" in blob and isinstance(blob["actions"], list):
        if not blob["actions"]:
            raise ActionError("empty actions array")
        blob = blob["actions"][0]
        if not isinstance(blob, dict):
            raise ActionError("action must be an object")
    raw_type = blob.get("action") or blob.get("type") or blob.get("name")
    if not isinstance(raw_type, str) or not raw_type.strip():
        raise ActionError("missing action type")
    atype = raw_type.strip().lower()
    atype = ACTION_ALIASES.get(atype, atype)
    if atype in FORBIDDEN_ACTIONS:
        raise ActionError(f"forbidden action: {atype}")
    if atype not in ALLOWED_ACTIONS:
        raise ActionError(f"unknown action: {atype}")

    out: dict[str, Any] = {"action": atype, "schema_version": 1}

    sel = blob.get("selector")
    if sel is None:
        sel = blob.get("css")
    if sel is not None:
        if not isinstance(sel, str) or not sel.strip():
            raise ActionError("selector must be a non-empty string")
        sel = sel.strip()
        if len(sel) > MAX_SELECTOR_LEN or not _SELECTOR_OK.match(sel):
            raise ActionError("selector rejected")
        low = sel.lower()
        if "javascript:" in low or "data:" in low:
            raise ActionError("selector rejected")
        out["selector"] = sel

    def _opt_int(key: str) -> int | None:
        if key not in blob or blob[key] is None:
            return None
        try:
            return int(blob[key])
        except (TypeError, ValueError) as e:
            raise ActionError(f"{key} must be an int") from e

    x = _opt_int("x")
    y = _opt_int("y")
    if x is not None:
        out["x"] = x
    if y is not None:
        out["y"] = y
    sid = blob.get("screenshot_id") or blob.get("screenshotId") or blob.get("observation_id")
    if sid is not None:
        out["screenshot_id"] = str(sid).strip()[:80]

    if atype == "click":
        if out.get("selector"):
            pass
        elif x is None or y is None:
            raise ActionError("click requires selector or x/y")
        elif not out.get("screenshot_id"):
            raise ActionError("coordinate click requires screenshot_id matching current observation")

    text_val = blob.get("text")
    if text_val is None and atype == "type":
        text_val = blob.get("value")
    if text_val is not None:
        if not isinstance(text_val, str):
            text_val = str(text_val)
        if len(text_val) > MAX_TEXT_LEN:
            raise ActionError(f"text exceeds {MAX_TEXT_LEN} chars")
        out["text"] = text_val

    field = blob.get("field")
    if field is not None:
        field = str(field).strip().lower()
        if field not in FIELD_PLACEHOLDERS:
            raise ActionError("field must be email|password|birthday|name|code")
        out["field"] = field

    if atype == "type" and "text" not in out and "field" not in out:
        raise ActionError("type requires text or field")

    if atype == "press":
        key_raw = blob.get("key") or blob.get("name") or text_val or "Enter"
        key_norm = str(key_raw).strip().lower().replace(" ", "")
        if key_norm not in ALLOWED_PRESS_KEYS:
            raise ActionError(f"press key not allowed: {key_raw}")
        out["key"] = PRESS_KEY_MAP.get(key_norm, str(key_raw))

    if atype == "scroll":
        dx = int(blob.get("delta_x", blob.get("dx", 0)) or 0)
        dy = int(blob.get("delta_y", blob.get("dy", 0)) or 0)
        if abs(dx) > MAX_SCROLL_DELTA or abs(dy) > MAX_SCROLL_DELTA:
            raise ActionError("scroll delta out of range")
        if dx == 0 and dy == 0 and not out.get("selector"):
            raise ActionError("scroll requires delta or selector")
        out["delta_x"] = dx
        out["delta_y"] = dy

    if atype == "wait":
        ms = int(blob.get("ms", blob.get("timeout", 0)) or 0)
        if ms < 0:
            raise ActionError("wait ms must be >= 0")
        if ms <= 0:
            ms = 500
        out["ms"] = min(ms, MAX_WAIT_MS)

    if atype == "imap_fetch_code":
        to = blob.get("timeout")
        if to is not None:
            try:
                out["timeout"] = max(10, min(300, int(to)))
            except (TypeError, ValueError):
                out["timeout"] = 180

    status = blob.get("status")
    if status is not None:
        status = str(status).strip()
        if status not in ALLOWED_STATUSES:
            raise ActionError(f"unknown status: {status}")
        out["status"] = status

    reason = blob.get("reason") or blob.get("message") or ""
    if not isinstance(reason, str):
        reason = str(reason)
    out["reason"] = reason[:MAX_REASON_LEN]
    path = blob.get("path")
    if path is not None:
        out["path"] = str(path)[:80]
    return out


def public_action(action: dict[str, Any], *, extra_secrets: list[str] | None = None) -> dict[str, Any]:
    d: dict[str, Any] = {"schema_version": 1, "action": action.get("action")}
    for k in ("selector", "x", "y", "screenshot_id", "key", "delta_x", "delta_y", "ms", "field", "status", "path"):
        if k in action and action[k] is not None:
            d[k] = action[k]
    if "text" in action and action["text"] is not None:
        text = action["text"]
        if _PLACEHOLDER_RE.fullmatch((text or "").strip()):
            d["text"] = text.strip()
        else:
            d["text"] = "[REDACTED]"
            d["text_len"] = len(text)
    if action.get("reason"):
        d["reason"] = redact_text(str(action["reason"])[:MAX_REASON_LEN], extra=extra_secrets)
    return d


def substitute_secrets(action: dict[str, Any], secrets: dict[str, str]) -> dict[str, Any]:
    out = dict(action)
    field = out.get("field")
    if field:
        key = FIELD_PLACEHOLDERS[field]
        if key not in secrets or secrets[key] == "":
            raise ActionError(f"no value for field {field}")
        out["text"] = secrets[key]
        out.pop("field", None)
    text = out.get("text")
    if isinstance(text, str) and "{{" in text:

        def _repl(m: re.Match[str]) -> str:
            name = m.group(1).upper()
            if name not in secrets:
                raise ActionError(f"unknown placeholder {{{{{name}}}}}")
            return secrets[name]

        out["text"] = _PLACEHOLDER_RE.sub(_repl, text)
    return out


def map_fail_status(action: dict[str, Any], heuristic: str = "") -> str:
    if action.get("status") in ALLOWED_STATUSES:
        return str(action["status"])
    blob = f"{action.get('reason') or ''} {heuristic}".lower()
    if "oops" in blob:
        return "oops_blocked"
    if "deactivat" in blob:
        return "account_deactivated"
    if "not_logged" in blob or "login wall" in blob or "unauth" in blob:
        return "not_logged_in"
    if "verify" in blob or "imap" in blob or "code" in blob and "fail" in blob:
        return "verify_soft_fail"
    return "visual_stuck"


def map_done_status(action: dict[str, Any]) -> str:
    st = action.get("status")
    if st in ALLOWED_STATUSES:
        return str(st)
    return "registered_ok"


def sniff_image_mime(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _llm_file_dict(root: Path) -> dict[str, Any]:
    path = root / "config" / "llm.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def discover_llm(
    root: Path,
    *,
    base_url_cli: str = "",
    model_cli: str = "",
    secrets: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Public LLM discovery. Never includes key values.

    Screenshot chat/completions uses the resolved vision model:
    CLI --model, then CLOAKCLI_LLM_VISION_MODEL, then llm.json vision_model,
    then text model (CLOAKCLI_LLM_MODEL / llm.json model / default grok-4.6).
    Missing model does not by itself make the config incomplete.
    """
    cfg_obj: Any = None
    source = "defaults"
    file_data = _llm_file_dict(root)
    if load_llm_config is not None:
        cfg_obj = load_llm_config(root)
        if cfg_obj is not None:
            source = "config/llm.json"
    elif file_data:
        source = "config/llm.json"
    env_base = (os.environ.get("CLOAKCLI_LLM_BASE_URL") or "").strip()
    env_model = (os.environ.get("CLOAKCLI_LLM_MODEL") or "").strip()
    env_vision = (os.environ.get("CLOAKCLI_LLM_VISION_MODEL") or "").strip()
    file_vision = str(file_data.get("vision_model") or "").strip()
    base = ""
    text_model = ""
    api_key_env = DEFAULT_API_KEY_ENV
    enabled = False
    if cfg_obj is not None:
        base = str(getattr(cfg_obj, "base_url", "") or "")
        text_model = str(getattr(cfg_obj, "model", "") or "")
        api_key_env = str(getattr(cfg_obj, "api_key_env", "") or DEFAULT_API_KEY_ENV)
        enabled = bool(getattr(cfg_obj, "enabled", False))
    elif file_data:
        raw_base = str(file_data.get("base_url") or "")
        base = normalize_base_url(raw_base) if normalize_base_url else raw_base.rstrip("/")
        text_model = str(file_data.get("model") or "").strip()
        api_key_env = str(file_data.get("api_key_env") or DEFAULT_API_KEY_ENV).strip() or DEFAULT_API_KEY_ENV
        enabled = bool(file_data.get("enabled", False))
    if env_base:
        base = normalize_base_url(env_base) if normalize_base_url else env_base.rstrip("/")
        source = "env:CLOAKCLI_LLM_BASE_URL" if source == "defaults" else source + "+env"
    if env_model:
        text_model = env_model
        source = "env:CLOAKCLI_LLM_MODEL" if source == "defaults" else source + "+env"
    if base_url_cli:
        base = normalize_base_url(base_url_cli) if normalize_base_url else base_url_cli.rstrip("/")
        source = "cli"
    if not text_model:
        text_model = DEFAULT_LLM_MODEL
        if source == "defaults":
            source = "default_model"

    vision = ""
    vision_source = "text_model"
    if model_cli.strip():
        vision = model_cli.strip()
        vision_source = "cli"
        source = "cli" if source == "defaults" else source + "+cli"
    elif env_vision:
        vision = env_vision
        vision_source = "env:CLOAKCLI_LLM_VISION_MODEL"
        source = vision_source if source in ("defaults", "default_model") else source + "+vision_env"
    elif file_vision:
        vision = file_vision
        vision_source = "config/llm.json:vision_model"
        source = source + "+vision_model" if "vision" not in source else source
    else:
        vision = text_model
        vision_source = "text_model"

    key_present, key_source = _key_present(api_key_env, secrets or {})
    grok_hint = "api.x.ai" in (base or "").lower()
    return {
        "base_url": base,
        "model": vision,
        "text_model": text_model,
        "vision_model": vision,
        "vision_source": vision_source,
        "api_key_env": api_key_env,
        "key_present": key_present,
        "key_source": key_source if key_present else "",
        "enabled": enabled,
        "source": source,
        "default_model": DEFAULT_LLM_MODEL,
        "chat_completions": (
            chat_completions_url(base) if chat_completions_url and base else ""
        ),
        "compat": "openai_chat_completions_image_url",
        "grok_compat": grok_hint,
        "grok_docs": {
            "base_url": "https://api.x.ai/v1",
            "api_key_env": "CLOAKCLI_LLM_API_KEY",
            "fallbacks": [COMPAT_API_KEY_ENV, "XAI_API_KEY"],
            "default_model": DEFAULT_LLM_MODEL,
            "vision_model_hint": "CLOAKCLI_LLM_VISION_MODEL or llm.json vision_model (e.g. grok-2-vision-1212)",
        },
    }


def _key_present(api_key_env: str, secrets: dict[str, str]) -> tuple[bool, str]:
    name = api_key_env or DEFAULT_API_KEY_ENV
    if os.environ.get(name):
        return True, f"env:{name}"
    if name == DEFAULT_API_KEY_ENV and os.environ.get(COMPAT_API_KEY_ENV):
        return True, f"env:{COMPAT_API_KEY_ENV}"
    if name == DEFAULT_API_KEY_ENV and os.environ.get("XAI_API_KEY"):
        return True, "env:XAI_API_KEY"
    for k in (name, DEFAULT_API_KEY_ENV, COMPAT_API_KEY_ENV, "XAI_API_KEY"):
        v = (secrets.get(k) or "").strip()
        if v:
            return True, f"secrets:{k}"
    return False, ""


def resolve_visual_api_key(disc: dict[str, Any], secrets: dict[str, str]) -> str:
    name = disc.get("api_key_env") or DEFAULT_API_KEY_ENV
    if resolve_api_key is not None and parse_llm_config is not None:
        cfg = parse_llm_config(
            {
                "enabled": True,
                "base_url": disc.get("base_url") or "https://api.openai.com/v1",
                "model": disc.get("model") or "x",
                "api_key_env": name,
            }
        )
        v = resolve_api_key(cfg)
        if v:
            return v
    for env_name in (name, COMPAT_API_KEY_ENV, "XAI_API_KEY"):
        v = os.environ.get(env_name) or ""
        if v:
            return v
    for k in (name, DEFAULT_API_KEY_ENV, COMPAT_API_KEY_ENV, "XAI_API_KEY"):
        v = (secrets.get(k) or "").strip()
        if v:
            return v
    return ""


def make_llm_cfg(disc: dict[str, Any]) -> Any:
    if parse_llm_config is None:
        raise RuntimeError("cloakcli_worker.llm_config unavailable")
    return parse_llm_config(
        {
            "enabled": True,
            "base_url": disc.get("base_url") or "",
            "model": disc.get("model") or "",
            "api_key_env": disc.get("api_key_env") or DEFAULT_API_KEY_ENV,
        }
    )


# --- dry-run page -----------------------------------------------------------

TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


class DryRunPage:
    """Playwright-shaped stub for --dry-run (no CloakBrowser, no system Chrome)."""

    def __init__(self, *, persist_logged_in: bool = False) -> None:
        self.url = "about:blank"
        self.viewport_size = {"width": 1280, "height": 720}
        self.clicked: list[str] = []
        self.coord_clicks: list[tuple[int, int]] = []
        self.typed: list[str] = []
        self.filled: list[tuple[str, str]] = []
        self.pressed: list[str] = []
        self.scrolls: list[Any] = []
        self.gotos: list[str] = []
        self.screenshot_calls: list[str] = []
        self.fields: dict[str, str] = {}
        self.focused: str | None = None
        self.keyboard = self
        self.mouse = self
        self._stage = "signup"
        self.logged_in = False
        self._persist_logged_in = persist_logged_in
        self.selector_counts: dict[str, int] | None = None
        self._body = "Sign up  Log in  Email  Password  Birthday  Continue"
        if persist_logged_in:
            self.enter_logged_in(persist=True)

    def enter_logged_in(self, *, persist: bool = False) -> None:
        self._stage = "logged_in"
        self.logged_in = True
        if persist:
            self._persist_logged_in = True
        self.url = "https://www.pinterest.com/homefeed/"
        self._body = "Search Pinterest  header-accounts  Your home feed"

    def _advance_after_continue(self) -> None:
        if self.logged_in:
            return
        if self._stage == "signup":
            self._stage = "code"
            self.url = SIGNUP_URL
            self._body = "Enter the code we sent you  Continue"
            return
        if self._stage == "code":
            self.enter_logged_in()

    def click(self, sel: Any, timeout: int = 0, force: bool = False, y: int | None = None) -> None:
        if isinstance(sel, (int, float)):
            yy = int(y if y is not None else timeout)
            self.coord_clicks.append((int(sel), yy))
            return
        s = str(sel)
        self.clicked.append(s)
        self.focused = s
        low = s.lower()
        if "continue" in low and "google" not in low:
            self._advance_after_continue()

    def goto(self, url: str, wait_until: str = "domcontentloaded", timeout: int = 0) -> None:
        self.gotos.append(url)
        if self._persist_logged_in:
            self.enter_logged_in(persist=True)
            return
        self.url = url
        if "signup" in url:
            self._stage = "signup"
            self.logged_in = False
            self._body = "Create your account  Email  Password  Birthday  Continue  Continue with Google"

    def screenshot(self, path: str | None = None, full_page: bool = False, **kwargs: Any) -> bytes:
        if full_page:
            raise RuntimeError("full_page screenshots are forbidden")
        self.screenshot_calls.append(path or "")
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(TINY_PNG)
        return TINY_PNG

    def fill(self, sel: str, text: str, timeout: int = 0) -> None:
        self.filled.append((sel, text))
        self.fields[sel] = text
        self.focused = sel

    def type(self, text: str, delay: int = 0) -> None:
        self.typed.append(text)
        if self.focused:
            self.fields[self.focused] = self.fields.get(self.focused, "") + text

    def press(self, key: str) -> None:
        self.pressed.append(key)

    def wheel(self, dx: int, dy: int) -> None:
        self.scrolls.append((dx, dy))

    def wait_for_timeout(self, ms: int) -> None:
        return

    def wait_for_load_state(self, state: str = "domcontentloaded", timeout: int = 0) -> None:
        return

    def inner_text(self, sel: str = "body") -> str:
        return self._body

    def locator(self, sel: str) -> "_DryLoc":
        return _DryLoc(self, sel)

    def _count_one(self, sel: str) -> int:
        if self.selector_counts is not None and sel in self.selector_counts:
            return int(self.selector_counts[sel])
        s = sel.strip()
        low = s.lower()
        if self.logged_in:
            if "header-accounts" in low or "header-profile" in low:
                return 1
            if "/pin/" in low:
                return 5
            if "unauth-header" in low or "simple-login" in low or "simple-signup" in low:
                return 0
            if s == "#code":
                return 0
            return 1
        if self._stage == "code":
            if s == "#code":
                return 1
            if "header-accounts" in low or "header-profile" in low or "/pin/" in low:
                return 0
            if "unauth-header" in low or "simple-login" in low or "simple-signup" in low:
                return 0
            return 1
        if "header-accounts" in low or "header-profile" in low or "/pin/" in low:
            return 0
        if "unauth-header" in low or "simple-login" in low or "simple-signup" in low:
            return 1
        if s == "#code":
            return 0
        return 1

    def evaluate(self, script: str, arg: Any = None) -> Any:
        return {
            "title": "Pinterest",
            "url": self.url,
            "items": [
                {"tag": "input", "text": "", "css": "#email", "bbox": {"x": 40, "y": 120, "w": 280, "h": 36}},
                {"tag": "input", "text": "", "css": "#password", "bbox": {"x": 40, "y": 170, "w": 280, "h": 36}},
                {"tag": "button", "text": "Continue", "css": "button:has-text('Continue')", "bbox": {"x": 40, "y": 280, "w": 200, "h": 40}},
            ],
        }


class _DryLoc:
    def __init__(self, page: DryRunPage, sel: str) -> None:
        self.page = page
        self.sel = sel

    @property
    def first(self) -> "_DryLoc":
        return self

    def count(self) -> int:
        if self.page.selector_counts is not None and self.sel in self.page.selector_counts:
            return int(self.page.selector_counts[self.sel])
        total = 0
        for part in (p.strip() for p in self.sel.split(",")):
            if part:
                total += self.page._count_one(part)
        return total

    def is_visible(self, timeout: int = 0) -> bool:
        return self.count() > 0

    def scroll_into_view_if_needed(self, timeout: int = 0) -> None:
        self.page.scrolls.append(("into_view", self.sel))

    def click(self, timeout: int = 0, force: bool = False) -> None:
        self.page.click(self.sel, timeout=timeout)


class MockVision:
    """Scripted vision for --dry-run smoke (no network)."""

    def __init__(self, script: list[dict[str, Any]] | None = None) -> None:
        self.script = list(script or default_dry_run_script())
        self.calls = 0
        self.images: list[int] = []

    def complete(
        self,
        cfg: Any,
        messages: list[dict[str, Any]],
        *,
        image_b64: str | None = None,
        timeout_sec: float = 60,
        image_mime: str | None = None,
    ) -> tuple[str, int]:
        self.calls += 1
        self.images.append(len(image_b64 or ""))
        if not self.script:
            return json.dumps({"schema_version": 1, "action": "done", "status": "registered_ok", "reason": "dry-run exhausted"}), 8
        step = self.script.pop(0)
        return json.dumps(step, ensure_ascii=False), 12


def default_dry_run_script() -> list[dict[str, Any]]:
    return [
        {"schema_version": 1, "action": "wait", "ms": 200},
        {"schema_version": 1, "action": "click", "selector": "#email"},
        {"schema_version": 1, "action": "type", "selector": "#email", "text": "{{EMAIL}}"},
        {"schema_version": 1, "action": "click", "selector": "#password"},
        {"schema_version": 1, "action": "type", "field": "password"},
        {"schema_version": 1, "action": "type", "selector": "#birthdate", "text": "{{BIRTHDAY}}"},
        {"schema_version": 1, "action": "click", "selector": "button:has-text('Continue')"},
        {"schema_version": 1, "action": "wait", "ms": 400},
        {"schema_version": 1, "action": "imap_fetch_code"},
        {"schema_version": 1, "action": "type", "selector": "#code", "text": "{{CODE}}"},
        {"schema_version": 1, "action": "press", "key": "Tab"},
        {"schema_version": 1, "action": "scroll", "delta_y": 200},
        {"schema_version": 1, "action": "click", "selector": "button:has-text('Continue')"},
        {"schema_version": 1, "action": "nurture"},
        {"schema_version": 1, "action": "done", "status": "registered_ok", "path": "code_ui", "reason": "dry-run"},
    ]


# --- pacing / observe / execute --------------------------------------------

def human_pause(page: Any, lo_ms: int, hi_ms: int, label: str = "", *, dry_run: bool = False) -> int:
    if dry_run:
        return 0
    if hi_ms < lo_ms:
        lo_ms, hi_ms = hi_ms, lo_ms
    lo_ms = max(0, int(lo_ms))
    hi_ms = max(lo_ms, int(hi_ms))
    ms = random.randint(lo_ms, hi_ms)
    log({"pause_ms": ms, "label": label})
    try:
        page.wait_for_timeout(ms)
    except Exception:
        time.sleep(ms / 1000.0)
    return ms


def looks_like_continue(action: dict[str, Any]) -> bool:
    blob = " ".join(
        str(action.get(k) or "")
        for k in ("selector", "reason", "text")
    ).lower()
    return "continue" in blob and "google" not in blob


def _page_body(page: Any, n: int = 3000) -> str:
    try:
        return (page.inner_text("body") or "")[:n]
    except Exception:
        return ""


def _page_url(page: Any) -> str:
    try:
        return str(page.url or "")
    except Exception:
        return ""


def _locator_count(page: Any, sel: str) -> int:
    try:
        return int(page.locator(sel).count() or 0)
    except Exception:
        return 0


def _code_visible(page: Any) -> bool:
    try:
        loc = page.locator("#code")
        return loc.count() > 0 and loc.first.is_visible()
    except Exception:
        return False


def page_login_gate(page: Any) -> dict[str, Any]:
    """Independent login-state check. Model claims are hints only.

    Logged-in requires account menu, pin feed, or feed-like page without
    unauth Log in+Sign up CTA. Signup / code UI / Oops never count.
    """
    body = _page_body(page)
    url = _page_url(page)
    unauth = _locator_count(page, UNAUTH_SELS)
    acct = _locator_count(page, ACCT_SELS)
    pins = _locator_count(page, PIN_LINK)
    code_vis = _code_visible(page)
    has_cta = "Log in" in body[:1500] and "Sign up" in body[:1500]
    feedish = (
        "Search Pinterest" in body
        or "/homefeed" in url
        or "/today" in url
        or "header-accounts" in body
    )
    info: dict[str, Any] = {
        "unauth": unauth,
        "acct": acct,
        "pins": pins,
        "has_cta": has_cta,
        "feedish": feedish,
        "url": url[:200],
    }
    if re.search(r"account has been deactivated", body, re.I):
        return {"ok": False, "gate": "account_deactivated", **info}
    if "Oops" in body and not code_vis and "Enter the code" not in body:
        return {"ok": False, "gate": "oops", **info}
    if "Enter the code" in body or code_vis:
        return {"ok": False, "gate": "code_ui", "reason": "code_ui", **info}
    if "signup" in url and acct == 0 and pins < 3:
        return {"ok": False, "gate": "not_logged_in", "reason": "signup_url", **info}
    if unauth > 0 and acct == 0:
        return {"ok": False, "gate": "not_logged_in", "reason": "unauth_header", **info}
    if has_cta and acct == 0 and pins < 3:
        return {"ok": False, "gate": "not_logged_in", "reason": "unauth_cta", **info}
    if acct > 0:
        return {"ok": True, "gate": "logged_in", "reason": "account_menu", **info}
    if pins >= 3 and not has_cta:
        return {"ok": True, "gate": "logged_in", "reason": "pin_feed", **info}
    if not has_cta and feedish:
        return {"ok": True, "gate": "logged_in", "reason": "feed_no_unauth_cta", **info}
    return {"ok": False, "gate": "not_logged_in", "reason": "no_login_signals", **info}


def confirm_logged_in(page: Any) -> bool:
    return bool(page_login_gate(page).get("ok"))


def status_when_login_unconfirmed(gate: dict[str, Any]) -> str:
    g = str(gate.get("gate") or "not_logged_in")
    if g == "account_deactivated":
        return "account_deactivated"
    if g == "oops":
        return "oops_blocked"
    if g == "not_logged_in":
        return "not_logged_in"
    return "visual_stuck"


def page_heuristic(page: Any) -> str:
    body = _page_body(page)
    url = _page_url(page)
    code_vis = _code_visible(page)
    if re.search(r"account has been deactivated", body, re.I):
        return "account_deactivated"
    if "Oops" in body and not code_vis and "Enter the code" not in body:
        return "oops"
    if "Enter the code" in body or code_vis:
        return "code_ui"
    if ("What's your name" in body) or ("Nice to meet you" in body):
        return "onboarding"
    gate = page_login_gate(page)
    if gate.get("ok"):
        return "logged_in"
    cta = "Log in" in body[:1500] and "Sign up" in body[:1500]
    if cta or "signup" in url:
        return "signup_form"
    return "unknown"


def capture_viewport(page: Any, dest: Path) -> dict[str, Any]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, Any] = {"path": str(dest), "full_page": False}
    try:
        kwargs["type"] = "jpeg"
        kwargs["quality"] = JPEG_QUALITY
        page.screenshot(**kwargs)
    except TypeError:
        dest = dest.with_suffix(".png")
        page.screenshot(path=str(dest), full_page=False)
    except Exception:
        dest = dest.with_suffix(".png")
        page.screenshot(path=str(dest), full_page=False)
    data = b""
    try:
        data = dest.read_bytes()
    except OSError:
        data = b""
    if len(data) > MAX_SCREENSHOT_BYTES:
        # Keep file; still attach (models tolerate modest oversize).
        pass
    mime = sniff_image_mime(data) if data else "image/jpeg"
    b64 = base64.b64encode(data).decode("ascii") if data else ""
    vp = {"width": 1280, "height": 720}
    try:
        vs = page.viewport_size
        if isinstance(vs, dict):
            vp = {"width": int(vs.get("width") or 1280), "height": int(vs.get("height") or 720)}
    except Exception:
        pass
    clickables: list[dict[str, Any]] = []
    title = ""
    try:
        ev = page.evaluate(_CLICKABLE_JS)
        if isinstance(ev, dict):
            title = str(ev.get("title") or "")[:120]
            items = ev.get("items") or []
            if isinstance(items, list):
                clickables = [it for it in items[:16] if isinstance(it, dict)]
    except Exception:
        try:
            title = str(page.evaluate("() => document.title") or "")[:120]
        except Exception:
            title = ""
    url = ""
    try:
        url = str(page.url or "")
    except Exception:
        url = ""
    return {
        "path": str(dest),
        "bytes": len(data),
        "mime": mime,
        "b64": b64,
        "viewport": vp,
        "url": url,
        "title": title,
        "clickables": clickables,
        "full_page": False,
    }


def vision_complete(
    cfg: Any,
    messages: list[dict[str, Any]],
    *,
    image_b64: str,
    image_mime: str,
    timeout_sec: float,
    api_key: str,
) -> tuple[str, int]:
    """OpenAI-compatible chat/completions + image_url. Never logs Authorization."""
    if OpenAICompatProvider is not None:
        # Inject key via env name already resolved; provider reads env.
        provider = OpenAICompatProvider()
        return provider.complete(
            cfg,
            messages,
            image_b64=image_b64,
            timeout_sec=timeout_sec,
            image_mime=image_mime,
        )
    url = chat_completions_url(cfg.base_url) if chat_completions_url else ""
    if not url:
        raise RuntimeError("llm config missing base_url")
    body_messages = list(messages)
    if image_b64 and body_messages:
        last = dict(body_messages[-1])
        content = last.get("content")
        if isinstance(content, str):
            last["content"] = [
                {"type": "text", "text": content},
                {"type": "image_url", "image_url": {"url": f"data:{image_mime};base64,{image_b64}"}},
            ]
            body_messages[-1] = last
    payload = {"model": cfg.model, "messages": body_messages, "temperature": 0, "max_tokens": 1024}
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "cloakcli-pinterest-visual-mm/0.2.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=max(5, float(timeout_sec))) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        err = ""
        try:
            err = e.read(400).decode("utf-8", "replace")
        except Exception:
            err = ""
        raise RuntimeError(redact_text(f"HTTP {e.code} {err}", extra=[api_key])) from None
    parsed = json.loads(raw.decode("utf-8"))
    choices = parsed.get("choices") or []
    text = ""
    if choices and isinstance(choices[0], dict):
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "".join(
                str(p.get("text") or "") if isinstance(p, dict) else str(p)
                for p in content
            )
    usage = parsed.get("usage") or {}
    tokens = int(usage.get("total_tokens") or 0)
    if not text.strip():
        raise RuntimeError("model returned empty content")
    return text, tokens


def imap_max_uid(secrets_path: str, root: Path) -> int:
    import subprocess

    return int(
        subprocess.check_output(
            [
                sys.executable,
                str(root / "scripts" / "outlook_imap_pinterest_code.py"),
                "--secrets",
                secrets_path,
                "--print-max-uid",
            ],
            text=True,
            cwd=str(root),
        ).strip()
    )


def imap_wait_code(
    secrets_path: str,
    after_uid: int,
    timeout: int,
    root: Path,
    extra_secrets: list[str] | None = None,
) -> dict[str, Any] | None:
    import subprocess

    proc = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "outlook_imap_pinterest_code.py"),
            "--secrets",
            secrets_path,
            "--after-uid",
            str(after_uid),
            "--timeout",
            str(timeout),
        ],
        cwd=str(root),
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        err = redact_text(proc.stderr or "", extra=extra_secrets)
        if err:
            sys.stderr.write(err if err.endswith("\n") else err + "\n")
            log({"status": "imap_stderr", "stderr": err[:500]})
        return None
    lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    if not lines:
        return None
    return json.loads(lines[-1])


def load_nurture_mod(root: Path) -> Any:
    import importlib.util

    nurture_path = root / "scripts" / "run_pinterest_nurture_browse.py"
    spec = importlib.util.spec_from_file_location("run_pinterest_nurture_browse", nurture_path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def session_ok_path(ud: Path) -> Path:
    return ud / SESSION_OK_NAME


def touch_session_ok(ud: Path) -> None:
    try:
        ud.mkdir(parents=True, exist_ok=True)
        session_ok_path(ud).touch()
    except Exception:
        pass


def flush_session(ctx: Any, page: Any, ud: Path, *, dry_run: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"storage_state": False, "home_nav": False, "wait_ms": 0}
    wait_ms = 0 if dry_run else random.randint(2000, 4000)
    try:
        page.wait_for_timeout(wait_ms)
        out["wait_ms"] = wait_ms
    except Exception:
        out["wait_ms"] = wait_ms
    if not dry_run:
        try:
            if hasattr(ctx, "storage_state"):
                state_file = ud / "playwright_storage_state.json"
                ctx.storage_state(path=str(state_file))
                out["storage_state"] = True
        except Exception as e:
            out["storage_err"] = type(e).__name__
        try:
            page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(random.randint(1500, 3000))
            out["home_nav"] = True
        except Exception as e:
            out["home_err"] = type(e).__name__
    log({"status": "session_flush", **{k: v for k, v in out.items() if k != "storage_state_path"}})
    return out


def chain_nurture(
    page: Any,
    ctx: Any,
    ud: Path,
    profile: str,
    root: Path,
    *,
    pins: int,
    min_sec: int,
    max_sec: int,
    dry_run: bool,
    extra_secrets: list[str] | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "nurture_status": "skipped",
        "nurture_elapsed_s": 0,
        "nurture_liked": False,
    }
    if dry_run:
        fields["nurture_status"] = "browsed_ok"
        fields["nurture_elapsed_s"] = 0
        fields["nurture_liked"] = True
        fields["nurture_dry_run"] = True
        log({"status": "nurture_done", **fields})
        return fields
    try:
        flush = flush_session(ctx, page, ud, dry_run=False)
        fields["session_flush"] = {
            k: flush.get(k) for k in ("storage_state", "home_nav", "wait_ms") if k in flush
        }
        mod = load_nurture_mod(root)
        if mod is None:
            fields["nurture_status"] = "import_failed"
            return fields
        probe = None
        if hasattr(mod, "session_keepalive_probe"):
            probe = mod.session_keepalive_probe(page)
        elif hasattr(mod, "detect_login_state"):
            gate = mod.detect_login_state(page)
            probe = {"ok": gate == "ok", "gate": gate, "url": getattr(page, "url", ""), "probe": "detect_login_state"}
        if probe is not None:
            log({"status": "session_keepalive_probe", **probe})
            fields["session_keepalive_probe"] = probe
            if not probe.get("ok"):
                fields["nurture_status"] = "session_lost_before_nurture"
                fields["session_lost_gate"] = probe.get("gate") or "not_logged_in"
                return fields
        run_art = root / "artifacts/pinterest/nurture-browse" / f"{profile}-run"
        log(
            {
                "status": "nurture_start",
                "profile": profile,
                "mode": "keep_open",
                "pins": pins,
                "version": VERSION,
            }
        )
        result = mod.run_nurture_session(
            page,
            profile=profile,
            run_art=run_art,
            pins=pins,
            min_sec=min_sec,
            max_sec=max_sec,
            navigate=True,
        )
        fields["nurture_status"] = result.get("status") or "like_failed"
        fields["nurture_elapsed_s"] = result.get("elapsed_sec") or 0
        fields["nurture_liked"] = bool(result.get("liked"))
        if result.get("pins_opened") is not None:
            fields["nurture_pins_opened"] = result.get("pins_opened")
        log(
            {
                "status": "nurture_done",
                "nurture_status": fields["nurture_status"],
                "nurture_elapsed_s": fields["nurture_elapsed_s"],
                "nurture_liked": fields["nurture_liked"],
            }
        )
    except Exception as e:
        msg = redact_text(f"{type(e).__name__}: {e}", extra=extra_secrets)[:200]
        fields["nurture_status"] = f"error:{type(e).__name__}"
        fields["nurture_error"] = msg
        log({"status": "nurture_error", "err": type(e).__name__, "message": msg})
    return fields


def execute_browser_action(page: Any, action: dict[str, Any], *, screenshot_id: str) -> dict[str, Any]:
    atype = action["action"]
    if atype == "click":
        sel = action.get("selector")
        if sel:
            page.click(sel, timeout=15000)
            return {"ok": True, "detail": "click selector"}
        if action.get("screenshot_id") != screenshot_id:
            return {"ok": False, "detail": "stale screenshot_id"}
        page.mouse.click(int(action["x"]), int(action["y"]))
        return {"ok": True, "detail": "click xy"}
    if atype == "type":
        text = action.get("text") or ""
        sel = action.get("selector")
        delay = random.randint(35, 70)
        if sel:
            try:
                page.click(sel, timeout=8000)
            except Exception:
                pass
            # date inputs prefer fill; React text fields prefer key events
            if "birth" in sel.lower():
                try:
                    page.fill(sel, text, timeout=8000)
                    return {"ok": True, "detail": "type fill-date"}
                except Exception:
                    pass
            try:
                page.keyboard.type(text, delay=delay)
            except Exception:
                page.fill(sel, text, timeout=8000)
        else:
            page.keyboard.type(text, delay=delay)
        return {"ok": True, "detail": "type"}
    if atype == "press":
        page.keyboard.press(action.get("key") or "Enter")
        return {"ok": True, "detail": "press"}
    if atype == "scroll":
        sel = action.get("selector")
        if sel:
            page.locator(sel).first.scroll_into_view_if_needed(timeout=8000)
        else:
            page.mouse.wheel(int(action.get("delta_x") or 0), int(action.get("delta_y") or 0))
        return {"ok": True, "detail": "scroll"}
    if atype == "wait":
        page.wait_for_timeout(int(action.get("ms") or 500))
        return {"ok": True, "detail": "wait"}
    return {"ok": False, "detail": f"unhandled {atype}"}


def maybe_named_shot(page: Any, art: Path, heuristic: str, seen: set[str], dry_run: bool) -> None:
    mapping = {
        "signup_form": "01-signup.png",
        "oops": "02-after-continue.png",
        "code_ui": "03-code-or-settings.png",
        "onboarding": "03-code-or-settings.png",
        "logged_in": "04-logged-in.png",
    }
    name = mapping.get(heuristic)
    if not name or name in seen:
        return
    seen.add(name)
    try:
        page.screenshot(path=str(art / name), full_page=False)
    except Exception:
        if not dry_run:
            pass


# --- main loop --------------------------------------------------------------

def run_loop(
    *,
    page: Any,
    ctx: Any,
    ud: Path,
    args: argparse.Namespace,
    secrets: dict[str, str],
    secrets_path: str,
    disc: dict[str, Any],
    extra_secrets: list[str],
    dry_run: bool,
    vision: Any,
    api_key: str,
    root: Path,
) -> dict[str, Any]:
    profile = args.profile
    art = root / "artifacts/pinterest/visual" / profile
    art.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    max_steps = int(args.max_steps)
    deadline = t0 + float(args.timeout_sec)
    screenshot_id = ""
    consecutive_rejects = 0
    last_feedback = ""
    path_hint = ""
    registered = False
    nurtured = False
    nurture_fields: dict[str, Any] = {
        "nurture_status": "skipped",
        "nurture_elapsed_s": 0,
    }
    steps_done = 0
    tokens = 0
    named_shots: set[str] = set()
    after_uid = 0
    if not dry_run:
        try:
            after_uid = imap_max_uid(secrets_path, root)
        except Exception as e:
            log({"status": "imap_max_uid_skip", "err": type(e).__name__})

    if dry_run:
        page.goto(SIGNUP_URL)
    else:
        page.goto(SIGNUP_URL, wait_until="domcontentloaded", timeout=90000)
        human_pause(page, 2500, 4500, "signup_land", dry_run=False)

    def finish(status: str, **extra: Any) -> dict[str, Any]:
        report = {
            "skill_id": SKILL_ID,
            "version": VERSION,
            "status": status,
            "path": extra.pop("path", path_hint) or path_hint or "",
            "nurture_status": nurture_fields.get("nurture_status", "skipped"),
            "nurture_elapsed_s": nurture_fields.get("nurture_elapsed_s", 0),
            "elapsed_s": round(time.time() - t0, 2),
            "profile": profile,
            "steps": steps_done,
            "tokens": tokens,
            "dry_run": dry_run,
            **{k: v for k, v in extra.items() if k != "path"},
        }
        if args.digest:
            report["digest"] = args.digest
        for k in ("nurture_liked", "session_keepalive_probe", "nurture_skip_warning"):
            if k in nurture_fields:
                report[k] = nurture_fields[k]
        return report

    def do_nurture() -> bool:
        """Chain nurture only after independent login confirmation.

        Never writes .cloak_session_ok unless page_login_gate says logged-in.
        Returns True if the login gate passed (register success still stands
        even if the subsequent nurture fails).
        """
        nonlocal nurtured, nurture_fields
        if nurtured:
            return confirm_logged_in(page)
        gate = page_login_gate(page)
        if not gate.get("ok"):
            log({"status": "session_ok_refused", **{k: gate.get(k) for k in ("gate", "reason", "url", "has_cta", "acct", "pins", "unauth")}})
            nurture_fields["nurture_status"] = "skipped_not_logged_in"
            nurture_fields["session_keepalive_probe"] = gate
            return False
        touch_session_ok(ud)
        if args.skip_nurture:
            log(
                {
                    "status": "nurture_skip_warning",
                    "warning": (
                        "skip_nurture set — closing without same-session nurture. "
                        "Independent nurture minutes later is NOT the product path."
                    ),
                }
            )
            nurture_fields["nurture_status"] = "skipped"
            nurture_fields["nurture_skip_warning"] = True
            nurtured = True
            return True
        nurture_fields.update(
            chain_nurture(
                page,
                ctx,
                ud,
                profile,
                root,
                pins=args.nurture_pins,
                min_sec=args.nurture_min_sec,
                max_sec=args.nurture_max_sec,
                dry_run=dry_run,
                extra_secrets=extra_secrets,
            )
        )
        nurtured = True
        try:
            page.screenshot(path=str(art / "05-nurture.png"), full_page=False)
        except Exception:
            pass
        return True

    while steps_done < max_steps and time.time() < deadline:
        heuristic = page_heuristic(page)
        maybe_named_shot(page, art, heuristic, named_shots, dry_run)
        sid = f"obs-{steps_done:03d}"
        shot = capture_viewport(page, art / f"{sid}.jpg")
        screenshot_id = sid
        obs = {
            "goal": "Pinterest email signup + verify + same-session nurture",
            "screenshot_id": sid,
            "url": shot["url"],
            "title": shot["title"],
            "viewport": shot["viewport"],
            "heuristic": heuristic,
            "clickables": shot["clickables"],
            "placeholders": ["{{EMAIL}}", "{{PASSWORD}}", "{{BIRTHDAY}}", "{{DISPLAY_NAME}}", "{{CODE}}"],
            "code_ready": bool(secrets.get("CODE")),
            "email_domain": (secrets.get("EMAIL") or "").split("@")[-1],
            "previous_feedback": last_feedback or None,
            "step": steps_done,
            "seconds_left": int(max(0, deadline - time.time())),
        }
        log(
            {
                "status": "observe",
                "screenshot_id": sid,
                "bytes": shot["bytes"],
                "mime": shot["mime"],
                "heuristic": heuristic,
                "full_page": False,
            }
        )
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(obs, ensure_ascii=False)},
        ]
        try:
            raw, used = vision.complete(
                make_llm_cfg(disc) if not dry_run else disc,
                messages,
                image_b64=shot["b64"],
                timeout_sec=min(60.0, max(8.0, deadline - time.time())),
                image_mime=shot["mime"],
            )
        except Exception as e:
            last_feedback = redact_text(f"model error: {type(e).__name__}: {e}", extra=extra_secrets)
            log({"status": "model_error", "error": last_feedback})
            consecutive_rejects += 1
            if consecutive_rejects >= 5:
                return finish("visual_stuck", reason=last_feedback)
            continue
        tokens += int(used or 0)
        try:
            action = parse_mm_action(raw)
        except ActionError as e:
            last_feedback = f"rejected: {e}"
            log({"status": "action_reject", "error": str(e)})
            consecutive_rejects += 1
            if consecutive_rejects >= 6:
                return finish("visual_stuck", reason=last_feedback)
            continue

        pub = public_action(action, extra_secrets=extra_secrets)
        log({"status": "model_action", "action": pub, "heuristic": heuristic})
        atype = action["action"]

        if atype == "fail":
            st = map_fail_status(action, heuristic)
            if st == "oops_blocked":
                log({"status": "oops_blocked", "note": "park_no_recontinue"})
            return finish(st, reason=action.get("reason") or st, path=action.get("path") or heuristic)

        if atype == "done":
            st = map_done_status(action)
            path_hint = action.get("path") or path_hint
            if st == "registered_ok":
                gate = page_login_gate(page)
                if not gate.get("ok"):
                    fail_st = status_when_login_unconfirmed(gate)
                    log(
                        {
                            "status": "login_gate_rejected_done",
                            "model_status": "registered_ok",
                            "gate": gate.get("gate"),
                            "reason": gate.get("reason"),
                        }
                    )
                    return finish(
                        fail_st,
                        reason="model registered_ok but page not logged-in",
                        path=path_hint,
                    )
                registered = True
                do_nurture()
                return finish("registered_ok", reason=action.get("reason") or st, path=path_hint)
            return finish(st, reason=action.get("reason") or st, path=path_hint)

        if atype == "nurture":
            gate = page_login_gate(page)
            if not gate.get("ok"):
                last_feedback = "nurture rejected: page not logged-in (login heuristic)"
                log(
                    {
                        "status": "nurture_rejected_not_logged_in",
                        "gate": gate.get("gate"),
                        "reason": gate.get("reason"),
                    }
                )
                consecutive_rejects += 1
                if consecutive_rejects >= 6:
                    return finish(
                        status_when_login_unconfirmed(gate),
                        reason=last_feedback,
                        path=path_hint,
                    )
                steps_done += 1
                continue
            registered = True
            path_hint = path_hint or heuristic or "logged_in"
            do_nurture()
            last_feedback = f"ok nurture {nurture_fields.get('nurture_status')}"
            consecutive_rejects = 0
            steps_done += 1
            continue

        if atype == "imap_fetch_code":
            if dry_run:
                secrets["CODE"] = "123456"
                last_feedback = "ok imap_fetch_code dry-run code_len=6"
                log({"status": "code_received", "code_len": 6, "dry_run": True})
            else:
                timeout = int(action.get("timeout") or 180)
                hit = imap_wait_code(
                    secrets_path, after_uid, timeout, root, extra_secrets=extra_secrets
                )
                if hit is None:
                    last_feedback = "imap timeout"
                    log({"status": "imap_timeout"})
                    consecutive_rejects += 1
                    if consecutive_rejects >= 3:
                        return finish("verify_soft_fail", reason="imap_timeout")
                    continue
                code = str(hit.get("code") or "")
                secrets["CODE"] = code
                try:
                    after_uid = max(after_uid, int(hit.get("uid") or after_uid))
                except (TypeError, ValueError):
                    pass
                last_feedback = f"ok imap_fetch_code code_len={len(code)}"
                log(
                    {
                        "status": "code_received",
                        "subject": hit.get("subject"),
                        "code_len": len(code),
                    }
                )
            consecutive_rejects = 0
            steps_done += 1
            human_pause(page, 800, 1800, "after_imap", dry_run=dry_run)
            continue

        # Browser actions
        try:
            bound = substitute_secrets(action, secrets)
        except ActionError as e:
            last_feedback = f"rejected: {e}"
            consecutive_rejects += 1
            log({"status": "action_reject", "error": str(e)})
            continue

        if atype == "click" and looks_like_continue(bound):
            human_pause(page, 2000, 5000, "before_continue", dry_run=dry_run)
        elif atype in ("click", "type", "press"):
            human_pause(page, 800, 2500, f"before_{atype}", dry_run=dry_run)

        try:
            result = execute_browser_action(page, bound, screenshot_id=screenshot_id)
        except Exception as e:
            result = {"ok": False, "detail": type(e).__name__}

        if not result.get("ok"):
            last_feedback = f"rejected execute: {result.get('detail')}"
            log({"status": "action_reject", "error": last_feedback, "action": pub})
            consecutive_rejects += 1
            if consecutive_rejects >= 6:
                return finish("visual_stuck", reason=last_feedback)
            continue

        consecutive_rejects = 0
        steps_done += 1
        last_feedback = f"ok {atype}"
        if atype == "click" and looks_like_continue(bound):
            human_pause(page, 3000, 8000, "after_continue_settle", dry_run=dry_run)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
        elif atype == "type":
            human_pause(page, 800, 2500, "after_type", dry_run=dry_run)

        h2 = page_heuristic(page)
        if h2 == "oops":
            # Do not auto-park on a single heuristic; tell the model next turn.
            last_feedback = "page heuristic=oops (park if still Oops; do not re-Continue spam)"
        if h2 == "logged_in" and confirm_logged_in(page):
            registered = True
            path_hint = path_hint or "logged_in"

    if registered:
        if not confirm_logged_in(page):
            gate = page_login_gate(page)
            return finish(
                status_when_login_unconfirmed(gate),
                reason="login heuristic failed at finish",
                path=path_hint,
            )
        do_nurture()
        return finish("registered_ok", path=path_hint or "timeout_after_register")
    if time.time() >= deadline:
        return finish("visual_stuck", reason="wall-clock budget exhausted")
    return finish("visual_stuck", reason="max steps exceeded")


def launch_cloakbrowser(ud: Path, headed: bool, proxy: str | None) -> Any:
    """Persistent CloakBrowser only. No system Chrome. No fingerprint knobs."""
    from cloakbrowser import launch_persistent_context

    kwargs: dict[str, Any] = {"user_data_dir": str(ud), "headless": not headed}
    if proxy:
        kwargs["proxy"] = proxy
    # Do not pass user_agent / fingerprint / stealth extras — profile identity stays as-is.
    return launch_persistent_context(**kwargs)


def load_stdin_payload() -> dict[str, Any]:
    if sys.stdin.isatty():
        return {}
    raw = sys.stdin.read()
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="")
    ap.add_argument("--secrets", default="")
    ap.add_argument(
        "--fresh-profile",
        action="store_true",
        help="Wipe user_data_dir BEFORE launch only. Refused if .cloak_session_ok exists.",
    )
    ap.add_argument("--headless", action="store_true")
    ap.add_argument(
        "--skip-nurture",
        action="store_true",
        help="Do not chain nurture before ctx.close (NOT recommended).",
    )
    ap.add_argument("--nurture-pins", type=int, default=3)
    ap.add_argument("--nurture-min-sec", type=int, default=120)
    ap.add_argument("--nurture-max-sec", type=int, default=180)
    ap.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    ap.add_argument("--timeout-sec", type=float, default=DEFAULT_TIMEOUT_SEC)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Mock vision + stub page (no CloakBrowser, no live LLM). Smoke only.",
    )
    ap.add_argument("--base-url", default="", help="OpenAI-compatible base (http(s); overrides llm.json)")
    ap.add_argument(
        "--model",
        default="",
        help="Vision model id for screenshot chat/completions (overrides CLOAKCLI_LLM_VISION_MODEL / llm.json vision_model)",
    )
    ap.add_argument("--digest", default="", help="Skill package digest (python_runner identity)")
    return ap


def main(argv: list[str] | None = None, stdin_payload: dict[str, Any] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if any(a in ("--api-key", "--apikey") or a.startswith("--api-key=") for a in argv):
        log(
            {
                "skill_id": SKILL_ID,
                "version": VERSION,
                "status": "visual_stuck",
                "error": "refuse --api-key argv (shell history); use CLOAKCLI_LLM_API_KEY / config/llm.json",
            }
        )
        return 2

    args = build_parser().parse_args(argv)
    payload = stdin_payload if stdin_payload is not None else load_stdin_payload()
    vars_ = payload.get("vars") if isinstance(payload.get("vars"), dict) else {}
    if not args.profile:
        args.profile = str(payload.get("profile") or vars_.get("PROFILE") or "geo02")
    if payload.get("digest") and not args.digest:
        args.digest = str(payload.get("digest"))
    if payload.get("version"):
        # identity is ours; payload version is informational
        pass

    dry_run = bool(args.dry_run)
    if dry_run:
        args.max_steps = min(int(args.max_steps), DRY_RUN_MAX_STEPS)
        if args.timeout_sec == DEFAULT_TIMEOUT_SEC:
            args.timeout_sec = 30.0

    secrets_arg = args.secrets or str(vars_.get("SECRETS_ENV") or "")
    if not secrets_arg:
        secrets_arg = str(ROOT / "data/secrets/pinterest-outlook-01.env")
    secrets_path = Path(secrets_arg)
    if not secrets_path.is_absolute():
        secrets_path = (ROOT / secrets_path).resolve()

    profile_path = ROOT / "profiles" / args.profile / "profile.json"
    env = load_env(secrets_path) if secrets_path.is_file() else {}
    extra_secrets = [v for v in env.values() if v and len(v) >= 4]

    email = env.get("PINTEREST_EMAIL") or ""
    password = env.get("PINTEREST_PASSWORD") or ""
    bday, age = random_birthday()
    display_name = random_display_name()
    subst = {
        "EMAIL": email,
        "PASSWORD": password,
        "BIRTHDAY": bday,
        "DISPLAY_NAME": display_name,
        "CODE": "",
        "PINTEREST_EMAIL": email,
        "PINTEREST_PASSWORD": password,
    }

    disc = discover_llm(
        ROOT,
        base_url_cli=args.base_url,
        model_cli=args.model,
        secrets=env,
    )
    public_llm = {
        k: disc[k]
        for k in (
            "base_url",
            "model",
            "text_model",
            "vision_model",
            "vision_source",
            "api_key_env",
            "key_present",
            "key_source",
            "source",
            "compat",
            "grok_compat",
            "grok_docs",
            "chat_completions",
            "default_model",
        )
        if k in disc
    }
    log(
        {
            "skill_id": SKILL_ID,
            "version": VERSION,
            "profile": args.profile,
            "email_domain": email.split("@")[-1] if email else "",
            "birthday": bday,
            "age": age,
            "headed": not args.headless,
            "dry_run": dry_run,
            "skip_nurture": bool(args.skip_nurture),
            "llm": public_llm,
            "product_path": "scripts/run_pinterest_register_visual_mm.py",
            "not_product_path": "computerUse",
        }
    )

    issues: list[str] = []
    if not dry_run:
        if not profile_path.is_file():
            issues.append(f"missing_profile:{profile_path}")
        if not secrets_path.is_file():
            issues.append(f"missing_secrets:{secrets_path}")
        else:
            missing = [k for k in REQUIRED_SECRET_KEYS if k not in env]
            if missing:
                issues.append("secrets_missing_keys:" + ",".join(missing))
        if not disc.get("base_url"):
            issues.append(
                "llm_incomplete: need config/llm.json or --base-url "
                "(Grok: https://api.x.ai/v1). Model defaults to grok-4.6; "
                "override vision with CLOAKCLI_LLM_VISION_MODEL or llm.json vision_model"
            )
        if not disc.get("key_present"):
            issues.append(
                f"llm_key_missing: set {disc.get('api_key_env') or DEFAULT_API_KEY_ENV} "
                f"(or OPENAI_API_KEY / XAI_API_KEY); never --api-key"
            )
        display = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY") or ""
        if not args.headless and not display:
            issues.append("no_display")
    if issues and not dry_run:
        report = {
            "skill_id": SKILL_ID,
            "version": VERSION,
            "status": "visual_stuck",
            "path": "preflight",
            "nurture_status": "skipped",
            "nurture_elapsed_s": 0,
            "elapsed_s": 0,
            "profile": args.profile,
            "issues": issues,
        }
        if args.digest:
            report["digest"] = args.digest
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return 2

    proxy = None
    meta: dict[str, Any] = {}
    if profile_path.is_file():
        try:
            meta = json.loads(profile_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
        proxy = meta.get("proxy") or None

    ud = ROOT / "data/profiles" / f"{args.profile}-pinterest-run"
    if args.fresh_profile and not dry_run:
        if ud.exists() and session_ok_path(ud).exists():
            report = {
                "skill_id": SKILL_ID,
                "version": VERSION,
                "status": "visual_stuck",
                "path": "fresh_profile_refused",
                "nurture_status": "skipped",
                "nurture_elapsed_s": 0,
                "elapsed_s": 0,
                "profile": args.profile,
                "reason": f"{SESSION_OK_NAME} present — never --fresh-profile after success",
            }
            print(json.dumps(report, ensure_ascii=False), flush=True)
            return 6
        if ud.exists():
            shutil.rmtree(ud)
    ud.mkdir(parents=True, exist_ok=True)

    ctx: Any = None
    page: Any
    vision: Any
    api_key = ""
    injected_key = False
    prev_key: str | None = None
    if dry_run:
        page = DryRunPage()
        vision = MockVision()
        if not subst["EMAIL"]:
            subst["EMAIL"] = "dry-run@example.com"
            subst["PINTEREST_EMAIL"] = subst["EMAIL"]
        if not subst["PASSWORD"]:
            subst["PASSWORD"] = "dry-run-password"
            subst["PINTEREST_PASSWORD"] = subst["PASSWORD"]
    else:
        api_key = resolve_visual_api_key(disc, env)
        # Temporarily expose key under api_key_env so OpenAICompatProvider can read it
        # (fleet python_runner strips CLOAKCLI_LLM_API_KEY; secrets-file fallback).
        env_name = disc.get("api_key_env") or DEFAULT_API_KEY_ENV
        prev_key = os.environ.get(env_name)
        injected_key = False
        if api_key and not os.environ.get(env_name):
            os.environ[env_name] = api_key
            injected_key = True
        headed = not args.headless
        try:
            ctx = launch_cloakbrowser(ud, headed=headed, proxy=proxy)
        except Exception as e:
            report = {
                "skill_id": SKILL_ID,
                "version": VERSION,
                "status": "visual_stuck",
                "path": "launch_failed",
                "nurture_status": "skipped",
                "nurture_elapsed_s": 0,
                "elapsed_s": 0,
                "profile": args.profile,
                "reason": redact_text(f"{type(e).__name__}: {e}", extra=extra_secrets + [api_key]),
            }
            print(json.dumps(report, ensure_ascii=False), flush=True)
            return 3
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        vision = OpenAICompatProvider() if OpenAICompatProvider is not None else None
        if vision is None:
            vision = _LiveVision(api_key)

    try:
        report = run_loop(
            page=page,
            ctx=ctx,
            ud=ud,
            args=args,
            secrets=subst,
            secrets_path=str(secrets_path),
            disc=disc,
            extra_secrets=extra_secrets + ([api_key] if api_key else []),
            dry_run=dry_run,
            vision=vision,
            api_key=api_key,
            root=ROOT,
        )
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass
        if not dry_run and injected_key:
            env_name = disc.get("api_key_env") or DEFAULT_API_KEY_ENV
            if prev_key is None:
                os.environ.pop(env_name, None)
            else:
                os.environ[env_name] = prev_key

    print(json.dumps(report, ensure_ascii=False), flush=True)
    st = report.get("status")
    if st in ("registered_ok", "browsed_ok"):
        return 0
    if st == "oops_blocked":
        return 2
    if st == "verify_soft_fail":
        return 5
    if st == "account_deactivated":
        return 7
    if st == "not_logged_in":
        return 8
    return 4


class _LiveVision:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def complete(self, cfg: Any, messages: list[dict[str, Any]], **kwargs: Any) -> tuple[str, int]:
        return vision_complete(cfg, messages, api_key=self.api_key, **kwargs)


if __name__ == "__main__":
    raise SystemExit(main())
