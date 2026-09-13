"""Unified Teach Chat timeline: agent + human Playwright steps.

Hub assigns global monotonic `seq`. This module is the local merge helper
used on takeover_stop and in tests. Raw DOM events never become exportable
steps.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

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
