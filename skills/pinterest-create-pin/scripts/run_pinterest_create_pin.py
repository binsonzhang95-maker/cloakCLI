#!/usr/bin/env python3
"""pinterest-create-pin 0.1.0 — publish one Pin on a logged-in CloakBrowser profile.

Live path ports the geo46 explore r2 flow (2026-09-23, pin 1152217885972567993).
Explore artifacts under artifacts/pinterest/create-pin/ stay the evidence reference.

Hard rules:
- CloakBrowser launch_persistent_context only. Never system Chrome. No fingerprint knobs.
- Prefer data/profiles/<id>-pinterest-run. Proxy comes from profiles/<id>/profile.json and is never logged.
- UI clicks go through human_click_locator (trail, hover, mouse down/up).
- Title, description, and new board name go through human_type_text. No element fill.
- Board pick is an exact option label inside role=option / board-row. Never a loose
  div:has-text(board name) — that hits the pin title (round 1).
- Publish only [data-test-id=storyboard-creation-nav-done], and only after
  [data-test-id=board-dropdown-placeholder] is gone.
- published_ok requires a pin id (toast "Navigate to created Pin", /pin/<id> redirect,
  or a link on the Publish Complete draft). Sidebar text alone is not success.

Last non-empty stdout line is the fleet report (skill_id, version, status, digest when provided).
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

VERSION = "0.1.0"
SKILL_ID = "pinterest-create-pin"
TZ = ZoneInfo("America/New_York")

HOME_URL = "https://www.pinterest.com/"
PIN_TOOL_URL = "https://www.pinterest.com/pin-creation-tool/"

SEL_CREATE_TAB = '[data-test-id="create-tab"]'
SEL_PIN_TOOL_LINK = 'a[href*="pin-creation-tool"]'
SEL_TITLE = "#storyboard-selector-title"
SEL_DESC_CONTAINER = '[data-test-id="storyboard-description-field-container"]'
SEL_DESC_EDITOR = '[data-test-id="editor-with-mentions"]'
SEL_BOARD_BUTTON = '[data-test-id="board-dropdown-select-button"]'
SEL_BOARD_PLACEHOLDER = '[data-test-id="board-dropdown-placeholder"]'
SEL_BOARD_FORM_SUBMIT = '[data-test-id="board-form-submit-button"]'
SEL_PUBLISH = '[data-test-id="storyboard-creation-nav-done"]'
SEL_CREATE_BOARD = 'div[role="button"]:has-text("Create board")'
SEL_SUCCESS_ICON = '[data-test-id="success-publish-icon-container"]'
SEL_TOAST_PIN = '[aria-label="Navigate to created Pin"]'

# Dropdown options only. The board name is matched in text, never interpolated into a loose selector.
BOARD_OPTION_SCOPES = (
    '[role="listbox"] [role="option"]',
    '[role="option"]',
    '[data-test-id*="board-row"]',
    '[data-test-id*="boardFromList"]',
    '[data-test-id*="boardWithoutSection"]',
)
# Header chrome sits above this. The verified Publish control is also high (~y=93)
# and must NOT use this filter — it applies only to board options.
BOARD_OPTION_MIN_Y = 120

CREATE_TAB_SELS = (
    SEL_CREATE_TAB,
    '[data-test-id="header-create-menu-button"]',
    '[data-test-id="create-button"]',
    'button[aria-label="Create"]',
    'a[aria-label="Create"]',
    '[aria-label="Create"]',
    'div[role="button"][aria-label="Create"]',
    'a[href="/pin-creation-tool/"]',
    SEL_PIN_TOOL_LINK,
)
PIN_MENU_SELS = (
    '[data-test-id="create-pin-button"]',
    '[data-test-id="createPin"]',
    SEL_PIN_TOOL_LINK,
    'div[role="menuitem"]:has-text("Pin")',
    'a:has-text("Create Pin")',
    'div[role="button"]:has-text("Pin")',
    'button:has-text("Create Pin")',
)
UPLOAD_AREA_SELS = (
    '[data-test-id="storyboard-upload-area"]',
    '[data-test-id="pin-draft-upload"]',
    'button:has-text("Upload from device")',
    'button:has-text("Upload")',
    '[aria-label*="Upload" i]',
    '[aria-label*="File Upload" i]',
    '[aria-label*="Add files" i]',
)
TITLE_SELS = (
    SEL_TITLE,
    '[data-test-id="storyboard-title-field-container"] input',
    '[data-test-id="storyboard-title-field-container"] textarea',
    '[data-test-id="pin-draft-title"] input',
    '[data-test-id="pin-draft-title"] textarea',
    'input[placeholder*="Add a title" i]',
    'textarea[placeholder*="Add a title" i]',
)
DESC_SELS = (
    f'{SEL_DESC_CONTAINER} {SEL_DESC_EDITOR} [contenteditable="true"]',
    f'{SEL_DESC_CONTAINER} [contenteditable="true"]',
    f'{SEL_DESC_EDITOR} [contenteditable="true"]',
    SEL_DESC_EDITOR,
    f'{SEL_DESC_CONTAINER} input',
    'input[placeholder*="Tell everyone what your Pin is about" i]',
    '[data-test-id="comment-editor-container"] [contenteditable="true"]',
    'div[contenteditable="true"][aria-label*="Describe" i]',
)
BOARD_OPEN_SELS = (
    SEL_BOARD_BUTTON,
    '[data-test-id="storyboard-selector-board"] button',
    'button[aria-label="Open dropdown"]',
)
CREATE_BOARD_SELS = (
    '[data-test-id="board-dropdown-create-board-button"]',
    '[role="option"]:has-text("Create board")',
    'button:has-text("Create board")',
    SEL_CREATE_BOARD,
)
BOARD_NAME_SELS = (
    '[data-test-id="board-name-input"] input',
    '[data-test-id="board-name-input"]',
    'input[placeholder*="Name" i]',
    'input[name="boardName"]',
    'input[aria-label*="name" i]',
)
BOARD_SUBMIT_SELS = (
    SEL_BOARD_FORM_SUBMIT,
    '[role="dialog"] button[type="submit"]',
    'form button[type="submit"]',
)

UNAUTH = (
    '[data-test-id="unauth-header"], '
    '[data-test-id="simple-login-button"], '
    '[data-test-id="simple-signup-button"], '
    'a[href*="/login"], button:has-text("Log in"), a:has-text("Log in"), '
    'button:has-text("Sign up"), a:has-text("Sign up")'
)
ACCT = (
    '[data-test-id="header-accounts-options-button"], '
    '[data-test-id="header-profile"], '
    '[data-test-id="header-profile-button"]'
)
CAPTCHA = (
    'iframe[src*="captcha"], iframe[src*="recaptcha"], '
    '[data-test-id*="captcha"], text=/verify you.?re human/i'
)

# Manifest exit codes (documentation + CLI). Host python_runner only keeps the
# JSON report when the process exits 0; see OPERATOR.md.
STATUS_EXIT: dict[str, int] = {
    "published_ok": 0,
    "dry_run_ok": 0,
    "login_required": 8,
    "upload_fail": 3,
    "board_missing": 5,
    "publish_fail": 6,
    "captcha_parked": 2,
    "oops_park": 2,
    "account_deactivated": 7,
    "ui_unknown_park": 4,
    "parked_risk": 2,
}
SUCCESS_STATUSES = frozenset({"published_ok", "dry_run_ok"})

PUBLISH_POLLS = 8
PUBLISH_EXTRA_POLLS_IF_COMPLETE = 4

PIN_ID_RE = re.compile(r"/pin/(\d{6,})")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PROXY_USER_RE = re.compile(r"://[^@\s/]+@")

VERIFIED_SELECTORS = {
    "create_tab": SEL_CREATE_TAB,
    "pin_tool_link": SEL_PIN_TOOL_LINK,
    "pin_tool": PIN_TOOL_URL,
    "title": SEL_TITLE,
    "description_container": SEL_DESC_CONTAINER,
    "description_editor": SEL_DESC_EDITOR,
    "board_button": SEL_BOARD_BUTTON,
    "board_placeholder": SEL_BOARD_PLACEHOLDER,
    "board_form_submit": SEL_BOARD_FORM_SUBMIT,
    "create_board": SEL_CREATE_BOARD,
    "publish": SEL_PUBLISH,
    "publish_complete_icon": SEL_SUCCESS_ICON,
    "toast_pin": SEL_TOAST_PIN,
}

_HERE = Path(__file__).resolve().parent


def find_root(start: Path | None = None) -> Path:
    """Walk parents for the CloakCLI checkout that owns nurture behavior helpers."""
    origin = start or _HERE
    for p in [origin, *origin.parents]:
        if (p / "scripts" / "pinterest_nurture_behavior.py").is_file():
            return p
    return Path("/workspace/CloakCLI")


ROOT = find_root()
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from pinterest_nurture_behavior import (  # noqa: E402
    ensure_page_visible,
    hang_before_close,
    human_click_locator,
    human_type_text,
    play_ambient_drift,
    reset_session_mouse,
    sample_pause_ms,
    sample_quiet_window_ms,
    session_mouse,
)


def now_et() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S ET")


def redact_text(text: str) -> str:
    s = EMAIL_RE.sub("***", text or "")
    return PROXY_USER_RE.sub("://***@", s)


def log(obj: dict) -> None:
    print(redact_text(json.dumps(obj, ensure_ascii=False, default=str)), flush=True)


def board_name_matches(option_text: str, board_name: str) -> bool:
    """Exact first-line match. Substring matches hit titles like '… | Classic World'."""
    want = (board_name or "").strip()
    if not want:
        return False
    first = (option_text or "").strip().splitlines()
    label = first[0].strip() if first else ""
    return label.casefold() == want.casefold()


def option_y_ok(y: float | None) -> bool:
    if y is None:
        return True
    return float(y) >= BOARD_OPTION_MIN_Y


def publish_gate_open(*, placeholder_visible: bool, picked_name: str | None) -> bool:
    return bool((picked_name or "").strip()) and not placeholder_visible


def board_block_status(*, placeholder_visible: bool, picked_name: str | None) -> str | None:
    if publish_gate_open(placeholder_visible=placeholder_visible, picked_name=picked_name):
        return None
    return "board_missing"


def parse_pin_ref(href: str) -> tuple[str | None, str | None]:
    if not href:
        return None, None
    clean = href.split("?", 1)[0].split("#", 1)[0].strip()
    m = PIN_ID_RE.search(clean)
    if not m:
        return None, None
    pin_id = m.group(1)
    if clean.startswith("http://") or clean.startswith("https://"):
        url = clean
    elif clean.startswith("/"):
        url = "https://www.pinterest.com" + clean
    else:
        url = f"https://www.pinterest.com/pin/{pin_id}/"
    if not url.endswith("/"):
        url += "/"
    return pin_id, url


def choose_published_pin(
    *,
    before_ids: set[str],
    page_url: str,
    toast_href: str | None,
    draft_hrefs: list[str] | None,
    page_hrefs: list[str] | None,
    publish_complete: bool,
) -> dict[str, str | None]:
    """Pick a new pin id. Loose page links count only with Publish Complete.

    Priority: toast "Navigate to created Pin", redirect off pin-creation-tool,
    then a link on the Publish Complete draft card.
    """
    before = set(before_ids or ())
    empty: dict[str, str | None] = {"pin_id": None, "pin_url": None, "source": None}

    def take(href: str | None, source: str) -> dict[str, str | None] | None:
        pin_id, pin_url = parse_pin_ref(href or "")
        if pin_id and pin_id not in before:
            return {"pin_id": pin_id, "pin_url": pin_url, "source": source}
        return None

    hit = take(toast_href, "toast")
    if hit:
        return hit
    url = page_url or ""
    if "pin-creation-tool" not in url:
        hit = take(url, "redirect")
        if hit:
            return hit
    for href in draft_hrefs or []:
        hit = take(href, "draft")
        if hit:
            return hit
    if publish_complete:
        for href in page_hrefs or []:
            hit = take(href, "publish_complete_link")
            if hit:
                return hit
    return empty


def publish_poll_budget(*, publish_complete: bool, pin_id: str | None) -> int:
    budget = PUBLISH_POLLS
    if publish_complete and not pin_id:
        budget += PUBLISH_EXTRA_POLLS_IF_COMPLETE
    return budget


def resolve_user_data_dir(root: Path, profile: str) -> Path | None:
    """Prefer data/profiles/<id>-pinterest-run, else profile.json user_data_dir."""
    name = (profile or "").strip()
    if not name:
        return None
    nurture = root / "data" / "profiles" / f"{name}-pinterest-run"
    if nurture.is_dir():
        return nurture
    meta_path = root / "profiles" / name / "profile.json"
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw = str(meta.get("user_data_dir") or "").strip()
    if not raw:
        return None
    ud = Path(raw)
    if not ud.is_absolute():
        ud = root / ud
    if ud.is_dir():
        return ud
    return None


def load_profile_meta(root: Path, profile: str) -> dict[str, Any]:
    path = root / "profiles" / profile / "profile.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def rel_to_root(path: Path | None, root: Path) -> str | None:
    if path is None:
        return None
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except (OSError, ValueError):
        return str(path)


def resolve_image(root: Path, raw: str) -> Path:
    image = Path(raw) if raw else Path("")
    if image.is_file():
        return image
    if raw:
        for base in (root, Path.cwd()):
            alt = base / raw
            if alt.is_file():
                return alt
    return image


def as_bool(value: Any, default: bool) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def flag_present(argv: list[str], *names: str) -> bool:
    for arg in argv:
        for name in names:
            if arg == name or arg.startswith(name + "="):
                return True
    return False


def load_stdin_payload() -> dict[str, Any]:
    if sys.stdin is None or sys.stdin.isatty():
        return {}
    raw = sys.stdin.read()
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def first_var(vars_: dict[str, Any], *keys: str) -> str:
    for key in keys:
        if key in vars_ and vars_[key] not in (None, ""):
            return str(vars_[key])
    return ""


def scrub_url(url: str) -> str:
    url = redact_text(url or "")
    if "?" in url:
        url = url.split("?", 1)[0]
    return url[:180]


def board_placeholder_visible(page: Any) -> bool:
    try:
        loc = page.locator(SEL_BOARD_PLACEHOLDER)
        if loc.count() == 0:
            return False
        return bool(loc.first.is_visible())
    except Exception:
        return False


def pause(page: Any, lo: int, hi: int, label: str = "", *, ambient: bool = False) -> int:
    ms = sample_pause_ms(lo, hi)
    log({"pause_ms": ms, "label": label})
    if ambient and ms >= 500 and random.random() < 0.55:
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


def shot(page: Any, out: Path, name: str) -> None:
    steps = out / "steps"
    try:
        steps.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(steps / f"{name}.png"), full_page=False)
        log({"screenshot": f"steps/{name}.png"})
    except Exception as e:
        log({"screenshot_fail": name, "error": type(e).__name__})


def first_visible(page: Any, selectors: tuple[str, ...] | list[str], timeout_each: int = 1500):
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            if loc.count() == 0:
                continue
            if loc.is_visible(timeout=timeout_each):
                return sel, loc
        except Exception:
            continue
    return None, None


def human_click_sel(page: Any, selectors: tuple[str, ...] | list[str], label: str) -> dict[str, Any]:
    sel, loc = first_visible(page, selectors)
    if not loc:
        return {"ok": False, "label": label, "reason": "not_found", "method": "not_found"}
    try:
        loc.evaluate("el => el.scrollIntoView({block:'center', inline:'nearest'})")
    except Exception:
        pass
    result = human_click_locator(page, loc, session_mouse())
    result["label"] = label
    result["selector"] = sel
    try:
        box = loc.bounding_box()
        if box:
            result["box_x"] = round(box["x"], 1)
            result["box_y"] = round(box["y"], 1)
            result["box_w"] = round(box["width"], 1)
            result["box_h"] = round(box["height"], 1)
    except Exception:
        pass
    log({"click": {k: result.get(k) for k in ("ok", "method", "label", "selector", "box_x", "box_y")}})
    return result


def gate_check(page: Any) -> str | None:
    url = scrub_url(getattr(page, "url", "") or "")
    try:
        body = page.locator("body").inner_text(timeout=3000)[:2500]
    except Exception:
        body = ""
    body_l = body.lower()
    if any(x in url.lower() for x in ("/login", "/signup", "/password")):
        return "login_required"
    try:
        if page.locator(UNAUTH).count() > 0 and page.locator(ACCT).count() == 0:
            if page.locator(UNAUTH).first.is_visible():
                return "login_required"
    except Exception:
        pass
    if "deactivated" in body_l or "been deactivated" in body_l:
        return "account_deactivated"
    try:
        if page.locator(CAPTCHA).count() > 0:
            return "captcha_parked"
    except Exception:
        pass
    if "captcha" in body_l or "verify you're human" in body_l or "verify youre human" in body_l:
        return "captcha_parked"
    if "suspicious" in body_l or "automated behavior" in body_l or "might be a bot" in body_l:
        return "parked_risk"
    if "oops" in body_l or "unusual activity" in body_l or "something went wrong" in body_l:
        return "oops_park"
    return None


def logged_in(page: Any) -> bool:
    try:
        if page.locator(ACCT).count() > 0:
            return True
    except Exception:
        pass
    try:
        if page.locator('a[href*="/pin/"]').count() > 3 and page.locator(UNAUTH).count() == 0:
            return True
    except Exception:
        pass
    return False


def snapshot_pin_ids(page: Any) -> set[str]:
    ids: set[str] = set()
    pid, _ = parse_pin_ref(getattr(page, "url", "") or "")
    if pid and "pin-creation-tool" not in (getattr(page, "url", "") or ""):
        ids.add(pid)
    try:
        hrefs = page.evaluate(
            """() => Array.from(document.querySelectorAll('a[href*="/pin/"]'))
                .map(a => a.getAttribute('href') || '')
                .slice(0, 60)"""
        )
    except Exception:
        hrefs = []
    for href in hrefs or []:
        found, _ = parse_pin_ref(str(href))
        if found:
            ids.add(found)
    return ids


def read_publish_evidence(page: Any) -> dict[str, Any]:
    try:
        data = page.evaluate(
            """() => {
              const body = (document.body && document.body.innerText) || '';
              const hrefs = [];
              document.querySelectorAll('a[href*="/pin/"]').forEach(a => {
                if (hrefs.length < 40) hrefs.push(a.getAttribute('href') || '');
              });
              const hrefOf = (el) => {
                if (!el) return '';
                const own = el.getAttribute('href') || '';
                if (own.indexOf('/pin/') >= 0) return own;
                const child = el.querySelector && el.querySelector('a[href*="/pin/"]');
                return child ? (child.getAttribute('href') || '') : '';
              };
              const toastEl = document.querySelector(
                'a[aria-label="Navigate to created Pin"], [aria-label="Navigate to created Pin"], [data-test-id="toast"] a[href*="/pin/"], [data-test-id="gestalt-toast-message"] a[href*="/pin/"]'
              );
              const toast = hrefOf(toastEl);
              const draftHrefs = [];
              const cards = document.querySelectorAll(
                '[data-test-id="storyboard-draft-rep"], [data-test-id^="pinDraft-"]'
              );
              cards.forEach(card => {
                const text = card.innerText || '';
                const icon = card.querySelector('[data-test-id="success-publish-icon-container"]');
                if (!/Publish Complete/i.test(text) && !icon) return;
                card.querySelectorAll('a[href*="/pin/"]').forEach(a => {
                  draftHrefs.push(a.getAttribute('href') || '');
                });
              });
              const complete = /Publish Complete/i.test(body)
                || !!document.querySelector('[data-test-id="success-publish-icon-container"]');
              return {publish_complete: complete, hrefs, toast_href: toast, draft_hrefs: draftHrefs};
            }"""
        )
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {
        "publish_complete": bool(data.get("publish_complete")),
        "hrefs": list(data.get("hrefs") or []),
        "toast_href": str(data.get("toast_href") or ""),
        "draft_hrefs": list(data.get("draft_hrefs") or []),
    }


def type_field(page: Any, loc: Any, text: str, label: str) -> dict[str, Any]:
    try:
        inner = loc.locator('[contenteditable="true"]').first
        if inner.count() and inner.is_visible(timeout=400):
            loc = inner
    except Exception:
        pass
    typed = human_type_text(page, loc, text, mouse=session_mouse())
    log(
        {
            label: {
                "typed": typed.get("typed"),
                "typos": typed.get("typos"),
                "used_fill": typed.get("used_fill"),
                "focus_ok": typed.get("focus_ok"),
                "focus": typed.get("focus"),
            }
        }
    )
    return typed


def typing_ok(typed: dict[str, Any], text: str) -> bool:
    if typed.get("used_fill"):
        return False
    if not typed.get("focus_ok"):
        return False
    return int(typed.get("typed") or 0) >= len(text)


def launch_cloakbrowser(ud: Path, headed: bool, proxy: str | None) -> Any:
    """Persistent CloakBrowser only. No system Chrome. No fingerprint knobs."""
    from cloakbrowser import launch_persistent_context

    kwargs: dict[str, Any] = {"user_data_dir": str(ud), "headless": not headed}
    if proxy:
        kwargs["proxy"] = proxy
    return launch_persistent_context(**kwargs)


def wrap_up_before_close(page: Any) -> None:
    """Short explore-r2 wrap-up unless CLOAKCLI_HANG_BEFORE_CLOSE_MS is set."""
    raw = os.environ.get("CLOAKCLI_HANG_BEFORE_CLOSE_MS")
    if raw is not None and str(raw).strip() != "":
        try:
            hang_before_close(page, session_mouse(), log_fn=log)
        except Exception:
            pass
        return
    try:
        page.wait_for_timeout(sample_pause_ms(2500, 5500))
    except Exception:
        pass


def flush_storage(ctx: Any, ud: Path) -> bool:
    try:
        ctx.storage_state(path=str(ud / "playwright_storage_state.json"))
        return True
    except Exception:
        return False


def pick_board_option(page: Any, board_name: str) -> tuple[str | None, str | None]:
    """Return (selector, matched label) after a trail click, or (None, None)."""
    for sel in BOARD_OPTION_SCOPES:
        try:
            locs = page.locator(sel)
            count = locs.count()
        except Exception:
            continue
        for i in range(min(int(count), 40)):
            loc = locs.nth(i)
            try:
                if not loc.is_visible(timeout=400):
                    continue
                text = (loc.inner_text(timeout=500) or "").strip()
            except Exception:
                continue
            if not board_name_matches(text, board_name):
                continue
            y = None
            try:
                box = loc.bounding_box()
                if box:
                    y = box.get("y")
            except Exception:
                y = None
            if not option_y_ok(y if y is None else float(y)):
                log({"skip_option_too_high": board_name, "y": y, "selector": sel})
                continue
            clicked = human_click_locator(page, loc, session_mouse())
            log(
                {
                    "board_pick": board_name,
                    "selector": sel,
                    "ok": clicked.get("ok"),
                    "method": clicked.get("method"),
                    "y": y,
                }
            )
            if clicked.get("ok"):
                return sel, board_name
    return None, None


def run_publish(page: Any, args: argparse.Namespace, out: Path, result: dict[str, Any]) -> dict[str, Any]:
    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=90000)
    quiet = sample_quiet_window_ms()
    log({"pause_ms": quiet, "label": "quiet_window_home"})
    page.wait_for_timeout(quiet)
    try:
        ensure_page_visible(page, log_fn=log)
    except Exception:
        pass
    shot(page, out, "01-home")
    reason = gate_check(page)
    if reason:
        return park(page, out, result, reason, "home")
    if not logged_in(page):
        return park(page, out, result, "login_required", "home_no_acct")
    result["steps"].append("home_logged_in")
    pause(page, 800, 2200, "after_home", ambient=True)

    human_click_sel(page, CREATE_TAB_SELS, "header_create")
    pause(page, 700, 1800, "after_create_click", ambient=True)
    shot(page, out, "02-after-create-click")
    if "pin-creation-tool" not in (page.url or ""):
        menu = human_click_sel(page, PIN_MENU_SELS, "create_pin_menu")
        pause(page, 900, 2200, "after_pin_menu")
        shot(page, out, "03-after-pin-menu")
        if "pin-creation-tool" not in (page.url or "") or not menu.get("ok"):
            if "pin-creation-tool" not in (page.url or ""):
                log({"nav": "direct_pin_creation_tool"})
                page.goto(PIN_TOOL_URL, wait_until="domcontentloaded", timeout=90000)
                pause(page, 1200, 2800, "after_direct_nav", ambient=True)
    shot(page, out, "04-create-ui")
    reason = gate_check(page)
    if reason:
        return park(page, out, result, reason, "create_ui")
    if "pin-creation-tool" not in (page.url or ""):
        return park(page, out, result, "ui_unknown_park", "create_ui_missing")
    result["steps"].append("create_ui_reached")
    result["create_url"] = scrub_url(page.url)

    uploaded = upload_image(page, Path(args.image))
    if not uploaded.get("ok"):
        shot(page, out, "05-upload-fail")
        return park(page, out, result, "upload_fail", "upload")
    pause(page, 1800, 4000, "after_upload_wait", ambient=True)
    shot(page, out, "05-after-upload")
    reason = gate_check(page)
    if reason:
        return park(page, out, result, reason, "after_upload")
    result["steps"].append("image_uploaded")
    result["image_used"] = Path(args.image).name
    result["upload"] = {k: uploaded.get(k) for k in ("method", "area")}

    try:
        desc_wrap = page.locator(SEL_DESC_CONTAINER).first
        if desc_wrap.count():
            desc_wrap.evaluate("el => el.scrollIntoView({block:'center'})")
            pause(page, 300, 700, "scroll_to_desc")
    except Exception:
        pass

    sel_t, loc_t = first_visible(page, TITLE_SELS, timeout_each=2500)
    if not loc_t:
        shot(page, out, "06-no-title")
        return park(page, out, result, "ui_unknown_park", "title_field")
    typed_title = type_field(page, loc_t, args.title, "title_typed")
    if not typing_ok(typed_title, args.title):
        return park(page, out, result, "ui_unknown_park", "title_type")
    result["steps"].append("title_typed")
    result["title_selector"] = sel_t
    pause(page, 600, 1600, "after_title", ambient=True)

    sel_d, loc_d = first_visible(page, DESC_SELS, timeout_each=2000)
    if not loc_d:
        shot(page, out, "06-no-description")
        return park(page, out, result, "ui_unknown_park", "description_field")
    typed_desc = type_field(page, loc_d, args.description, "desc_typed")
    if not typing_ok(typed_desc, args.description):
        return park(page, out, result, "ui_unknown_park", "description_type")
    result["steps"].append("description_typed")
    result["description_selector"] = sel_d
    pause(page, 700, 1800, "after_desc", ambient=True)
    shot(page, out, "06-metadata-filled")
    reason = gate_check(page)
    if reason:
        return park(page, out, result, reason, "after_metadata")

    try:
        board_wrap = page.locator(
            '[data-test-id="storyboard-selector-board"], ' + SEL_BOARD_BUTTON
        ).first
        if board_wrap.count():
            board_wrap.evaluate("el => el.scrollIntoView({block:'center'})")
            pause(page, 400, 900, "scroll_to_board")
    except Exception:
        pass
    board_clicked = human_click_sel(page, BOARD_OPEN_SELS, "board_dropdown")
    pause(page, 900, 1800, "after_board_dropdown")
    shot(page, out, "07-board-dropdown")

    _sel, picked = pick_board_option(page, args.board)
    if picked:
        result["steps"].append("board_selected")
    elif args.create_board_if_missing:
        created = create_board(page, out, args.board)
        if created:
            picked = args.board
            result["steps"].append("board_created")
        else:
            return park(page, out, result, "board_missing", "create_board")
    else:
        return park(page, out, result, "board_missing", "board_not_in_list")

    pause(page, 800, 1600, "after_board_pick")
    placeholder = board_placeholder_visible(page)
    if placeholder:
        pause(page, 700, 1400, "recheck_board_placeholder")
        placeholder = board_placeholder_visible(page)
    result["board"] = picked
    result["board_placeholder_after"] = placeholder
    result["board_dropdown_ok"] = bool(board_clicked.get("ok"))
    shot(page, out, "07c-board-after-pick")
    blocked = board_block_status(placeholder_visible=placeholder, picked_name=picked)
    if blocked:
        return park(page, out, result, blocked, "board_not_selected")

    if not args.publish:
        result["status"] = "publish_fail"
        result["reason"] = "publish_skipped_by_flag"
        result["publish_skipped"] = True
        result["ended_at"] = now_et()
        shot(page, out, "08-pre-publish")
        return result

    pause(page, 800, 1800, "pre_publish_dwell", ambient=True)
    shot(page, out, "08-pre-publish")
    reason = gate_check(page)
    if reason:
        return park(page, out, result, reason, "pre_publish")
    if board_placeholder_visible(page):
        return park(page, out, result, "board_missing", "board_placeholder_before_publish")

    pub_state = publish_button_state(page)
    result["publish_state"] = pub_state
    log({"publish_state": pub_state})
    if not pub_state.get("found"):
        return park(page, out, result, "publish_fail", "publish_button")
    if pub_state.get("disabled"):
        pause(page, 800, 1600, "wait_publish_enable")
        pub_state = publish_button_state(page)
        result["publish_state"] = pub_state
        if pub_state.get("disabled") or not pub_state.get("found"):
            return park(page, out, result, "publish_fail", "publish_disabled")

    before_ids = snapshot_pin_ids(page)
    result["pin_ids_before_publish"] = sorted(before_ids)
    pause(page, 1200, 2800, "natural_dwell_before_publish", ambient=True)
    pub = human_click_sel(page, (SEL_PUBLISH,), "publish")
    if not pub.get("ok"):
        shot(page, out, "09-publish-not-found")
        return park(page, out, result, "publish_fail", "publish_button")
    result["publish_click"] = {
        "selector": pub.get("selector"),
        "box_y": pub.get("box_y"),
        "box_x": pub.get("box_x"),
        "ok": pub.get("ok"),
        "method": pub.get("method"),
    }
    result["steps"].append("publish_clicked")

    chosen: dict[str, str | None] = {"pin_id": None, "pin_url": None, "source": None}
    evidence: dict[str, Any] = {}
    polls = 0
    while True:
        polls += 1
        pause(page, 700, 1400, f"post_publish_wait_{polls}")
        reason = gate_check(page)
        if reason:
            return park(page, out, result, reason, "post_publish")
        evidence = read_publish_evidence(page)
        chosen = choose_published_pin(
            before_ids=before_ids,
            page_url=page.url or "",
            toast_href=str(evidence.get("toast_href") or ""),
            draft_hrefs=list(evidence.get("draft_hrefs") or []),
            page_hrefs=list(evidence.get("hrefs") or []),
            publish_complete=bool(evidence.get("publish_complete")),
        )
        if chosen.get("pin_id"):
            break
        budget = publish_poll_budget(
            publish_complete=bool(evidence.get("publish_complete")),
            pin_id=None,
        )
        if polls >= budget:
            break

    shot(page, out, "09-after-publish")
    result["final_url"] = scrub_url(page.url)
    result["publish_complete"] = bool(evidence.get("publish_complete"))
    result["publish_polls"] = polls
    result["pin_id"] = chosen.get("pin_id")
    result["pin_url"] = chosen.get("pin_url")
    result["pin_source"] = chosen.get("source")
    result["ended_at"] = now_et()
    if chosen.get("pin_id"):
        result["published_ok"] = True
        result["published"] = True
        result["status"] = "published_ok"
        return result
    result["status"] = "publish_fail"
    result["reason"] = "no_pin_url"
    return result


def upload_image(page: Any, image: Path) -> dict[str, Any]:
    file_input = page.locator('input[type="file"]').first
    _sel_area, loc_area = first_visible(page, UPLOAD_AREA_SELS, timeout_each=2000)
    try:
        has_input = file_input.count() > 0
    except Exception:
        has_input = False
    try:
        if has_input:
            if loc_area:
                human_click_locator(page, loc_area, session_mouse())
                pause(page, 400, 1100, "after_upload_area_click")
            else:
                pause(page, 300, 800, "before_set_files")
            file_input.set_input_files(str(image))
            log({"upload": "set_input_files", "image": image.name})
            return {"ok": True, "method": "set_input_files", "area": _sel_area}
        if loc_area:
            with page.expect_file_chooser(timeout=15000) as fc_info:
                human_click_locator(page, loc_area, session_mouse())
            fc_info.value.set_files(str(image))
            log({"upload": "file_chooser", "image": image.name})
            return {"ok": True, "method": "file_chooser", "area": _sel_area}
    except Exception as e:
        log({"upload_error": type(e).__name__, "detail": redact_text(str(e)[:160])})
    return {"ok": False, "method": "upload_fail", "area": _sel_area}


def create_board(page: Any, out: Path, board_name: str) -> bool:
    clicked = human_click_sel(page, CREATE_BOARD_SELS, "create_board")
    if not clicked.get("ok"):
        return False
    pause(page, 700, 1500, "after_create_board")
    shot(page, out, "07b-create-board")
    _sel, loc_n = first_visible(page, BOARD_NAME_SELS, timeout_each=2000)
    if not loc_n:
        log({"warn": "board_name_input_missing"})
        return False
    typed = type_field(page, loc_n, board_name, "board_name_typed")
    if not typing_ok(typed, board_name):
        return False
    pause(page, 500, 1200, "after_board_name")
    confirm = human_click_sel(page, BOARD_SUBMIT_SELS, "confirm_create_board")
    pause(page, 1000, 2400, "after_board_created")
    return bool(confirm.get("ok"))


def publish_button_state(page: Any) -> dict[str, Any]:
    state: dict[str, Any] = {"found": False, "disabled": True, "selector": SEL_PUBLISH}
    try:
        done = page.locator(SEL_PUBLISH).first
        if done.count() == 0:
            return state
        done.evaluate("el => el.scrollIntoView({block:'center'})")
        state["found"] = True
        disabled = False
        try:
            disabled = bool(done.is_disabled())
        except Exception:
            pass
        try:
            if done.get_attribute("aria-disabled") == "true":
                disabled = True
        except Exception:
            pass
        state["disabled"] = disabled
        try:
            box = done.bounding_box()
            if box:
                state["box"] = {k: round(box[k], 1) for k in ("x", "y", "width", "height")}
        except Exception:
            pass
        try:
            state["text"] = (done.inner_text(timeout=500) or "")[:40]
        except Exception:
            pass
    except Exception as e:
        state["error"] = type(e).__name__
    return state


def park(page: Any, out: Path, result: dict[str, Any], status: str, step: str) -> dict[str, Any]:
    result["status"] = status if status in STATUS_EXIT else "ui_unknown_park"
    result["parked_at_step"] = step
    result["ended_at"] = now_et()
    try:
        result["final_url"] = scrub_url(page.url)
    except Exception:
        result["final_url"] = None
    shot(page, out, f"99-parked-{result['status']}")
    log({"park": result["status"], "step": step, "url": result.get("final_url")})
    return result


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=f"{SKILL_ID} {VERSION}")
    ap.add_argument("--profile", default="", help="CloakCLI profile id")
    ap.add_argument("--image", default="", help="Local image path (jpg/png)")
    ap.add_argument("--title", default="", help="Pin title")
    ap.add_argument("--description", default="", help="Pin description")
    ap.add_argument("--board", default="", help="Board name")
    ap.add_argument(
        "--create-board-if-missing",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Create the board when it is not in the list (default: true)",
    )
    ap.add_argument(
        "--headed",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Headed CloakBrowser (default: true)",
    )
    ap.add_argument(
        "--headless",
        action="store_true",
        help="Opt-in headless. Overrides --headed.",
    )
    ap.add_argument(
        "--publish",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Click Publish after the board placeholder clears (default: true)",
    )
    ap.add_argument("--dry-run", action="store_true", help="Validate params only; no browser")
    ap.add_argument("--digest", default="", help="Skill package digest (python_runner identity)")
    ap.add_argument(
        "--out",
        default="",
        help="Artifact dir (default artifacts/pinterest/create-pin/<profile>-run/<ts>)",
    )
    return ap


def apply_payload(args: argparse.Namespace, argv: list[str], payload: dict[str, Any]) -> None:
    vars_ = payload.get("vars") if isinstance(payload.get("vars"), dict) else {}
    assert isinstance(vars_, dict)
    if not args.profile:
        args.profile = first_var(vars_, "PROFILE", "profile") or str(payload.get("profile") or "")
    if not args.image:
        args.image = first_var(vars_, "IMAGE_PATH", "IMAGE", "image")
    if not args.title:
        args.title = first_var(vars_, "TITLE", "title")
    if not args.description:
        args.description = first_var(vars_, "DESCRIPTION", "description")
    if not args.board:
        args.board = first_var(vars_, "BOARD", "board")
    if not flag_present(argv, "--create-board-if-missing", "--no-create-board-if-missing"):
        if any(k in vars_ for k in ("CREATE_BOARD_IF_MISSING", "create_board_if_missing")):
            raw = vars_.get("CREATE_BOARD_IF_MISSING", vars_.get("create_board_if_missing"))
            args.create_board_if_missing = as_bool(raw, True)
    if args.headless or flag_present(argv, "--headless"):
        args.headed = False
    elif not flag_present(argv, "--headed", "--no-headed"):
        if any(k in vars_ for k in ("HEADED", "headed")):
            args.headed = as_bool(vars_.get("HEADED", vars_.get("headed")), True)
        if as_bool(vars_.get("HEADLESS", vars_.get("headless")), False):
            args.headed = False
    if not flag_present(argv, "--publish", "--no-publish"):
        if any(k in vars_ for k in ("PUBLISH", "publish")):
            args.publish = as_bool(vars_.get("PUBLISH", vars_.get("publish")), True)
    if not args.dry_run and as_bool(vars_.get("DRY_RUN", vars_.get("dry_run")), False):
        args.dry_run = True
    if not args.digest and payload.get("digest"):
        args.digest = str(payload.get("digest"))
    if not args.out:
        args.out = first_var(vars_, "OUT", "out")


def preflight(args: argparse.Namespace, root: Path) -> tuple[str | None, list[str], Path | None]:
    issues: list[str] = []
    profile = (args.profile or "").strip()
    title = (args.title or "").strip()
    description = (args.description or "").strip()
    board = (args.board or "").strip()
    image = resolve_image(root, args.image or "")
    args.profile = profile
    args.title = title
    args.description = description
    args.board = board
    args.image = str(image) if image.is_file() else (args.image or "")

    if not image.is_file():
        issues.append(f"missing image: {args.image or '(empty)'}")
        return "upload_fail", issues, None
    if not profile:
        issues.append("missing profile")
    if not title:
        issues.append("empty title")
    if not description:
        issues.append("empty description")
    if not board:
        issues.append("empty board")
    if issues:
        return "ui_unknown_park", issues, image
    ud = resolve_user_data_dir(root, profile)
    if ud is None and args.dry_run:
        issues.append(
            f"warn: no user_data_dir for {profile} "
            f"(expected data/profiles/{profile}-pinterest-run or profiles/{profile}/profile.json)"
        )
    if ud is None and not args.dry_run:
        issues.append(f"missing user_data_dir for {profile}")
        return "login_required", issues, image
    return None, issues, image


def base_result(args: argparse.Namespace, root: Path, ud: Path | None, issues: list[str]) -> dict[str, Any]:
    description = args.description or ""
    return {
        "skill_id": SKILL_ID,
        "skill": SKILL_ID,
        "version": VERSION,
        "status": "dry_run_ok" if args.dry_run else "ui_unknown_park",
        "success": False,
        "dry_run": bool(args.dry_run),
        "profile": args.profile,
        "user_data_dir": rel_to_root(ud, root),
        "image": args.image,
        "image_exists": bool(args.image) and Path(args.image).is_file(),
        "title": args.title,
        "title_chars": len(args.title or ""),
        "description_chars": len(description),
        "description_preview": description[:120],
        "board": args.board,
        "create_board_if_missing": bool(args.create_board_if_missing),
        "headed": bool(args.headed),
        "publish": bool(args.publish),
        "published": False,
        "published_ok": False,
        "pin_url": None,
        "pin_id": None,
        "issues": issues,
        "steps": [],
        "verified_selectors": VERIFIED_SELECTORS,
        "humanization": {
            "click": "human_click_locator",
            "type": "human_type_text",
            "source": "scripts/pinterest_nurture_behavior.py",
        },
        "explore_reference": "artifacts/pinterest/create-pin/",
        "started_at": now_et(),
    }


def finalize(result: dict[str, Any], digest: str) -> dict[str, Any]:
    status = str(result.get("status") or "ui_unknown_park")
    if status not in STATUS_EXIT:
        status = "ui_unknown_park"
        result["status"] = status
    result["success"] = status in SUCCESS_STATUSES
    result["exit"] = STATUS_EXIT[status]
    if digest:
        result["digest"] = digest
    if result.get("pin_url"):
        result["pin_url"] = scrub_url(str(result["pin_url"]))
    return result


def emit_report(result: dict[str, Any]) -> None:
    print(redact_text(json.dumps(result, ensure_ascii=False, default=str)), flush=True)


def run_live(args: argparse.Namespace, root: Path, out: Path, result: dict[str, Any]) -> dict[str, Any]:
    ud = resolve_user_data_dir(root, args.profile)
    if ud is None:
        result["status"] = "login_required"
        result["issues"] = list(result.get("issues") or []) + ["missing user_data_dir"]
        return result
    meta = load_profile_meta(root, args.profile)
    proxy = meta.get("proxy")
    proxy_s = proxy if isinstance(proxy, str) and proxy.strip() else None
    result["proxy_set"] = bool(proxy_s)
    result["user_data_dir"] = rel_to_root(ud, root)
    reset_session_mouse()
    log(
        {
            "phase": "launch",
            "profile": args.profile,
            "ud": result["user_data_dir"],
            "headed": bool(args.headed),
            "proxy_set": bool(proxy_s),
            "image": Path(args.image).name,
        }
    )
    ctx = None
    page = None
    try:
        ctx = launch_cloakbrowser(ud, bool(args.headed), proxy_s)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            page.set_viewport_size({"width": 1440, "height": 960})
        except Exception:
            pass
        return run_publish(page, args, out, result)
    except Exception as e:
        result["status"] = "ui_unknown_park"
        result["error"] = type(e).__name__
        result["error_detail"] = redact_text(str(e))[:200]
        result["ended_at"] = now_et()
        log({"exception": result["error"], "detail": result["error_detail"]})
        if page is not None:
            shot(page, out, "99-exception")
        return result
    finally:
        if page is not None:
            wrap_up_before_close(page)
        if ctx is not None:
            result["storage_flushed"] = flush_storage(ctx, ud)
            try:
                ctx.close()
            except Exception:
                pass


def main(argv: list[str] | None = None, stdin_payload: dict[str, Any] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)
    payload = stdin_payload if stdin_payload is not None else load_stdin_payload()
    apply_payload(args, argv, payload)

    root = ROOT
    status, issues, _image = preflight(args, root)
    ud = resolve_user_data_dir(root, args.profile) if args.profile else None
    ts = datetime.now(TZ).strftime("%Y%m%dT%H%M%S")
    out = (
        Path(args.out)
        if args.out
        else root / "artifacts" / "pinterest" / "create-pin" / f"{args.profile or 'unknown'}-run" / ts
    )
    if not out.is_absolute():
        out = root / out
    out.mkdir(parents=True, exist_ok=True)

    result = base_result(args, root, ud, issues)
    if status:
        result["status"] = status
    elif args.dry_run:
        result["status"] = "dry_run_ok"
        result["steps"] = ["dry_run"]
    else:
        result = run_live(args, root, out, result)

    result = finalize(result, args.digest or "")
    result["ended_at"] = result.get("ended_at") or now_et()
    text = redact_text(json.dumps(result, indent=2, ensure_ascii=False, default=str)) + "\n"
    (out / "result.json").write_text(text, encoding="utf-8")
    emit_report(result)
    return int(result["exit"])


if __name__ == "__main__":
    sys.exit(main())
