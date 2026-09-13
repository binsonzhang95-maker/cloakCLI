"""Unified Teach Chat timeline: agent + human Playwright steps.

Hub assigns global monotonic `seq`. This module is the local merge helper
used on takeover_stop and in tests. Raw DOM events never become exportable
steps.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import json

from .normalize import is_raw_dom_event, redact_step
from .redact import redact_any, redact_text

SCHEMA_VERSION = 1


def _now_ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _source_of(obj: dict[str, Any], default: str = "system") -> str:
    s = obj.get("source") or obj.get("origin")
    if isinstance(s, str):
        s = s.strip().lower()
        if s in {"llm", "agent"}:
            return "llm"
        if s in {"human", "extension"}:
            return "human" if s == "human" else "extension"
        if s in {"system", "worker"}:
            return s
    return default


@dataclass
class TimelineEvent:
    seq: int
    ts: str
    source: str
    event: str
    payload: dict[str, Any]
    exportable: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "seq": self.seq,
            "ts": self.ts,
            "source": self.source,
            "event": self.event,
            "exportable": self.exportable,
            "payload": self.payload,
        }


class Timeline:
    """Monotonic seq, timestamps, source. Dedup by request_id + action_index."""

    def __init__(self) -> None:
        self.seq = 0
        self.events: list[TimelineEvent] = []
        self._seen: set[tuple[str, int]] = set()

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def append(
        self,
        event: str,
        payload: dict[str, Any] | None = None,
        *,
        source: str = "system",
        ts: str | None = None,
        exportable: bool = False,
        request_id: str | None = None,
        action_index: int | None = None,
    ) -> TimelineEvent:
        payload = dict(payload or {})
        if request_id is not None and action_index is not None:
            key = (str(request_id), int(action_index))
            if key in self._seen:
                # Identical action already recorded; do not duplicate.
                return self.events[-1] if self.events else TimelineEvent(
                    seq=self.seq, ts=ts or _now_ts(), source=source, event=event,
                    payload=payload, exportable=False,
                )
            self._seen.add(key)
        rec = TimelineEvent(
            seq=self.next_seq(),
            ts=ts or _now_ts(),
            source=_source_of({"source": source}, source),
            event=event,
            payload=redact_any(payload) if not isinstance(payload, dict) else _redact_payload(payload),
            exportable=exportable and not is_raw_dom_event(payload),
        )
        self.events.append(rec)
        return rec

    def append_action(
        self,
        action: dict[str, Any],
        *,
        source: str,
        ts: str | None = None,
        request_id: str | None = None,
        action_index: int | None = None,
    ) -> TimelineEvent | None:
        if is_raw_dom_event(action):
            # Never store raw DOM as an exportable action.
            return None
        if action.get("action") in (None, ""):
            return None
        step = redact_step(action)
        step["source"] = "human" if source == "human" else step.get("source") or source
        if source == "human":
            step["source"] = "human"
        elif source in {"llm", "agent"}:
            step["source"] = "llm"
        return self.append(
            "action",
            step,
            source=step["source"],
            ts=ts or action.get("ts"),
            exportable=True,
            request_id=request_id,
            action_index=action_index,
        )

    def merge_human_steps(
        self,
        steps: list[dict[str, Any]],
        *,
        request_id: str | None = None,
    ) -> list[TimelineEvent]:
        """Insert normalized Playwright steps with source=human, original order."""
        out: list[TimelineEvent] = []
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            if is_raw_dom_event(step):
                continue
            rec = self.append_action(
                step,
                source="human",
                ts=step.get("ts") if isinstance(step.get("ts"), str) else None,
                request_id=request_id,
                action_index=i,
            )
            if rec is not None:
                out.append(rec)
        return out

    def merge_agent_steps(
        self,
        steps: list[dict[str, Any]],
        *,
        request_id: str | None = None,
    ) -> list[TimelineEvent]:
        out: list[TimelineEvent] = []
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            rec = self.append_action(
                step,
                source="llm",
                request_id=request_id,
                action_index=i,
            )
            if rec is not None:
                out.append(rec)
        return out

    def exportable_steps(self) -> list[dict[str, Any]]:
        """Skill-shaped steps: unified action schema only, source preserved."""
        out: list[dict[str, Any]] = []
        for ev in self.events:
            if not ev.exportable:
                continue
            if ev.event != "action":
                continue
            p = ev.payload
            if is_raw_dom_event(p):
                continue
            if not p.get("action"):
                continue
            step = dict(p)
            step["source"] = ev.source if ev.source in {"llm", "human"} else p.get("source")
            step.pop("kind", None)
            out.append(step)
        return out

    def snapshot(self) -> list[dict[str, Any]]:
        return [e.as_dict() for e in self.events]

    def seq_is_monotonic(self) -> bool:
        last = 0
        for e in self.events:
            if e.seq <= last:
                return False
            last = e.seq
        return True

    def human_summary(self) -> str:
        parts = []
        for s in self.exportable_steps():
            if s.get("source") != "human":
                continue
            act = s.get("action")
            if act == "goto":
                parts.append(f"goto {redact_text(str(s.get('url') or ''))}")
            elif act in ("fill", "type"):
                parts.append(f"{act} {s.get('selector') or ''} [REDACTED]")
            elif act == "click":
                parts.append(f"click {s.get('selector') or '(coords)'}")
            else:
                parts.append(str(act))
        return " → ".join(parts) if parts else "(no human steps)"


def _redact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    from .normalize import redact_step

    if payload.get("action") in ("fill", "type", "click", "goto", "press", "select", "wait", "scroll"):
        return redact_step(payload)
    return redact_any(payload) if isinstance(payload, dict) else payload


def merge_timelines(
    agent_steps: list[dict[str, Any]],
    human_steps: list[dict[str, Any]],
) -> Timeline:
    """Agent first (already executed), then human takeover steps in order."""
    tl = Timeline()
    tl.merge_agent_steps(agent_steps, request_id="agent")
    tl.merge_human_steps(human_steps, request_id="human")
    return tl


_EXPORT_ACTIONS = {
    "goto",
    "click",
    "fill",
    "type",
    "wait",
    "scroll",
    "press",
    "select",
}
_SKIP_ACTIONS = {"done", "fail", "ask_human"}
_SECRET_MARKERS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
)


def _source_export(src: Any) -> str:
    s = str(src or "").strip().lower()
    if s == "human":
        return "human"
    return "agent"


def _looks_secret_step(step: dict[str, Any]) -> bool:
    hay = " ".join(
        str(step.get(k) or "")
        for k in ("field_name", "selector", "css")
    ).lower()
    field = step.get("field") if isinstance(step.get("field"), dict) else {}
    hay = f"{hay} " + " ".join(
        str(field.get(k) or "") for k in ("type", "name", "id", "autocomplete")
    )
    h = hay.lower()
    if "pass" in h:
        return True
    return any(m in h for m in _SECRET_MARKERS)


def _var_name(step: dict[str, Any]) -> str:
    hay = " ".join(
        str(step.get(k) or "") for k in ("field_name", "selector")
    ).lower()
    if "token" in hay or "jwt" in hay:
        return "TOKEN"
    if "pass" in hay:
        return "PASSWORD"
    if "cookie" in hay:
        return "COOKIE"
    if "auth" in hay or "secret" in hay:
        return "SECRET"
    fn = str(step.get("field_name") or "").strip()
    if fn:
        ident = "".join(c.upper() if c.isalnum() else "_" for c in fn).strip("_")
        if ident:
            return ident[:32]
    return "SECRET"


def _placeholder(name: str) -> str:
    return "{{vars." + name + "}}"


def _is_placeholder(text: str) -> bool:
    t = (text or "").strip()
    return t.startswith("{{vars.") and t.endswith("}}") and len(t) <= 80 and "\n" not in t


def build_skill_draft(
    name: str,
    goal: str | None,
    steps: list[dict[str, Any]],
) -> dict[str, Any]:
    """Canonical skill.json body from merged Playwright steps.

    `source` is `human` or `agent`. Secrets become `{{vars.NAME}}`.
    Raw DOM events raise. Control actions (done/fail/ask_human) are skipped.
    """
    from .actions import FORBIDDEN_ACTIONS, validate_action
    from .normalize import HUMAN_EXTRA, is_raw_dom_event, sanitize_goto_url

    out_steps: list[dict[str, Any]] = []
    vars_list: list[str] = []
    skipped: list[str] = []
    n_agent = 0
    n_human = 0

    for i, raw in enumerate(steps):
        if not isinstance(raw, dict):
            raise ValueError(f"steps[{i}]: not an object")
        if is_raw_dom_event(raw):
            raise ValueError(f"steps[{i}]: raw DOM event is not an exportable skill step")
        action = str(raw.get("action") or raw.get("type") or "").strip().lower()
        if not action:
            raise ValueError(f"steps[{i}]: missing action type")
        if action in FORBIDDEN_ACTIONS:
            raise ValueError(f"steps[{i}]: forbidden action: {action}")
        if action in _SKIP_ACTIONS:
            skipped.append(f"steps[{i}]: skipped control action {action}")
            continue
        if action not in _EXPORT_ACTIONS:
            raise ValueError(f"steps[{i}]: unknown action: {action}")
        extra = HUMAN_EXTRA if _source_export(raw.get("source")) == "human" else None
        validate_action(raw, extra_allowed=extra)
        source = _source_export(raw.get("source"))
        step: dict[str, Any] = {"action": action, "source": source}
        sel = raw.get("selector") or raw.get("css")
        if isinstance(sel, str) and sel.strip():
            step["selector"] = sel.strip()
        if action == "click" and "selector" not in step:
            skipped.append(f"steps[{i}]: coordinate click is not exportable")
            continue
        if action == "goto":
            url = sanitize_goto_url(str(raw.get("url") or ""))
            if not url:
                raise ValueError(f"steps[{i}]: goto url rejected")
            # Skill drafts keep query values unescaped (`next=/app`) like Rust export.
            step["url"] = url.replace("%2F", "/").replace("%2f", "/")
        elif action in ("fill", "type"):
            text = raw.get("text", raw.get("value", ""))
            text = "" if text is None else str(text)
            if _is_placeholder(text):
                inner = text.strip()[8:-2]
                if inner and inner not in vars_list:
                    vars_list.append(inner)
                step["text"] = text.strip()
            elif _looks_secret_step(raw) or text == "[REDACTED]":
                name_v = _var_name(raw)
                if name_v not in vars_list:
                    vars_list.append(name_v)
                step["text"] = _placeholder(name_v)
            else:
                step["text"] = text
        elif action == "wait":
            step["ms"] = int(raw.get("ms") or raw.get("timeout") or 500)
        elif action == "scroll":
            step["delta_x"] = int(raw.get("delta_x") or raw.get("dx") or 0)
            step["delta_y"] = int(raw.get("delta_y") or raw.get("dy") or 0)
        elif action == "press" and raw.get("key"):
            step["key"] = raw["key"]
        elif action == "select" and raw.get("value") is not None:
            step["value"] = raw["value"]
        if raw.get("field_name"):
            step["field_name"] = raw["field_name"]
        if isinstance(raw.get("selectors"), list):
            step["selectors"] = [s for s in raw["selectors"] if isinstance(s, str) and s]
        blob = str(step)
        if "hunter2" in blob or "Bearer " in blob:
            raise ValueError(f"steps[{i}]: refusing plaintext secret")
        out_steps.append(step)
        if source == "human":
            n_human += 1
        else:
            n_agent += 1

    if not out_steps:
        raise ValueError("empty steps: record at least one Playwright action before export")

    description = f"Taught skill: {goal}" if goal else "Taught skill (Teach Chat)"
    params = [{"name": n, "required": True} for n in vars_list]
    skill: dict[str, Any] = {
        "schema_version": 1,
        "name": name,
        "description": description,
        "params": params,
        "steps": out_steps,
    }
    if goal:
        skill["goal"] = goal
    skill["_audit"] = {
        "n_steps": len(out_steps),
        "n_agent": n_agent,
        "n_human": n_human,
        "skipped": skipped,
        "params": vars_list,
    }
    dumped = json.dumps(skill)
    if "hunter2" in dumped:
        raise ValueError("refusing plaintext secret in skill draft")
    return skill
