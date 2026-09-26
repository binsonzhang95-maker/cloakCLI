#!/usr/bin/env python3
"""Pinterest nurture browse (0.2.6).

Logged-in feed browse with behavior hardening (Gemini 2026-09-21 + 2026-09-23):
default headed, continuous randomized mouse trails, inertial scroll, session
personas (browse_only / light_like / deep_browse / bounce_early),
log-normal/gamma pauses (independently re-sampled at every site), quiet window
after load, longer pin linger, visibility keepalive, occasional micro reverse
scroll, mixed close paths, zero-pin feed bounce + browsed_ok gate.
CloakBrowser persistent context; one account ↔ one geo/proxy. Use persisted
fingerprint_seed from profile.json; do not randomize per launch.

At session start, clears name onboarding ("What's your name") then use-case
picker if present. 0.2.6: before like_failed/no_pin_links, re-run login gate
(unauth → not_logged_in), blank-paint wait+reload once, re-clear name/gender/
use-case NUX with broader picker detection (≥3 tiles via _hclick). Does NOT
wipe user_data_dir. Does NOT attempt login / credential recovery (dead-account
audit owns that). Emits fleet status JSON as last stdout line.

Statuses: browsed_ok | not_logged_in | like_failed | account_deactivated | session_lost_before_nurture (register chain)
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from pathlib import Path

# Skill package may live under skills/...; CloakCLI root is parents[3] or parents[1]
_HERE = Path(__file__).resolve()
if _HERE.parent.name == "scripts" and _HERE.parents[1].name == "pinterest-nurture-browse":
    ROOT = _HERE.parents[3]  # skills/pinterest-nurture-browse/scripts -> CloakCLI
else:
    ROOT = _HERE.parents[1]  # scripts/ at repo root

if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))
_py = ROOT / "python"
if str(_py) not in sys.path:
    sys.path.insert(0, str(_py))

from pinterest_nurture_behavior import (  # noqa: E402
    PERSONA_NAMES,
    browsed_ok,
    choose_close_path,
    close_path_fallback_order,
    ensure_page_visible,
    human_click_locator,
    human_move_to,
    human_type_text,
    inertial_scroll,
    hang_before_close,
    maybe_micro_reverse_scroll,
    plan_nurture_session,
    plan_pin_linger,
    play_ambient_drift,
    reset_session_mouse,
    sample_esc_key_hold_ms,
    sample_gamma_ms,
    sample_lognormal_ms,
    sample_pause_ms,
    sample_quiet_window_ms,
    scroll_plan_down_magnitude,
    session_mouse,
)

ART = ROOT / "artifacts/pinterest/nurture-browse"
VERSION = "0.2.6"

# Draft selectors — refine after a healthy logged-in probe
PIN_LINK = 'a[href*="/pin/"]'
PIN_CARD_CANDIDATES = [
    '[data-test-id="pin"]',
    '[data-test-id="pinWrapper"]',
    '[data-test-id="pinrep-image"]',
    '[data-test-id="non-story-pin-image"]',
    '[data-test-id="basil-pinwrapper"]',
    PIN_LINK,
]
CLOSE_CANDIDATES = [
    # Verified 2026-09-19 CST geo46 closeup:
    '[data-test-id="back-icon-button"]',
    '[data-test-id="back-button"] button',
    '[data-test-id="closeup-back-button"]',
    '[data-test-id="closeup-close-button"]',
    'button[aria-label="Back"]',
    'button[aria-label="Close"]',
    '[aria-label="Close"]',
]
LIKE_CANDIDATES = [
    # Verified 2026-09-19 CST geo46 closeup: BUTTON data-test-id=react-button aria=React
    '[data-test-id="react-button"]',
    'button[data-test-id="react-button"]',
    '[data-test-id="pin-reaction-button"]',
    '[data-test-id="reaction-button"]',
    'button[aria-label="React"]',
    'button[aria-label*="React" i]',
    'button[aria-label*="reaction" i]',
    'button[aria-label*="Like" i]',
]
UNAUTH_SELS = (
    '[data-test-id="unauth-header"], '
    '[data-test-id="simple-login-button"], '
    '[data-test-id="simple-signup-button"]'
)
ACCT_SELS = (
    '[data-test-id="header-accounts-options-button"], '
    '[data-test-id="header-profile"]'
)


def log(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def pause(page, lo_ms: int, hi_ms: int, label: str = "", *, ambient: bool = False) -> int:
    """Heavy-tailed pause (log-normal). Optional ambient mouse drift while reading.

    Every call re-samples duration independently (no fixed chains across steps).
    Long pauses may soft-check page visibility (orthogonal to hang/cookies).
    """
    if hi_ms < lo_ms:
        lo_ms, hi_ms = hi_ms, lo_ms
    lo_ms = max(0, int(lo_ms))
    hi_ms = max(lo_ms, int(hi_ms))
    ms = sample_pause_ms(lo_ms, hi_ms)
    log({"pause_ms": ms, "label": label})
    # Periodic soft visibility check during long pauses (independent of hang).
    if ms >= 2500 and random.random() < 0.45:
        try:
            ensure_page_visible(page, log_fn=log)
        except Exception:
            pass
    if ambient and ms >= 500 and random.random() < 0.6:
        budget = min(ms // 3, 900)
        try:
            play_ambient_drift(page, session_mouse(), budget_ms=budget)
        except Exception:
            pass
        remain = ms - budget
        if remain > 0:
            page.wait_for_timeout(remain)
    else:
        page.wait_for_timeout(ms)
    return ms


def quiet_window(page) -> int:
    """No pointer/key events until TTI quiet window elapses."""
    ms = sample_quiet_window_ms()
    log({"pause_ms": ms, "label": "quiet_window"})
    page.wait_for_timeout(ms)
    return ms


def _hclick(page, loc) -> dict:
    result = human_click_locator(page, loc, session_mouse())
    if result.get("hover_ms"):
        log({"pause_ms": result["hover_ms"], "label": "hover_before_click"})
    if not result.get("ok"):
        log({"human_click": result})
    return result


def body_text(page, n: int = 2500) -> str:
    try:
        return (page.inner_text("body") or "")[:n]
    except Exception as e:
        return f"<body_err:{type(e).__name__}>"


def emit_status(status: str, **extra) -> None:
    """Last non-empty stdout line must be the report object for fleet."""
    report = {"status": status, **extra}
    print(json.dumps(report, ensure_ascii=False), flush=True)


def page_looks_blank(page) -> dict:
    """Heuristic for blank/white incomplete paint (e.g. ~3KB white home).

    Returns {blank: bool, body_len: int, pins: int, reason?: str}.
    Pure signal helper — does not navigate or click.
    """
    out: dict = {"blank": False, "body_len": 0, "pins": 0}
    try:
        b = body_text(page, 4000)
    except Exception:
        b = ""
    out["body_len"] = len((b or "").strip())
    try:
        out["pins"] = int(page.locator(PIN_LINK).count())
    except Exception:
        out["pins"] = 0
    # Strip common whitespace / zero-width; treat near-empty as blank paint.
    compact = re.sub(r"\s+", "", b or "")
    if out["pins"] > 0:
        return out
    if out["body_len"] < 40 or len(compact) < 24:
        out["blank"] = True
        out["reason"] = "short_body"
        return out
    # White / loading shells often only expose tiny chrome strings.
    if out["body_len"] < 120 and not re.search(
        r"Log in|Sign up|Pinterest|pin/|mood|interest|identify|Continue",
        b or "",
        re.I,
    ):
        out["blank"] = True
        out["reason"] = "sparse_shell"
    return out


def _unauth_cta_signals(page, body: str) -> dict:
    """Detect logged-out marketing / signup wall (broader than one test-id)."""
    b = body or ""
    head = b[:2000]
    unauth = 0
    acct = 0
    try:
        unauth = page.locator(UNAUTH_SELS).count()
        acct = page.locator(ACCT_SELS).count()
    except Exception:
        pass
    pins = 0
    try:
        pins = page.locator(PIN_LINK).count()
    except Exception:
        pass
    has_login = bool(re.search(r"\bLog in\b", head))
    has_signup = bool(re.search(r"\bSign up\b", head))
    has_cta = has_login and has_signup
    marketing = bool(
        re.search(
            r"Create a free account|Already a member|Welcome to Pinterest|"
            r"Sign up to get|Log in to get|birthday|When.?s your birthday|"
            r"Continue as guest",
            head,
            re.I,
        )
    )
    url = ""
    try:
        url = page.url or ""
    except Exception:
        url = ""
    url_unauth = bool(re.search(r"/login|/signup|/sec/|"
                                 r"pinterest\.com/?$", url, re.I)) and has_cta
    return {
        "unauth_sels": unauth,
        "acct": acct,
        "pins": pins,
        "has_cta": has_cta,
        "has_login": has_login,
        "has_signup": has_signup,
        "marketing": marketing,
        "url_unauth": url_unauth,
        "url": url,
    }


def detect_login_state(page) -> str:
    """Return browsed_ok-path gate: ok | not_logged_in | account_deactivated.

    Positive logged-in evidence preferred (acct header / pin links). Blank paint
    with zero signals is NOT treated as logged-in (avoids false ok → like_failed).
    """
    b = body_text(page, 3000)
    if re.search(r"account has been deactivated|your account has been deactivated", b, re.I):
        return "account_deactivated"
    try:
        if page.locator('text=/account has been deactivated/i').count():
            return "account_deactivated"
    except Exception:
        pass
    sig = _unauth_cta_signals(page, b)
    unauth = int(sig["unauth_sels"])
    acct = int(sig["acct"])
    pins = int(sig["pins"])
    has_cta = bool(sig["has_cta"])
    marketing = bool(sig["marketing"])
    blank = page_looks_blank(page)
    # Strong unauth: DOM CTA or Log in+Sign up with no account chrome.
    if unauth > 0 or (has_cta and acct == 0 and pins < 3):
        return "not_logged_in"
    # Marketing / birthday wall without account chrome or pins (i10-021 class).
    if marketing and acct == 0 and pins < 1 and not has_cta:
        # birthday Continue alone can appear on NUX too; require login-ish words
        if sig["has_login"] or sig["has_signup"] or unauth > 0:
            return "not_logged_in"
        if re.search(r"\bLog in\b|\bSign up\b|Create a free account|Already a member", b, re.I):
            return "not_logged_in"
    if acct > 0 or pins >= 3:
        return "ok"
    if has_cta and acct == 0:
        return "not_logged_in"
    # Blank / incomplete paint with zero unauth CTA: defer to recover_empty_feed
    # (wait + reload once) instead of hard not_logged_in / false ok→like_failed.
    if blank.get("blank") and acct == 0 and pins < 1:
        return "ok"
    # Ambiguous but some body content and no unauth CTA → allow browse path.
    if not has_cta:
        return "ok"
    return "not_logged_in"


def session_keepalive_probe(page) -> dict:
    """Brief pre-nurture check: still logged-in vs login wall / deactivated.

    Call this before counting any browse success. If the server session was
    revoked (cookies on disk but dead), gate is not_logged_in / deactivated.
    Returns {ok, gate, url, probe}.
    """
    gate = detect_login_state(page)
    url = ""
    try:
        url = page.url or ""
    except Exception:
        url = ""
    return {
        "ok": gate == "ok",
        "gate": gate,
        "url": url,
        "probe": "session_keepalive_probe",
    }


def dismiss_light(page) -> None:
    for _ in range(2):
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(250)
        except Exception:
            pass




NAME_ONBOARD_MARKERS = (
    "What's your name",
    "Nice to meet you",
)
NAME_INPUT_SELS = (
    'input[name="name"]',
    'input[id="name"]',
    'input[aria-label="Name"]',
    '[data-test-id="name-input"] input',
    'label:has-text("Name") ~ input',
    'label:has-text("Name") + input',
)
_FIRST_NAMES = (
    "James", "Oliver", "Noah", "Liam", "Ethan", "Mason", "Logan", "Lucas",
    "Emma", "Olivia", "Ava", "Sophia", "Mia", "Harper", "Amelia", "Evelyn",
    "Marcus", "Elena", "Nathan", "Claire", "Owen", "Grace", "Caleb", "Nora",
)


def _random_display_name() -> str:
    return random.choice(_FIRST_NAMES)


def name_onboarding_visible(page) -> bool:
    try:
        body = body_text(page, 2500)
    except Exception:
        body = ""
    if any(m in body for m in NAME_ONBOARD_MARKERS):
        return True
    try:
        if page.locator('text=/What\'?s your name/i').count():
            return True
        if page.locator('text=/Nice to meet you/i').count():
            return True
    except Exception:
        pass
    return False


def complete_name_onboarding(page, run_art: Path | None = None) -> dict:
    """Clear Pinterest NUX "Nice to meet you! What's your name?" if present.

    Register may fill a name without clicking Continue; nurture reopen then
    hits this wall and cannot reach feed / like. Fill a human first name,
    click Continue, then hand off to use-case picker.
    """
    out: dict = {"seen": False, "filled": False, "continued": False}
    if not name_onboarding_visible(page):
        return out
    out["seen"] = True
    out["step_title"] = _onboarding_step_title(page) if "_onboarding_step_title" in globals() else "What's your name"
    try:
        if run_art is not None:
            try:
                page.screenshot(path=str(run_art / "01a-name-onboarding.png"))
            except Exception:
                pass
        pause(page, 600, 1400, "name_onboard_settle")
        name_loc = None
        for sel in NAME_INPUT_SELS:
            try:
                loc = page.locator(sel)
                if loc.count() and loc.first.is_visible(timeout=500):
                    name_loc = loc.first
                    break
            except Exception:
                continue
        if name_loc is None:
            try:
                name_loc = page.get_by_label("Name")
                if not name_loc.count():
                    name_loc = None
                else:
                    name_loc = name_loc.first
            except Exception:
                name_loc = None
        if name_loc is None:
            out["error"] = "name_input_missing"
            return out

        display = _random_display_name()
        try:
            cur = (name_loc.input_value(timeout=1000) or "").strip()
        except Exception:
            cur = ""
        # Replace empty / email-local / long garbage; keep a short human name
        looks_bad = (
            (not cur)
            or ("@" in cur)
            or (len(cur) > 24)
            or any(ch.isdigit() for ch in cur)
            or cur.lower().endswith((".com", ".net", "hotmail", "outlook", "gmail"))
            or (len(cur) > 12 and " " not in cur and cur == cur.lower())
        )
        if looks_bad:
            typed = human_type_text(page, name_loc, display, mouse=session_mouse())
            out["filled"] = True
            out["name_len"] = len(display)
            out["typed"] = typed
        else:
            out["kept_existing"] = True
        pause(page, 400, 900, "name_after_type")

        # Continue button on name step (not Google)
        btn = page.locator('button:has-text("Continue")').filter(
            has_not_text="Google"
        )
        clicked = False
        for _ in range(8):
            try:
                if not btn.count():
                    break
                b = btn.first
                disabled = False
                try:
                    disabled = b.is_disabled(timeout=300)
                except Exception:
                    pass
                if disabled:
                    page.wait_for_timeout(400)
                    continue
                pause(page, 400, 900, "name_before_continue")
                clk = _hclick(page, b)
                clicked = bool(clk.get("ok"))
                out["continued"] = clicked
                if clicked:
                    pause(page, 2000, 4000, "name_after_continue")
                    break
            except Exception:
                page.wait_for_timeout(400)
        if not clicked:
            # text / role fallback
            try:
                _hclick(page, page.get_by_role("button", name="Continue"))
                out["continued"] = True
                pause(page, 2000, 4000, "name_continue_fallback")
            except Exception as e:
                out["continue_error"] = type(e).__name__

        if run_art is not None:
            try:
                page.screenshot(path=str(run_art / "01b-after-name-onboarding.png"))
            except Exception:
                pass
        # If still on name wall, note it
        if name_onboarding_visible(page):
            out["still_visible"] = True
    except Exception as e:
        out["error"] = f"{type(e).__name__}:{str(e)[:160]}"
    return out


GENDER_ONBOARD_MARKERS = ("How do you identify",)
GENDER_CHOICES = ("Female", "Male", "Other")


def _gender_button(page, label: str):
    """Return a visible exact-label gender button, if one is present."""
    pattern = re.compile(rf"^\s*{re.escape(label)}\s*$", re.I)
    try:
        loc = page.get_by_role("button", name=pattern)
        if loc.count() and loc.first.is_visible(timeout=500):
            return loc.first
    except Exception:
        pass
    for sel in (
        f'button:has-text("{label}")',
        f'[role="button"]:has-text("{label}")',
    ):
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 8)):
                candidate = loc.nth(i)
                if not candidate.is_visible(timeout=500):
                    continue
                text = (candidate.inner_text(timeout=500) or "").strip()
                if text.casefold() == label.casefold():
                    return candidate
        except Exception:
            continue
    return None


def gender_onboarding_visible(page) -> bool:
    try:
        body = body_text(page, 2500)
        if any(marker.casefold() in body.casefold() for marker in GENDER_ONBOARD_MARKERS):
            return True
    except Exception:
        pass
    return any(_gender_button(page, label) is not None for label in GENDER_CHOICES)


def _onboarding_step_title(page) -> str:
    body = body_text(page, 2500)
    for pattern in (
        r"How do you identify\??",
        r"What are you in the mood to do\??",
        r"Nice to meet you[^\n]{0,100}",
        r"What\s*'?s your name[^\n]{0,100}",
    ):
        match = re.search(pattern, body, re.I)
        if match:
            return re.sub(r"\s+", " ", match.group(0)).strip()[:160]
    for line in body.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line and len(line) <= 160:
            return line
    return ""


def _onboarding_progress_state(page) -> dict:
    """Read modal/progress state without assuming one Pinterest DOM version."""
    state = {"modal": False, "segments": 0, "filled": 0, "complete": False}
    try:
        raw = page.evaluate(
            """() => {
              const visible = (el) => {
                const s = getComputedStyle(el), r = el.getBoundingClientRect();
                return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
              };
              const nodes = Array.from(document.querySelectorAll(
                '[role="dialog"], [role="progressbar"], progress, [aria-valuenow], '
                '[data-test-id*="progress" i], [data-test-id*="step" i], '
                '[aria-label*="step" i], [class*="progress" i]'
              )).filter(visible);
              const dialog = nodes.find(el => el.getAttribute('role') === 'dialog');
              const progress = nodes.some(el => el.matches(
                '[role="progressbar"], progress, [aria-valuenow], [data-test-id*="progress" i], [class*="progress" i]'
              ));
              let segments = 0, filled = 0, complete = false;
              for (const el of nodes) {
                const now = Number(el.getAttribute('aria-valuenow') ?? el.value);
                const max = Number(el.getAttribute('aria-valuemax') || el.max || 100);
                if (Number.isFinite(now)) {
                  complete = complete || now >= max;
                  segments = Math.max(segments, max || 0);
                  filled = Math.max(filled, now);
                }
                const kids = Array.from(el.children);
                const marked = kids.filter(k => /active|filled|complete|done|selected|current/i.test(
                  (k.className && String(k.className)) + ' ' + (k.getAttribute('data-state') || '') + ' ' +
                  (k.getAttribute('aria-current') || '')
                ));
                if (kids.length > 1 && marked.length) {
                  segments = Math.max(segments, kids.length);
                  filled = Math.max(filled, marked.length);
                }
              }
              return { modal: !!dialog || progress, segments, filled, complete };
            }"""
        )
        if isinstance(raw, dict):
            state.update({k: raw[k] for k in state if k in raw})
    except Exception:
        pass
    if not state["modal"]:
        try:
            state["modal"] = bool(
                gender_onboarding_visible(page)
                or use_case_picker_visible(page)
                or page.locator('text=/What\'?s your name/i').count()
            )
        except Exception:
            state["modal"] = False
    state["title"] = _onboarding_step_title(page)
    state["progress_known"] = bool(state["segments"])
    if state["progress_known"] and state["filled"] >= state["segments"]:
        state["complete"] = True
    return state


def _click_onboarding_continue(page) -> dict:
    """Click an exact Continue/Next option for an unrecognized NUX step."""
    for label in ("Continue", "Next"):
        pattern = re.compile(rf"^\s*{label}\s*$", re.I)
        btn = None
        try:
            loc = page.get_by_role("button", name=pattern)
            if loc.count() and loc.first.is_visible(timeout=500):
                btn = loc.first
        except Exception:
            pass
        if btn is None:
            try:
                for selector in (
                    f'button:has-text("{label}")',
                    f'[role="button"]:has-text("{label}")',
                ):
                    loc = page.locator(selector)
                    for i in range(min(loc.count(), 8)):
                        candidate = loc.nth(i)
                        text = (candidate.inner_text(timeout=500) or "").strip()
                        if (candidate.is_visible(timeout=500) and label.casefold() in text.casefold()
                                and "google" not in text.casefold()):
                            btn = candidate
                            break
                    if btn is not None:
                        break
            except Exception:
                pass
        if btn is None:
            continue
        try:
            if btn.is_disabled(timeout=300):
                continue
        except Exception:
            pass
        try:
            clk = _hclick(page, btn)
            if clk.get("ok"):
                pause(page, 1800, 3500, "onboard_continue")
                return {"clicked": True, "label": label, "click": clk.get("method")}
            return {"clicked": False, "label": label, "error": clk.get("error") or "click_failed"}
        except Exception as e:
            return {"clicked": False, "label": label, "error": type(e).__name__}
    return {"clicked": False}


def complete_gender_onboarding(page, run_art: Path | None = None) -> dict:
    """Clear Pinterest NUX "How do you identify?" if present."""
    out: dict = {"seen": False, "choice": None, "continued": False}
    if not gender_onboarding_visible(page):
        return out
    out["seen"] = True
    out["step_title"] = _onboarding_step_title(page)
    try:
        if run_art is not None:
            try:
                page.screenshot(path=str(run_art / "01c-gender-onboarding.png"))
            except Exception:
                pass
        pause(page, 600, 1400, "gender_onboard_settle")
        available = {label: _gender_button(page, label) for label in GENDER_CHOICES}
        preferred = [label for label in ("Female", "Male") if available[label] is not None]
        if preferred:
            choice = random.choice(preferred)
        elif available["Other"] is not None:
            choice = "Other"
        else:
            out["error"] = "gender_choice_missing"
            return out
        _hclick(page, available[choice])
        out["choice"] = choice
        pause(page, 700, 1400, "gender_after_choice")
        next_step = _click_onboarding_continue(page)
        out["continued"] = bool(next_step.get("clicked"))
        if next_step.get("label"):
            out["continue_text"] = next_step["label"]
        if next_step.get("error"):
            out["continue_error"] = next_step["error"]
    except Exception as e:
        out["error"] = f"{type(e).__name__}:{str(e)[:160]}"
    finally:
        if run_art is not None:
            try:
                page.screenshot(path=str(run_art / "01d-after-gender-onboarding.png"))
            except Exception:
                pass
    return out


def complete_onboarding_progress(page, run_art: Path | None = None, max_steps: int = 8) -> dict:
    """Finish any remaining segmented onboarding modal without dismissing it."""
    out: dict = {"steps": [], "max_steps": max_steps, "complete": False}
    for step in range(1, max_steps + 1):
        state = _onboarding_progress_state(page)
        pin_count = 0
        try:
            pin_count = page.locator(PIN_LINK).count()
        except Exception:
            pass
        if state.get("complete") or (pin_count and not state.get("modal")):
            out["complete"] = True
            out["stop_reason"] = "progress_full" if state.get("complete") else "modal_gone_with_pins"
            break
        if not state.get("modal"):
            out["stop_reason"] = "no_onboarding_modal"
            break
        title = state.get("title") or f"step_{step}"
        before = f"01-onboarding-step-{step:02d}-before.png"
        after = f"01-onboarding-step-{step:02d}-after.png"
        if run_art is not None:
            try:
                page.screenshot(path=str(run_art / before))
            except Exception:
                pass
        if gender_onboarding_visible(page):
            action = "gender"
            result = complete_gender_onboarding(page, run_art=None)
        elif use_case_picker_visible(page):
            action = "use_case"
            result = complete_use_case_picker(page, run_art=None)
        elif name_onboarding_visible(page):
            action = "name"
            result = complete_name_onboarding(page, run_art=None)
        else:
            action = "continue"
            result = _click_onboarding_continue(page)
        if run_art is not None:
            try:
                page.screenshot(path=str(run_art / after))
            except Exception:
                pass
        out["steps"].append({"step": step, "title": title, "action": action, "result": result, "progress": state})
        if action == "continue" and not result.get("clicked"):
            out["stop_reason"] = "no_action"
            break
    else:
        out["stop_reason"] = "step_limit"
    out["step_titles"] = [item["title"] for item in out["steps"]]
    return out


USE_CASE_PICKER = '[data-test-id="desktop-use-case-picker"]'
USE_CASE_TILE = '[data-test-id^="use-case-tap-area-"]'
USE_CASE_CONTINUE = '[data-test-id="skip-or-continue-button"]'
USE_CASE_TEXT_HINTS = (
    r"What are you in the mood to do",
    r"pick\s*3",
    r"Pick\s*3\s*or\s*more",
    r"continue to (your )?feed",
    r"Choose your interests",
    r"Tell us what you.?re interested",
    r"Select topics",
    r"interests",
)


def use_case_picker_visible(page) -> bool:
    """Broader than single data-test-id — tiles / continue / mood text."""
    try:
        if page.locator(USE_CASE_PICKER).count():
            return True
    except Exception:
        pass
    try:
        if page.locator(USE_CASE_TILE).count():
            return True
    except Exception:
        pass
    try:
        if page.locator(USE_CASE_CONTINUE).count():
            # skip-or-continue alone is weak; require mood/pick-3 context
            body = body_text(page, 2500)
            if re.search(r"mood|interest|pick\s*3|topics|feed", body, re.I):
                return True
    except Exception:
        pass
    try:
        body = body_text(page, 2500)
        for pat in USE_CASE_TEXT_HINTS:
            if re.search(pat, body, re.I):
                return True
        if page.locator('text=/What are you in the mood to do/i').count():
            return True
        if page.locator('text=/pick 3 or more/i').count():
            return True
        if page.locator('text=/continue to your feed/i').count():
            return True
    except Exception:
        pass
    return False


def _use_case_tile_locators(page) -> list:
    """Collect clickable use-case / interest tiles (≥3 target).

    Prefer verified use-case-tap-area-* nodes. Fallback selectors are deduped by
    normalized label so the same chip is not collected twice across CSS queries.
    """
    found = []
    seen_keys: set[str] = set()

    def _tile_key(el, idx: int) -> str:
        try:
            tid = el.get_attribute("data-test-id") or ""
        except Exception:
            tid = ""
        try:
            label = (el.inner_text(timeout=400) or "").strip().casefold()[:80]
        except Exception:
            label = ""
        if tid:
            return f"tid:{tid}"
        if label:
            return f"label:{label}"
        return f"idx:{idx}"

    try:
        tiles = page.locator(USE_CASE_TILE)
        n = tiles.count()
        for i in range(n):
            el = tiles.nth(i)
            key = _tile_key(el, i)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            found.append(el)
    except Exception:
        pass
    if len(found) >= 3:
        return found
    # Fallback: role=button / clickable cards inside picker container.
    try:
        root = page.locator(USE_CASE_PICKER)
        if not root.count():
            root = page.locator('[role="dialog"]').filter(
                has_text=re.compile(r"mood|interest|pick\s*3|topics", re.I)
            )
        scope = root.first if root.count() else page
        for sel in (
            '[data-test-id*="use-case" i]',
            '[data-test-id*="interest" i]',
            '[data-test-id*="topic" i]',
            'button[aria-pressed]',
            '[role="button"][aria-pressed]',
            '[role="checkbox"]',
            '[role="option"]',
        ):
            try:
                loc = scope.locator(sel)
                for i in range(min(loc.count(), 24)):
                    el = loc.nth(i)
                    key = _tile_key(el, len(found) + i)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    found.append(el)
            except Exception:
                continue
    except Exception:
        pass
    return found


def complete_use_case_picker(page, run_art: Path | None = None) -> dict:
    """Clear Pinterest NUX "What are you in the mood to do?" if present.

    Verified 2026-09-19 CST on geo46: desktop-use-case-picker +
    use-case-tap-area-* tiles + skip-or-continue-button.
    0.2.6: broader detection (text / continue / interests) + tile fallbacks.
    Pick >=3 tiles via _hclick (never locator.click / force), then continue.
    """
    out: dict = {"seen": False, "picked": 0, "continued": False}
    try:
        if not use_case_picker_visible(page):
            return out
        out["seen"] = True
        out["step_title"] = _onboarding_step_title(page)
    except Exception:
        return out

    try:
        pause(page, 800, 1600, "use_case_settle")
        tiles = _use_case_tile_locators(page)
        n = len(tiles)
        out["tile_candidates"] = n
        # pick 3–5 distinct tiles with human pacing
        want = min(max(3, random.randint(3, 5)), n if n else 3)
        idxs = list(range(n))
        random.shuffle(idxs)
        chosen = []
        seen_labels: set[str] = set()
        click_fail = 0
        for i in idxs:
            if len(chosen) >= want:
                break
            try:
                el = tiles[i]
                try:
                    if not el.is_visible(timeout=500):
                        continue
                except Exception:
                    pass
                try:
                    label = (el.inner_text(timeout=500) or "").strip()[:40]
                except Exception:
                    label = ""
                label_key = (label or f"tile_{i}").casefold()
                if label_key in seen_labels:
                    continue
                try:
                    el.scroll_into_view_if_needed(timeout=3000)
                except Exception:
                    pass
                pause(page, 400, 1100, "use_case_before_tile")
                clk = _hclick(page, el)
                if not clk.get("ok"):
                    click_fail += 1
                    continue
                chosen.append(label or f"tile_{i}")
                seen_labels.add(label_key)
                pause(page, 600, 1400, "use_case_after_tile")
            except Exception:
                click_fail += 1
                continue
        out["picked"] = len(chosen)
        out["labels"] = chosen
        out["click_fail"] = click_fail
        log({"use_case_picked": chosen})
        if run_art is not None:
            try:
                page.screenshot(path=str(run_art / "01e-use-case-picked.png"))
            except Exception:
                pass

        # wait for continue to enable (button text changes / becomes clickable)
        btn = page.locator(USE_CASE_CONTINUE)
        clicked_continue = False
        for _ in range(12):
            try:
                if not btn.count():
                    break
                txt = (btn.first.inner_text(timeout=500) or "").lower()
                # enabled when not "pick 3 or more"
                disabled = False
                try:
                    disabled = btn.first.is_disabled(timeout=300)
                except Exception:
                    pass
                if ("continue" in txt and "pick 3" not in txt) or (
                    "continue" in txt and not disabled and out["picked"] >= 3
                ):
                    pause(page, 500, 1200, "use_case_before_continue")
                    clk = _hclick(page, btn.first)
                    out["continued"] = bool(clk.get("ok"))
                    out["continue_text"] = txt[:60]
                    if out["continued"]:
                        clicked_continue = True
                        pause(page, 2500, 4500, "use_case_after_continue")
                    break
                # Trail-only retry after enough picks even if the label still says pick.
                if out["picked"] >= 3 and _ >= 4:
                    try:
                        clk = _hclick(page, btn.first)
                        if clk.get("ok"):
                            out["continued"] = True
                            out["continue_after_pick_retry"] = True
                            clicked_continue = True
                            pause(page, 2500, 4500, "use_case_continue_after_pick_retry")
                            break
                    except Exception:
                        pass
            except Exception:
                pass
            page.wait_for_timeout(500)

        # Text / role fallback when data-test-id continue missing.
        if not clicked_continue and out["picked"] >= 3:
            for pattern in (
                r"continue to (your )?feed",
                r"^\s*Continue\s*$",
                r"^\s*Next\s*$",
            ):
                try:
                    loc = page.get_by_role("button", name=re.compile(pattern, re.I))
                    if loc.count() and loc.first.is_visible(timeout=500):
                        pause(page, 500, 1200, "use_case_before_continue_fb")
                        clk = _hclick(page, loc.first)
                        if clk.get("ok"):
                            out["continued"] = True
                            out["continue_fallback"] = pattern
                            pause(page, 2500, 4500, "use_case_after_continue_fb")
                            break
                except Exception:
                    continue

        # Do not Escape a residual onboarding modal: the progress loop below
        # must finish its next segmented step instead of dismissing it.

        # Wait for pin feed to appear
        for _ in range(15):
            try:
                if page.locator(PIN_LINK).count() >= 1:
                    break
            except Exception:
                pass
            inertial_scroll(page, direction=1)
            pause(page, 700, 1400, "use_case_wait_feed")
        if run_art is not None:
            try:
                page.screenshot(path=str(run_art / "01f-after-use-case.png"))
            except Exception:
                pass
    except Exception as e:
        out["error"] = f"{type(e).__name__}:{str(e)[:160]}"
    return out


def recover_empty_feed(page, run_art: Path | None = None) -> dict:
    """Before like_failed/no_pin_links: re-gate, blank reload once, re-clear NUX.

    Returns dict with gate, blank, reloaded, nux bits, pin_count, detail.
    Does not wipe user_data_dir or attempt credential login.
    """
    out: dict = {
        "gate": "ok",
        "blank": False,
        "reloaded": False,
        "pin_count": 0,
        "detail": None,
    }
    try:
        gate = detect_login_state(page)
        out["gate"] = gate
        if gate != "ok":
            out["detail"] = gate
            if run_art is not None:
                try:
                    page.screenshot(path=str(run_art / "02-empty-feed-diagnose.png"))
                except Exception:
                    pass
            return out

        blank = page_looks_blank(page)
        out["blank_probe"] = blank
        out["blank"] = bool(blank.get("blank"))
        if out["blank"]:
            # Quiet wait then single reload + re-gate.
            try:
                pause(page, 1800, 3500, "empty_feed_blank_wait")
            except Exception:
                page.wait_for_timeout(2000)
            try:
                page.reload(wait_until="domcontentloaded", timeout=90000)
                out["reloaded"] = True
            except Exception as e:
                out["reload_error"] = type(e).__name__
            try:
                quiet_window(page)
            except Exception:
                page.wait_for_timeout(1200)
            gate = detect_login_state(page)
            out["gate"] = gate
            out["gate_after_reload"] = gate
            if gate != "ok":
                out["detail"] = gate
                if run_art is not None:
                    try:
                        page.screenshot(path=str(run_art / "02-empty-feed-diagnose.png"))
                    except Exception:
                        pass
                return out
            # If still blank after reload while "ok", keep going to NUX clear.
            blank2 = page_looks_blank(page)
            out["blank_after_reload"] = blank2
            if blank2.get("blank"):
                out["detail"] = "blank_paint"

        # Re-clear name → gender → use-case → onboarding progress.
        name_nux = complete_name_onboarding(page, run_art)
        gender_nux = complete_gender_onboarding(page, run_art)
        use_nux = complete_use_case_picker(page, run_art)
        onboard = complete_onboarding_progress(page, run_art)
        out["name_onboarding"] = name_nux
        out["gender_onboarding"] = gender_nux
        out["use_case_picker"] = use_nux
        out["onboarding_progress"] = onboard

        # Wait until at least one pin link appears.
        pin_count = 0
        for _ in range(12):
            try:
                pin_count = int(page.locator(PIN_LINK).count())
            except Exception:
                pin_count = 0
            if pin_count >= 1:
                break
            try:
                inertial_scroll(page, direction=1)
            except Exception:
                pass
            try:
                pause(page, 700, 1400, "empty_feed_wait_pins")
            except Exception:
                page.wait_for_timeout(800)
        out["pin_count"] = pin_count

        # Final gate — unauth wall may appear after NUX attempt.
        gate = detect_login_state(page)
        out["gate"] = gate
        if gate != "ok":
            out["detail"] = gate
        elif pin_count < 1:
            if use_nux.get("seen") and not use_nux.get("continued"):
                out["detail"] = "nux_uncleared"
            elif out.get("detail") == "blank_paint" or (
                out.get("blank") and page_looks_blank(page).get("blank")
            ):
                out["detail"] = "blank_paint"
            else:
                out["detail"] = "no_pin_links"

        if pin_count < 1 and run_art is not None:
            try:
                page.screenshot(path=str(run_art / "02-empty-feed-diagnose.png"))
            except Exception:
                pass
        try:
            page.screenshot(path=str(run_art / "02-feed.png")) if run_art is not None else None
        except Exception:
            pass
    except Exception as e:
        out["error"] = f"{type(e).__name__}:{str(e)[:160]}"
        out["detail"] = out.get("detail") or "no_pin_links"
    return out


def collect_pin_ids(page, want: int = 3, pool: int = 24) -> list[str]:
    """Gather visible pin ids then randomly sample `want` (not always first N)."""
    links = page.locator(PIN_LINK)
    pool_ids: list[str] = []
    seen: set[str] = set()
    n = min(links.count(), 80)
    for i in range(n):
        try:
            el = links.nth(i)
            if not el.is_visible(timeout=200):
                continue
            href = (el.get_attribute("href") or "").split("?")[0]
            m = re.search(r"/pin/(\d+)", href)
            if not m or m.group(1) in seen:
                continue
            seen.add(m.group(1))
            pool_ids.append(m.group(1))
            if len(pool_ids) >= max(want, pool):
                break
        except Exception:
            continue
    if not pool_ids:
        return []
    if len(pool_ids) <= want:
        random.shuffle(pool_ids)
        return pool_ids
    return random.sample(pool_ids, want)


def _close_still_on_pin(page) -> bool:
    try:
        return "/pin/" in (page.url or "")
    except Exception:
        return False


def _close_via_button(page) -> str | None:
    for sel in CLOSE_CANDIDATES:
        try:
            ensure_page_visible(page, log_fn=log)
            loc = page.locator(sel)
            if loc.count() and loc.first.is_visible(timeout=500):
                clk = _hclick(page, loc.first)
                if clk.get("ok"):
                    pause(page, 800, 1600, "after_close_click")
                    if not _close_still_on_pin(page):
                        return f"button:{sel}"
                    return f"button:{sel}"
        except Exception:
            continue
    return None


def _close_via_escape(page) -> str | None:
    try:
        ensure_page_visible(page, log_fn=log)
        # Prefer focus inside modal/dialog before Esc.
        try:
            page.evaluate(
                """() => {
                  const root = document.querySelector(
                    '[role=dialog], [data-test-id*=closeup], [data-test-id*=Closeup]'
                  ) || document.body;
                  if (root && root.focus) root.focus();
                  else if (document.body && document.body.focus) document.body.focus();
                }"""
            )
        except Exception:
            pass
        hold = sample_esc_key_hold_ms()
        # Real keydown/keyup timing (not a single press helper when available).
        try:
            page.keyboard.down("Escape")
            page.wait_for_timeout(int(hold))
            page.keyboard.up("Escape")
        except Exception:
            page.keyboard.press("Escape")
            page.wait_for_timeout(int(hold))
        pause(page, 700, 1600, "after_escape")
        if not _close_still_on_pin(page):
            return "escape"
        return None
    except Exception:
        return None


def _close_via_history_back(page) -> str | None:
    try:
        ensure_page_visible(page, log_fn=log)
        page.go_back(wait_until="domcontentloaded", timeout=30000)
        pause(page, 900, 1800, "after_history_back")
        if not _close_still_on_pin(page):
            return "history_back"
        return None
    except Exception:
        return None


def _close_via_backdrop(page) -> str | None:
    """Click near viewport edge / dimmed backdrop (low weight path)."""
    try:
        ensure_page_visible(page, log_fn=log)
        box = page.evaluate(
            """() => {
              const dlg = document.querySelector('[role=dialog]');
              const w = window.innerWidth || 1200;
              const h = window.innerHeight || 800;
              // Prefer a point left/top outside the dialog box if present.
              if (dlg) {
                const r = dlg.getBoundingClientRect();
                if (r.left > 24) return {x: Math.max(8, r.left / 2), y: Math.min(h - 8, r.top + 24)};
                if (r.top > 24) return {x: Math.min(w - 8, r.left + 24), y: Math.max(8, r.top / 2)};
              }
              return {x: 12 + Math.random() * 18, y: 12 + Math.random() * 18};
            }"""
        )
        if not isinstance(box, dict):
            return None
        human_move_to(page, float(box["x"]), float(box["y"]), session_mouse())
        page.wait_for_timeout(sample_gamma_ms(40, 140, alpha=2.5, beta=22.0))
        page.mouse.down()
        page.wait_for_timeout(sample_gamma_ms(35, 120, alpha=3.0, beta=18.0))
        page.mouse.up()
        pause(page, 800, 1700, "after_backdrop")
        if not _close_still_on_pin(page):
            return "backdrop"
        return None
    except Exception:
        return None


def close_pin(page, *, preferred: str | None = None) -> str:
    """Mixed return paths from closeup (weighted + fallback). Logs which worked."""
    primary = preferred or choose_close_path()
    order = close_path_fallback_order(primary)
    log({"close_pin_plan": {"primary": primary, "order": order}})
    handlers = {
        "button": _close_via_button,
        "escape": _close_via_escape,
        "history_back": _close_via_history_back,
        "backdrop": _close_via_backdrop,
    }
    for path in order:
        fn = handlers.get(path)
        if not fn:
            continue
        try:
            got = fn(page)
        except Exception as e:
            log({"close_path_err": path, "error": type(e).__name__})
            got = None
        if got:
            log({"close_pin_worked": got, "path": path, "primary": primary})
            return got
    log({"close_pin_worked": "close_failed", "primary": primary, "order": order})
    return "close_failed"



def dump_closeup_selectors(page) -> dict:
    """Probe live closeup DOM for like/close selectors (hardening)."""
    try:
        return page.evaluate(
            """() => {
              const pick = (nodes) => Array.from(nodes).slice(0, 40).map(el => ({
                tag: el.tagName,
                testId: el.getAttribute('data-test-id'),
                aria: (el.getAttribute('aria-label')||'').slice(0,80),
                role: el.getAttribute('role'),
              }));
              const all = document.querySelectorAll('[data-test-id], button, [role=button]');
              const interesting = Array.from(all).filter(el => {
                const t = ((el.getAttribute('data-test-id')||'') + ' ' +
                           (el.getAttribute('aria-label')||'')).toLowerCase();
                return /react|reaction|like|close|back|closeup|save/.test(t);
              });
              return {
                url: location.href,
                interesting: pick(interesting),
                pinLinks: document.querySelectorAll('a[href*="/pin/"]').length,
              };
            }"""
        )
    except Exception as e:
        return {"error": type(e).__name__, "message": str(e)[:200]}

def like_pin(page) -> str | None:
    for sel in LIKE_CANDIDATES:
        try:
            loc = page.locator(sel)
            if loc.count() and loc.first.is_visible(timeout=700):
                clk = _hclick(page, loc.first)
                if clk.get("ok"):
                    pause(page, 800, 1600, "after_like_click")
                    return sel
        except Exception:
            continue
    try:
        box = page.evaluate(
            """() => {
              for (const el of document.querySelectorAll('button,[role=button],[data-test-id]')) {
                const t = ((el.getAttribute('aria-label')||'') + ' ' +
                           (el.getAttribute('data-test-id')||'')).toLowerCase();
                if (/react-button|pin-reaction|reaction-button/.test(t) ||
                    (/\\breact\\b/.test(t) && !/create/.test(t))) {
                  const r = el.getBoundingClientRect();
                  if (r.width > 2 && r.height > 2) {
                    return {
                      x: r.x, y: r.y, width: r.width, height: r.height,
                      id: (el.getAttribute('data-test-id') ||
                           el.getAttribute('aria-label') || 'fuzzy').slice(0, 80)
                    };
                  }
                }
              }
              return null;
            }"""
        )
        if isinstance(box, dict) and box.get("width"):
            tx = float(box["x"]) + float(box["width"]) * random.uniform(0.22, 0.78)
            ty = float(box["y"]) + float(box["height"]) * random.uniform(0.22, 0.78)
            human_move_to(page, tx, ty, session_mouse())
            hover = sample_lognormal_ms(450, 1300, mu=-0.15, sigma=0.4)
            log({"pause_ms": hover, "label": "hover_before_like_fuzzy"})
            page.wait_for_timeout(hover)
            page.mouse.down()
            page.wait_for_timeout(sample_gamma_ms(35, 140, alpha=3.0, beta=18.0))
            page.mouse.up()
            pause(page, 800, 1600, "after_like_fuzzy")
            return str(box.get("id") or "fuzzy-box-trail")
    except Exception:
        pass
    return None


def run_nurture_session(
    page,
    *,
    profile: str,
    run_art: Path,
    pins: int = 0,
    min_sec: int = 120,
    max_sec: int = 180,
    navigate: bool = True,
    persona: str | None = None,
) -> dict:
    """Run nurture browse on an existing Playwright page (keep browser open).

    Returns a dict with at least: status, elapsed_sec, liked, pins_opened.
    Does not close the page/context — caller owns lifecycle.
    pins<=0 means the persona chooses 0–12 (incl. feed-only bounce). 0 likes OK.
    """
    worked: dict = {}
    t0 = time.time()
    run_art.mkdir(parents=True, exist_ok=True)
    reset_session_mouse()
    plan = plan_nurture_session(
        persona=persona, pins=pins, min_sec=min_sec, max_sec=max_sec
    )
    worked["plan"] = plan
    log({"nurture_plan": plan})
    n_pins = int(plan["pins"])
    like_p = float(plan["like_prob"])
    target_sec = int(plan["target_sec"])

    try:
        if navigate:
            page.goto(
                "https://www.pinterest.com/",
                wait_until="domcontentloaded",
                timeout=90000,
            )
        else:
            url = page.url or ""
            if "pinterest.com" not in url or "/signup" in url or "/login" in url:
                page.goto(
                    "https://www.pinterest.com/",
                    wait_until="domcontentloaded",
                    timeout=90000,
                )
        quiet_window(page)

        try:
            if not _onboarding_progress_state(page).get("modal"):
                dismiss_light(page)
        except Exception:
            pass
        try:
            page.screenshot(path=str(run_art / "01-home.png"))
        except Exception:
            pass

        probe = session_keepalive_probe(page)
        log({"session_keepalive_probe": probe})
        gate = probe.get("gate") or detect_login_state(page)
        if gate != "ok":
            return {
                "status": gate,
                "profile": profile,
                "url": page.url,
                "elapsed_sec": round(time.time() - t0, 1),
                "liked": False,
                "pins_opened": 0,
                "persona": plan["persona"],
                "session_keepalive_probe": probe,
            }

        name_nux = complete_name_onboarding(page, run_art)
        log({"name_onboarding": name_nux})
        worked["name_onboarding"] = name_nux

        gender_nux = complete_gender_onboarding(page, run_art)
        log({"gender_onboarding": gender_nux})
        worked["gender_onboarding"] = gender_nux

        nux = complete_use_case_picker(page, run_art)
        log({"use_case_picker": nux})
        worked["use_case_picker"] = nux

        onboarding = complete_onboarding_progress(page, run_art)
        log({"onboarding_progress": onboarding})
        worked["onboarding_progress"] = onboarding
        if onboarding.get("complete"):
            dismiss_light(page)

        # 0.2.6: if feed still has zero pin links, recover before browse/fail.
        pin_boot = 0
        try:
            pin_boot = int(page.locator(PIN_LINK).count())
        except Exception:
            pin_boot = 0
        if pin_boot < 1:
            recovery = recover_empty_feed(page, run_art)
            log({"empty_feed_recovery": recovery})
            worked["empty_feed_recovery"] = recovery
            gate2 = recovery.get("gate") or detect_login_state(page)
            if gate2 != "ok":
                return {
                    "status": gate2,
                    "profile": profile,
                    "url": page.url,
                    "error": recovery.get("detail") or gate2,
                    "note": "empty_feed_recovery_gate",
                    "elapsed_sec": round(time.time() - t0, 1),
                    "liked": False,
                    "pins_opened": 0,
                    "persona": plan["persona"],
                    "worked": worked,
                }
            try:
                pin_boot = int(page.locator(PIN_LINK).count())
            except Exception:
                pin_boot = int(recovery.get("pin_count") or 0)

        # Visibility keepalive before first feed interaction burst.
        try:
            ensure_page_visible(page, log_fn=log)
        except Exception:
            pass

        feed_t0 = time.time()
        scroll_distance_px = 0
        last_flip_mono = None
        feed_only = bool(plan.get("feed_only") or int(plan.get("pins") or 0) == 0)
        if feed_only and int(plan.get("feed_scroll_screens") or 0) > 0:
            scroll_rounds = int(plan["feed_scroll_screens"])
        elif plan["bounce"]:
            scroll_rounds = random.randint(1, 3)
        else:
            scroll_rounds = random.randint(3, 7)

        for i in range(scroll_rounds):
            try:
                ensure_page_visible(page, log_fn=log)
            except Exception:
                pass
            plan_steps = inertial_scroll(page, direction=1)
            down_mag = scroll_plan_down_magnitude(plan_steps)
            scroll_distance_px += int(down_mag)
            rev = maybe_micro_reverse_scroll(
                page,
                down_mag,
                persona=plan.get("persona"),
                last_flip_mono=last_flip_mono,
                log_fn=log,
            )
            if rev.get("did"):
                scroll_distance_px += int(rev.get("reverse_px") or 0)  # distance traveled
                last_flip_mono = rev.get("last_flip_mono") or last_flip_mono
            pause(page, 3500, 9000, f"feed_scroll_{i}", ambient=True)
            # Optional hover-without-click on zero-pin / feed phases (~35%).
            if feed_only and random.random() < 0.35:
                try:
                    nlinks = page.locator(PIN_LINK).count()
                    if nlinks > 0:
                        loc = page.locator(PIN_LINK).nth(random.randrange(min(nlinks, 12)))
                        box = loc.bounding_box()
                        if box and box.get("width"):
                            tx = float(box["x"]) + float(box["width"]) * random.uniform(0.25, 0.75)
                            ty = float(box["y"]) + float(box["height"]) * random.uniform(0.25, 0.75)
                            human_move_to(page, tx, ty, session_mouse())
                            pause(page, 1000, 2500, f"feed_hover_{i}", ambient=True)
                except Exception:
                    pass
            if feed_only:
                # Independently re-sampled feed dwell target may already be met.
                if time.time() - feed_t0 >= float(plan.get("feed_dwell_sec") or 25):
                    break
            if time.time() - t0 > plan["max_sec"] - 50:
                break

        # Extra feed dwell for zero-pin until planned band (independently sampled pauses).
        if feed_only:
            target_feed = float(plan.get("feed_dwell_sec") or 45)
            while time.time() - feed_t0 < target_feed and time.time() - t0 < plan["max_sec"]:
                try:
                    ensure_page_visible(page, log_fn=log)
                except Exception:
                    pass
                if random.random() < 0.55:
                    plan_steps = inertial_scroll(page, direction=1)
                    down_mag = scroll_plan_down_magnitude(plan_steps)
                    scroll_distance_px += int(down_mag)
                    rev = maybe_micro_reverse_scroll(
                        page,
                        down_mag,
                        persona=plan.get("persona"),
                        last_flip_mono=last_flip_mono,
                        log_fn=log,
                    )
                    if rev.get("did"):
                        scroll_distance_px += int(rev.get("reverse_px") or 0)
                        last_flip_mono = rev.get("last_flip_mono") or last_flip_mono
                pause(page, 2000, 7000, "feed_only_dwell", ambient=True)

        feed_dwell_sec = round(time.time() - feed_t0, 1)
        worked["feed_dwell_sec"] = feed_dwell_sec
        worked["scroll_distance_px"] = int(scroll_distance_px)
        log(
            {
                "feed_stats": {
                    "feed_dwell_sec": feed_dwell_sec,
                    "scroll_distance_px": int(scroll_distance_px),
                    "feed_only": feed_only,
                }
            }
        )

        try:
            page.screenshot(path=str(run_art / "02-feed.png"))
        except Exception:
            pass

        opened = 0
        liked = False
        like_sel = None
        like_attempts = 0
        pin_ids: list[str] = []

        if not feed_only:
            for _ in range(6):
                if page.locator(PIN_LINK).count() >= max(n_pins, 3):
                    break
                plan_steps = inertial_scroll(page, direction=1)
                scroll_distance_px += scroll_plan_down_magnitude(plan_steps)
                pause(page, 900, 2000, "load_more_pins", ambient=True)

            pin_ids = collect_pin_ids(page, want=n_pins, pool=max(n_pins * 4, 16))
            log({"pin_ids_n": len(pin_ids), "pin_ids": pin_ids})
            if not pin_ids:
                recovery = worked.get("empty_feed_recovery")
                if not isinstance(recovery, dict):
                    recovery = recover_empty_feed(page, run_art)
                    log({"empty_feed_recovery": recovery})
                    worked["empty_feed_recovery"] = recovery
                    pin_ids = collect_pin_ids(page, want=n_pins, pool=max(n_pins * 4, 16))
                    log({"pin_ids_n_after_recovery": len(pin_ids), "pin_ids": pin_ids})
                gate3 = (recovery or {}).get("gate") or detect_login_state(page)
                if gate3 != "ok":
                    return {
                        "status": gate3,
                        "profile": profile,
                        "error": (recovery or {}).get("detail") or gate3,
                        "note": "empty_feed_reclassified",
                        "elapsed_sec": round(time.time() - t0, 1),
                        "liked": False,
                        "pins_opened": 0,
                        "feed_dwell_sec": feed_dwell_sec,
                        "scroll_distance_px": int(scroll_distance_px),
                        "persona": plan["persona"],
                        "worked": worked,
                    }
                if not pin_ids:
                    detail = (recovery or {}).get("detail") or "no_pin_links"
                    note = "logged_in_but_empty_feed"
                    if detail == "nux_uncleared":
                        note = "nux_uncleared"
                    elif detail == "blank_paint":
                        note = "blank_paint"
                    try:
                        page.screenshot(path=str(run_art / "02-empty-feed-diagnose.png"))
                    except Exception:
                        pass
                    return {
                        "status": "like_failed",
                        "profile": profile,
                        "error": "no_pin_links" if detail in (None, "no_pin_links") else detail,
                        "note": note,
                        "elapsed_sec": round(time.time() - t0, 1),
                        "liked": False,
                        "pins_opened": 0,
                        "feed_dwell_sec": feed_dwell_sec,
                        "scroll_distance_px": int(scroll_distance_px),
                        "persona": plan["persona"],
                        "worked": worked,
                    }

            for pi, pid in enumerate(pin_ids):
                if time.time() - t0 > plan["max_sec"] - 15:
                    break
                pause(page, 2500, 7500, f"before_open_{pi}", ambient=True)
                try:
                    ensure_page_visible(page, log_fn=log)
                except Exception:
                    pass
                try:
                    loc = page.locator(f'a[href*="/pin/{pid}"]').first
                    loc.scroll_into_view_if_needed(timeout=5000)
                    pause(page, 700, 2200, f"into_view_{pi}", ambient=True)
                    clk = _hclick(page, loc)
                    if not clk.get("ok"):
                        raise RuntimeError(clk.get("error") or "open_click_failed")
                except Exception as e:
                    log({"open_err": str(e)[:120]})
                    try:
                        nlinks = page.locator(PIN_LINK).count()
                        if nlinks <= 0:
                            continue
                        alt = page.locator(PIN_LINK).nth(random.randrange(min(nlinks, 20)))
                        clk = _hclick(page, alt)
                        if not clk.get("ok"):
                            continue
                    except Exception:
                        continue
                pause(page, 1400, 3200, f"after_open_{pi}", ambient=True)
                opened += 1
                worked["pin_card"] = PIN_LINK
                try:
                    page.screenshot(path=str(run_art / f"03-pin-{pi + 1}.png"))
                except Exception:
                    pass

                # --- P0 longer pin linger (independently re-sampled each pin) ---
                linger = plan_pin_linger(
                    persona=plan.get("persona"),
                    bounce=bool(plan.get("bounce")),
                )
                log({"pin_linger_plan": linger, "pin_index": pi})
                pin_t0 = time.time()

                # 1) Initial gaze quiet (no like yet) — re-sample independently
                if linger.get("bounce"):
                    pause(page, 800, 2800, f"pin_gaze_{pi}", ambient=True)
                else:
                    pause(page, 2500, 5500, f"pin_gaze_{pi}", ambient=True)

                # 2) Optional ~50% light inertial peek scroll inside closeup
                if linger.get("do_peek") and int(linger.get("peek_px") or 0) > 0:
                    try:
                        ensure_page_visible(page, log_fn=log)
                        remain = float(linger["peek_px"])
                        steps = 0
                        while remain > 20 and steps < 10:
                            chunk = max(20.0, remain * random.uniform(0.2, 0.45))
                            page.mouse.wheel(0, int(chunk))
                            page.wait_for_timeout(
                                sample_gamma_ms(12, 50, alpha=2.2, beta=10.0)
                            )
                            remain -= chunk
                            steps += 1
                        # Re-sample peek settle independently (optional beat).
                        if random.random() < 0.85:
                            pause(page, 350, 1200, f"pin_peek_{pi}", ambient=True)
                    except Exception:
                        pass

                want_like = random.random() < like_p
                if want_like or pi == 0:
                    probe = dump_closeup_selectors(page)
                    log({"closeup_probe": probe})
                    try:
                        (run_art / f"03-pin-{pi + 1}-dom.json").write_text(
                            json.dumps(probe, ensure_ascii=False, indent=2), encoding="utf-8"
                        )
                    except Exception:
                        pass

                # 3) Like only in mid/late linger (60–85% of planned total)
                if want_like:
                    like_at = float(linger.get("like_at_ms") or linger["total_ms"] * 0.7)
                    elapsed_pin_ms = (time.time() - pin_t0) * 1000.0
                    wait_more = max(0, int(like_at - elapsed_pin_ms))
                    if wait_more > 0:
                        # Re-sample a wait band around remaining gap (not a fixed sleep).
                        lo = max(200, int(wait_more * 0.55))
                        hi = max(lo, int(wait_more * 1.15))
                        pause(page, lo, hi, f"pre_like_linger_{pi}", ambient=True)
                    like_attempts += 1
                    try:
                        ensure_page_visible(page, log_fn=log)
                    except Exception:
                        pass
                    sel = like_pin(page)
                    log({"like_result": sel, "like_prob": like_p, "pin_index": pi})
                    if sel:
                        liked = True
                        like_sel = sel
                        worked["like"] = sel
                        try:
                            page.screenshot(path=str(run_art / "04-liked.png"))
                        except Exception:
                            pass
                    else:
                        try:
                            page.screenshot(path=str(run_art / "04-like-failed.png"))
                        except Exception:
                            pass

                # 4) Pre-exit dwell then close — always re-sample at this site
                pause(page, 1000, 3000, f"pre_exit_{pi}", ambient=True)

                # Fill remaining total linger if still short (independent pause).
                elapsed_pin_ms = (time.time() - pin_t0) * 1000.0
                remain_total = int(linger["total_ms"] - elapsed_pin_ms)
                if remain_total > 400:
                    pause(
                        page,
                        max(300, remain_total // 2),
                        max(400, remain_total),
                        f"linger_fill_{pi}",
                        ambient=True,
                    )

                worked["close"] = close_pin(page)
                pause(page, 1800, 5500, f"after_close_{pi}", ambient=True)
                if pi < len(pin_ids) - 1 and random.random() < 0.7:
                    try:
                        ensure_page_visible(page, log_fn=log)
                    except Exception:
                        pass
                    plan_steps = inertial_scroll(page, direction=1)
                    down_mag = scroll_plan_down_magnitude(plan_steps)
                    scroll_distance_px += int(down_mag)
                    rev = maybe_micro_reverse_scroll(
                        page,
                        down_mag,
                        persona=plan.get("persona"),
                        last_flip_mono=last_flip_mono,
                        log_fn=log,
                    )
                    if rev.get("did"):
                        scroll_distance_px += int(rev.get("reverse_px") or 0)
                        last_flip_mono = rev.get("last_flip_mono") or last_flip_mono
                    pause(page, 1600, 4500, f"between_pins_scroll_{pi}", ambient=True)

        linger_until = max(int(plan["min_sec"]), target_sec)
        while time.time() - t0 < linger_until:
            remain = linger_until - (time.time() - t0)
            if remain <= 0:
                break
            if time.time() - t0 > plan["max_sec"]:
                break
            try:
                ensure_page_visible(page, log_fn=log)
            except Exception:
                pass
            plan_steps = inertial_scroll(page, direction=1)
            down_mag = scroll_plan_down_magnitude(plan_steps)
            scroll_distance_px += int(down_mag)
            rev = maybe_micro_reverse_scroll(
                page,
                down_mag,
                persona=plan.get("persona"),
                last_flip_mono=last_flip_mono,
                log_fn=log,
            )
            if rev.get("did"):
                scroll_distance_px += int(rev.get("reverse_px") or 0)
                last_flip_mono = rev.get("last_flip_mono") or last_flip_mono
            hi = max(400, min(8000, int(remain * 1000) + 500))
            lo = min(3000, hi)
            pause(page, lo, hi, "final_linger", ambient=True)

        try:
            page.screenshot(path=str(run_art / "05-final.png"))
        except Exception:
            pass

        # Refresh feed dwell to include any post-pin feed time for gate.
        feed_dwell_sec = round(max(feed_dwell_sec, time.time() - feed_t0), 1)
        elapsed = round(time.time() - t0, 1)
        payload = {
            "profile": profile,
            "pins_opened": opened,
            "pins_planned": n_pins,
            "like": like_sel,
            "like_attempts": like_attempts,
            "close": worked.get("close"),
            "elapsed_sec": elapsed,
            "liked": liked,
            "persona": plan["persona"],
            "like_prob": round(like_p, 3),
            "target_sec": target_sec,
            "feed_only": feed_only,
            "feed_dwell_sec": feed_dwell_sec,
            "scroll_distance_px": int(scroll_distance_px),
            "worked": worked,
        }
        # browsed_ok: pins_opened>=1 OR (feed_dwell>=min AND scroll_distance>=min)
        # Do NOT call zero-pin success like_failed.
        if browsed_ok(
            pins_opened=opened,
            feed_dwell_sec=feed_dwell_sec,
            scroll_distance_px=scroll_distance_px,
        ):
            return {"status": "browsed_ok", **payload}
        return {"status": "like_failed", "like_done": liked, **payload}
    except Exception as e:
        return {
            "status": "like_failed",
            "profile": profile,
            "error": type(e).__name__,
            "message": str(e)[:300],
            "elapsed_sec": round(time.time() - t0, 1),
            "liked": False,
            "pins_opened": 0,
            "persona": plan.get("persona"),
        }



def flush_storage_before_close(ctx, ud: Path, page=None) -> dict:
    """Best-effort storage_state + short wait before ctx.close."""
    out: dict = {"storage_state": False, "wait_ms": 0}
    wait_ms = sample_lognormal_ms(800, 2200, mu=-0.1, sigma=0.35)
    try:
        if page is not None:
            page.wait_for_timeout(int(wait_ms))
        out["wait_ms"] = int(wait_ms)
    except Exception:
        out["wait_ms"] = int(wait_ms)
    try:
        if ctx is not None and hasattr(ctx, "storage_state"):
            ud.mkdir(parents=True, exist_ok=True)
            state_file = ud / "playwright_storage_state.json"
            ctx.storage_state(path=str(state_file))
            out["storage_state"] = True
            out["storage_state_path"] = str(state_file)
    except Exception as e:
        out["storage_err"] = type(e).__name__
    log({"status": "session_flush_before_close", **{k: v for k, v in out.items() if k != "storage_state_path"}})
    return out


def run_nurture_reopen(
    *,
    profile: str,
    headed: bool = True,
    pins: int = 0,
    min_sec: int = 120,
    max_sec: int = 180,
    persona: str | None = None,
) -> dict:
    """Launch persistent context for profile and run nurture (reopen path)."""
    meta_path = ROOT / "profiles" / profile / "profile.json"
    if not meta_path.is_file():
        return {
            "status": "not_logged_in",
            "error": "missing_profile_json",
            "profile": profile,
            "liked": False,
            "pins_opened": 0,
            "elapsed_sec": 0,
        }
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    ud = ROOT / "data/profiles" / f"{profile}-pinterest-run"
    if not ud.is_dir():
        return {
            "status": "not_logged_in",
            "error": "missing_user_data_dir",
            "profile": profile,
            "liked": False,
            "pins_opened": 0,
            "elapsed_sec": 0,
        }

    run_art = ART / f"{profile}-run"
    run_art.mkdir(parents=True, exist_ok=True)
    log(
        {
            "skill": "pinterest-nurture-browse",
            "version": VERSION,
            "profile": profile,
            "headed": headed,
            "headless": not headed,
            "user_data_dir": str(ud),
            "artifact_dir": str(run_art),
            "proxy_set": bool(meta.get("proxy")),
            "mode": "reopen",
            "persona": persona or "auto",
        }
    )
    if not headed:
        log(
            {
                "warning": "headless_opt_in",
                "note": "reCAPTCHA HeadlessChrome risk; prefer headed + Xvfb",
            }
        )

    from cloakbrowser import launch_persistent_context
    from cloakcli_worker.fingerprint import (
        apply_to_launch_kwargs,
        ensure_fingerprint_seed,
        log_fingerprint_seed,
    )

    # Headed is the default. Use persisted fingerprint_seed; do not randomize per launch.
    seed = ensure_fingerprint_seed(meta_path)
    log_fingerprint_seed(seed)
    kwargs: dict = {
        "user_data_dir": str(ud),
        "headless": not headed,
    }
    if meta.get("proxy"):
        kwargs["proxy"] = meta["proxy"]
    apply_to_launch_kwargs(
        kwargs, seed=seed, proxy=meta.get("proxy"), headed=headed, profile_meta_path=meta_path
    )

    ctx = launch_persistent_context(**kwargs)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    try:
        return run_nurture_session(
            page,
            profile=profile,
            run_art=run_art,
            pins=pins,
            min_sec=min_sec,
            max_sec=max_sec,
            navigate=True,
            persona=persona,
        )
    finally:
        try:
            hang_before_close(page, session_mouse())
        except Exception:
            pass
        try:
            flush_storage_before_close(ctx, ud, page)
        except Exception:
            pass
        try:
            ctx.close()
        except Exception:
            pass


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True, help="profiles/<name>/profile.json id")
    ap.add_argument(
        "--headless",
        action="store_true",
        help="Opt-in headless (default is headed; Linux Xvfb is OK)",
    )
    ap.add_argument(
        "--pins",
        type=int,
        default=0,
        help="Pins to open (0=persona chooses 1–12)",
    )
    ap.add_argument(
        "--persona",
        default="auto",
        choices=[*PERSONA_NAMES, "auto"],
        help="Session persona (default auto)",
    )
    ap.add_argument(
        "--min-sec", type=int, default=120, help="Target minimum session seconds"
    )
    ap.add_argument(
        "--max-sec", type=int, default=180, help="Soft cap for pacing (default 180)"
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_arg_parser()
    args = ap.parse_args(argv)
    headed = not args.headless
    persona = None if args.persona == "auto" else args.persona

    result = run_nurture_reopen(
        profile=args.profile,
        headed=headed,
        pins=args.pins,
        min_sec=args.min_sec,
        max_sec=args.max_sec,
        persona=persona,
    )
    status = result.get("status") or "like_failed"
    payload = {k: v for k, v in result.items() if k != "worked"}
    payload["version"] = VERSION
    if status == "like_failed" and "worked" in result:
        payload["worked"] = result["worked"]
    emit_status(**payload)
    if status == "browsed_ok":
        return 0
    if status == "not_logged_in":
        return 2
    if status == "account_deactivated":
        return 3
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
