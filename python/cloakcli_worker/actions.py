"""Unified Teach Chat / Recover action schema (whitelist + bounds).

Canonical field is `selector`; legacy Recover `css` is accepted when reading.
Teach Chat allows at most 3 actions per turn. Recover wraps this module and
keeps press/select plus a 4-action cap.

Never execute raw model text, shell, eval, file I/O, or javascript:/data:/file: URLs.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlparse

from .redact import redact_any, redact_text

# Teach Chat unified enum (spec B). Recover adds press/select via extra_allowed.
UNIFIED_ACTIONS = {
    "goto",
    "click",
    "fill",
    "type",
    "scroll",
    "wait",
    "done",
    "fail",
    "ask_human",
}

RECOVER_EXTRA_ACTIONS = {
    "press",
    "select",
}

ALLOWED_ACTIONS = set(UNIFIED_ACTIONS)

ALLOWED_PRESS_KEYS = {
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

# Explicitly rejected (defense in depth — never execute).
FORBIDDEN_ACTIONS = {
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
    "screenshot",
    "javascript",
    "js",
    "file",
    "system",
    "popen",
    "subprocess",
}

SCHEMA_VERSION = 1
MAX_TEXT_LEN = 4000
MAX_WAIT_MS = 30_000
MAX_SCROLL_DELTA = 800
MAX_ACTIONS_PER_TURN = 3  # Teach Chat
MAX_SELECTOR_LEN = 500
MAX_CSS_LEN = MAX_SELECTOR_LEN
MAX_URL_LEN = 2048
MAX_REASON_LEN = 500

_SELECTOR_OK = re.compile(r"^[^;{}]{1,500}$")

BLOCKED_SCHEMES = {
    "file",
    "javascript",
    "data",
    "vbscript",
    "about",
    "blob",
    "chrome",
    "chrome-extension",
    "view-source",
}


class ActionError(ValueError):
    """Illegal or malformed action."""


@dataclass
class Action:
    type: str
    selector: str | None = None
    x: int | None = None
    y: int | None = None
    screenshot_id: str | None = None
    observation_id: str | None = None
    text: str | None = None
    delta_x: int = 0
    delta_y: int = 0
    ms: int = 0
    url: str | None = None
    reason: str = ""
    key: str | None = None
    value: str | None = None
    source: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def css(self) -> str | None:
        """Legacy Recover alias for `selector`."""
        return self.selector

    @css.setter
    def css(self, value: str | None) -> None:
        self.selector = value

    def public_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"schema_version": SCHEMA_VERSION, "action": self.type}
        if self.selector:
            d["selector"] = self.selector
            d["css"] = self.selector
        if self.x is not None:
            d["x"] = self.x
        if self.y is not None:
            d["y"] = self.y
        if self.screenshot_id:
            d["screenshot_id"] = self.screenshot_id
        if self.observation_id:
            d["observation_id"] = self.observation_id
        if self.text is not None:
            d["text"] = _redact_text_for_log(self.text)
        if self.type == "scroll":
            d["delta_x"] = self.delta_x
            d["delta_y"] = self.delta_y
        if self.type == "wait":
            d["ms"] = self.ms
        if self.url:
            d["url"] = sanitize_action_url(self.url)
        if self.key:
            d["key"] = self.key
        if self.value is not None:
            d["value"] = _redact_text_for_log(self.value)
        if self.reason:
            d["reason"] = self.reason[:MAX_REASON_LEN]
        if self.source:
            d["source"] = self.source
        return d


# Recover import alias.
RecoverAction = Action


@dataclass
class ParseResult:
    actions: list[Action]
    errors: list[str]
    schema_version: int = SCHEMA_VERSION


@dataclass
class ActionOutcome:
    status: str  # ok|rejected|cancelled|done|fail|ask_human|needs_confirm
    reason: str = ""
    page: dict[str, Any] | None = None
    action: dict[str, Any] | None = None


def parse_model_output(
    text: str,
    *,
    extra_allowed: set[str] | None = None,
    max_actions: int = MAX_ACTIONS_PER_TURN,
    reject_over_max: bool = True,
) -> ParseResult:
    """Parse model JSON into validated actions. Never treats raw prose as executable."""
    errors: list[str] = []
    blob = _extract_json(text)
    if blob is None:
        return ParseResult(actions=[], errors=["model output is not valid JSON"])

    schema_version = SCHEMA_VERSION
    items: list[Any]
    if isinstance(blob, list):
        items = blob
    elif isinstance(blob, dict):
        try:
            schema_version = int(blob.get("schema_version", SCHEMA_VERSION))
        except (TypeError, ValueError):
            schema_version = SCHEMA_VERSION
        if schema_version != SCHEMA_VERSION:
            errors.append(f"unsupported schema_version {schema_version}")
            return ParseResult(actions=[], errors=errors, schema_version=schema_version)
        if "actions" in blob and isinstance(blob["actions"], list):
            items = blob["actions"]
        elif "action" in blob:
            items = [blob]
        else:
            return ParseResult(actions=[], errors=["JSON missing action/actions"])
    else:
        return ParseResult(actions=[], errors=["JSON must be object or array"])

    if reject_over_max and len(items) > max_actions:
        return ParseResult(
            actions=[],
            errors=[f"too many actions ({len(items)}); max {max_actions} per turn"],
            schema_version=schema_version,
        )

    actions: list[Action] = []
    for i, item in enumerate(items[:max_actions]):
        try:
            actions.append(validate_action(item, extra_allowed=extra_allowed))
        except ActionError as e:
            errors.append(f"actions[{i}]: {e}")
    return ParseResult(actions=actions, errors=errors, schema_version=schema_version)


def parse_actions_payload(
    data: dict[str, Any] | list[Any] | None,
    *,
    extra_allowed: set[str] | None = None,
    max_actions: int = MAX_ACTIONS_PER_TURN,
) -> ParseResult:
    """Parse already-decoded hub `action_request` data. Rejects raw strings."""
    if data is None:
        return ParseResult(actions=[], errors=["missing action payload"])
    if isinstance(data, str):
        return ParseResult(actions=[], errors=["refusing raw model text; expected JSON actions"])
    if isinstance(data, list):
        blob: Any = {"schema_version": SCHEMA_VERSION, "actions": data}
    elif isinstance(data, dict):
        blob = data
        if "text" in blob and "actions" not in blob and "action" not in blob:
            return ParseResult(
                actions=[],
                errors=["refusing raw model text; expected JSON actions"],
            )
    else:
        return ParseResult(actions=[], errors=["action payload must be an object or array"])
    return parse_model_output(
        json.dumps(blob),
        extra_allowed=extra_allowed,
        max_actions=max_actions,
        reject_over_max=True,
    )


def validate_action(
    item: Any,
    *,
    extra_allowed: set[str] | None = None,
) -> Action:
    if not isinstance(item, dict):
        raise ActionError("action must be an object")
    raw_type = item.get("action") or item.get("type") or item.get("name")
    if not isinstance(raw_type, str) or not raw_type.strip():
        raise ActionError("missing action type")
    atype = raw_type.strip().lower()
    if atype in FORBIDDEN_ACTIONS:
        raise ActionError(f"forbidden action: {atype}")
    allowed = set(UNIFIED_ACTIONS)
    if extra_allowed:
        allowed |= extra_allowed
    if atype not in allowed:
        raise ActionError(f"unknown action: {atype}")

    selector = _canonical_selector(item)

    def _opt_int(key: str) -> int | None:
        if key not in item or item[key] is None:
            return None
        try:
            return int(item[key])
        except (TypeError, ValueError) as e:
            raise ActionError(f"{key} must be an int") from e

    x = _opt_int("x")
    y = _opt_int("y")
    sid = item.get("screenshot_id") or item.get("screenshotId")
    if sid is not None:
        sid = str(sid).strip()[:80] or None
    else:
        sid = None
    oid = item.get("observation_id") or item.get("observationId")
    if oid is not None:
        oid = str(oid).strip()[:80] or None
    else:
        oid = None
    if sid is None and oid:
        sid = oid

    text = item.get("text")
    if text is None and atype != "select":
        text = item.get("value")
    if text is not None:
        if not isinstance(text, str):
            text = str(text)
        if len(text) > MAX_TEXT_LEN:
            raise ActionError(f"text exceeds {MAX_TEXT_LEN} chars")
        _reject_injected_code(text)

    dx = int(item.get("delta_x", item.get("dx", 0)) or 0)
    dy = int(item.get("delta_y", item.get("dy", 0)) or 0)
    if abs(dx) > MAX_SCROLL_DELTA or abs(dy) > MAX_SCROLL_DELTA:
        raise ActionError("scroll delta out of range")

    ms = int(item.get("ms", item.get("timeout", 0)) or 0)
    if ms < 0:
        raise ActionError("wait ms must be >= 0")
    if ms > MAX_WAIT_MS:
        ms = MAX_WAIT_MS

    url = item.get("url")
    if url is not None:
        if not isinstance(url, str) or not url.strip():
            raise ActionError("url must be a non-empty string")
        url = url.strip()
        if len(url) > MAX_URL_LEN:
            raise ActionError("url too long")
        _reject_dangerous_url(url)

    reason = item.get("reason") or item.get("message") or ""
    if not isinstance(reason, str):
        reason = str(reason)
    reason = reason[:MAX_REASON_LEN]
    key = None
    source = item.get("source")
    if source is not None:
        source = str(source).strip().lower() or None
        if source not in (None, "llm", "human"):
            raise ActionError("source must be llm or human")

    if atype == "click":
        if selector:
            pass
        elif x is None or y is None:
            raise ActionError("click requires selector/css or x/y")
        elif not sid:
            raise ActionError(
                "coordinate click requires screenshot_id matching current observation"
            )
    elif atype in ("type", "fill"):
        if text is None:
            raise ActionError(f"{atype} requires text")
    elif atype == "scroll":
        if dx == 0 and dy == 0 and not selector:
            raise ActionError("scroll requires delta or selector")
    elif atype == "wait":
        if ms <= 0:
            ms = 500
    elif atype == "goto":
        if not url:
            raise ActionError("goto requires url")
        _reject_dangerous_url(url)
    elif atype == "press":
        key = _normalize_press_key(item.get("key") or item.get("name") or text or "Enter")
    elif atype == "select":
        if not selector:
            raise ActionError("select requires selector/css")
        sel_val = item.get("value", item.get("text"))
        if sel_val is None:
            raise ActionError("select requires value/text")
        if not isinstance(sel_val, str):
            sel_val = str(sel_val)
        if len(sel_val) > MAX_TEXT_LEN:
            raise ActionError(f"value exceeds {MAX_TEXT_LEN} chars")
        text = sel_val
    elif atype in ("done", "fail", "ask_human"):
        if not reason:
            reason = atype

    key_out = key if atype == "press" else None
    value_out = text if atype == "select" else None

    raw = {k: v for k, v in item.items() if k not in ("text", "value")}
    return Action(
        type=atype,
        selector=selector,
        x=x,
        y=y,
        screenshot_id=sid,
        observation_id=oid,
        text=text,
        delta_x=dx,
        delta_y=dy,
        ms=ms,
        url=url,
        reason=reason,
        key=key_out,
        value=value_out,
        source=source,
        raw=raw,
    )


def goto_risk(
    url: str,
    *,
    allow_origins: list[str] | None,
    current_origin: str | None = None,
) -> tuple[str, str]:
    """Return (ok|reject|needs_confirm, reason) for a goto URL."""
    try:
        _reject_dangerous_url(url)
    except ActionError as e:
        return "reject", str(e)
    dest = origin_of_url(url)
    if not dest:
        return "reject", "could not parse origin"
    allow = [o.rstrip("/") for o in (allow_origins or []) if o]
    if dest not in allow:
        return "reject", "origin not in allowlist"
    cur = (current_origin or "").rstrip("/")
    if cur and dest != cur:
        return "needs_confirm", "cross-origin navigation requires confirmation"
    return "ok", "allowlist"


def execute_action(
    page: Any,
    action: Action,
    *,
    allow_origins: list[str] | None = None,
    current_origin: str | None = None,
    cancel_check: Callable[[], bool] | None = None,
    timeout_ms: int = 15_000,
    confirmed: bool = False,
    executor_paused: bool = False,
) -> ActionOutcome:
    """Execute one validated schema action. Caller must parse/validate first.

    M3: set executor_paused while a human has taken over so this executor
    cannot race the human on the same Playwright page.
    """
    if executor_paused:
        return ActionOutcome(status="rejected", reason="executor_paused", action=action.public_dict())
    if cancel_check and cancel_check():
        return ActionOutcome(status="cancelled", reason="cancelled", action=action.public_dict())

    timeout = max(1000, min(timeout_ms, 15_000))
    try:
        if action.type == "done":
            return ActionOutcome(status="done", reason=action.reason, action=action.public_dict())
        if action.type == "fail":
            return ActionOutcome(status="fail", reason=action.reason, action=action.public_dict())
        if action.type == "ask_human":
            return ActionOutcome(
                status="ask_human", reason=action.reason, action=action.public_dict()
            )

        if action.type == "goto":
            risk, reason = goto_risk(
                action.url or "",
                allow_origins=allow_origins,
                current_origin=current_origin or _page_origin(page),
            )
            if risk == "reject":
                return ActionOutcome(
                    status="rejected", reason=reason, action=action.public_dict()
                )
            if risk == "needs_confirm" and not confirmed:
                return ActionOutcome(
                    status="needs_confirm",
                    reason=reason,
                    action=action.public_dict(),
                    page=_page_snapshot(page),
                )
            if cancel_check and cancel_check():
                return ActionOutcome(status="cancelled", reason="cancelled", action=action.public_dict())
            page.goto(action.url, wait_until="domcontentloaded", timeout=timeout)
            return ActionOutcome(
                status="ok",
                reason=f"ok goto {origin_of_url(action.url)}",
                action=action.public_dict(),
                page=_page_snapshot(page),
            )

        if action.type == "click":
            if action.selector:
                page.click(action.selector, timeout=timeout)
            else:
                x, y = action.x, action.y
                if x is None or y is None:
                    return ActionOutcome(
                        status="rejected",
                        reason="click requires selector or x/y",
                        action=action.public_dict(),
                    )
                page.mouse.click(x, y)
            return ActionOutcome(
                status="ok",
                reason="ok click",
                action=action.public_dict(),
                page=_page_snapshot(page),
            )

        if action.type == "press":
            key = action.key or "Enter"
            page.keyboard.press(key)
            return ActionOutcome(
                status="ok",
                reason=f"ok press {key}",
                action=action.public_dict(),
                page=_page_snapshot(page),
            )

        if action.type == "select":
            if not action.selector:
                return ActionOutcome(
                    status="rejected", reason="select requires selector", action=action.public_dict()
                )
            value = action.value if action.value is not None else (action.text or "")
            if hasattr(page, "select_option"):
                page.select_option(action.selector, value, timeout=timeout)
            else:
                page.fill(action.selector, value, timeout=timeout)
            return ActionOutcome(
                status="ok",
                reason="ok select",
                action=action.public_dict(),
                page=_page_snapshot(page),
            )

        if action.type in ("type", "fill"):
            text = action.text or ""
            if action.selector:
                if action.type == "fill":
                    page.fill(action.selector, text, timeout=timeout)
                else:
                    page.click(action.selector, timeout=timeout)
                    page.keyboard.type(text, delay=20)
            else:
                page.keyboard.type(text, delay=20)
            logged = dict(action.public_dict())
            logged["text"] = "[REDACTED]"
            logged["text_len"] = len(text)
            return ActionOutcome(
                status="ok",
                reason=f"ok {action.type}",
                action=logged,
                page=_page_snapshot(page),
            )

        if action.type == "scroll":
            if action.selector:
                loc = page.locator(action.selector).first
                loc.scroll_into_view_if_needed(timeout=timeout)
            else:
                page.mouse.wheel(action.delta_x, action.delta_y)
            return ActionOutcome(
                status="ok",
                reason="ok scroll",
                action=action.public_dict(),
                page=_page_snapshot(page),
            )

        if action.type == "wait":
            remaining = int(action.ms)
            if cancel_check:
                while remaining > 0:
                    if cancel_check():
                        return ActionOutcome(
                            status="cancelled",
                            reason="cancelled",
                            action=action.public_dict(),
                        )
                    chunk = min(100, remaining)
                    page.wait_for_timeout(chunk)
                    remaining -= chunk
            else:
                page.wait_for_timeout(int(action.ms))
            return ActionOutcome(
                status="ok",
                reason=f"ok wait {action.ms}ms",
                action=action.public_dict(),
                page=_page_snapshot(page),
            )

        return ActionOutcome(
            status="rejected",
            reason=f"unknown action {action.type}",
            action=action.public_dict(),
        )
    except ActionError as e:
        return ActionOutcome(status="rejected", reason=str(e), action=action.public_dict())
    except Exception as e:
        name = type(e).__name__
        return ActionOutcome(
            status="rejected",
            reason=redact_text(f"action {action.type} error: {name}"),
            action=action.public_dict(),
            page=_page_snapshot(page),
        )


def execute_actions(
    page: Any,
    actions: list[Action],
    *,
    allow_origins: list[str] | None = None,
    cancel_check: Callable[[], bool] | None = None,
    timeout_ms: int = 15_000,
    confirmed: bool = False,
    executor_paused: bool = False,
) -> list[ActionOutcome]:
    """Execute 1–3 validated actions. Stops on cancel, failure, or needs_confirm."""
    out: list[ActionOutcome] = []
    current = _page_origin(page)
    for action in actions[:MAX_ACTIONS_PER_TURN]:
        if cancel_check and cancel_check():
            out.append(
                ActionOutcome(status="cancelled", reason="cancelled", action=action.public_dict())
            )
            break
        result = execute_action(
            page,
            action,
            allow_origins=allow_origins,
            current_origin=current,
            cancel_check=cancel_check,
            timeout_ms=timeout_ms,
            confirmed=confirmed,
            executor_paused=executor_paused,
        )
        if result.page and result.page.get("origin"):
            current = result.page.get("origin")
        out.append(result)
        if result.status in ("cancelled", "rejected", "fail", "needs_confirm", "ask_human"):
            break
        if result.status == "done":
            break
    return out


def origin_of_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        p = urlparse(url.strip())
    except Exception:
        return None
    if p.scheme not in ("http", "https") or not p.hostname:
        return None
    host = p.hostname.lower().rstrip(".")
    port = p.port
    default = 80 if p.scheme == "http" else 443
    if port and port != default:
        return f"{p.scheme}://{host}:{port}"
    return f"{p.scheme}://{host}"


def sanitize_action_url(url: str) -> str:
    """Origin + path only; drop userinfo, fragment, and secret query keys."""
    try:
        p = urlparse(url)
    except Exception:
        return ""
    origin = origin_of_url(url) or ""
    path = p.path or "/"
    return f"{origin}{path}"


def _canonical_selector(item: dict[str, Any]) -> str | None:
    raw = item.get("selector")
    if raw is None:
        raw = item.get("css")
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ActionError("css/selector must be a non-empty string")
    selector = raw.strip()
    if len(selector) > MAX_SELECTOR_LEN or not _SELECTOR_OK.match(selector):
        raise ActionError("css/selector rejected")
    low = selector.lower()
    if "javascript:" in low or "data:" in low:
        raise ActionError("css/selector rejected")
    return selector


def _reject_dangerous_url(url: str) -> None:
    raw = (url or "").strip()
    low = raw.lower()
    if low.startswith("javascript:") or "javascript:" in low:
        raise ActionError("blocked scheme: javascript")
    if low.startswith("data:"):
        raise ActionError("blocked scheme: data")
    if low.startswith("file:"):
        raise ActionError("blocked scheme: file")
    try:
        p = urlparse(raw)
    except Exception as e:
        raise ActionError("invalid url") from e
    scheme = (p.scheme or "").lower()
    if scheme in BLOCKED_SCHEMES or scheme not in ("http", "https"):
        raise ActionError(f"blocked scheme: {scheme or '(none)'}")
    if p.username or p.password:
        raise ActionError("url must not contain credentials")
    if not p.hostname:
        raise ActionError("url missing host")


def _reject_injected_code(text: str) -> None:
    low = text.lower()
    if "javascript:" in low:
        raise ActionError("text contains javascript:")
    if "<script" in low:
        raise ActionError("text contains script")


def _normalize_press_key(raw: Any) -> str:
    key = str(raw or "Enter").strip()
    low = key.lower()
    if low not in ALLOWED_PRESS_KEYS:
        raise ActionError(f"press key not allowed: {key[:32]}")
    mapping = {
        "enter": "Enter",
        "tab": "Tab",
        "escape": "Escape",
        "esc": "Escape",
        "space": "Space",
        "backspace": "Backspace",
        "arrowup": "ArrowUp",
        "arrowdown": "ArrowDown",
        "arrowleft": "ArrowLeft",
        "arrowright": "ArrowRight",
        "home": "Home",
        "end": "End",
    }
    return mapping[low]


def _extract_json(text: str) -> Any | None:
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


def _redact_text_for_log(text: str) -> str:
    if len(text) > 80:
        text = text[:77] + "..."
    return redact_text(text)


def _page_origin(page: Any) -> str | None:
    try:
        url = getattr(page, "url", None) or ""
    except Exception:
        return None
    return origin_of_url(str(url))


def _page_snapshot(page: Any) -> dict[str, Any]:
    url = ""
    title = ""
    try:
        url = str(getattr(page, "url", "") or "")
    except Exception:
        url = ""
    try:
        title_fn = getattr(page, "title", None)
        if callable(title_fn):
            title = str(title_fn() or "")
        else:
            title = str(getattr(page, "title", "") or "")
    except Exception:
        title = ""
    safe_url = sanitize_action_url(url) if url else ""
    origin = origin_of_url(url) or ""
    return redact_any(
        {
            "url": safe_url,
            "origin": origin,
            "title": title[:200],
        }
    )


def sleep_cancellable(ms: int, cancel_check: Callable[[], bool] | None) -> bool:
    """Sleep up to ms milliseconds. Returns True if cancelled."""
    deadline = time.monotonic() + max(0, ms) / 1000.0
    while time.monotonic() < deadline:
        if cancel_check and cancel_check():
            return True
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
    return False
