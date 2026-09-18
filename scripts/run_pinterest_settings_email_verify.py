#!/usr/bin/env python3
"""Pinterest Settings → email verify via Outlook IMAP (post-signup path).

Use after signup lands on `signup_ok_no_code_challenge` / onboarding without an
inline code challenge. This path is **IP/geo dependent**: some geos skip the
signup code challenge and leave email Unconfirmed until Settings → Account
management → Confirm Email.

Onboarding name: always a realistic human given name (never email local-part).
Default name: Otis (override with --name).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Reuse helpers from the register+verify runner
sys.path.insert(0, str(ROOT / "scripts"))
from run_pinterest_register_outlook_verify import (  # noqa: E402
    load_env,
    snip_error,
    submit_code_and_settle,
    wait_continue_enabled,
)

SETTINGS_ACCOUNT_URL = "https://www.pinterest.com/settings/account-settings/"
CONFIRM_EMAIL_SEL = 'button:has-text("Confirm Email"), button:has-text("Confirm email")'


def is_emailish_name(val: str, human: str) -> bool:
    v = (val or "").strip()
    if not v:
        return True
    if v.lower() == human.lower():
        return False
    if "@" in v or "_" in v or re.search(r"\d", v):
        return True
    # Concatenated local-part style (no spaces, long)
    if " " not in v and len(v) > 10:
        return True
    return True  # prefer overwriting unknown autofill with human name


def dismiss_overlays(page) -> None:
    for _ in range(2):
        try:
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
        except Exception:
            pass


def body_text(page, n: int = 3000) -> str:
    try:
        return page.inner_text("body")[:n]
    except Exception as e:
        return f"<body_err:{e}>"


def finish_onboarding(page, human_name: str, art: Path | None = None) -> str | None:
    """Clear onboarding; return name actually used (or None if never seen)."""
    used: str | None = None
    for step in range(18):
        b = body_text(page, 4000)
        if "What's your name" in b or "Nice to meet you" in b:
            inputs = page.locator(
                '[role="dialog"] input, form input[type="text"], '
                'input[name="full_name"], input#name'
            )
            loc = inputs.first
            loc.wait_for(state="visible", timeout=10000)
            cur = ""
            try:
                cur = loc.input_value() or ""
            except Exception:
                pass
            if is_emailish_name(cur, human_name):
                loc.click()
                loc.press("Control+A")
                loc.press("Backspace")
                page.wait_for_timeout(80)
                try:
                    loc.press_sequentially(human_name, delay=45)
                except Exception:
                    loc.fill(human_name)
            used = human_name
            if art is not None:
                page.screenshot(path=str(art / f"settings-onboard-name-{step}.png"))
            cont = page.locator('button:has-text("Continue")').filter(has_not_text="Google").first
            wait_continue_enabled(cont, 8000)
            cont.click()
            page.wait_for_timeout(1800)
            continue

        if "How do you identify" in b:
            for label in ("Male", "Other", "Female"):
                btn = page.locator(
                    f'button:has-text("{label}"), div[role="button"]:has-text("{label}")'
                ).first
                try:
                    if btn.count() and btn.is_visible(timeout=600):
                        btn.click(timeout=4000)
                        page.wait_for_timeout(1600)
                        break
                except Exception:
                    continue
            continue

        skip = page.locator(
            'button:has-text("Skip"), a:has-text("Skip"), [role="button"]:has-text("Skip")'
        )
        try:
            if skip.count() and skip.first.is_visible(timeout=500):
                skip.first.click(timeout=3500)
                page.wait_for_timeout(1500)
                continue
        except Exception:
            pass

        if re.search(r"What are you in the mood|interest|topic|what are you into", b, re.I):
            # Pick a few chips then look for Next/Done; else Escape + leave
            chips = page.locator('[role="dialog"] button')
            try:
                for i in range(min(chips.count(), 4)):
                    txt = (chips.nth(i).inner_text() or "").strip().lower()
                    if txt in ("continue", "next", "skip", "done"):
                        continue
                    try:
                        chips.nth(i).click(timeout=1200)
                    except Exception:
                        pass
            except Exception:
                pass
            page.wait_for_timeout(400)
            nxt = page.locator(
                'button:has-text("Next"), button:has-text("Done"), button:has-text("Continue")'
            ).first
            try:
                if nxt.count() and nxt.is_visible(timeout=800):
                    # Topics Continue can stay disabled until enough picks; force if needed
                    try:
                        nxt.click(timeout=2500)
                    except Exception:
                        nxt.click(force=True, timeout=2500)
                    page.wait_for_timeout(1600)
                    continue
            except Exception:
                pass

        cont = page.locator(
            'button:has-text("Continue"), button:has-text("Next"), '
            'button:has-text("Done"), button:has-text("Finish")'
        ).filter(has_not_text="Google")
        markers = (
            "What's your name",
            "Nice to meet you",
            "How do you identify",
            "Where do you live",
            "What are you in the mood",
            "Tell us more",
        )
        try:
            if cont.count() and cont.first.is_visible(timeout=400) and any(m in b for m in markers):
                cont.first.click(timeout=3500)
                page.wait_for_timeout(1600)
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
        page.wait_for_timeout(500)
    return used


def ensure_display_name(page, human_name: str) -> None:
    """If Edit profile Name looks emailish, set to human_name and Save."""
    page.goto("https://www.pinterest.com/settings/", wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(2000)
    dismiss_overlays(page)
    name_inp = page.locator(
        'input[name="first_name"], input#first_name, '
        'form:has-text("Edit profile") input[type="text"]'
    ).first
    try:
        # Prefer labeled Name field under Edit profile
        candidates = page.locator("input")
        target = None
        for i in range(min(candidates.count(), 20)):
            el = candidates.nth(i)
            try:
                if not el.is_visible(timeout=200):
                    continue
            except Exception:
                continue
            # Heuristic: near "Name" label — use placeholder/aria
            aria = (el.get_attribute("aria-label") or "") + (el.get_attribute("placeholder") or "")
            name_attr = el.get_attribute("name") or el.get_attribute("id") or ""
            if re.search(r"name|full.?name|first", aria + name_attr, re.I):
                target = el
                break
        if target is None:
            # Fallback: first text input in main settings content
            target = page.locator('main input[type="text"], [data-test-id] input[type="text"]').first
        if target is None or target.count() == 0:
            return
        cur = ""
        try:
            cur = target.input_value() or ""
        except Exception:
            return
        if not is_emailish_name(cur, human_name):
            return
        target.click()
        target.press("Control+A")
        target.press("Backspace")
        target.press_sequentially(human_name, delay=40)
        page.wait_for_timeout(400)
        save = page.locator('button:has-text("Save")').first
        try:
            if save.count() and save.is_visible(timeout=500) and not save.is_disabled():
                save.click(timeout=4000)
                page.wait_for_timeout(1500)
        except Exception:
            pass
    except Exception:
        pass


def click_confirm_email(page) -> bool:
    dismiss_overlays(page)
    btn = page.locator(CONFIRM_EMAIL_SEL).first
    try:
        btn.wait_for(state="visible", timeout=12000)
    except Exception:
        return False
    btn.scroll_into_view_if_needed()
    page.wait_for_timeout(250)
    dismiss_overlays(page)
    try:
        btn.click(timeout=5000)
    except Exception:
        try:
            btn.click(force=True, timeout=5000)
        except Exception:
            page.evaluate(
                """() => {
                  const b = Array.from(document.querySelectorAll('button'))
                    .find(e => /confirm\\s*email/i.test((e.innerText||'').trim()));
                  if (!b) throw new Error('Confirm Email button not found');
                  b.click();
                }"""
            )
    page.wait_for_timeout(2000)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--secrets", default=str(ROOT / "data/secrets/pinterest-outlook-03.env"))
    ap.add_argument("--profile", default="geo03")
    ap.add_argument("--name", default="Otis", help="Human given name for onboarding (not email local-part)")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--fresh-profile", action="store_true", help="Wipe user_data_dir before launch")
    ap.add_argument("--imap-timeout", type=int, default=120)
    ap.add_argument("--fix-display-name", action="store_true", help="Also set Edit profile Name if emailish")
    args = ap.parse_args()
    headed = not args.headless

    env = load_env(Path(args.secrets))
    art = ROOT / "artifacts/pinterest"
    art.mkdir(parents=True, exist_ok=True)

    base_uid = int(
        subprocess.check_output(
            [
                sys.executable,
                str(ROOT / "scripts/outlook_imap_pinterest_code.py"),
                "--secrets",
                args.secrets,
                "--print-max-uid",
            ],
            text=True,
            cwd=str(ROOT),
        ).strip()
    )
    print(
        json.dumps(
            {
                "profile": args.profile,
                "headed": headed,
                "human_name": args.name,
                "imap_base_uid": base_uid,
                "note": "settings_email_verify_path_is_ip_geo_dependent",
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    from cloakbrowser import launch_persistent_context

    meta = json.loads((ROOT / "profiles" / args.profile / "profile.json").read_text())
    ud = ROOT / "data/profiles" / f"{args.profile}-pinterest-run"
    if args.fresh_profile and ud.exists():
        shutil.rmtree(ud)
    ud.mkdir(parents=True, exist_ok=True)
    kwargs = {"user_data_dir": str(ud), "headless": not headed}
    if meta.get("proxy"):
        kwargs["proxy"] = meta["proxy"]
    ctx = launch_persistent_context(**kwargs)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()

    result = {
        "status": "unknown",
        "urls": [],
        "selectors": [
            SETTINGS_ACCOUNT_URL,
            CONFIRM_EMAIL_SEL,
            "#code",
            'div:has(#code) button:has-text("Continue")',
        ],
        "code_email_arrived": False,
        "code_subject": None,
        "code_len": None,
        "final_verified": False,
        "onboarding_name_used": None,
        "geo_note": "Confirm Email settings path observed when signup skipped code challenge (IP/geo dependent)",
    }

    try:
        page.goto("https://www.pinterest.com/", wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(3000)
        result["urls"].append(page.url)

        # Login if session dead
        b = body_text(page, 1500)
        if re.search(r"\bLog in\b", b[:1200]) and re.search(r"\bSign up\b", b[:1200]):
            page.goto("https://www.pinterest.com/login/", wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(2000)
            page.wait_for_selector("#email", timeout=30000)
            page.click("#email")
            page.keyboard.type(env["PINTEREST_EMAIL"], delay=25)
            page.click("#password")
            page.keyboard.type(env["PINTEREST_PASSWORD"], delay=25)
            page.locator('button:has-text("Log in"), button[type="submit"]').first.click()
            page.wait_for_timeout(4000)
            print(json.dumps({"status": "logged_in"}), flush=True)

        used = finish_onboarding(page, args.name, art=art)
        result["onboarding_name_used"] = used
        if args.fix_display_name:
            ensure_display_name(page, args.name)

        # Refresh IMAP baseline just before CTA
        base_uid = int(
            subprocess.check_output(
                [
                    sys.executable,
                    str(ROOT / "scripts/outlook_imap_pinterest_code.py"),
                    "--secrets",
                    args.secrets,
                    "--print-max-uid",
                ],
                text=True,
                cwd=str(ROOT),
            ).strip()
        )

        page.goto(SETTINGS_ACCOUNT_URL, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(3000)
        if any(x in body_text(page) for x in ("How do you identify", "What's your name", "What are you in the mood")):
            finish_onboarding(page, args.name, art=art)
            page.goto(SETTINGS_ACCOUNT_URL, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(3000)
        dismiss_overlays(page)
        result["urls"].append(page.url)
        page.screenshot(path=str(art / "settings-runner-01-account.png"))
        b = body_text(page)
        if "Confirmed" in b and "Unconfirmed" not in b and "Confirm Email" not in b and "Confirm email" not in b:
            result["status"] = "already_verified"
            result["final_verified"] = True
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return 0
        if "Unconfirmed" not in b and "Confirm Email" not in b and "Confirm email" not in b:
            result["status"] = "no_confirm_cta"
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return 3

        if not click_confirm_email(page):
            result["status"] = "confirm_click_failed"
            page.screenshot(path=str(art / "settings-runner-confirm-fail.png"))
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return 4
        page.screenshot(path=str(art / "settings-runner-02-code-modal.png"))
        print(json.dumps({"status": "confirm_email_clicked"}), flush=True)

        proc = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/outlook_imap_pinterest_code.py"),
                "--secrets",
                args.secrets,
                "--after-uid",
                str(base_uid),
                "--timeout",
                str(args.imap_timeout),
            ],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
        )
        if proc.returncode != 0:
            result["status"] = "imap_timeout"
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return 5
        hit = json.loads(proc.stdout.strip().splitlines()[-1])
        code = hit["code"]
        result["code_email_arrived"] = True
        result["code_subject"] = hit.get("subject")
        result["code_len"] = len(code)
        print(
            json.dumps(
                {"status": "code_received", "subject": hit.get("subject"), "code_len": len(code)},
                ensure_ascii=False,
            ),
            flush=True,
        )

        # Wait for #code
        for _ in range(40):
            try:
                if page.locator("#code").count() and page.locator("#code").first.is_visible(timeout=300):
                    break
            except Exception:
                pass
            page.wait_for_timeout(250)

        last_uid = int(hit.get("uid") or base_uid)
        settle = submit_code_and_settle(
            page,
            code=code,
            last_uid=last_uid,
            secrets=args.secrets,
            imap_timeout=args.imap_timeout,
            art=art,
            shot_filled="settings-runner-03-code-filled.png",
            shot_after="settings-runner-04-after-code.png",
        )
        result["error_snip"] = settle.get("error_snip")
        result["verify_retried"] = settle.get("verify_retried")
        result["inline_kind"] = settle.get("inline_kind")
        result["still_code_ui"] = settle.get("still_code_ui")
        result["code_len"] = settle.get("code_value_len") or result.get("code_len")
        if settle["status"] == "imap_timeout":
            result["status"] = "imap_timeout"
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return 5
        if settle["status"] == "oops_blocked":
            result["status"] = "oops_blocked"
            result["note"] = "proxy_or_fingerprint"
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return 2
        if settle["status"] != "ok" and settle.get("still_code_ui"):
            result["status"] = settle["status"]
            page.screenshot(path=str(art / "settings-runner-04-after-code.png"))
            print(json.dumps(result, ensure_ascii=False), flush=True)
            return 6

        page.goto(SETTINGS_ACCOUNT_URL, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(2500)
        dismiss_overlays(page)
        page.screenshot(path=str(art / "settings-runner-05-final.png"))
        b2 = body_text(page)
        verified = ("Confirmed" in b2) and ("Unconfirmed" not in b2)
        result["final_verified"] = verified
        if verified:
            result["status"] = "ok"
        elif settle["status"] != "ok":
            result["status"] = settle["status"]
            if not result.get("error_snip"):
                result["error_snip"] = snip_error(b2)
        else:
            result["status"] = "code_submitted_check_ui"
        result["urls"].append(page.url)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0 if verified else 6
    finally:
        ctx.close()


if __name__ == "__main__":
    raise SystemExit(main())
