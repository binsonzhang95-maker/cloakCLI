#!/usr/bin/env python3
"""E2E Pinterest register + Outlook IMAP 6-digit verify.

Birthday: random age 25–35 as YYYY-MM-DD for #birthdate (type=date).
Prefer --headed (default). Headless often gets Pinterest Oops.

If signup returns path signup_ok_no_code_challenge (IP/geo dependent),
email may stay Unconfirmed — complete via Settings Confirm Email using
scripts/run_pinterest_settings_email_verify.py (same secrets/profile).
Onboarding name must be a realistic given name (never email local-part).

Statuses after code Continue:
- ok: code UI gone, no verify error, not login/signup CTA
- ok + path toast_email_confirmed: page/toast says "Email confirmed" (even if
  leftover #code modal) and Account settings Email badge is Confirmed
- verify_soft_oops: still on #code UI with "Something went wrong" (not invalid code)
- oops_blocked: signup-level Oops! modal (proxy/fingerprint — selectors won't fix 风控)
- code_error: incorrect/invalid/expired on the code field
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

ROOT = Path(__file__).resolve().parents[1]

# Inline field / body errors (verify UI). "Oops!" heading is classified separately.
ERROR_RE = re.compile(
    r"(sorry!?\s+something went wrong|something went wrong(?:\s+on our end)?|"
    r"incorrect|invalid|try again|expired|too many|rate.?limit|\boops)",
    re.I,
)
SOFT_OOPS_RE = re.compile(
    r"sorry!?\s+something went wrong|something went wrong(?:\s+on our end)?",
    re.I,
)
INVALID_RE = re.compile(
    r"incorrect(?:\s+code)?|invalid(?:\s+code)?|wrong code|"
    r"doesn'?t look right|didn'?t work|try again",
    re.I,
)
EXPIRED_RE = re.compile(r"expired|too many|rate.?limit", re.I)
OOPS_HEADING_RE = re.compile(r"\boops!", re.I)
EMAIL_CONFIRMED_TOAST_RE = re.compile(r"email confirmed", re.I)
CODE_DIGIT_RE = re.compile(r"(?<!\d)\d{6}(?!\d)")
SETTINGS_ACCOUNT_URL = "https://www.pinterest.com/settings/account-settings/"
ONBOARDING_MARKERS = (
    "What's your name",
    "Nice to meet you",
    "How do you identify",
)

SEND_NEW_CODE_SELS = (
    '[data-test-id="verification-code-form"] button:has-text("Send new code")',
    'form:has(#code) button:has-text("Send new code")',
    '[role="dialog"] button:has-text("Send new code")',
    'div:has(#code) button:has-text("Send new code")',
    'button:has-text("Send new code")',
    'a:has-text("Send new code")',
    '[role="button"]:has-text("Send new code")',
    'button:has-text("Resend code")',
    'button:has-text("Resend")',
)

OKAY_SELS = (
    '[role="dialog"] button:has-text("Okay")',
    '[role="alertdialog"] button:has-text("Okay")',
    'button:has-text("Okay")',
    '[role="dialog"] button:has-text("OK")',
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


def redact_raw_secrets(text: str) -> str:
    """Never echo raw 6-digit codes (or leftover code-shaped digits) in logs."""
    if not text:
        return text
    return CODE_DIGIT_RE.sub("******", text)


def snip_error(body: str) -> str | None:
    if not body:
        return None
    m = ERROR_RE.search(body)
    if not m:
        return None
    start = max(0, m.start() - 40)
    end = min(len(body), m.end() + 160)
    return redact_raw_secrets(body[start:end][:200])


def inline_error_kind(text: str) -> str | None:
    """Classify visible verify-field copy. Independent of the Oops! modal heading."""
    if not text:
        return None
    if SOFT_OOPS_RE.search(text):
        return "soft_oops"
    if EXPIRED_RE.search(text):
        return "expired"
    if INVALID_RE.search(text):
        return "invalid"
    return None


def detect_email_confirmed_toast(text: str) -> bool:
    """True when page/toast copy says Email confirmed (case-insensitive)."""
    return bool(text and EMAIL_CONFIRMED_TOAST_RE.search(text))


def email_badge_confirmed(text: str) -> bool:
    """Account settings Email badge is Confirmed (not Unconfirmed)."""
    if not text:
        return False
    return "Confirmed" in text and "Unconfirmed" not in text


def detect_oops_modal(body: str, *, code_visible: bool) -> bool:
    """Signup-level Oops! dialog (Okay), not the red inline #code message.

    geo06/geo10: heading 'Oops!' + 'Something went wrong on our end.' + Okay,
    overlaying the signup form (no #code). Proxy/fingerprint — not a selector bug.
    geo07 inline error is 'Sorry! Something went wrong on our end.' WITHOUT 'Oops!'.
    """
    if not body or not OOPS_HEADING_RE.search(body):
        return False
    if code_visible:
        return bool(re.search(r"\bokay\b", body, re.I))
    return True


def classify_verify_status(
    *,
    still_code_ui: bool,
    oops_modal: bool,
    inline_kind: str | None,
    onboarding: bool,
    login_signup_cta: bool,
) -> str:
    """Distinguish verify_soft_oops vs oops_blocked vs ok (and code_error).

    Do not declare ok from onboarding text while a verify error or #code UI remains.
    """
    if oops_modal and not still_code_ui:
        return "oops_blocked"
    if still_code_ui:
        if inline_kind == "soft_oops":
            return "verify_soft_oops"
        if inline_kind in ("invalid", "expired"):
            return "code_error"
        if oops_modal:
            return "oops_blocked"
        return "code_submitted_check_ui"
    if inline_kind == "soft_oops":
        return "verify_soft_oops"
    if inline_kind in ("invalid", "expired"):
        return "code_error"
    if oops_modal:
        return "oops_blocked"
    if login_signup_cta and not onboarding:
        return "code_submitted_check_ui"
    return "ok"


def random_display_name() -> str:
    """Reasonable human first name for onboarding — not email local-part."""
    first = [
        "James", "Oliver", "Noah", "Liam", "Ethan", "Mason", "Logan", "Lucas",
        "Emma", "Olivia", "Ava", "Sophia", "Mia", "Harper", "Amelia", "Evelyn",
        "Marcus", "Elena", "Nathan", "Claire", "Owen", "Grace", "Caleb", "Nora",
    ]
    return random.choice(first)


def fill_code_react(page, code: str) -> str:
    """Fill #code in a React-controlled-friendly way; return input_value."""
    loc = page.locator("#code").first
    loc.wait_for(state="visible", timeout=15000)
    loc.click()
    try:
        loc.fill("")
    except Exception:
        loc.press("Control+A")
        loc.press("Backspace")
    page.wait_for_timeout(150)
    # Prefer sequential key events so React controlled input updates
    try:
        loc.press_sequentially(code, delay=50)
    except Exception:
        # Fallback: native setter + input/change events
        page.evaluate(
            """([sel, val]) => {
              const el = document.querySelector(sel);
              if (!el) return;
              const proto = window.HTMLInputElement.prototype;
              const desc = Object.getOwnPropertyDescriptor(proto, 'value');
              if (desc && desc.set) desc.set.call(el, val);
              else el.value = val;
              el.dispatchEvent(new Event('input', { bubbles: true }));
              el.dispatchEvent(new Event('change', { bubbles: true }));
            }""",
            ["#code", code],
        )
    page.wait_for_timeout(200)
    val = loc.input_value()
    if val != code:
        # Last resort: fill() then re-verify
        loc.fill(code)
        page.wait_for_timeout(200)
        val = loc.input_value()
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
    return page.locator('button:has-text("Continue")').first, "button:has-text(\"Continue\")"


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


def page_body_text(page, n: int | None = None) -> str:
    try:
        t = page.inner_text("body")
    except Exception:
        return ""
    return t if n is None else t[:n]


def code_input_visible(page) -> bool:
    try:
        loc = page.locator("#code").first
        return bool(loc.count() and loc.is_visible(timeout=400))
    except Exception:
        return False


def scrape_toast_text(page) -> str:
    """Visible toast/live-region copy (Pinterest success toasts often sit outside #code)."""
    try:
        return (
            page.evaluate(
                """() => {
                  const parts = [];
                  const nodes = document.querySelectorAll(
                    '[role="status"], [role="alert"], [aria-live], [data-test-id*="toast"]'
                  );
                  for (const el of nodes) {
                    const t = (el.innerText || el.textContent || '').trim();
                    if (t) parts.push(t);
                  }
                  return parts.join('\\n').slice(0, 800);
                }"""
            )
            or ""
        )
    except Exception:
        return ""


def scrape_code_ui_text(page) -> str:
    """Visible text around #code (scroll into view so scrolled modals still scrape)."""
    try:
        loc = page.locator("#code").first
        if loc.count() and loc.is_visible(timeout=400):
            try:
                loc.scroll_into_view_if_needed()
            except Exception:
                pass
    except Exception:
        pass
    try:
        text = page.evaluate(
            """() => {
              const input = document.querySelector('#code');
              if (!input) return '';
              const root = input.closest('form')
                || input.closest('[data-test-id="verification-code-form"]')
                || input.closest('[role="dialog"]')
                || input.parentElement;
              if (!root) return '';
              return (root.innerText || '').slice(0, 1200);
            }"""
        )
        return text or ""
    except Exception:
        return ""


def snapshot_verify(page) -> dict:
    body = page_body_text(page)
    code_vis = code_input_visible(page)
    enter = "Enter the code" in body
    scoped = scrape_code_ui_text(page)
    kind = inline_error_kind(scoped) or (
        inline_error_kind(body) if (code_vis or enter) else None
    )
    # Full-page "something went wrong" without #code is the Oops modal copy, not inline.
    if kind == "soft_oops" and not (code_vis or enter) and detect_oops_modal(body, code_visible=False):
        kind = None
    oops_modal = detect_oops_modal(body, code_visible=code_vis)
    onboarding = any(m in body for m in ONBOARDING_MARKERS)
    cta = "Log in" in body[:1500] and "Sign up" in body[:1500]
    err = snip_error(scoped) or snip_error(body)
    toast_bits = scrape_toast_text(page)
    toast = (
        detect_email_confirmed_toast(body)
        or detect_email_confirmed_toast(scoped)
        or detect_email_confirmed_toast(toast_bits)
    )
    return {
        "still_code_ui": bool(code_vis or enter),
        "oops_modal": oops_modal,
        "inline_kind": kind,
        "onboarding": onboarding,
        "login_signup_cta": cta,
        "error_snip": err,
        "email_confirmed_toast": toast,
        "url": page.url,
    }


def _try_networkidle(page, timeout_ms: int = 6000) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        pass


def wait_after_verify_continue(page, timeout_ms: int = 25000) -> dict:
    """Wait until code UI gone, OR inline/modal error, OR onboarding, OR toast.

    Does not treat onboarding as success while #code / verify errors remain.
    An "Email confirmed" toast is a success candidate even if #code is still up.
    Soft-oops inline error holds briefly so a lagging toast can still appear
    (geo07/08 retry: leftover modal + success toast).
    """
    _try_networkidle(page, timeout_ms=min(8000, timeout_ms))
    deadline = time.time() + timeout_ms / 1000.0
    gone_since: float | None = None
    error_since: float | None = None
    last = snapshot_verify(page)
    while time.time() < deadline:
        last = snapshot_verify(page)
        if last.get("email_confirmed_toast"):
            return last
        if last["inline_kind"] or last["oops_modal"]:
            if error_since is None:
                error_since = time.time()
            elif time.time() - error_since >= 2.0:
                return last
        else:
            error_since = None
        if last["still_code_ui"]:
            gone_since = None
        else:
            if last["onboarding"]:
                return last
            if gone_since is None:
                gone_since = time.time()
            elif time.time() - gone_since >= 1.2:
                return last
        page.wait_for_timeout(250)
    return last


def dismiss_overlays(page) -> None:
    """Escape leftover #code / account-menu overlays so Settings is reachable."""
    for _ in range(3):
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
        except Exception:
            pass


def assert_settings_email_confirmed(
    page, *, art: Path | None = None, shot: str = "run-04-settings-email.png"
) -> bool:
    """Open Account settings and return True if Email badge is Confirmed."""
    dismiss_overlays(page)
    page.goto(SETTINGS_ACCOUNT_URL, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(2500)
    dismiss_overlays(page)
    if art is not None:
        try:
            page.screenshot(path=str(art / shot))
        except Exception:
            pass
    return email_badge_confirmed(page_body_text(page))


def page_has_email_confirmed_toast(page, snap: dict | None = None) -> bool:
    if snap and snap.get("email_confirmed_toast"):
        return True
    body = page_body_text(page)
    return detect_email_confirmed_toast(body) or detect_email_confirmed_toast(
        scrape_toast_text(page)
    )


def follow_email_confirmed_toast(
    page,
    snap: dict,
    *,
    art: Path | None = None,
    shot: str = "run-04-settings-email.png",
) -> dict | None:
    """If toast/page says Email confirmed, confirm via Settings Email badge.

    Returns a status overlay, or None if this is not a toast candidate.
    Does not log codes. Success: ok + path toast_email_confirmed.
    Settings still Unconfirmed: keep verify_soft_oops with a note.
    """
    if not page_has_email_confirmed_toast(page, snap):
        return None
    print(json.dumps({"status": "toast_email_confirmed_candidate"}), flush=True)
    confirmed = assert_settings_email_confirmed(page, art=art, shot=shot)
    if confirmed:
        print(
            json.dumps(
                {"status": "ok", "path": "toast_email_confirmed"},
                ensure_ascii=False,
            ),
            flush=True,
        )
        return {
            "status": "ok",
            "path": "toast_email_confirmed",
            "still_code_ui": False,
            "onboarding": False,
            "url": page.url,
        }
    print(
        json.dumps(
            {
                "status": "verify_soft_oops",
                "note": "toast_email_confirmed_but_settings_unconfirmed",
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return {
        "status": "verify_soft_oops",
        "note": "toast_email_confirmed_but_settings_unconfirmed",
        "url": page.url,
    }


def click_send_new_code(page) -> bool:
    for sel in SEND_NEW_CODE_SELS:
        loc = page.locator(sel).first
        try:
            if loc.count() == 0:
                continue
            if loc.is_visible(timeout=700):
                loc.scroll_into_view_if_needed()
                loc.click(timeout=4000)
                return True
        except Exception:
            continue
    try:
        page.get_by_text("Send new code", exact=True).click(timeout=2500)
        return True
    except Exception:
        return False


def dismiss_oops_okay(page) -> bool:
    for sel in OKAY_SELS:
        loc = page.locator(sel).first
        try:
            if loc.count() == 0:
                continue
            if loc.is_visible(timeout=600):
                loc.click(timeout=3000)
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


def imap_wait_code(secrets: str, after_uid: int, timeout: int) -> dict | None:
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
            # IMAP helper already avoids dumping tokens; keep stderr off the JSON log.
            print(proc.stderr, file=sys.stderr)
        return None
    lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    if not lines:
        return None
    hit = json.loads(lines[-1])
    if not isinstance(hit, dict) or not hit.get("code"):
        return None
    return hit


def _log_code_received(hit: dict) -> None:
    print(
        json.dumps(
            {
                "status": "code_received",
                "subject": hit.get("subject"),
                "code_len": len(hit.get("code") or ""),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


def _click_verify_continue(page) -> tuple[object, str, bool]:
    cont, cont_sel = find_verify_continue(page)
    print(json.dumps({"status": "continue_locator", "matched": cont_sel}), flush=True)
    cont.wait_for(state="visible", timeout=15000)
    enabled = wait_continue_enabled(cont, timeout_ms=15000)
    print(json.dumps({"status": "continue_ready", "enabled": enabled}), flush=True)
    cont.click(force=False)
    print(json.dumps({"status": "continue_clicked"}), flush=True)
    return cont, cont_sel, enabled


def _settle_result(
    *,
    status: str,
    snap: dict,
    page,
    continue_enabled_before: bool,
    val: str,
    cont_sel: str,
    retried: bool,
    extra: dict | None = None,
) -> dict:
    out = {
        "status": status,
        "still_code_ui": snap["still_code_ui"],
        "login_signup_cta": snap["login_signup_cta"],
        "has_oops": snap["oops_modal"],
        "error_snip": snap.get("error_snip"),
        "inline_kind": snap.get("inline_kind"),
        "onboarding": snap["onboarding"],
        "email_confirmed_toast": bool(snap.get("email_confirmed_toast")),
        "url": snap.get("url") or page.url,
        "continue_enabled_before_click": continue_enabled_before,
        "code_value_len": len(val),
        "continue_matched": cont_sel,
        "verify_retried": retried,
    }
    if extra:
        out.update(extra)
    return out


def submit_code_and_settle(
    page,
    *,
    code: str,
    last_uid: int,
    secrets: str,
    imap_timeout: int,
    art: Path | None = None,
    shot_filled: str = "run-02b-code-filled.png",
    shot_after: str = "run-03-after-code.png",
) -> dict:
    """Fill #code, scoped Continue, wait/settle, one Send-new-code retry on soft oops.

    Never logs raw codes. Cap retry at 1.
    If page/toast says "Email confirmed" (even with leftover #code), skip further
    retries, dismiss the modal, and assert Settings Email badge Confirmed.
    """
    val = fill_code_react(page, code)
    print(
        json.dumps(
            {
                "status": "code_filled",
                "len": len(code),
                "value_len": len(val),
                "value_match": val == code,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    page.wait_for_timeout(500)
    if art is not None:
        page.screenshot(path=str(art / shot_filled))

    _cont, cont_sel, continue_enabled_before = _click_verify_continue(page)
    snap = wait_after_verify_continue(page)
    status = classify_verify_status(
        still_code_ui=snap["still_code_ui"],
        oops_modal=snap["oops_modal"],
        inline_kind=snap["inline_kind"],
        onboarding=snap["onboarding"],
        login_signup_cta=snap["login_signup_cta"],
    )
    if art is not None:
        page.screenshot(path=str(art / shot_after))

    retried = False
    toast_now = page_has_email_confirmed_toast(page, snap)
    # Toast can coexist with leftover #code error — don't Send-new-code over a
    # success. Confirm via Settings instead (geo07 Gabriel / geo08 Celestino).
    if toast_now:
        follow = follow_email_confirmed_toast(page, snap, art=art)
        if follow:
            return _settle_result(
                status=status,
                snap=snap,
                page=page,
                continue_enabled_before=continue_enabled_before,
                val=val,
                cont_sel=cont_sel,
                retried=retried,
                extra=follow,
            )

    if status == "verify_soft_oops":
        print(
            json.dumps(
                {
                    "status": "verify_retry",
                    "reason": "verify_soft_oops",
                    "attempt": 1,
                    "error_snip": snap.get("error_snip"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        retried = True
        # Watermark IMAP before resend so a fast new message is not skipped by a
        # post-click max_uid. Wait for uid > this baseline (consumed code / inbox max).
        try:
            watermark = max(int(last_uid), imap_max_uid(secrets))
        except Exception:
            watermark = int(last_uid)
        sent = click_send_new_code(page)
        print(
            json.dumps({"status": "verify_retry", "send_new_code": sent}),
            flush=True,
        )
        if sent:
            page.wait_for_timeout(800)
            print(
                json.dumps({"status": "verify_retry", "imap_rebaseline": True}),
                flush=True,
            )
            hit = imap_wait_code(secrets, watermark, imap_timeout)
            if hit is None:
                return _settle_result(
                    status="imap_timeout",
                    snap=snap,
                    page=page,
                    continue_enabled_before=continue_enabled_before,
                    val=val,
                    cont_sel=cont_sel,
                    retried=True,
                    extra={"still_code_ui": True, "url": page.url},
                )
            _log_code_received(hit)
            new_code = hit["code"]
            val = fill_code_react(page, new_code)
            print(
                json.dumps(
                    {
                        "status": "code_filled",
                        "len": len(new_code),
                        "value_len": len(val),
                        "value_match": val == new_code,
                        "retry": True,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if art is not None:
                page.screenshot(path=str(art / "run-03b-retry-code-filled.png"))
            _cont, cont_sel, continue_enabled_before = _click_verify_continue(page)
            snap = wait_after_verify_continue(page)
            status = classify_verify_status(
                still_code_ui=snap["still_code_ui"],
                oops_modal=snap["oops_modal"],
                inline_kind=snap["inline_kind"],
                onboarding=snap["onboarding"],
                login_signup_cta=snap["login_signup_cta"],
            )
            if art is not None:
                page.screenshot(path=str(art / "run-03-after-retry.png"))
                page.screenshot(path=str(art / shot_after))

    follow = follow_email_confirmed_toast(page, snap, art=art)
    return _settle_result(
        status=status,
        snap=snap,
        page=page,
        continue_enabled_before=continue_enabled_before,
        val=val,
        cont_sel=cont_sel,
        retried=retried,
        extra=follow,
    )


def _log_oops_blocked(page, *, retried_continue: bool, dismissed_okay: bool) -> None:
    body = page_body_text(page)
    print(
        json.dumps(
            {
                "status": "oops_blocked",
                "kind": "signup_modal",
                "error_snip": snip_error(body),
                "note": "proxy_or_fingerprint",
                "dismissed_okay": dismissed_okay,
                "retried_continue": retried_continue,
                "url": page.url,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--secrets", default=str(ROOT / "data/secrets/pinterest-outlook-01.env"))
    ap.add_argument("--profile", default="geo02")
    ap.add_argument("--fresh-profile", action="store_true",
                    help="Wipe user_data_dir before launch (default: keep cookies)")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--imap-timeout", type=int, default=180)
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
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    base_uid = imap_max_uid(args.secrets)

    from cloakbrowser import launch_persistent_context

    meta = json.loads((ROOT / "profiles" / args.profile / "profile.json").read_text())
    proxy = meta.get("proxy")
    ud = ROOT / "data/profiles" / f"{args.profile}-pinterest-run"
    if getattr(args, "fresh_profile", False) and ud.exists():
        shutil.rmtree(ud)
    ud.mkdir(parents=True, exist_ok=True)
    kwargs = {"user_data_dir": str(ud), "headless": not headed}
    if proxy:
        kwargs["proxy"] = proxy
    ctx = launch_persistent_context(**kwargs)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    art = ROOT / "artifacts/pinterest"
    art.mkdir(parents=True, exist_ok=True)

    try:
        page.goto("https://www.pinterest.com/signup/", wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(3000)
        page.wait_for_selector("#email", timeout=30000)
        page.click("#email")
        page.keyboard.type(email, delay=30)
        page.wait_for_timeout(400)
        page.click("#password")
        page.keyboard.type(password, delay=30)
        page.wait_for_timeout(400)
        page.fill("#birthdate", bday)  # type=date
        page.wait_for_timeout(800)
        # blur so React validators enable Continue
        try:
            page.locator("#birthdate").blur()
        except Exception:
            page.keyboard.press("Tab")
        page.wait_for_timeout(1500)
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
        signup_cont.click()
        print(json.dumps({"status": "signup_continue_clicked"}), flush=True)
        # Wait for code UI OR logged-in/onboarding (no Log in+Sign up CTA)
        code_ui = False
        logged_in = False
        signup_oops_retried = False
        dismissed_okay = False
        for _ in range(30):
            page.wait_for_timeout(500)
            body = page_body_text(page)
            code_vis = code_input_visible(page)
            if detect_oops_modal(body, code_visible=code_vis):
                page.screenshot(path=str(art / "run-02-after-continue.png"))
                if not signup_oops_retried:
                    dismissed_okay = dismiss_oops_okay(page)
                    print(
                        json.dumps(
                            {
                                "status": "signup_oops_dismiss_retry",
                                "dismissed_okay": dismissed_okay,
                                "error_snip": snip_error(body),
                                "note": "proxy_or_fingerprint",
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    page.wait_for_timeout(600)
                    try:
                        wait_continue_enabled(signup_cont, timeout_ms=4000)
                        signup_cont.click(timeout=4000)
                    except Exception:
                        pass
                    signup_oops_retried = True
                    continue
                _log_oops_blocked(
                    page, retried_continue=True, dismissed_okay=dismissed_okay
                )
                return 2
            if "Enter the code" in body or page.locator("#code").count() > 0:
                try:
                    if page.locator("#code").first.is_visible(timeout=500) or "Enter the code" in body:
                        code_ui = True
                        break
                except Exception:
                    if "Enter the code" in body:
                        code_ui = True
                        break
            cta = "Log in" in body[:1500] and "Sign up" in body[:1500]
            onboarding = any(m in body for m in ONBOARDING_MARKERS)
            if onboarding or (not cta and ("Search Pinterest" in body or "/homefeed" in page.url)):
                logged_in = True
                break
            # still on form — retry Continue once mid-loop
            if _ == 10 and page.locator("#email").count() > 0:
                try:
                    if page.locator("#email").first.is_visible():
                        signup_cont.click(timeout=3000)
                        print(json.dumps({"status": "signup_continue_retry"}), flush=True)
                except Exception:
                    pass
        page.screenshot(path=str(art / "run-02-after-continue.png"))
        body = page_body_text(page)
        if detect_oops_modal(body, code_visible=code_input_visible(page)):
            _log_oops_blocked(
                page, retried_continue=signup_oops_retried, dismissed_okay=dismissed_okay
            )
            return 2
        if logged_in and not code_ui:
            # Onboarding name: use a reasonable human name, not email local-part
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
                    name_loc.click()
                    name_loc.press("Control+A")
                    name_loc.press("Backspace")
                    name_loc.press_sequentially(display_name, delay=40)
                    page.wait_for_timeout(400)
                    print(json.dumps({"status": "onboarding_name_set", "name_len": len(display_name)}), flush=True)
                    page.screenshot(path=str(art / "run-02b-onboarding-name.png"))
            except Exception as e:
                print(json.dumps({"status": "onboarding_name_skip", "err": type(e).__name__}), flush=True)
            cta = "Log in" in body[:1500] and "Sign up" in body[:1500]
            page.screenshot(path=str(art / "run-03-after-code.png"))
            print(
                json.dumps(
                    {
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
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return 0 if not cta else 5
        if not code_ui and "Enter the code" not in body:
            print(
                json.dumps(
                    {
                        "status": "unexpected_after_continue",
                        "snip": redact_raw_secrets(body[:350]),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return 3

        hit = imap_wait_code(args.secrets, base_uid, args.imap_timeout)
        if hit is None:
            print(json.dumps({"status": "imap_timeout"}), flush=True)
            return 4
        code = hit["code"]
        last_uid = int(hit.get("uid") or base_uid)
        _log_code_received(hit)

        settle = submit_code_and_settle(
            page,
            code=code,
            last_uid=last_uid,
            secrets=args.secrets,
            imap_timeout=args.imap_timeout,
            art=art,
        )
        settle["birthday"] = bday
        print(json.dumps(settle, ensure_ascii=False), flush=True)
        if settle["status"] == "ok":
            return 0
        if settle["status"] == "oops_blocked":
            return 2
        if settle["status"] == "imap_timeout":
            return 4
        return 5
    finally:
        ctx.close()


if __name__ == "__main__":
    raise SystemExit(main())
