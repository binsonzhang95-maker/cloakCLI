#!/usr/bin/env python3
"""E2E Pinterest register + Outlook IMAP 6-digit verify (0.1.4).

Birthday: random age 25–35 as YYYY-MM-DD for #birthdate (type=date).
Prefer --headed (default). Headless often gets Pinterest Oops.

If signup returns path signup_ok_no_code_challenge (IP/geo dependent),
email may stay Unconfirmed — complete via Settings Confirm Email using
scripts/run_pinterest_settings_email_verify.py (same secrets/profile).
Onboarding name must be a realistic given name (never email local-part).

After register/login success (status ok), chains pinterest-nurture-browse in the
same browser session (keep-open) BEFORE ctx.close. Register success is preserved
even if nurture fails. Use --skip-nurture only when batch already nurtures
separately (logs a warning). Never treat independent nurture minutes later as
the primary post-register path.

Behavior (0.1.4): after nurture (or skip), hang idle ~60–180s with ambient
drift + storage flush before ctx.close. Shares nurture 0.2.3+ human helpers — trail-only
human_click_locator (never locator.click / force teleport), human_type_text
key stream, log-normal pauses, quiet window after signup land. After typing
#code, if input_value != target, fail immediately (verify_soft_fail) and do
not click Continue. 0.1.2: same human helpers. 0.1.1: human pacing between
fields / around Continue; longer settle after Continue before judging Oops vs
soft verify vs success; Oops parks (no re-Continue spam); soft verify may
Send-new-code once.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve()
ROOT = _HERE.parents[1]
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))
_py = ROOT / "python"
if str(_py) not in sys.path:
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

VERSION = "0.1.4"
SESSION_OK_NAME = ".cloak_session_ok"

ERROR_RE = re.compile(
    r"(incorrect|invalid|try again|expired|too many|rate.?limit|oops|"
    r"something went wrong)",
    re.I,
)
SOFT_VERIFY_RE = re.compile(
    r"(incorrect|invalid|try again|expired|something went wrong|sorry)",
    re.I,
)


def load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k] = v
    return out


def random_birthday(age_min: int = 25, age_max: int = 35) -> tuple[str, int]:
    age = random.randint(age_min, age_max)
    b = date(date.today().year - age, random.randint(1, 12), random.randint(1, 28))
    return b.isoformat(), age


def human_pause(page, lo_ms: int, hi_ms: int, label: str = "", *, ambient: bool | None = None) -> int:
    """Log-normal human-like delay; optional ambient drift on longer pauses."""
    if hi_ms < lo_ms:
        lo_ms, hi_ms = hi_ms, lo_ms
    lo_ms = max(0, int(lo_ms))
    hi_ms = max(lo_ms, int(hi_ms))
    ms = sample_pause_ms(lo_ms, hi_ms)
    payload = {"pause_ms": ms, "label": label}
    print(json.dumps(payload, ensure_ascii=False), flush=True)
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
    """No pointer/key events until TTI quiet window elapses (after goto)."""
    ms = sample_quiet_window_ms()
    print(json.dumps({"pause_ms": ms, "label": "quiet_window"}), flush=True)
    page.wait_for_timeout(ms)
    return ms


def _hclick(page, loc) -> dict:
    """Trail click; one soft retry with a new trail. Never force/teleport."""
    result = human_click_locator(page, loc, session_mouse())
    if result.get("hover_ms"):
        print(
            json.dumps({"pause_ms": result["hover_ms"], "label": "hover_before_click"}),
            flush=True,
        )
    if result.get("ok"):
        return result
    result = human_click_locator(page, loc, session_mouse())
    if not result.get("ok"):
        print(json.dumps({"human_click": result}, ensure_ascii=False), flush=True)
    return result


def fill_code_react(page, code: str) -> str:
    """Type #code via human key stream (React-friendly); return input_value."""
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
    """Prefer Continue scoped to verification UI; return (locator, matched_sel)."""
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
    """Wait until Continue is visible and not disabled / aria-disabled."""
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
    """Inline verify soft-fail (still on code UI with error), not full-page Oops."""
    if not still_code or not body:
        return False
    if re.search(r"Oops!\s*Sorry", body) and "Enter the code" not in body:
        return False
    return bool(SOFT_VERIFY_RE.search(body))


def random_display_name() -> str:
    """Reasonable human first name for onboarding — not email local-part."""
    first = [
        "James", "Oliver", "Noah", "Liam", "Ethan", "Mason", "Logan", "Lucas",
        "Emma", "Olivia", "Ava", "Sophia", "Mia", "Harper", "Amelia", "Evelyn",
        "Marcus", "Elena", "Nathan", "Claire", "Owen", "Grace", "Caleb", "Nora",
    ]
    return random.choice(first)


def session_ok_path(ud: Path) -> Path:
    return ud / SESSION_OK_NAME


def touch_session_ok(ud: Path) -> None:
    try:
        ud.mkdir(parents=True, exist_ok=True)
        session_ok_path(ud).touch()
    except Exception:
        pass


def flush_session(ctx, page, ud: Path, *, home_nav: bool = True) -> dict:
    """Best-effort cookie/storage flush; optional home navigate (before nurture).

    Before ctx.close, call with home_nav=False after hang_before_close (storage + short wait).
    """
    out: dict = {"storage_state": False, "home_nav": False, "wait_ms": 0}
    wait_ms = random.randint(2000, 4000) if home_nav else random.randint(800, 2200)
    try:
        page.wait_for_timeout(wait_ms)
        out["wait_ms"] = wait_ms
    except Exception:
        out["wait_ms"] = wait_ms
    try:
        if hasattr(ctx, "storage_state"):
            state_file = ud / "playwright_storage_state.json"
            ctx.storage_state(path=str(state_file))
            out["storage_state"] = True
            out["storage_state_path"] = str(state_file)
    except Exception as e:
        out["storage_err"] = type(e).__name__
    if home_nav:
        try:
            page.goto(
                "https://www.pinterest.com/",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            page.wait_for_timeout(random.randint(1500, 3000))
            out["home_nav"] = True
        except Exception as e:
            out["home_err"] = type(e).__name__
    status = "session_flush" if home_nav else "session_flush_before_close"
    print(
        json.dumps(
            {"status": status, **{k: v for k, v in out.items() if k != "storage_state_path"}},
            ensure_ascii=False,
        ),
        flush=True,
    )
    return out

def settle_after_continue(
    page,
    *,
    settle_lo: int = 3000,
    settle_hi: int = 8000,
    poll_ms: int = 500,
    max_extra_s: float = 25.0,
) -> dict:
    """After Continue: human settle, then poll for code UI / login / Oops / toast.

    Distinguishes hard Oops vs soft verify vs success without rushing.
    """
    url0 = ""
    try:
        url0 = page.url or ""
    except Exception:
        url0 = ""
    settle_ms = human_pause(page, settle_lo, settle_hi, "after_continue_settle")
    # Prefer network idle when Playwright supports it; ignore failures
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    t0 = time.time()
    outcome = "unknown"
    body = ""
    while time.time() - t0 < max_extra_s:
        try:
            body = page.inner_text("body") or ""
        except Exception:
            body = ""
        url = ""
        try:
            url = page.url or ""
        except Exception:
            url = ""
        code_vis = False
        try:
            code_vis = page.locator("#code").count() > 0 and page.locator("#code").first.is_visible()
        except Exception:
            code_vis = False
        if "Enter the code" in body or code_vis:
            outcome = "code_ui"
            break
        if has_email_confirmed_toast(body):
            outcome = "email_confirmed_toast"
            break
        # Full-page Oops (not merely soft inline on code form)
        if "Oops" in body and not code_vis and "Enter the code" not in body:
            outcome = "oops"
            break
        cta = "Log in" in body[:1500] and "Sign up" in body[:1500]
        onboarding = ("What's your name" in body) or ("Nice to meet you" in body)
        feedish = ("Search Pinterest" in body) or ("/homefeed" in url) or ("/today" in url)
        if onboarding or (not cta and feedish):
            outcome = "logged_in"
            break
        if url != url0 and "signup" not in url and "pinterest.com" in url:
            # URL moved off signup — keep polling a bit for body signals
            pass
        page.wait_for_timeout(poll_ms)
    else:
        try:
            body = page.inner_text("body") or ""
        except Exception:
            body = ""
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
        "url": getattr(page, "url", "") or "",
        "url0": url0,
    }
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def click_send_new_code(page) -> bool:
    """Click 'Send new code' if visible. Returns True if clicked."""
    sels = [
        'button:has-text("Send new code")',
        'a:has-text("Send new code")',
        '[role="button"]:has-text("Send new code")',
        'text=Send new code',
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
                print(json.dumps({"status": "verify_retry", "action": "send_new_code", "sel": sel}), flush=True)
                return True
        except Exception:
            continue
    return False


def imap_max_uid(secrets: str) -> int:
    return int(
        subprocess.check_output(
            [
                sys.executable,
                str(ROOT / "scripts/outlook_imap_pinterest_code.py"),
                "--secrets",
                secrets,
                "--print-max-uid",
            ],
            text=True,
            cwd=str(ROOT),
        ).strip()
    )


def imap_wait_code(secrets: str, after_uid: int, timeout: int = 180) -> dict | None:
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/outlook_imap_pinterest_code.py"),
            "--secrets",
            secrets,
            "--after-uid",
            str(after_uid),
            "--timeout",
            str(timeout),
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        if proc.stderr:
            print(proc.stderr, file=sys.stderr)
        return None
    return json.loads(proc.stdout.strip().splitlines()[-1])


def load_nurture_mod():
    import importlib.util

    nurture_path = ROOT / "scripts" / "run_pinterest_nurture_browse.py"
    spec = importlib.util.spec_from_file_location(
        "run_pinterest_nurture_browse", nurture_path
    )
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def chain_nurture(page, ctx, ud: Path, profile: str, *, pins: int, min_sec: int, max_sec: int) -> dict:
    """Keep-open nurture after register success. Never raises; returns nurture fields.

    Flushes session, probes login, then browses. Does NOT close ctx.
    """
    fields = {
        "nurture_status": "skipped",
        "nurture_elapsed_s": 0,
        "nurture_liked": False,
    }
    try:
        flush = flush_session(ctx, page, ud)
        fields["session_flush"] = {
            k: flush.get(k) for k in ("storage_state", "home_nav", "wait_ms") if k in flush
        }
        mod = load_nurture_mod()
        if mod is None:
            fields["nurture_status"] = "import_failed"
            return fields

        # Brief keepalive probe before counting any browse success
        probe = None
        if hasattr(mod, "session_keepalive_probe"):
            probe = mod.session_keepalive_probe(page)
        elif hasattr(mod, "detect_login_state"):
            gate = mod.detect_login_state(page)
            probe = {"ok": gate == "ok", "gate": gate, "url": page.url, "probe": "detect_login_state"}
        if probe is not None:
            print(json.dumps({"status": "session_keepalive_probe", **probe}, ensure_ascii=False), flush=True)
            fields["session_keepalive_probe"] = probe
            if not probe.get("ok"):
                gate = probe.get("gate") or "not_logged_in"
                fields["nurture_status"] = "session_lost_before_nurture"
                fields["session_lost_gate"] = gate
                # Do not count as browsed_ok
                return fields

        run_art = ROOT / "artifacts/pinterest/nurture-browse" / f"{profile}-run"
        print(
            json.dumps(
                {
                    "status": "nurture_start",
                    "profile": profile,
                    "mode": "keep_open",
                    "pins": pins,
                    "version": VERSION,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        # navigate=False: flush already went home once; nurture still clears NUX
        result = mod.run_nurture_session(
            page,
            profile=profile,
            run_art=run_art,
            pins=pins,
            min_sec=min_sec,
            max_sec=max_sec,
            navigate=True,  # go home / clear NUX after register (idempotent)
        )
        st = result.get("status") or "like_failed"
        # If nurture itself saw login wall immediately, normalize
        if st in ("not_logged_in", "account_deactivated") and fields.get("session_keepalive_probe"):
            # probe passed but later lost — keep nurture status from result
            pass
        fields["nurture_status"] = st
        fields["nurture_elapsed_s"] = result.get("elapsed_sec") or 0
        fields["nurture_liked"] = bool(result.get("liked"))
        if result.get("pins_opened") is not None:
            fields["nurture_pins_opened"] = result.get("pins_opened")
        if result.get("like"):
            fields["nurture_like"] = result.get("like")
        print(
            json.dumps(
                {
                    "status": "nurture_done",
                    "nurture_status": fields["nurture_status"],
                    "nurture_elapsed_s": fields["nurture_elapsed_s"],
                    "nurture_liked": fields["nurture_liked"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    except Exception as e:
        fields["nurture_status"] = f"error:{type(e).__name__}"
        fields["nurture_error"] = str(e)[:200]
        print(
            json.dumps(
                {
                    "status": "nurture_error",
                    "err": type(e).__name__,
                    "message": str(e)[:200],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    return fields


def maybe_nurture(page, ctx, ud: Path, args, register_payload: dict) -> dict:
    """Attach nurture fields; preserve register status. Mandatory unless --skip-nurture."""
    out = dict(register_payload)
    if getattr(args, "skip_nurture", False):
        print(
            json.dumps(
                {
                    "status": "nurture_skip_warning",
                    "warning": (
                        "skip_nurture set — closing without same-session nurture. "
                        "Independent nurture minutes later is NOT the primary path; "
                        "server may revoke session (cookies on disk ≠ alive)."
                    ),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        out["nurture_status"] = "skipped"
        out["nurture_elapsed_s"] = 0
        out["nurture_liked"] = False
        out["nurture_skip_warning"] = True
        return out
    # Mark ops success before nurture so --fresh-profile cannot wipe a live account
    touch_session_ok(ud)
    fields = chain_nurture(
        page,
        ctx,
        ud,
        args.profile,
        pins=getattr(args, "nurture_pins", 3),
        min_sec=getattr(args, "nurture_min_sec", 120),
        max_sec=getattr(args, "nurture_max_sec", 180),
    )
    out.update(fields)
    return out


def submit_verify_code(page, code: str) -> dict:
    """Fill code, paced Continue, settle; return diagnostic dict."""
    val = fill_code_react(page, code)
    match = val == code
    print(
        json.dumps(
            {
                "status": "code_filled",
                "len": len(code),
                "value_len": len(val),
                "value_match": match,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if not match:
        print(
            json.dumps(
                {
                    "status": "code_value_mismatch",
                    "len": len(code),
                    "value_len": len(val),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return {
            "status": "verify_soft_fail",
            "still_code_ui": True,
            "login_signup_cta": False,
            "has_oops": False,
            "error_snip": "code_value_mismatch",
            "url": getattr(page, "url", "") or "",
            "code_value_len": len(val),
            "value_match": False,
        }
    human_pause(page, 1200, 2800, "after_code_fill")
    art = ROOT / "artifacts/pinterest"
    art.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(art / "run-02b-code-filled.png"))

    cont, cont_sel = find_verify_continue(page)
    print(json.dumps({"status": "continue_locator", "matched": cont_sel}), flush=True)
    cont.wait_for(state="visible", timeout=15000)
    continue_enabled_before = wait_continue_enabled(cont, timeout_ms=15000)
    print(
        json.dumps({"status": "continue_ready", "enabled": continue_enabled_before}),
        flush=True,
    )
    human_pause(page, 2000, 5000, "before_verify_continue")
    clk = _hclick(page, cont)
    if not clk.get("ok"):
        print(
            json.dumps(
                {
                    "status": "click_failed",
                    "where": "verify_continue",
                    "method": clk.get("method"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return {
            "status": "verify_soft_fail",
            "still_code_ui": True,
            "login_signup_cta": False,
            "has_oops": False,
            "error_snip": None,
            "url": getattr(page, "url", "") or "",
            "continue_enabled_before_click": continue_enabled_before,
            "code_value_len": len(val),
            "continue_matched": cont_sel,
            "click_failed": clk.get("method"),
        }
    print(json.dumps({"status": "continue_clicked"}), flush=True)

    settle = settle_after_continue(page, settle_lo=3000, settle_hi=8000, max_extra_s=20.0)
    page.screenshot(path=str(art / "run-03-after-code.png"))
    try:
        body2 = page.inner_text("body") or ""
    except Exception:
        body2 = ""
    still = "Enter the code" in body2
    try:
        still_code_el = page.locator("#code").count() > 0 and page.locator("#code").first.is_visible()
    except Exception:
        still_code_el = False
    still = still or still_code_el
    cta = "Log in" in body2[:1200] and "Sign up" in body2[:1200]
    toast_ok = has_email_confirmed_toast(body2) or settle.get("outcome") == "email_confirmed_toast"
    # Hard Oops: full-page / no code UI
    has_oops = ("Oops" in body2) and (not still) and (not toast_ok)
    if settle.get("outcome") == "oops" and not still and not toast_ok:
        has_oops = True
    err = snip_error(body2)
    soft = soft_verify_error(body2, still) and not toast_ok

    if toast_ok and (still or cta):
        # Toast says confirmed — treat as success candidate; try dismiss
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
        "url": page.url,
        "continue_enabled_before_click": continue_enabled_before,
        "code_value_len": len(val),
        "continue_matched": cont_sel,
        "settle_outcome": settle.get("outcome"),
        "email_confirmed_toast": toast_ok,
        "soft_verify": soft,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--secrets", default=str(ROOT / "data/secrets/pinterest-outlook-01.env"))
    ap.add_argument("--profile", default="geo02")
    ap.add_argument(
        "--fresh-profile",
        action="store_true",
        help=(
            "Wipe user_data_dir BEFORE launch only (start of a register attempt). "
            "Refused if .cloak_session_ok exists (prior register success). "
            "NEVER use after a successful register."
        ),
    )
    ap.add_argument("--headless", action="store_true")
    ap.add_argument(
        "--skip-nurture",
        action="store_true",
        help=(
            "Do not chain nurture before ctx.close (NOT recommended). "
            "Logs a warning; independent nurture later often hits not_logged_in."
        ),
    )
    ap.add_argument(
        "--nurture-pins",
        type=int,
        default=0,
        help="Nurture pins to open (0=persona chooses 1–12)",
    )
    ap.add_argument("--nurture-min-sec", type=int, default=120)
    ap.add_argument("--nurture-max-sec", type=int, default=180)
    args = ap.parse_args()
    headed = not args.headless

    env = load_env(Path(args.secrets))
    email = env["PINTEREST_EMAIL"]
    password = env["PINTEREST_PASSWORD"]
    bday, age = random_birthday()
    print(
        json.dumps(
            {
                "profile": args.profile,
                "email_domain": email.split("@")[-1],
                "birthday": bday,
                "age": age,
                "headed": headed,
                "version": VERSION,
                "skip_nurture": bool(args.skip_nurture),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    base_uid = imap_max_uid(args.secrets)

    from cloakbrowser import launch_persistent_context
    from cloakcli_worker.fingerprint import (  # noqa: E402
        ensure_fingerprint_seed,
        fingerprint_chrome_args,
        log_fingerprint_seed,
    )

    meta_path = ROOT / "profiles" / args.profile / "profile.json"
    meta = json.loads(meta_path.read_text())
    proxy = meta.get("proxy")
    ud = ROOT / "data/profiles" / f"{args.profile}-pinterest-run"
    if getattr(args, "fresh_profile", False):
        if ud.exists() and session_ok_path(ud).exists():
            print(
                json.dumps(
                    {
                        "status": "fresh_profile_refused",
                        "reason": (
                            f"{SESSION_OK_NAME} present — prior register success. "
                            "--fresh-profile only at start of a new attempt, never after success."
                        ),
                        "user_data_dir": str(ud),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return 6
        if ud.exists():
            shutil.rmtree(ud)
        # Void prior fingerprint when wiping user_data_dir
        ensure_fingerprint_seed(meta_path, regenerate=True)
    ud.mkdir(parents=True, exist_ok=True)
    seed = ensure_fingerprint_seed(meta_path)
    log_fingerprint_seed(seed)
    kwargs = {
        "user_data_dir": str(ud),
        "headless": not headed,
        "args": fingerprint_chrome_args(seed),
    }
    if proxy:
        kwargs["proxy"] = proxy
    ctx = launch_persistent_context(**kwargs)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    art = ROOT / "artifacts/pinterest"
    art.mkdir(parents=True, exist_ok=True)

    try:
        reset_session_mouse()
        page.goto("https://www.pinterest.com/signup/", wait_until="domcontentloaded", timeout=90000)
        quiet_window(page)
        page.wait_for_selector("#email", timeout=30000)

        # Human pacing between form fields (800–2500ms log-normal)
        email_loc = page.locator("#email").first
        typed_email = human_type_text(page, email_loc, email, mouse=session_mouse())
        if not typed_email.get("focus_ok"):
            print(
                json.dumps(
                    {"status": "click_failed", "where": "email", "method": typed_email.get("focus")},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return 5
        human_pause(page, 800, 2500, "between_email_password")
        pw_loc = page.locator("#password").first
        typed_pw = human_type_text(page, pw_loc, password, mouse=session_mouse())
        if not typed_pw.get("focus_ok"):
            print(
                json.dumps(
                    {"status": "click_failed", "where": "password", "method": typed_pw.get("focus")},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return 5
        human_pause(page, 800, 2500, "between_password_birthdate")
        bday_loc = page.locator("#birthdate").first
        bday_clk = _hclick(page, bday_loc)
        if not bday_clk.get("ok"):
            print(
                json.dumps(
                    {"status": "click_failed", "where": "birthdate", "method": bday_clk.get("method")},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return 5
        page.fill("#birthdate", bday)  # type=date
        human_pause(page, 800, 2500, "after_birthdate")
        # blur so React validators enable Continue
        try:
            page.locator("#birthdate").blur()
        except Exception:
            page.keyboard.press("Tab")
        human_pause(page, 1200, 2800, "after_birthdate_blur")
        page.screenshot(path=str(art / "run-01-filled.png"))

        # Prefer signup-form Continue (not "Continue with Google")
        form_btns = page.locator("form:has(#email) button:has-text('Continue')").filter(
            has_not_text="Google"
        )
        signup_cont = form_btns.first if form_btns.count() else page.locator(
            "button:has-text('Continue')"
        ).filter(has_not_text="Google").first
        signup_cont.wait_for(state="visible", timeout=15000)
        wait_continue_enabled(signup_cont, timeout_ms=10000)
        human_pause(page, 2000, 5000, "before_signup_continue")
        clk = _hclick(page, signup_cont)
        if not clk.get("ok"):
            print(
                json.dumps(
                    {
                        "status": "click_failed",
                        "where": "signup_continue",
                        "method": clk.get("method"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return 5
        print(json.dumps({"status": "signup_continue_clicked"}), flush=True)

        settle = settle_after_continue(page, settle_lo=3000, settle_hi=8000, max_extra_s=22.0)
        page.screenshot(path=str(art / "run-02-after-continue.png"))
        try:
            body = page.inner_text("body") or ""
        except Exception:
            body = ""

        if settle.get("outcome") == "oops" or (
            "Oops" in body and "Enter the code" not in body and page.locator("#code").count() == 0
        ):
            print(json.dumps({"status": "oops_blocked", "note": "park_no_recontinue"}), flush=True)
            return 2

        code_ui = settle.get("outcome") == "code_ui" or (
            "Enter the code" in body
            or (page.locator("#code").count() > 0 and page.locator("#code").first.is_visible())
        )
        logged_in = settle.get("outcome") in ("logged_in", "email_confirmed_toast")
        if not code_ui and not logged_in:
            # Still on form after settle — ONE carefully paced retry only (no spam)
            still_form = False
            try:
                still_form = page.locator("#email").count() > 0 and page.locator("#email").first.is_visible()
            except Exception:
                still_form = False
            if still_form and "Oops" not in body:
                human_pause(page, 3000, 6000, "before_signup_continue_one_retry")
                try:
                    wait_continue_enabled(signup_cont, timeout_ms=5000)
                    retry_clk = _hclick(page, signup_cont)
                    if not retry_clk.get("ok"):
                        print(
                            json.dumps(
                                {
                                    "status": "click_failed",
                                    "where": "signup_continue_retry",
                                    "method": retry_clk.get("method"),
                                },
                                ensure_ascii=False,
                            ),
                            flush=True,
                        )
                    else:
                        print(json.dumps({"status": "signup_continue_retry_once"}), flush=True)
                    settle = settle_after_continue(
                        page, settle_lo=3000, settle_hi=8000, max_extra_s=20.0
                    )
                    page.screenshot(path=str(art / "run-02-after-continue.png"))
                    body = page.inner_text("body") or ""
                except Exception:
                    pass
                if settle.get("outcome") == "oops" or (
                    "Oops" in body and "Enter the code" not in body
                ):
                    print(json.dumps({"status": "oops_blocked", "note": "park_after_one_retry"}), flush=True)
                    return 2
                code_ui = settle.get("outcome") == "code_ui" or "Enter the code" in body
                logged_in = settle.get("outcome") in ("logged_in", "email_confirmed_toast")

        if "Oops" in body and not code_ui:
            print(json.dumps({"status": "oops_blocked"}), flush=True)
            return 2

        if logged_in and not code_ui:
            display_name = None
            try:
                if "What's your name" in body or "Nice to meet you" in body:
                    display_name = random_display_name()
                    name_loc = page.locator(
                        'input[name="name"], input[id="name"], input[aria-label="Name"], '
                        '[data-test-id="name-input"] input, label:has-text("Name") ~ input, '
                        'label:has-text("Name") + input'
                    )
                    if name_loc.count() == 0:
                        name_loc = page.get_by_label("Name")
                    else:
                        name_loc = name_loc.first
                    name_loc.wait_for(state="visible", timeout=8000)
                    human_type_text(page, name_loc, display_name, mouse=session_mouse())
                    human_pause(page, 800, 2000, "after_onboarding_name")
                    print(
                        json.dumps(
                            {"status": "onboarding_name_set", "name_len": len(display_name)},
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    page.screenshot(path=str(art / "run-02b-onboarding-name.png"))
            except Exception as e:
                print(
                    json.dumps({"status": "onboarding_name_skip", "err": type(e).__name__}),
                    flush=True,
                )
            try:
                body = page.inner_text("body") or ""
            except Exception:
                body = ""
            cta = "Log in" in body[:1500] and "Sign up" in body[:1500]
            page.screenshot(path=str(art / "run-03-after-code.png"))
            payload = {
                "status": "ok" if not cta else "code_submitted_check_ui",
                "still_code_ui": False,
                "login_signup_cta": cta,
                "has_oops": False,
                "error_snip": None,
                "url": page.url,
                "continue_enabled_before_click": None,
                "code_value_len": 0,
                "path": "signup_ok_no_code_challenge",
                "birthday": bday,
                "display_name": display_name,
                "version": VERSION,
            }
            if not cta:
                # Do not ctx.close until nurture finishes (or explicit skip)
                payload = maybe_nurture(page, ctx, ud, args, payload)
            print(json.dumps(payload, ensure_ascii=False), flush=True)
            return 0 if not cta else 5

        if not code_ui and "Enter the code" not in body:
            print(
                json.dumps(
                    {"status": "unexpected_after_continue", "snip": body[:350]},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return 3

        hit = imap_wait_code(args.secrets, base_uid, timeout=180)
        if hit is None:
            print(json.dumps({"status": "imap_timeout"}), flush=True)
            return 4
        code = hit["code"]
        print(
            json.dumps(
                {
                    "status": "code_received",
                    "subject": hit.get("subject"),
                    "code_len": len(code),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

        payload = submit_verify_code(page, code)
        payload["birthday"] = bday
        payload["version"] = VERSION

        # Soft verify: one Send-new-code retry (cap 1). Oops: park, no spam.
        if payload.get("status") == "verify_soft_oops":
            human_pause(page, 2000, 4000, "before_soft_verify_retry")
            if click_send_new_code(page):
                human_pause(page, 2500, 5000, "after_send_new_code")
                try:
                    new_uid = imap_max_uid(args.secrets)
                except Exception:
                    new_uid = base_uid
                hit2 = imap_wait_code(args.secrets, new_uid, timeout=180)
                if hit2 is None:
                    payload["status"] = "verify_soft_fail"
                    payload["verify_retry"] = "imap_timeout"
                    print(json.dumps(payload, ensure_ascii=False), flush=True)
                    return 5
                code2 = hit2["code"]
                print(
                    json.dumps(
                        {
                            "status": "code_received",
                            "verify_retry": True,
                            "subject": hit2.get("subject"),
                            "code_len": len(code2),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                payload = submit_verify_code(page, code2)
                payload["birthday"] = bday
                payload["version"] = VERSION
                payload["verify_retry"] = True
                if payload.get("status") == "verify_soft_oops":
                    payload["status"] = "verify_soft_fail"
            else:
                payload["status"] = "verify_soft_fail"
                payload["verify_retry"] = "send_new_code_not_found"

        if payload.get("status") == "ok":
            payload = maybe_nurture(page, ctx, ud, args, payload)
            print(json.dumps(payload, ensure_ascii=False), flush=True)
            return 0
        print(json.dumps(payload, ensure_ascii=False), flush=True)
        if payload.get("status") == "oops_blocked":
            return 2
        return 5
    finally:
        # Lifecycle: nurture (if any) already finished; hang + flush, then close
        try:
            hang_before_close(page, session_mouse())
        except Exception:
            pass
        try:
            flush_session(ctx, page, ud, home_nav=False)
        except Exception:
            pass
        try:
            ctx.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
