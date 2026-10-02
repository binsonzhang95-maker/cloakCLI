#!/usr/bin/env python3
"""Hubstudio Pinterest register — selector Playwright over CDP (0.1.3).

Attach to an already-started Hubstudio env via chromium.connect_over_cdp.
Never launches CloakBrowser or system Chrome. Never a vision / MM loop.

Mirrors Cloak scripts/run_pinterest_register_outlook_verify.py 0.1.4:
fill #email #password #birthdate (YYYY-MM-DD, age 25–35) with nurture
human_click_locator / human_type_text; Continue (not Google); settle;
Oops → oops_blocked; Path A #code + IMAP; Path B signup_ok_no_code_challenge
→ Settings Confirm Email (best-effort). Soft verify Send-new-code once.
Default same-session nurture before Hub browser stop.

Success bar = logged-in with a positive DOM signal (onboarding / account
menu / pin feed / Search Pinterest or header-accounts). URL-only
/homefeed/ or /today/ with an empty body is not registered_ok. Email
Confirmed is preferred same-run, not required for registered_ok.

Terminal statuses come from this package manifest.json.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

SKILL_ID = "hubstudio-pinterest-register"
VERSION = "0.1.3"
SIGNUP_URL = "https://www.pinterest.com/signup/"
HOME_URL = "https://www.pinterest.com/"
SETTINGS_ACCOUNT_URL = "https://www.pinterest.com/settings/account-settings/"
CONFIRM_EMAIL_SEL = 'button:has-text("Confirm Email"), button:has-text("Confirm email")'
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
DEFAULT_STATE_PARENT = Path("/home/box/.local/state/hubstudio-registration")
PROTECTED_CONTAINER_CODES = frozenset({1863073221, 1863107506, 1863108423})
ACCOUNTS_RAW_SEP = "----"
_FAIL_FALLBACK_ID = "adapter_error"
_DEFAULT_SUCCESS_EXIT = 0
_DEFAULT_FAIL_EXIT = 4
_HISTORICAL_FAIL_EXITS = {
    "oops_blocked": 2,
    "verify_soft_fail": 5,
    "imap_timeout": 5,
    "account_deactivated": 7,
    "not_logged_in": 8,
    "env_protected": 9,
    "env_missing": 10,
    "adapter_error": 4,
}

ERROR_RE = re.compile(
    r"(incorrect|invalid|try again|expired|too many|rate.?limit|oops|"
    r"something went wrong)",
    re.I,
)
SOFT_VERIFY_RE = re.compile(
    r"(incorrect|invalid|try again|expired|something went wrong|sorry)",
    re.I,
)

_HERE = Path(__file__).resolve()
if _HERE.parent.name == "scripts":
    PKG = _HERE.parent.parent
else:
    PKG = _HERE.parent
if (PKG.parent.name == "skills") and (PKG.parent.parent / "scripts").is_dir():
    ROOT = PKG.parent.parent
else:
    ROOT = Path(os.environ.get("HUB_CLOAKCLI_ROOT") or os.environ.get("CLOAKCLI_ROOT") or PKG)

if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))
_py = ROOT / "python"
if _py.is_dir() and str(_py) not in sys.path:
    sys.path.insert(0, str(_py))

from pinterest_nurture_behavior import (  # noqa: E402
    hang_before_close,
    human_click_locator,
    human_type_text,
    play_ambient_drift,
    reset_session_mouse,
    sample_pause_ms,
    sample_quiet_window_ms,
    session_mouse,
)

try:
    from cloakcli_worker.redact import redact_text
except ImportError:  # pragma: no cover

    def redact_text(text: str, extra: list[str] | None = None) -> str:  # type: ignore[misc]
        s = text or ""
        for v in extra or []:
            if v and len(v) >= 4:
                s = s.replace(v, "***")
        return s

from hubstudio_api import (  # noqa: E402
    browser_start,
    browser_stop,
    find_env_by_name,
    is_protected_container,
    proxy_update,
    wait_cdp,
)


def skill_manifest_path() -> Path | None:
    cands = [PKG / "manifest.json", _HERE.parent / "manifest.json", ROOT / "skills" / SKILL_ID / "manifest.json"]
    seen: set[Path] = set()
    for p in cands:
        try:
            rp = p.resolve()
        except OSError:
            continue
        if rp in seen or not rp.is_file():
            continue
        seen.add(rp)
        return rp
    return None


def load_skill_status_catalog(manifest: Path | None = None) -> dict[str, Any]:
    path = manifest if manifest is not None else skill_manifest_path()
    fail_safe = {
        "version": VERSION,
        "path": str(path) if path else "",
        "allowed": frozenset({_FAIL_FALLBACK_ID}),
        "success": frozenset(),
        "retryable": frozenset({_FAIL_FALLBACK_ID}),
        "fail_fallback": _FAIL_FALLBACK_ID,
        "exits": {_FAIL_FALLBACK_ID: _DEFAULT_FAIL_EXIT},
        "labels": {_FAIL_FALLBACK_ID: "Adapter / CDP / unexpected UI error"},
    }
    if path is None or not path.is_file():
        return fail_safe
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fail_safe
    raw = data.get("statuses")
    if not isinstance(raw, list) or not raw:
        return fail_safe
    allowed: set[str] = set()
    success: set[str] = set()
    retryable: set[str] = set()
    exits: dict[str, int] = {}
    labels: dict[str, str] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("id") or "").strip()
        if not sid or sid in allowed:
            continue
        allowed.add(sid)
        if bool(item.get("success")):
            success.add(sid)
        if bool(item.get("retryable")):
            retryable.add(sid)
        labels[sid] = str(item.get("label") or sid)
        if "exit" in item:
            try:
                exits[sid] = int(item["exit"])
            except (TypeError, ValueError):
                exits[sid] = (
                    _DEFAULT_SUCCESS_EXIT if sid in success else _HISTORICAL_FAIL_EXITS.get(sid, _DEFAULT_FAIL_EXIT)
                )
        elif sid in success:
            exits[sid] = _DEFAULT_SUCCESS_EXIT
        else:
            exits[sid] = _HISTORICAL_FAIL_EXITS.get(sid, _DEFAULT_FAIL_EXIT)
    if not allowed:
        return fail_safe
    fail_fallback = _FAIL_FALLBACK_ID if _FAIL_FALLBACK_ID in allowed else next(
        (s for s in sorted(allowed) if s not in success), next(iter(sorted(allowed)))
    )
    ver = str(data.get("version") or VERSION).strip() or VERSION
    return {
        "version": ver,
        "path": str(path),
        "allowed": frozenset(allowed),
        "success": frozenset(success),
        "retryable": frozenset(retryable),
        "fail_fallback": fail_fallback,
        "exits": exits,
        "labels": labels,
    }


STATUS_CATALOG = load_skill_status_catalog()
VERSION = str(STATUS_CATALOG.get("version") or VERSION)
ALLOWED_STATUSES: frozenset[str] = STATUS_CATALOG["allowed"]
SUCCESS_STATUSES: frozenset[str] = STATUS_CATALOG["success"]
FAIL_FALLBACK_STATUS: str = str(STATUS_CATALOG["fail_fallback"])
STATUS_EXITS: dict[str, int] = dict(STATUS_CATALOG["exits"])


def emit(payload: dict[str, Any], extra_secrets: list[str] | None = None) -> None:
    print(json.dumps(redact_result(payload, extra_secrets), ensure_ascii=False), flush=True)


def redact_result(value: Any, extra: list[str] | None = None) -> Any:
    if isinstance(value, str):
        return redact_text(value, extra=extra)
    if isinstance(value, list):
        return [redact_result(v, extra) for v in value]
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            lk = str(k).lower()
            if any(
                s in lk
                for s in (
                    "password",
                    "passwd",
                    "secret",
                    "token",
                    "cookie",
                    "proxy",
                    "authorization",
                    "refresh",
                )
            ):
                out[k] = "***"
            else:
                out[k] = redact_result(v, extra)
        return out
    return value


def secret_bag(secrets: dict[str, str] | None) -> list[str]:
    if not secrets:
        return []
    return [v for v in secrets.values() if isinstance(v, str) and len(v) >= 4]


def proxy_secret_bag(proxy: dict[str, Any] | None) -> list[str]:
    if not proxy:
        return []
    out: list[str] = []
    for k in ("host", "account", "password"):
        v = proxy.get(k)
        if isinstance(v, str) and v:
            out.append(v)
    return out


def redact_proxy_msg(msg: Any, proxy: dict[str, Any] | None) -> str:
    s = "" if msg is None else str(msg)
    extra = proxy_secret_bag(proxy)
    for v in extra:
        if v:
            s = s.replace(v, "***")
    return redact_text(s, extra=extra)


def hub_api_success_code(code: Any) -> bool:
    """Hub API success is only literal int 0 or str '0'.

    False, 0.0, None, and missing fail. Membership tests treat False/0.0 as 0.
    """
    return code is 0 or code == "0"


def load_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def parse_accounts_raw_line(line: str) -> dict[str, str]:
    """outlook----pwd----uuid----token----evenemail----evenpwd (no logging of values)."""
    parts = [p.strip() for p in line.strip().split(ACCOUNTS_RAW_SEP)]
    if len(parts) < 4:
        raise ValueError("accounts_raw_line_fields")
    while len(parts) < 6:
        parts.append("")
    outlook, pwd, uuid, token, evenemail, evenpwd = parts[:6]
    pinterest_email = evenemail or outlook
    pinterest_password = evenpwd or pwd
    return {
        "PINTEREST_EMAIL": pinterest_email,
        "PINTEREST_PASSWORD": pinterest_password,
        "OUTLOOK_EMAIL": outlook,
        "OUTLOOK_PASSWORD": pwd,
        "OUTLOOK_CLIENT_ID": uuid,
        "OUTLOOK_REFRESH_TOKEN": token,
    }


def parse_proxy_line(line: str) -> dict[str, Any]:
    host, port, user, pw = line.strip().split(":", 3)
    return {
        "host": host,
        "port": int(port),
        "account": user,
        "password": pw,
        "asDynamicType": 0,
    }


def row_num(row_id: str) -> int:
    return int(str(row_id).split("-")[-1])


def batch_date_from_state(state_dir: Path | None) -> str:
    if state_dir is not None:
        name = state_dir.name
        if re.fullmatch(r"\d{8}", name):
            return name
    env = os.environ.get("HUB_BATCH_DATE") or os.environ.get("HUB_BATCH")
    if env and re.fullmatch(r"\d{8}", env):
        return env
    return date.today().strftime("%Y%m%d")


def default_env_name(row_id: str, state_dir: Path | None) -> str:
    n = row_num(row_id)
    return f"hub-reg-{batch_date_from_state(state_dir)}-{n:02d}"


def load_plan_row(state_dir: Path, row_id: str) -> dict[str, Any] | None:
    plan_path = state_dir / "plan.json"
    if not plan_path.is_file():
        return None
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    for rec in plan.get("records") or []:
        if rec.get("row_id") == row_id:
            return rec
    return None


def load_accounts_raw_secrets(state_dir: Path, row_id: str) -> dict[str, str]:
    raw = state_dir / "secrets" / "accounts-raw.txt"
    if not raw.is_file():
        raise FileNotFoundError("accounts_raw_missing")
    lines = [ln for ln in raw.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.strip().startswith("#")]
    idx = row_num(row_id) - 1
    if idx < 0 or idx >= len(lines):
        raise IndexError("accounts_raw_row_oob")
    return parse_accounts_raw_line(lines[idx])


def load_secrets(
    *,
    secrets_path: Path | None,
    state_dir: Path | None,
    row_id: str | None,
) -> dict[str, str]:
    if secrets_path is not None and secrets_path.is_file():
        env = load_env_file(secrets_path)
        # Cloak-style aliases
        if "PINTEREST_EMAIL" not in env and "EMAIL" in env:
            env["PINTEREST_EMAIL"] = env["EMAIL"]
        if "PINTEREST_PASSWORD" not in env and "PASSWORD" in env:
            env["PINTEREST_PASSWORD"] = env["PASSWORD"]
        return env
    if state_dir is not None and row_id:
        return load_accounts_raw_secrets(state_dir, row_id)
    raise FileNotFoundError("secrets_not_found")


def load_proxy_for_row(state_dir: Path, n: int) -> dict[str, Any] | None:
    secrets = state_dir / "secrets"
    if not secrets.is_dir():
        return None
    candidates = sorted(secrets.glob("proxies*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    # Prefer proxies-fresh-* when present
    fresh = [p for p in candidates if "fresh" in p.name]
    ordered = fresh + [p for p in candidates if p not in fresh]
    lines: list[str] = []
    for p in ordered:
        lines = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.strip().startswith("#")]
        if lines:
            break
    if not lines:
        return None
    return parse_proxy_line(lines[(n - 1) % len(lines)])


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


def human_pause(page, lo_ms: int, hi_ms: int, label: str = "", *, ambient: bool | None = None) -> int:
    if hi_ms < lo_ms:
        lo_ms, hi_ms = hi_ms, lo_ms
    lo_ms = max(0, int(lo_ms))
    hi_ms = max(lo_ms, int(hi_ms))
    ms = sample_pause_ms(lo_ms, hi_ms)
    emit({"pause_ms": ms, "label": label})
    use_ambient = bool(ambient) if ambient is not None else hi_ms >= 2000
    if use_ambient and ms >= 500 and random.random() < 0.6:
        budget = min(ms // 3, 900)
        try:
            play_ambient_drift(page, session_mouse(), budget_ms=budget)
        except Exception:
            pass
        remain = ms - budget
        if remain > 0:
            page.wait_for_timeout(remain)
        return ms
    page.wait_for_timeout(ms)
    return ms


def quiet_window(page) -> int:
    ms = sample_quiet_window_ms()
    emit({"pause_ms": ms, "label": "quiet_window"})
    page.wait_for_timeout(ms)
    return ms


def _hclick(page, loc) -> dict:
    """Trail click; one soft retry with a new trail. Never force/teleport."""
    result = human_click_locator(page, loc, session_mouse())
    if result.get("hover_ms"):
        emit({"pause_ms": result["hover_ms"], "label": "hover_before_click"})
    if result.get("ok"):
        return result
    result = human_click_locator(page, loc, session_mouse())
    if not result.get("ok"):
        emit({"human_click": result})
    return result


def fill_code_react(page, code: str) -> str:
    loc = page.locator("#code").first
    loc.wait_for(state="visible", timeout=15000)
    typed = human_type_text(page, loc, code, mouse=session_mouse())
    try:
        val = loc.input_value()
    except Exception:
        val = ""
    if val != code and typed.get("focus_ok"):
        typed = human_type_text(page, loc, code, mouse=session_mouse())
        try:
            val = loc.input_value()
        except Exception:
            val = ""
    return val


def find_verify_continue(page):
    candidates = [
        '[data-test-id="verification-code-form"] button:has-text("Continue")',
        'form:has(#code) button:has-text("Continue")',
        '[role="dialog"] button:has-text("Continue")',
        'div:has(#code) button:has-text("Continue")',
        'button:has-text("Continue")',
    ]
    for sel in candidates:
        loc = page.locator(sel).first
        try:
            if loc.count() == 0:
                continue
        except Exception:
            pass
        try:
            if loc.is_visible(timeout=500):
                return loc, sel
        except Exception:
            continue
    return page.locator('button:has-text("Continue")').first, 'button:has-text("Continue")'


def wait_continue_enabled(cont, timeout_ms: int = 15000) -> bool:
    deadline_ms = timeout_ms
    step = 250
    waited = 0
    while waited < deadline_ms:
        try:
            if cont.is_visible():
                disabled = cont.is_disabled()
                aria = (cont.get_attribute("aria-disabled") or "").lower()
                if not disabled and aria not in ("true", "1"):
                    return True
        except Exception:
            pass
        cont.page.wait_for_timeout(step)
        waited += step
    return False


def snip_error(body: str) -> str | None:
    if not body:
        return None
    m = ERROR_RE.search(body)
    if not m:
        return None
    start = max(0, m.start() - 40)
    end = min(len(body), m.end() + 160)
    return body[start:end][:200]


def has_email_confirmed_toast(body: str) -> bool:
    if not body:
        return False
    return bool(re.search(r"email\s+confirmed", body, re.I))


def soft_verify_error(body: str, still_code: bool) -> bool:
    if not still_code or not body:
        return False
    if re.search(r"Oops!\s*Sorry", body) and "Enter the code" not in body:
        return False
    return bool(SOFT_VERIFY_RE.search(body))


def body_text(page, n: int = 3000) -> str:
    try:
        return (page.inner_text("body") or "")[:n]
    except Exception:
        return ""


def page_url(page) -> str:
    try:
        return page.url or ""
    except Exception:
        return ""


def _locator_count(page, sel: str) -> int:
    try:
        return int(page.locator(sel).count())
    except Exception:
        return 0


def _code_visible(page) -> bool:
    try:
        loc = page.locator("#code").first
        return loc.count() > 0 and loc.is_visible()
    except Exception:
        return False


def is_onboarding(body: str) -> bool:
    return ("What's your name" in body) or ("Nice to meet you" in body)


def page_login_gate(page) -> dict[str, Any]:
    """Independent login-state check. Success bar for registered_ok.

    Logged-in requires a positive DOM signal: account menu, pin feed,
    onboarding, or feed chrome in the body (Search Pinterest /
    header-accounts) without unauth Log in+Sign up CTA. A blank page,
    or a /homefeed/|/today/ URL with empty body and no locators, is not
    success. URL alone is never enough. Signup / code UI / Oops never count.
    """
    body = body_text(page, 4000)
    url = page_url(page)
    unauth = _locator_count(page, UNAUTH_SELS)
    acct = _locator_count(page, ACCT_SELS)
    pins = _locator_count(page, PIN_LINK)
    code_vis = _code_visible(page)
    has_cta = "Log in" in body[:1500] and "Sign up" in body[:1500]
    onboard = is_onboarding(body)
    feedish = ("Search Pinterest" in body) or ("header-accounts" in body)
    info: dict[str, Any] = {
        "unauth": unauth,
        "acct": acct,
        "pins": pins,
        "has_cta": has_cta,
        "feedish": feedish,
        "onboarding": onboard,
        "url": url[:200],
    }
    if re.search(r"account has been deactivated", body, re.I):
        return {"ok": False, "gate": "account_deactivated", **info}
    if "Oops" in body and not code_vis and "Enter the code" not in body:
        return {"ok": False, "gate": "oops", **info}
    if "Enter the code" in body or code_vis:
        return {"ok": False, "gate": "code_ui", "reason": "code_ui", **info}
    if onboard:
        return {"ok": True, "gate": "logged_in", "reason": "onboarding", **info}
    if "signup" in url and acct == 0 and pins < 3 and not onboard:
        return {"ok": False, "gate": "not_logged_in", "reason": "signup_url", **info}
    if unauth > 0 and acct == 0 and not onboard:
        return {"ok": False, "gate": "not_logged_in", "reason": "unauth_header", **info}
    if has_cta and acct == 0 and pins < 3 and not onboard:
        return {"ok": False, "gate": "not_logged_in", "reason": "unauth_cta", **info}
    if acct > 0:
        return {"ok": True, "gate": "logged_in", "reason": "account_menu", **info}
    if pins >= 3 and not has_cta:
        return {"ok": True, "gate": "logged_in", "reason": "pin_feed", **info}
    if not has_cta and feedish:
        return {"ok": True, "gate": "logged_in", "reason": "feed_no_unauth_cta", **info}
    return {"ok": False, "gate": "not_logged_in", "reason": "no_login_signals", **info}


def login_gate_fail_status(gate: dict[str, Any]) -> str | None:
    """Map a failed independent login gate to a terminal status. None if logged in."""
    if gate.get("ok"):
        return None
    g = str(gate.get("gate") or "")
    if g == "account_deactivated":
        return "account_deactivated"
    if g == "oops":
        return "oops_blocked"
    if g == "code_ui":
        return "verify_soft_fail"
    return "not_logged_in"


def settle_after_continue(
    page,
    *,
    settle_lo: int = 3000,
    settle_hi: int = 8000,
    poll_ms: int = 500,
    max_extra_s: float = 25.0,
) -> dict:
    url0 = page_url(page)
    settle_ms = human_pause(page, settle_lo, settle_hi, "after_continue_settle")
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    t0 = time.time()
    outcome = "unknown"
    body = ""
    while time.time() - t0 < max_extra_s:
        body = body_text(page, 4000)
        url = page_url(page)
        code_vis = _code_visible(page)
        if "Enter the code" in body or code_vis:
            outcome = "code_ui"
            break
        if has_email_confirmed_toast(body):
            outcome = "email_confirmed_toast"
            break
        if "Oops" in body and not code_vis and "Enter the code" not in body:
            outcome = "oops"
            break
        cta = "Log in" in body[:1500] and "Sign up" in body[:1500]
        onboarding = is_onboarding(body)
        feedish = ("Search Pinterest" in body) or ("header-accounts" in body)
        if onboarding or (not cta and feedish):
            outcome = "logged_in"
            break
        gate = page_login_gate(page)
        if gate.get("ok"):
            outcome = "logged_in"
            break
        page.wait_for_timeout(poll_ms)
    else:
        body = body_text(page, 4000)
        if "Oops" in body:
            outcome = "oops"
        elif "Enter the code" in body:
            outcome = "code_ui"
        elif has_email_confirmed_toast(body):
            outcome = "email_confirmed_toast"
    result = {
        "status": "settle_after_continue",
        "outcome": outcome,
        "settle_ms": settle_ms,
        "url": page_url(page),
        "url0": url0,
    }
    emit(result)
    return result


def click_send_new_code(page) -> bool:
    sels = [
        'button:has-text("Send new code")',
        'a:has-text("Send new code")',
        '[role="button"]:has-text("Send new code")',
        "text=Send new code",
    ]
    for sel in sels:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            if loc.is_visible(timeout=800):
                human_pause(page, 800, 1800, "before_send_new_code")
                clk = _hclick(page, loc)
                if not clk.get("ok"):
                    continue
                emit({"status": "verify_retry", "action": "send_new_code", "sel": sel})
                return True
        except Exception:
            continue
    return False


def load_imap_mod():
    import importlib.util

    for cand in (
        _HERE.parent / "outlook_imap_pinterest_code.py",
        ROOT / "scripts" / "outlook_imap_pinterest_code.py",
    ):
        if not cand.is_file():
            continue
        spec = importlib.util.spec_from_file_location("outlook_imap_pinterest_code", cand)
        if spec is None or spec.loader is None:
            continue
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    return None


def imap_max_uid(secrets: dict[str, str]) -> int:
    mod = load_imap_mod()
    if mod is None:
        raise RuntimeError("imap_helper_missing")
    return int(mod.max_uid(secrets))


def imap_wait_code(secrets: dict[str, str], after_uid: int, timeout: int = 180) -> dict | None:
    mod = load_imap_mod()
    if mod is None:
        return None
    try:
        return mod.wait_code(secrets, after_uid, timeout, 6)
    except Exception as e:
        emit({"status": "imap_error", "err": type(e).__name__, "message": redact_text(str(e)[:200], extra=secret_bag(secrets))})
        return None


def load_nurture_mod():
    import importlib.util

    for cand in (
        ROOT / "scripts" / "run_pinterest_nurture_browse.py",
        ROOT / "skills" / "pinterest-nurture-browse" / "scripts" / "run_pinterest_nurture_browse.py",
        _HERE.parent / "run_pinterest_nurture_browse.py",
    ):
        if not cand.is_file():
            continue
        spec = importlib.util.spec_from_file_location("run_pinterest_nurture_browse", cand)
        if spec is None or spec.loader is None:
            continue
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    return None


def submit_verify_code(page, code: str, art: Path | None = None) -> dict:
    val = fill_code_react(page, code)
    match = val == code
    emit(
        {
            "status": "code_filled",
            "len": len(code),
            "value_len": len(val),
            "value_match": match,
        }
    )
    if not match:
        emit({"status": "code_value_mismatch", "len": len(code), "value_len": len(val)})
        return {
            "status": "verify_soft_fail",
            "still_code_ui": True,
            "login_signup_cta": False,
            "has_oops": False,
            "error_snip": "code_value_mismatch",
            "url": page_url(page),
            "code_value_len": len(val),
            "value_match": False,
        }
    human_pause(page, 1200, 2800, "after_code_fill")
    if art is not None:
        try:
            art.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(art / "run-02b-code-filled.png"))
        except Exception:
            pass

    cont, cont_sel = find_verify_continue(page)
    emit({"status": "continue_locator", "matched": cont_sel})
    cont.wait_for(state="visible", timeout=15000)
    continue_enabled_before = wait_continue_enabled(cont, timeout_ms=15000)
    emit({"status": "continue_ready", "enabled": continue_enabled_before})
    human_pause(page, 2000, 5000, "before_verify_continue")
    clk = _hclick(page, cont)
    if not clk.get("ok"):
        emit({"status": "click_failed", "where": "verify_continue", "method": clk.get("method")})
        return {
            "status": "verify_soft_fail",
            "still_code_ui": True,
            "login_signup_cta": False,
            "has_oops": False,
            "error_snip": None,
            "url": page_url(page),
            "continue_enabled_before_click": continue_enabled_before,
            "code_value_len": len(val),
            "continue_matched": cont_sel,
            "click_failed": clk.get("method"),
        }
    emit({"status": "continue_clicked"})
    settle = settle_after_continue(page, settle_lo=3000, settle_hi=8000, max_extra_s=20.0)
    if art is not None:
        try:
            page.screenshot(path=str(art / "run-03-after-code.png"))
        except Exception:
            pass
    body2 = body_text(page, 4000)
    still = "Enter the code" in body2 or _code_visible(page)
    cta = "Log in" in body2[:1200] and "Sign up" in body2[:1200]
    toast_ok = has_email_confirmed_toast(body2) or settle.get("outcome") == "email_confirmed_toast"
    has_oops = ("Oops" in body2) and (not still) and (not toast_ok)
    if settle.get("outcome") == "oops" and not still and not toast_ok:
        has_oops = True
    err = snip_error(body2)
    soft = soft_verify_error(body2, still) and not toast_ok

    if toast_ok and (still or cta):
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(800)
        except Exception:
            pass
        status = "ok"
        still = False
    elif has_oops:
        status = "oops_blocked"
    elif soft:
        status = "verify_soft_oops"
    elif not still and not cta and not has_oops:
        status = "ok"
    else:
        status = "code_submitted_check_ui"

    return {
        "status": status,
        "still_code_ui": still,
        "login_signup_cta": cta,
        "has_oops": has_oops,
        "error_snip": err,
        "url": page_url(page),
        "continue_enabled_before_click": continue_enabled_before,
        "code_value_len": len(val),
        "continue_matched": cont_sel,
        "settle_outcome": settle.get("outcome"),
        "email_confirmed_toast": toast_ok,
        "soft_verify": soft,
    }


def maybe_verify_path_a(page, secrets: dict[str, str], base_uid: int, art: Path | None) -> dict:
    hit = imap_wait_code(secrets, base_uid, timeout=180)
    if hit is None:
        return {"status": "imap_timeout"}
    code = hit["code"]
    emit({"status": "code_received", "subject": hit.get("subject"), "code_len": len(code)})
    payload = submit_verify_code(page, code, art=art)
    if payload.get("status") == "verify_soft_oops":
        human_pause(page, 2000, 4000, "before_soft_verify_retry")
        try:
            # Watermark before Send-new-code so a fast-arriving mail is not skipped.
            watermark = imap_max_uid(secrets)
        except Exception:
            watermark = base_uid
        if click_send_new_code(page):
            human_pause(page, 2500, 5000, "after_send_new_code")
            hit2 = imap_wait_code(secrets, watermark, timeout=180)
            if hit2 is None:
                payload["status"] = "verify_soft_fail"
                payload["verify_retry"] = "imap_timeout"
                return payload
            emit(
                {
                    "status": "code_received",
                    "verify_retry": True,
                    "subject": hit2.get("subject"),
                    "code_len": len(hit2["code"]),
                }
            )
            payload = submit_verify_code(page, hit2["code"], art=art)
            payload["verify_retry"] = True
            if payload.get("status") == "verify_soft_oops":
                payload["status"] = "verify_soft_fail"
        else:
            payload["status"] = "verify_soft_fail"
            payload["verify_retry"] = "send_new_code_not_found"
    return payload


def dismiss_overlays(page) -> None:
    for _ in range(2):
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
        except Exception:
            pass


def finish_onboarding(page, human_name: str, art: Path | None = None) -> str | None:
    """Clear onboarding with trail clicks / human type. Name is never email local-part."""
    used: str | None = None
    markers = (
        "What's your name",
        "Nice to meet you",
        "How do you identify",
        "Where do you live",
        "What are you in the mood",
        "Tell us more",
    )
    for step in range(18):
        b = body_text(page, 4000)
        if "What's your name" in b or "Nice to meet you" in b:
            loc = page.locator(
                'input[name="name"], input[id="name"], input[aria-label="Name"], '
                '[data-test-id="name-input"] input, label:has-text("Name") ~ input, '
                'label:has-text("Name") + input, [role="dialog"] input, '
                'form input[type="text"], input[name="full_name"]'
            ).first
            try:
                loc.wait_for(state="visible", timeout=8000)
                human_type_text(page, loc, human_name, mouse=session_mouse())
                used = human_name
                human_pause(page, 800, 2000, "after_onboarding_name")
                if art is not None:
                    try:
                        page.screenshot(path=str(art / f"onboard-name-{step}.png"))
                    except Exception:
                        pass
            except Exception as e:
                emit({"status": "onboarding_name_skip", "err": type(e).__name__})
            cont = page.locator('button:has-text("Continue")').filter(has_not_text="Google").first
            try:
                wait_continue_enabled(cont, 8000)
                _hclick(page, cont)
                human_pause(page, 1200, 2200, "after_onboarding_name_continue")
            except Exception:
                pass
            continue

        if "How do you identify" in b:
            labels = ["Male", "Female"]
            random.shuffle(labels)
            for label in labels + ["Other"]:
                btn = page.locator(
                    f'button:has-text("{label}"), div[role="button"]:has-text("{label}")'
                ).first
                try:
                    if btn.count() and btn.is_visible(timeout=600):
                        _hclick(page, btn)
                        human_pause(page, 800, 1800, "after_gender")
                        break
                except Exception:
                    continue
            continue

        skip = page.locator(
            'button:has-text("Skip"), a:has-text("Skip"), [role="button"]:has-text("Skip")'
        )
        try:
            if skip.count() and skip.first.is_visible(timeout=500):
                _hclick(page, skip.first)
                human_pause(page, 800, 1600, "after_skip")
                continue
        except Exception:
            pass

        if re.search(r"What are you in the mood|interest|topic|what are you into", b, re.I):
            chips = page.locator('[role="dialog"] button')
            clicked = 0
            try:
                for i in range(min(chips.count(), 8)):
                    if clicked >= 3:
                        break
                    txt = (chips.nth(i).inner_text() or "").strip().lower()
                    if txt in ("continue", "next", "skip", "done"):
                        continue
                    clk = _hclick(page, chips.nth(i))
                    if clk.get("ok"):
                        clicked += 1
            except Exception:
                pass
            human_pause(page, 400, 900, "after_interest_picks")
            nxt = page.locator(
                'button:has-text("Next"), button:has-text("Done"), button:has-text("Continue")'
            ).first
            try:
                if nxt.count() and nxt.is_visible(timeout=800):
                    _hclick(page, nxt)
                    human_pause(page, 800, 1600, "after_interest_continue")
                    continue
            except Exception:
                pass

        cont = page.locator(
            'button:has-text("Continue"), button:has-text("Next"), '
            'button:has-text("Done"), button:has-text("Finish")'
        ).filter(has_not_text="Google")
        try:
            if cont.count() and cont.first.is_visible(timeout=400) and any(m in b for m in markers):
                _hclick(page, cont.first)
                human_pause(page, 800, 1600, "after_onboard_continue")
                continue
        except Exception:
            pass

        if not any(m in b for m in markers):
            try:
                dlg = page.locator('[role="dialog"]')
                if dlg.count() == 0 or not dlg.first.is_visible(timeout=400):
                    break
                dtxt = dlg.first.inner_text()[:400]
                if not any(m in dtxt for m in markers):
                    break
            except Exception:
                break
        page.wait_for_timeout(400)
    return used


def settings_confirm_email(page, secrets: dict[str, str], art: Path | None = None) -> dict:
    """Best-effort Path B Confirm Email. Failure does not negate registered_ok."""
    out = {"email_verified": False, "settings_path": "not_attempted"}
    try:
        dismiss_overlays(page)
        page.goto(SETTINGS_ACCOUNT_URL, wait_until="domcontentloaded", timeout=90000)
        human_pause(page, 2000, 4000, "settings_land", ambient=True)
        dismiss_overlays(page)
        b = body_text(page, 4000)
        if "Confirmed" in b and "Unconfirmed" not in b and "Confirm Email" not in b and "Confirm email" not in b:
            out["email_verified"] = True
            out["settings_path"] = "already_verified"
            return out
        if "Unconfirmed" not in b and "Confirm Email" not in b and "Confirm email" not in b:
            out["settings_path"] = "no_confirm_cta"
            return out
        try:
            base_uid = imap_max_uid(secrets)
        except Exception as e:
            out["settings_path"] = f"imap_uid_err:{type(e).__name__}"
            return out
        btn = page.locator(CONFIRM_EMAIL_SEL).first
        try:
            btn.wait_for(state="visible", timeout=12000)
        except Exception:
            out["settings_path"] = "confirm_not_visible"
            return out
        clk = _hclick(page, btn)
        if not clk.get("ok"):
            out["settings_path"] = "confirm_click_failed"
            return out
        emit({"status": "confirm_email_clicked"})
        human_pause(page, 1500, 3000, "after_confirm_email")
        hit = imap_wait_code(secrets, base_uid, timeout=120)
        if hit is None:
            out["settings_path"] = "imap_timeout"
            return out
        code = hit["code"]
        emit({"status": "code_received", "subject": hit.get("subject"), "code_len": len(code), "path": "settings"})
        for _ in range(40):
            if _code_visible(page):
                break
            page.wait_for_timeout(250)
        val = fill_code_react(page, code)
        if val != code:
            out["settings_path"] = "code_value_mismatch"
            return out
        cont, cont_sel = find_verify_continue(page)
        wait_continue_enabled(cont, 12000)
        human_pause(page, 1200, 2500, "before_settings_verify_continue")
        clk2 = _hclick(page, cont)
        if not clk2.get("ok"):
            out["settings_path"] = "settings_continue_click_failed"
            return out
        emit({"status": "continue_clicked", "sel": cont_sel, "path": "settings"})
        human_pause(page, 2500, 4500, "after_settings_code")
        page.goto(SETTINGS_ACCOUNT_URL, wait_until="domcontentloaded", timeout=90000)
        human_pause(page, 1500, 3000, "settings_recheck")
        dismiss_overlays(page)
        b2 = body_text(page, 4000)
        verified = ("Confirmed" in b2) and ("Unconfirmed" not in b2)
        out["email_verified"] = verified
        out["settings_path"] = "confirmed" if verified else "code_submitted_check_ui"
        if art is not None:
            try:
                page.screenshot(path=str(art / "settings-final.png"))
            except Exception:
                pass
        return out
    except Exception as e:
        out["settings_path"] = f"error:{type(e).__name__}"
        return out


def chain_nurture(page, *, profile: str, pins: int, min_sec: int, max_sec: int, art: Path) -> dict:
    fields = {
        "nurture_status": "skipped",
        "nurture_elapsed_s": 0,
        "nurture_liked": False,
    }
    try:
        mod = load_nurture_mod()
        if mod is None:
            fields["nurture_status"] = "import_failed"
            return fields
        probe = None
        if hasattr(mod, "session_keepalive_probe"):
            probe = mod.session_keepalive_probe(page)
        if probe is not None:
            emit({"status": "session_keepalive_probe", **probe})
            fields["session_keepalive_probe"] = probe
            if not probe.get("ok"):
                fields["nurture_status"] = "session_lost_before_nurture"
                fields["session_lost_gate"] = probe.get("gate") or "not_logged_in"
                return fields
        emit(
            {
                "status": "nurture_start",
                "profile": profile,
                "mode": "keep_open_cdp",
                "pins": pins,
                "version": VERSION,
            }
        )
        result = mod.run_nurture_session(
            page,
            profile=profile,
            run_art=art / "nurture",
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
        emit(
            {
                "status": "nurture_done",
                "nurture_status": fields["nurture_status"],
                "nurture_elapsed_s": fields["nurture_elapsed_s"],
                "nurture_liked": fields["nurture_liked"],
            }
        )
    except Exception as e:
        fields["nurture_status"] = f"error:{type(e).__name__}"
        fields["nurture_error"] = redact_text(str(e)[:200])
        emit({"status": "nurture_error", "err": type(e).__name__, "message": fields["nurture_error"]})
    return fields


def maybe_nurture(page, args, register_payload: dict, *, profile: str, art: Path) -> dict:
    out = dict(register_payload)
    skip = bool(getattr(args, "skip_nurture", False)) or os.environ.get("HUB_SKIP_NURTURE", "0") in (
        "1",
        "true",
        "True",
        "yes",
    )
    if skip:
        emit(
            {
                "status": "nurture_skip_warning",
                "warning": (
                    "skip_nurture set — disconnecting without same-session nurture. "
                    "Independent nurture minutes later is NOT the primary path."
                ),
            }
        )
        out["nurture_status"] = "skipped"
        out["nurture_elapsed_s"] = 0
        out["nurture_liked"] = False
        out["nurture_skip_warning"] = True
        return out
    fields = chain_nurture(
        page,
        profile=profile,
        pins=getattr(args, "nurture_pins", 0),
        min_sec=getattr(args, "nurture_min_sec", 120),
        max_sec=getattr(args, "nurture_max_sec", 180),
        art=art,
    )
    out.update(fields)
    return out


def fill_signup_form(page, email: str, password: str, bday: str) -> str | None:
    """Return None on success, or a click_failed where-string."""
    page.goto(SIGNUP_URL, wait_until="domcontentloaded", timeout=90000)
    quiet_window(page)
    page.wait_for_selector("#email", timeout=30000)
    email_loc = page.locator("#email").first
    typed_email = human_type_text(page, email_loc, email, mouse=session_mouse())
    if not typed_email.get("focus_ok"):
        emit({"status": "click_failed", "where": "email", "method": typed_email.get("focus")})
        return "email"
    human_pause(page, 800, 2500, "between_email_password")
    pw_loc = page.locator("#password").first
    typed_pw = human_type_text(page, pw_loc, password, mouse=session_mouse())
    if not typed_pw.get("focus_ok"):
        emit({"status": "click_failed", "where": "password", "method": typed_pw.get("focus")})
        return "password"
    human_pause(page, 800, 2500, "between_password_birthdate")
    bday_loc = page.locator("#birthdate").first
    bday_clk = _hclick(page, bday_loc)
    if not bday_clk.get("ok"):
        emit({"status": "click_failed", "where": "birthdate", "method": bday_clk.get("method")})
        return "birthdate"
    page.fill("#birthdate", bday)
    human_pause(page, 800, 2500, "after_birthdate")
    try:
        page.locator("#birthdate").blur()
    except Exception:
        page.keyboard.press("Tab")
    human_pause(page, 1200, 2800, "after_birthdate_blur")
    return None


def click_signup_continue(page) -> bool:
    form_btns = page.locator("form:has(#email) button:has-text('Continue')").filter(has_not_text="Google")
    signup_cont = (
        form_btns.first
        if form_btns.count()
        else page.locator("button:has-text('Continue')").filter(has_not_text="Google").first
    )
    signup_cont.wait_for(state="visible", timeout=15000)
    wait_continue_enabled(signup_cont, timeout_ms=10000)
    human_pause(page, 2000, 5000, "before_signup_continue")
    clk = _hclick(page, signup_cont)
    if not clk.get("ok"):
        emit({"status": "click_failed", "where": "signup_continue", "method": clk.get("method")})
        return False
    emit({"status": "signup_continue_clicked"})
    return True


def map_internal_status(raw: str) -> str:
    """Map runner-internal tokens onto declared manifest statuses."""
    if raw in ALLOWED_STATUSES:
        return raw
    mapping = {
        "ok": "registered_ok",
        "verify_soft_oops": "verify_soft_fail",
        "code_submitted_check_ui": "verify_soft_fail",
        "unexpected_after_continue": "not_logged_in",
        "click_failed": "adapter_error",
    }
    mapped = mapping.get(raw, FAIL_FALLBACK_STATUS)
    if mapped not in ALLOWED_STATUSES:
        return FAIL_FALLBACK_STATUS
    return mapped


def status_exit(status: str) -> int:
    if status in SUCCESS_STATUSES:
        return int(STATUS_EXITS.get(status, _DEFAULT_SUCCESS_EXIT))
    return int(STATUS_EXITS.get(status, _HISTORICAL_FAIL_EXITS.get(status, _DEFAULT_FAIL_EXIT)))


def connect_over_cdp(endpoint: str):
    """Attach Playwright to Hubstudio Chrome. Never launches a local browser."""
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(endpoint)
    if browser.contexts:
        ctx = browser.contexts[0]
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
    else:
        ctx = browser.new_context()
        page = ctx.new_page()
    return pw, browser, ctx, page


def disconnect_cdp(pw, browser) -> None:
    """Drop the CDP websocket without sending Browser.close to Hubstudio Chrome."""
    try:
        browser.close = lambda *a, **k: None  # type: ignore[method-assign]
    except Exception:
        pass
    try:
        pw.stop()
    except Exception:
        pass


def start_hub_browser(container_code: int, proxy: dict[str, Any] | None) -> dict[str, Any]:
    if is_protected_container(container_code) or int(container_code) in PROTECTED_CONTAINER_CODES:
        raise RuntimeError("protected_env_refused")
    if proxy:
        extra_px = proxy_secret_bag(proxy)
        d = proxy_update(
            container_code,
            proxy["host"],
            proxy["port"],
            proxy["account"],
            proxy["password"],
            as_dynamic_type=int(proxy.get("asDynamicType", 0)),
        )
        emit(
            {
                "status": "proxy_update",
                "code": d.get("code"),
                "msg": redact_proxy_msg(d.get("msg"), proxy),
            },
            extra_px,
        )
        if d.get("msg") == "protected_env_refused" or d.get("code") == -1:
            raise RuntimeError("protected_env_refused")
        if not hub_api_success_code(d.get("code")):
            raise RuntimeError("proxy_update_failed")
    d = browser_start(container_code)
    if d.get("msg") == "protected_env_refused" or d.get("code") == -1:
        raise RuntimeError("protected_env_refused")
    data = d.get("data") or {}
    if not hub_api_success_code(d.get("code")):
        raise RuntimeError(f"browser_start_failed code={d.get('code')}")
    port = data.get("debuggingPort")
    if not port:
        raise RuntimeError("browser_start_no_port")
    if not wait_cdp(int(port), timeout=90):
        try:
            sd = browser_stop(container_code)
            emit({"status": "hub_browser_stop", "code": sd.get("code")})
        except Exception as e:
            emit({"status": "hub_browser_stop_err", "err": type(e).__name__})
        raise RuntimeError(f"cdp_timeout port={port}")
    return {
        "debugging_port": int(port),
        "cdp_endpoint": f"http://127.0.0.1:{int(port)}",
        "browser_id": data.get("browserID") or data.get("containerId"),
        "duplicate": data.get("duplicate"),
    }


def resolve_container(rec: dict[str, Any] | None, env_name: str) -> tuple[int | None, str]:
    if rec:
        eid = rec.get("environment_id") or rec.get("containerCode")
        if eid:
            return int(eid), rec.get("environment_name") or env_name
        name = rec.get("environment_name") or env_name
        existing = find_env_by_name(name)
        if existing:
            return int(existing["containerCode"]), name
        return None, name
    existing = find_env_by_name(env_name)
    if existing:
        return int(existing["containerCode"]), env_name
    return None, env_name


def update_plan_from_report(state_dir: Path, row_id: str, report: dict[str, Any]) -> None:
    plan_path = state_dir / "plan.json"
    if not plan_path.is_file():
        return
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    rec = None
    for r in plan.get("records") or []:
        if r.get("row_id") == row_id:
            rec = r
            break
    if rec is None:
        return
    st = report.get("status")
    fleet = "failed"
    if st == "registered_ok":
        fleet = "ok"
    elif st == "oops_blocked":
        fleet = "parked"
    elif st == "env_protected":
        fleet = "deferred"
    rec["status"] = fleet
    rec["skill_status"] = st
    if st == "registered_ok":
        rec["registration_status"] = "completed"
        rec["email_verified"] = bool(report.get("email_verified"))
        rec["onboarding_status"] = rec.get("onboarding_status") or (
            "completed" if report.get("display_name") or report.get("onboarding_name_used") else rec.get("onboarding_status") or "unknown"
        )
        ns = report.get("nurture_status")
        if ns == "browsed_ok":
            rec["browse_status"] = "completed"
        elif ns == "skipped":
            rec["browse_status"] = rec.get("browse_status") or "not_started"
        elif ns:
            rec["browse_status"] = "like_failed" if ns != "session_lost_before_nurture" else "not_started"
    rec["failure_reason"] = "" if fleet == "ok" else str(st or "")
    if fleet == "failed":
        rec["retries_used"] = int(rec.get("retries_used") or 0) + 1
    tmp = plan_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(plan_path)
    try:
        os.chmod(plan_path, 0o600)
    except Exception:
        pass
    reports = state_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    safe = {
        "row_id": row_id,
        "environment_id": rec.get("environment_id"),
        "environment_name": rec.get("environment_name"),
        "status": rec.get("status"),
        "skill_status": st,
        "registration_status": rec.get("registration_status"),
        "email_verified": bool(rec.get("email_verified")),
        "onboarding_status": rec.get("onboarding_status"),
        "browse_status": rec.get("browse_status"),
        "nurture_status": report.get("nurture_status"),
        "path": report.get("path"),
        "failure_reason": rec.get("failure_reason"),
    }
    out = reports / f"{row_id}-result.json"
    out.write_text(json.dumps(safe, indent=2) + "\n")
    os.chmod(out, 0o600)


def artifact_dir(state_dir: Path | None, row_id: str | None, profile: str) -> Path:
    if state_dir is not None and row_id:
        p = state_dir / "reports" / row_id
        p.mkdir(parents=True, exist_ok=True)
        return p
    p = ROOT / "artifacts" / "pinterest" / "hubstudio-register" / (row_id or profile)
    p.mkdir(parents=True, exist_ok=True)
    return p


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
    ap.add_argument("--row-id", default="", help="Fleet row id, e.g. row-0041")
    ap.add_argument(
        "--state-dir",
        default="",
        help="Hubstudio registration state root (plan.json / secrets / reports)",
    )
    ap.add_argument(
        "--cdp-endpoint",
        default="",
        help="Attach to already-started Hubstudio Chrome (http://127.0.0.1:PORT)",
    )
    ap.add_argument("--container-code", default="", help="Hubstudio containerCode (standalone start)")
    ap.add_argument("--env-name", default="", help="Hubstudio containerName, e.g. hub-reg-20260930-41")
    ap.add_argument("--secrets", default="", help="Cloak-style data/secrets/*.env")
    ap.add_argument("--profile", default="hubstudio", help="Label for artifacts / nurture")
    ap.add_argument(
        "--skip-nurture",
        action="store_true",
        help="Do not chain nurture before disconnect (or set HUB_SKIP_NURTURE=1)",
    )
    ap.add_argument("--nurture-only", action="store_true")
    ap.add_argument("--nurture-pins", type=int, default=0)
    ap.add_argument("--nurture-min-sec", type=int, default=120)
    ap.add_argument("--nurture-max-sec", type=int, default=180)
    ap.add_argument(
        "--external-lifecycle",
        action="store_true",
        help="Do not Hub API start/stop (run_api_row already owns the env)",
    )
    ap.add_argument("--digest", default="", help="Skill package digest (python_runner identity)")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate I/O (secrets keys, protected env, plan) without CDP/Hub start",
    )
    return ap


def finish_report(
    status: str,
    *,
    digest: str,
    extra_secrets: list[str],
    **extra: Any,
) -> dict[str, Any]:
    if status not in ALLOWED_STATUSES:
        status = FAIL_FALLBACK_STATUS
    report = {
        "skill_id": SKILL_ID,
        "version": VERSION,
        "status": status,
    }
    if digest:
        report["digest"] = digest
    for k, v in extra.items():
        report[k] = redact_result(v, extra_secrets)
    return report


def run_register_flow(page, args, secrets: dict[str, str], art: Path, profile: str) -> dict[str, Any]:
    extra = secret_bag(secrets)
    email = secrets["PINTEREST_EMAIL"]
    password = secrets["PINTEREST_PASSWORD"]
    bday, age = random_birthday()
    emit(
        {
            "profile": profile,
            "email_domain": email.split("@")[-1] if "@" in email else "",
            "birthday": bday,
            "age": age,
            "version": VERSION,
            "skip_nurture": bool(args.skip_nurture) or os.environ.get("HUB_SKIP_NURTURE", "0") in ("1", "true", "True", "yes"),
            "browser": "hubstudio_cdp",
        },
        extra,
    )
    reset_session_mouse()
    try:
        base_uid = imap_max_uid(secrets)
    except Exception as e:
        emit({"status": "imap_max_uid_skip", "err": type(e).__name__}, extra)
        base_uid = 0

    # Already logged in? Do not re-submit signup.
    try:
        page.goto(HOME_URL, wait_until="domcontentloaded", timeout=90000)
        quiet_window(page)
    except Exception:
        pass
    gate0 = page_login_gate(page)
    emit({"status": "login_gate", "phase": "start", **{k: gate0[k] for k in ("ok", "gate", "reason") if k in gate0}})
    path = ""
    display_name = None
    email_verified = False
    settings_info: dict[str, Any] = {}

    if gate0.get("ok"):
        path = "already_logged_in"
        display_name = finish_onboarding(page, random_display_name(), art=art)
        settings_info = settings_confirm_email(page, secrets, art=art)
        email_verified = bool(settings_info.get("email_verified"))
        payload = {
            "status": "registered_ok",
            "path": path,
            "birthday": bday,
            "display_name": display_name,
            "email_verified": email_verified,
            "settings_path": settings_info.get("settings_path"),
            "version": VERSION,
        }
        payload = maybe_nurture(page, args, payload, profile=profile, art=art)
        return payload

    where = fill_signup_form(page, email, password, bday)
    if where:
        return {"status": "adapter_error", "path": f"click_failed:{where}", "birthday": bday, "version": VERSION}
    try:
        page.screenshot(path=str(art / "run-01-filled.png"))
    except Exception:
        pass
    if not click_signup_continue(page):
        return {"status": "adapter_error", "path": "signup_continue_click_failed", "birthday": bday, "version": VERSION}

    settle = settle_after_continue(page, settle_lo=3000, settle_hi=8000, max_extra_s=22.0)
    try:
        page.screenshot(path=str(art / "run-02-after-continue.png"))
    except Exception:
        pass
    body = body_text(page, 4000)
    if settle.get("outcome") == "oops" or (
        "Oops" in body and "Enter the code" not in body and not _code_visible(page)
    ):
        emit({"status": "oops_blocked", "note": "park_no_recontinue"})
        return {"status": "oops_blocked", "path": "oops_after_continue", "birthday": bday, "version": VERSION}

    code_ui = settle.get("outcome") == "code_ui" or ("Enter the code" in body or _code_visible(page))
    logged_in = settle.get("outcome") in ("logged_in", "email_confirmed_toast")
    if not code_ui and not logged_in:
        still_form = False
        try:
            still_form = page.locator("#email").count() > 0 and page.locator("#email").first.is_visible()
        except Exception:
            still_form = False
        if still_form and "Oops" not in body:
            human_pause(page, 3000, 6000, "before_signup_continue_one_retry")
            try:
                click_signup_continue(page)
                settle = settle_after_continue(page, settle_lo=3000, settle_hi=8000, max_extra_s=20.0)
                body = body_text(page, 4000)
            except Exception:
                pass
            if settle.get("outcome") == "oops" or ("Oops" in body and "Enter the code" not in body):
                emit({"status": "oops_blocked", "note": "park_after_one_retry"})
                return {"status": "oops_blocked", "path": "oops_after_retry", "birthday": bday, "version": VERSION}
            code_ui = settle.get("outcome") == "code_ui" or "Enter the code" in body or _code_visible(page)
            logged_in = settle.get("outcome") in ("logged_in", "email_confirmed_toast")

    if "Oops" in body and not code_ui:
        emit({"status": "oops_blocked"})
        return {"status": "oops_blocked", "path": "oops", "birthday": bday, "version": VERSION}

    if logged_in and not code_ui:
        path = "signup_ok_no_code_challenge"
        display_name = finish_onboarding(page, random_display_name(), art=art)
        settings_info = settings_confirm_email(page, secrets, art=art)
        email_verified = bool(settings_info.get("email_verified"))
        gate = page_login_gate(page)
        if not gate.get("ok"):
            # onboarding counted earlier; re-check after settings nav
            if gate.get("gate") == "account_deactivated":
                return {"status": "account_deactivated", "path": path, "birthday": bday, "version": VERSION}
            if gate.get("gate") == "oops":
                return {"status": "oops_blocked", "path": path, "birthday": bday, "version": VERSION}
            # If we left settings and lost signals, still honor earlier logged_in settle
            emit({"status": "login_gate", "phase": "path_b", **{k: gate[k] for k in ("ok", "gate", "reason") if k in gate}})
        payload = {
            "status": "registered_ok",
            "path": path,
            "birthday": bday,
            "display_name": display_name,
            "email_verified": email_verified,
            "settings_path": settings_info.get("settings_path"),
            "version": VERSION,
        }
        payload = maybe_nurture(page, args, payload, profile=profile, art=art)
        return payload

    if not code_ui and "Enter the code" not in body:
        emit({"status": "unexpected_after_continue", "snip": redact_text(body[:350], extra=extra)})
        return {
            "status": "not_logged_in",
            "path": "unexpected_after_continue",
            "birthday": bday,
            "version": VERSION,
        }

    path = "path_a_code"
    payload = maybe_verify_path_a(page, secrets, base_uid, art)
    payload["birthday"] = bday
    payload["version"] = VERSION
    payload["path"] = path
    raw = payload.get("status")
    if raw == "ok":
        display_name = finish_onboarding(page, random_display_name(), art=art)
        payload["display_name"] = display_name
        toast = bool(payload.get("email_confirmed_toast"))
        payload["email_verified"] = toast
        if not toast:
            settings_info = settings_confirm_email(page, secrets, art=art)
            payload["email_verified"] = bool(settings_info.get("email_verified"))
            payload["settings_path"] = settings_info.get("settings_path")
        gate = page_login_gate(page)
        emit({"status": "login_gate", "phase": "path_a", **{k: gate[k] for k in ("ok", "gate", "reason") if k in gate}})
        fail = login_gate_fail_status(gate)
        if fail:
            payload["status"] = fail
            payload["login_gate_reason"] = gate.get("reason")
            return payload
        payload["status"] = "registered_ok"
        payload = maybe_nurture(page, args, payload, profile=profile, art=art)
        return payload
    payload["status"] = map_internal_status(str(raw))
    return payload


def run_nurture_only_flow(page, args, art: Path, profile: str) -> dict[str, Any]:
    gate = page_login_gate(page)
    emit({"status": "login_gate", "phase": "nurture_only", **{k: gate[k] for k in ("ok", "gate", "reason") if k in gate}})
    if not gate.get("ok"):
        mapped = {
            "account_deactivated": "account_deactivated",
            "oops": "oops_blocked",
        }.get(str(gate.get("gate")), "not_logged_in")
        return {"status": mapped, "path": "nurture_only", "version": VERSION}
    fields = maybe_nurture(page, args, {"status": "registered_ok", "path": "nurture_only", "version": VERSION}, profile=profile, art=art)
    ns = fields.get("nurture_status")
    if ns == "browsed_ok":
        fields["status"] = "browsed_ok"
    elif ns == "session_lost_before_nurture":
        fields["status"] = "not_logged_in"
    else:
        # Register-adjacent: stay registered_ok if still logged in; browse miss is not a hard fail
        fields["status"] = "registered_ok"
    return fields


def main(argv: list[str] | None = None, stdin_payload: dict[str, Any] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)
    payload = stdin_payload if stdin_payload is not None else load_stdin_payload()
    vars_ = payload.get("vars") if isinstance(payload.get("vars"), dict) else {}
    if payload.get("digest") and not args.digest:
        args.digest = str(payload.get("digest"))
    if not args.row_id:
        args.row_id = str(vars_.get("ROW_ID") or payload.get("row_id") or "")
    if not args.state_dir:
        args.state_dir = str(vars_.get("STATE_DIR") or payload.get("state_dir") or "")
    if not args.cdp_endpoint:
        args.cdp_endpoint = str(vars_.get("CDP_ENDPOINT") or payload.get("cdp_endpoint") or "")
    if not args.secrets:
        args.secrets = str(vars_.get("SECRETS_ENV") or vars_.get("SECRETS") or "")
    if not args.profile:
        args.profile = str(payload.get("profile") or vars_.get("PROFILE") or "hubstudio")
    if vars_.get("SKIP_NURTURE") in ("1", "true", "True", True):
        args.skip_nurture = True

    state_dir = Path(args.state_dir).expanduser() if args.state_dir else None
    if state_dir is None and args.row_id:
        # Default batch root if only row-id given
        env_state = os.environ.get("HUB_STATE_DIR")
        if env_state:
            state_dir = Path(env_state)
        elif DEFAULT_STATE_PARENT.is_dir():
            # newest batch dir
            batches = sorted(
                [p for p in DEFAULT_STATE_PARENT.iterdir() if p.is_dir() and re.fullmatch(r"\d{8}", p.name)],
                reverse=True,
            )
            if batches:
                state_dir = batches[0]

    row_id = args.row_id or ""
    rec = load_plan_row(state_dir, row_id) if state_dir is not None and row_id else None
    env_name = args.env_name or (rec.get("environment_name") if rec else "") or (
        default_env_name(row_id, state_dir) if row_id else ""
    )
    profile = args.profile or env_name or "hubstudio"
    art = artifact_dir(state_dir, row_id or None, profile)
    extra_secrets: list[str] = []
    started_cc: int | None = None
    pw = browser = None
    secrets: dict[str, str] = {}

    def done(status: str, **extra: Any) -> int:
        status = map_internal_status(status) if status not in ALLOWED_STATUSES else status
        extra.setdefault("row_id", row_id or None)
        extra.setdefault("env_name", env_name or None)
        extra.setdefault("elapsed_s", round(time.time() - t0, 2))
        report = finish_report(
            status,
            digest=args.digest,
            extra_secrets=extra_secrets,
            **extra,
        )
        emit(report, extra_secrets)
        if state_dir is not None and row_id:
            try:
                update_plan_from_report(state_dir, row_id, report)
            except Exception as e:
                # Do not append a second identity-less JSON after the terminal report.
                print(
                    redact_text(f"plan_update_err {type(e).__name__}", extra=extra_secrets),
                    file=sys.stderr,
                    flush=True,
                )
        return status_exit(status)

    t0 = time.time()
    try:
        secrets_path = Path(args.secrets) if args.secrets else None
        secrets = load_secrets(secrets_path=secrets_path, state_dir=state_dir, row_id=row_id or None)
        extra_secrets = secret_bag(secrets)
    except Exception as e:
        return done("adapter_error", path="secrets_load", reason=type(e).__name__)

    cc: int | None = None
    if args.container_code:
        try:
            cc = int(args.container_code)
        except ValueError:
            return done("adapter_error", path="bad_container_code")
    elif rec or env_name:
        try:
            cc, env_name = resolve_container(rec, env_name)
        except Exception as e:
            return done("adapter_error", path="env_lookup", reason=type(e).__name__)

    if cc is not None and (is_protected_container(cc) or int(cc) in PROTECTED_CONTAINER_CODES):
        return done("env_protected", path="protected_container", container_code=int(cc))

    if args.dry_run:
        return done(
            "not_logged_in",
            path="dry_run",
            dry_run=True,
            reason="dry-run validates I/O only (no CDP)",
            has_email=bool(secrets.get("PINTEREST_EMAIL")),
            has_outlook=bool(secrets.get("OUTLOOK_CLIENT_ID") and secrets.get("OUTLOOK_REFRESH_TOKEN")),
            container_code=cc,
        )

    cdp = args.cdp_endpoint.strip()
    external = bool(args.external_lifecycle) or bool(cdp)
    if not cdp:
        if cc is None:
            return done("env_missing", path="no_container", env_name=env_name or None)
        if external:
            return done("adapter_error", path="cdp_required_for_external_lifecycle")
        try:
            proxy = None
            if state_dir is not None and row_id:
                proxy = load_proxy_for_row(state_dir, row_num(row_id))
            bind = start_hub_browser(cc, proxy)
            started_cc = cc
            cdp = bind["cdp_endpoint"]
            emit({"status": "hub_browser_started", "debugging_port": bind["debugging_port"], "dup": bind.get("duplicate")})
        except RuntimeError as e:
            msg = str(e)
            if "cdp_timeout" in msg and cc is not None:
                try:
                    sd = browser_stop(cc)
                    emit({"status": "hub_browser_stop", "code": sd.get("code")})
                except Exception as se:
                    emit({"status": "hub_browser_stop_err", "err": type(se).__name__})
            if "protected_env" in msg:
                return done("env_protected", path="start_refused")
            if "proxy_update" in msg:
                return done("adapter_error", path="proxy_update", reason=type(e).__name__)
            path = "cdp_timeout" if "cdp_timeout" in msg else "hub_start"
            return done("adapter_error", path=path, reason=type(e).__name__)
        except Exception as e:
            return done("adapter_error", path="hub_start", reason=type(e).__name__)

    result: dict[str, Any] = {}
    try:
        pw, browser, _ctx, page = connect_over_cdp(cdp)
        emit({"status": "cdp_attached", "endpoint_host": "127.0.0.1"})
        if args.nurture_only:
            result = run_nurture_only_flow(page, args, art, profile)
        else:
            result = run_register_flow(page, args, secrets, art, profile)
        try:
            hang_before_close(page, session_mouse())
        except Exception:
            pass
    except Exception as e:
        result = {
            "status": "adapter_error",
            "path": "cdp_or_flow",
            "reason": type(e).__name__,
            "message": redact_text(str(e)[:200], extra=extra_secrets),
        }
    finally:
        if pw is not None:
            disconnect_cdp(pw, browser)
        if started_cc is not None:
            try:
                d = browser_stop(started_cc)
                emit({"status": "hub_browser_stop", "code": d.get("code")})
            except Exception as e:
                emit({"status": "hub_browser_stop_err", "err": type(e).__name__})

    status = map_internal_status(str(result.get("status") or FAIL_FALLBACK_STATUS))
    extra = {k: v for k, v in result.items() if k != "status"}
    return done(status, **extra)


if __name__ == "__main__":
    raise SystemExit(main())
