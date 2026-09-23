#!/usr/bin/env python3
"""Pinterest nurture browse (0.2.2).

Logged-in feed browse with behavior hardening (Gemini 2026-09-21): default
headed, continuous randomized mouse trails, inertial scroll, session personas
(browse_only / light_like / deep_browse / bounce_early), log-normal/gamma
pauses, quiet window after load. CloakBrowser persistent context; one account
↔ one geo/proxy. Does NOT change fingerprint/launch knobs.

At session start, clears name onboarding ("What's your name") then use-case
picker if present. Does NOT wipe user_data_dir. Does NOT attempt login /
credential recovery (dead-account audit owns that). Emits fleet status JSON
as last stdout line.

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

from pinterest_nurture_behavior import (  # noqa: E402
    PERSONA_NAMES,
    human_click_locator,
    human_move_to,
    human_type_text,
    inertial_scroll,
    hang_before_close,
    plan_nurture_session,
    play_ambient_drift,
    reset_session_mouse,
    sample_gamma_ms,
    sample_lognormal_ms,
    sample_pause_ms,
    sample_quiet_window_ms,
    session_mouse,
)

ART = ROOT / "artifacts/pinterest/nurture-browse"
VERSION = "0.2.2"

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
    """Heavy-tailed pause (log-normal). Optional ambient mouse drift while reading."""
    if hi_ms < lo_ms:
        lo_ms, hi_ms = hi_ms, lo_ms
    lo_ms = max(0, int(lo_ms))
    hi_ms = max(lo_ms, int(hi_ms))
    ms = sample_pause_ms(lo_ms, hi_ms)
    log({"pause_ms": ms, "label": label})
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


def detect_login_state(page) -> str:
    """Return browsed_ok-path gate: ok | not_logged_in | account_deactivated."""
    b = body_text(page, 3000)
    if re.search(r"account has been deactivated|your account has been deactivated", b, re.I):
        return "account_deactivated"
    try:
        if page.locator('text=/account has been deactivated/i').count():
            return "account_deactivated"
    except Exception:
        pass
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
    has_cta = bool(re.search(r"\bLog in\b", b[:1500])) and bool(
        re.search(r"\bSign up\b", b[:1500])
    )
    if unauth > 0 or (has_cta and acct == 0 and pins < 3):
        return "not_logged_in"
    if acct > 0 or pins >= 3 or not has_cta:
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
                or page.locator(USE_CASE_PICKER).count()
                or page.locator('text=/What are you in the mood to do/i').count()
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
        elif page.locator(USE_CASE_PICKER).count() or page.locator('text=/What are you in the mood to do/i').count():
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


def complete_use_case_picker(page, run_art: Path | None = None) -> dict:
    """Clear Pinterest NUX "What are you in the mood to do?" if present.

    Verified 2026-09-19 CST on geo46: desktop-use-case-picker +
    use-case-tap-area-* tiles + skip-or-continue-button.
    Pick >=3 tiles (human delay), then continue to feed.
    """
    out: dict = {"seen": False, "picked": 0, "continued": False}
    try:
        picker = page.locator(USE_CASE_PICKER)
        if not picker.count():
            # text fallback
            if not page.locator('text=/What are you in the mood to do/i').count():
                return out
        out["seen"] = True
        out["step_title"] = _onboarding_step_title(page)
    except Exception:
        return out

    try:
        pause(page, 800, 1600, "use_case_settle")
        tiles = page.locator(USE_CASE_TILE)
        n = tiles.count()
        # pick 3–5 distinct tiles with human pacing
        want = min(max(3, random.randint(3, 5)), n if n else 3)
        idxs = list(range(n))
        random.shuffle(idxs)
        chosen = []
        for i in idxs:
            if len(chosen) >= want:
                break
            try:
                el = tiles.nth(i)
                if not el.is_visible(timeout=500):
                    continue
                label = (el.inner_text(timeout=500) or "").strip()[:40]
                el.scroll_into_view_if_needed(timeout=3000)
                pause(page, 400, 1100, "use_case_before_tile")
                _hclick(page, el)
                chosen.append(label or f"tile_{i}")
                pause(page, 600, 1400, "use_case_after_tile")
            except Exception:
                continue
        out["picked"] = len(chosen)
        out["labels"] = chosen
        log({"use_case_picked": chosen})
        if run_art is not None:
            try:
                page.screenshot(path=str(run_art / "01e-use-case-picked.png"))
            except Exception:
                pass

        # wait for continue to enable (button text changes / becomes clickable)
        btn = page.locator(USE_CASE_CONTINUE)
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
                        pause(page, 2500, 4500, "use_case_after_continue")
                    break
                # Trail-only retry after enough picks even if the label still says pick.
                if out["picked"] >= 3 and _ >= 4:
                    try:
                        clk = _hclick(page, btn.first)
                        if clk.get("ok"):
                            out["continued"] = True
                            out["continue_forced"] = True
                            pause(page, 2500, 4500, "use_case_continue_forced")
                            break
                    except Exception:
                        pass
            except Exception:
                pass
            page.wait_for_timeout(500)

        # Do not Escape a residual onboarding modal: the progress loop below
        # must finish its next segmented step instead of dismissing it.

        # Wait for pin feed to appear
        for _ in range(15):
            try:
                if page.locator(PIN_LINK).count() >= 3:
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


def close_pin(page) -> str:
    for sel in CLOSE_CANDIDATES:
        try:
            loc = page.locator(sel)
            if loc.count() and loc.first.is_visible(timeout=500):
                clk = _hclick(page, loc.first)
                if clk.get("ok"):
                    pause(page, 800, 1600, "after_close_click")
                    return sel
        except Exception:
            continue
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(1000)
        if "/pin/" not in (page.url or ""):
            return "Escape"
        page.go_back(wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1200)
        return "history_back"
    except Exception:
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
    pins<=0 means the persona chooses 1–12. 0 likes is a valid success.
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
    view_lo = int(2800 * float(plan["view_scale"]))
    view_hi = int(8500 * float(plan["view_scale"]))
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

        scroll_rounds = random.randint(1, 3) if plan["bounce"] else random.randint(3, 7)
        for i in range(scroll_rounds):
            inertial_scroll(page, direction=1)
            pause(page, 3500, 9000, f"feed_scroll_{i}", ambient=True)
            if time.time() - t0 > plan["max_sec"] - 50:
                break

        try:
            page.screenshot(path=str(run_art / "02-feed.png"))
        except Exception:
            pass

        for _ in range(6):
            if page.locator(PIN_LINK).count() >= max(n_pins, 3):
                break
            inertial_scroll(page, direction=1)
            pause(page, 900, 2000, "load_more_pins", ambient=True)

        pin_ids = collect_pin_ids(page, want=n_pins, pool=max(n_pins * 4, 16))
        log({"pin_ids_n": len(pin_ids), "pin_ids": pin_ids})
        if not pin_ids:
            return {
                "status": "like_failed",
                "profile": profile,
                "error": "no_pin_links",
                "note": "logged_in_but_empty_feed",
                "elapsed_sec": round(time.time() - t0, 1),
                "liked": False,
                "pins_opened": 0,
                "persona": plan["persona"],
                "worked": worked,
            }

        opened = 0
        liked = False
        like_sel = None
        like_attempts = 0
        for pi, pid in enumerate(pin_ids):
            if time.time() - t0 > plan["max_sec"] - 15:
                break
            pause(page, 2500, 7500, f"before_open_{pi}", ambient=True)
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

            pause(page, view_lo, view_hi, f"view_pin_{pi}", ambient=True)

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

            if want_like:
                like_attempts += 1
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

            worked["close"] = close_pin(page)
            pause(page, 1800, 5500, f"after_close_{pi}", ambient=True)
            if pi < len(pin_ids) - 1 and random.random() < 0.7:
                inertial_scroll(page, direction=1)
                pause(page, 1600, 4500, f"between_pins_scroll_{pi}", ambient=True)

        linger_until = max(int(plan["min_sec"]), target_sec)
        while time.time() - t0 < linger_until:
            remain = linger_until - (time.time() - t0)
            if remain <= 0:
                break
            if time.time() - t0 > plan["max_sec"]:
                break
            inertial_scroll(page, direction=1)
            hi = max(400, min(8000, int(remain * 1000) + 500))
            lo = min(3000, hi)
            pause(page, lo, hi, "final_linger", ambient=True)

        try:
            page.screenshot(path=str(run_art / "05-final.png"))
        except Exception:
            pass

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
            "worked": worked,
        }
        # 0 likes is allowed (browse_only / unlucky light_like). Empty feed already returned.
        if opened >= 1:
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
    """Best-effort storage_state + short wait before ctx.close (no fingerprint knobs)."""
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

    # Headed is the default. Do not add fingerprint/launch knobs here.
    kwargs: dict = {"user_data_dir": str(ud), "headless": not headed}
    if meta.get("proxy"):
        kwargs["proxy"] = meta["proxy"]

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
