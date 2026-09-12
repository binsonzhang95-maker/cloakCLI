"""Versioned recover action schema (whitelist + parameter bounds)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

ALLOWED_ACTIONS = {
    "click",
    "type",
    "fill",
    "scroll",
    "wait",
    "goto",
    "done",
    "fail",
    "ask_human",
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
    "screenshot",  # observation is automatic; not a model action
}

SCHEMA_VERSION = 1
MAX_TEXT_LEN = 4000
MAX_WAIT_MS = 30_000
MAX_SCROLL_DELTA = 20_000
MAX_ACTIONS_PER_TURN = 4
MAX_CSS_LEN = 500
MAX_URL_LEN = 2048
MAX_REASON_LEN = 500

_CSS_OK = re.compile(r"^[^;{}]{1,500}$")


class ActionError(ValueError):
    """Illegal or malformed recover action."""


@dataclass
class RecoverAction:
    type: str
    css: str | None = None
    x: int | None = None
    y: int | None = None
    screenshot_id: str | None = None
    text: str | None = None
    delta_x: int = 0
    delta_y: int = 0
    ms: int = 0
    url: str | None = None
    reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def public_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"action": self.type}
        if self.css:
            d["css"] = self.css
        if self.x is not None:
            d["x"] = self.x
        if self.y is not None:
            d["y"] = self.y
        if self.screenshot_id:
            d["screenshot_id"] = self.screenshot_id
        if self.text is not None:
            d["text"] = self.text if len(self.text) <= 80 else self.text[:77] + "..."
        if self.type == "scroll":
            d["delta_x"] = self.delta_x
            d["delta_y"] = self.delta_y
        if self.type == "wait":
            d["ms"] = self.ms
        if self.url:
            d["url"] = self.url
        if self.reason:
            d["reason"] = self.reason[:MAX_REASON_LEN]
        return d


@dataclass
class ParseResult:
    actions: list[RecoverAction]
    errors: list[str]
    schema_version: int = SCHEMA_VERSION


def parse_model_output(text: str) -> ParseResult:
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

    actions: list[RecoverAction] = []
    for i, item in enumerate(items[:MAX_ACTIONS_PER_TURN]):
        try:
            actions.append(validate_action(item))
        except ActionError as e:
            errors.append(f"actions[{i}]: {e}")
    return ParseResult(actions=actions, errors=errors, schema_version=schema_version)


def validate_action(item: Any) -> RecoverAction:
    if not isinstance(item, dict):
        raise ActionError("action must be an object")
    raw_type = item.get("action") or item.get("type") or item.get("name")
    if not isinstance(raw_type, str) or not raw_type.strip():
        raise ActionError("missing action type")
    atype = raw_type.strip().lower()
    if atype in FORBIDDEN_ACTIONS:
        raise ActionError(f"forbidden action: {atype}")
    if atype not in ALLOWED_ACTIONS:
        raise ActionError(f"unknown action: {atype}")

    css = item.get("css") or item.get("selector")
    if css is not None:
        if not isinstance(css, str) or not css.strip():
            raise ActionError("css/selector must be a non-empty string")
        css = css.strip()
        if len(css) > MAX_CSS_LEN or not _CSS_OK.match(css):
            raise ActionError("css/selector rejected")
    else:
        css = None

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
        sid = str(sid)[:80]
    else:
        sid = None

    text = item.get("text", item.get("value"))
    if text is not None:
        if not isinstance(text, str):
            text = str(text)
        if len(text) > MAX_TEXT_LEN:
            raise ActionError(f"text exceeds {MAX_TEXT_LEN} chars")

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

    reason = item.get("reason") or item.get("message") or ""
    if not isinstance(reason, str):
        reason = str(reason)
    reason = reason[:MAX_REASON_LEN]

    if atype == "click":
        if not css and (x is None or y is None):
            raise ActionError("click requires css or x/y")
    elif atype in ("type", "fill"):
        if text is None:
            raise ActionError(f"{atype} requires text")
    elif atype == "scroll":
        if dx == 0 and dy == 0 and not css:
            raise ActionError("scroll requires delta or css")
    elif atype == "wait":
        if ms <= 0:
            ms = 500
    elif atype == "goto":
        if not url:
            raise ActionError("goto requires url")
    elif atype in ("done", "fail", "ask_human"):
        if not reason:
            reason = atype

    return RecoverAction(
        type=atype,
        css=css,
        x=x,
        y=y,
        screenshot_id=sid,
        text=text,
        delta_x=dx,
        delta_y=dy,
        ms=ms,
        url=url,
        reason=reason,
        raw={k: v for k, v in item.items() if k not in ("text", "value")},
    )


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
    # Find first { ... } or [ ... ]
    for opener, closer in (("{", "}"), ("[", "]")):
        start = s.find(opener)
        end = s.rfind(closer)
        if start >= 0 and end > start:
            try:
                return json.loads(s[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None
