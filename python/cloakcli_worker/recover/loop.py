"""Recover state machine: observe → model → whitelist actions on the current page."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..llm_config import LlmConfig
from ..paths import ensure_under_root
from ..redact import redact_any, redact_text
from .actions import ActionError, RecoverAction, parse_model_output
from .observe import CoordBinding, Observation, binding_still_valid, capture_observation
from .origin import origin_of, url_allowed
from .provider import OpenAICompatProvider, ProviderError

SYSTEM_PROMPT = """You recover a stalled browser automation skill on the EXISTING page.
You may click, type/fill, scroll, wait, or goto (policy-limited). Observation screenshots are provided each turn.
You CANNOT read local files, run shell/host code, or execute arbitrary JavaScript.
Return a JSON object only, schema_version 1, one action:
{"schema_version":1,"action":"click|type|fill|scroll|wait|goto|done|fail|ask_human"}
click: {"css":"..."} OR {"x":int,"y":int,"screenshot_id":"obs-NNN"} (coords are CSS pixels of the CURRENT screenshot)
type/fill: {"css":"...","text":"..."}  (do not invent passwords/API keys)
scroll: {"delta_y":int} optional delta_x or css
wait: {"ms":int}
goto: {"url":"..."} same origin unless allow_hosts; never file:/javascript:/data:
done: {"reason":"..."} when the goal is achieved
fail: {"reason":"..."} when the goal cannot be achieved
ask_human: {"reason":"..."} when a human must decide
Prefer css selectors from the clickable list over coordinates.
"""


@dataclass
class RecoverResult:
    status: str
    reason: str
    trajectory_path: str
    run_id: str
    loops: int = 0
    actions: int = 0

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


class AskHumanError(Exception):
    def __init__(self, result: RecoverResult):
        super().__init__(f"ASK_HUMAN: {result.reason}")
        self.result = result


class RecoverFailed(Exception):
    def __init__(self, result: RecoverResult):
        prefix = {
            "timeout": "RECOVER_TIMEOUT",
            "cancelled": "RECOVER_CANCELLED",
            "paused": "ASK_HUMAN",
        }.get(result.status, "RECOVER_FAILED")
        super().__init__(f"{prefix}: {result.reason}")
        self.result = result


@dataclass
class _State:
    events: list[dict[str, Any]] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)
    loops: int = 0
    actions: int = 0
    tokens: int = 0
    last_feedback: str = ""
    consecutive_rejects: int = 0
    binding: CoordBinding | None = None


def run_recover(
    *,
    page: Any,
    goal: str,
    stall: dict[str, Any],
    artifacts_dir: Path,
    skill_name: str,
    task_origin: str | None,
    cfg: LlmConfig,
    root: Path,
    provider: Any | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> RecoverResult:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    traj_dir = ensure_under_root(artifacts_dir / "recover" / run_id, root)
    traj_dir.mkdir(parents=True, exist_ok=True)
    events_path = traj_dir / "events.jsonl"
    traj_path = traj_dir / "trajectory.json"

    deadline = time.monotonic() + float(cfg.recover_timeout_sec)
    provider = provider or OpenAICompatProvider()
    st = _State()
    stall_safe = redact_any(stall)

    def audit(event: str, **fields: Any) -> None:
        rec = {"ts": time.time(), "event": event, **redact_any(fields)}
        st.events.append(rec)
        with events_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def write_traj(status: str, reason: str) -> RecoverResult:
        result = RecoverResult(
            status=status,
            reason=redact_text(reason),
            trajectory_path=str(traj_path),
            run_id=run_id,
            loops=st.loops,
            actions=st.actions,
        )
        body = {
            "schema_version": 1,
            "skill": skill_name,
            "goal": goal,
            "stall": stall_safe,
            "task_origin": task_origin,
            "status": status,
            "reason": result.reason,
            "run_id": run_id,
            "loops": st.loops,
            "actions": st.actions,
            "tokens": st.tokens,
            "screenshots": st.screenshots,
            "events": st.events,
            "recover_timeout_sec": cfg.recover_timeout_sec,
            "model": cfg.model,
            "base_url": cfg.base_url,
            "ended_at": datetime.now(timezone.utc).isoformat(),
        }
        traj_path.write_text(
            json.dumps(redact_any(body), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return result

    def cancelled() -> bool:
        if cancel_check and cancel_check():
            return True
        if (traj_dir / "CANCEL").is_file():
            return True
        return False

    def page_closed() -> bool:
        try:
            fn = getattr(page, "is_closed", None)
            if callable(fn):
                return bool(fn())
        except Exception:
            return True
        return False

    audit(
        "recover_start",
        goal=goal,
        stall=stall_safe,
        timeout_sec=cfg.recover_timeout_sec,
        task_origin=task_origin,
        model=cfg.model,
        base_url=cfg.base_url,
    )

    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                audit("recover_end", status="timeout")
                return write_traj("timeout", "recover wall-clock budget exhausted")
            if cancelled():
                audit("recover_end", status="cancelled")
                return write_traj("cancelled", "cancelled")
            if page_closed():
                audit("recover_end", status="failed")
                return write_traj("failed", "browser page closed")
            if st.loops >= int(cfg.max_loops):
                audit("recover_end", status="failed")
                return write_traj("failed", "max recover loops exceeded")
            if st.actions >= int(cfg.max_actions):
                audit("recover_end", status="failed")
                return write_traj("failed", "max recover actions exceeded")
            if st.tokens >= int(cfg.max_tokens_per_recover):
                audit("recover_end", status="failed")
                return write_traj("failed", "token soft cap exceeded")

            st.loops += 1
            try:
                obs = capture_observation(page, traj_dir, st.loops, root)
            except Exception as e:
                audit("observe_error", error=type(e).__name__)
                return write_traj("failed", f"observe failed: {type(e).__name__}")
            st.binding = obs.binding
            st.screenshots.append(obs.screenshot_path)
            audit(
                "observe",
                screenshot_id=obs.screenshot_id,
                path=obs.screenshot_path,
                url=obs.url_safe,
                viewport={"width": obs.viewport[0], "height": obs.viewport[1]},
                clickable_count=len(obs.clickables),
            )

            messages = _build_messages(
                goal=goal,
                stall=stall_safe,
                obs=obs,
                feedback=st.last_feedback,
                remaining=remaining,
                allow_hosts=cfg.allow_hosts,
                task_origin=task_origin,
            )
            audit("model_request", loop=st.loops, model=cfg.model)
            try:
                raw, usage = provider.complete(
                    cfg,
                    messages,
                    image_b64=obs.image_b64,
                    timeout_sec=min(60.0, max(8.0, remaining)),
                )
            except ProviderError as e:
                audit("model_error", error=str(e))
                return write_traj("failed", f"model error: {e}")
            except Exception as e:
                audit("model_error", error=type(e).__name__)
                return write_traj("failed", f"model error: {type(e).__name__}")

            st.tokens += int(usage or 0)
            parsed = parse_model_output(raw)
            audit(
                "model_response",
                actions=[a.public_dict() for a in parsed.actions],
                errors=parsed.errors,
                tokens=st.tokens,
            )
            if not parsed.actions:
                st.consecutive_rejects += 1
                st.last_feedback = "rejected: " + ("; ".join(parsed.errors) or "no actions")
                if st.consecutive_rejects >= 5:
                    return write_traj("failed", "too many invalid model outputs")
                continue

            stop: RecoverResult | None = None
            for action in parsed.actions:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    stop = write_traj("timeout", "recover wall-clock budget exhausted")
                    break
                if cancelled():
                    stop = write_traj("cancelled", "cancelled")
                    break
                outcome = _execute_action(
                    page=page,
                    action=action,
                    obs=obs,
                    cfg=cfg,
                    task_origin=task_origin,
                    remaining_ms=int(remaining * 1000),
                    audit=audit,
                    st=st,
                )
                if outcome == "rejected":
                    st.consecutive_rejects += 1
                    if st.consecutive_rejects >= 8:
                        stop = write_traj("failed", "too many rejected actions")
                        break
                    continue
                st.consecutive_rejects = 0
                st.actions += 1
                if outcome == "done":
                    audit("recover_end", status="done")
                    stop = write_traj("done", action.reason or "goal completed")
                    break
                if outcome == "fail":
                    audit("recover_end", status="failed")
                    stop = write_traj("failed", action.reason or "model failed")
                    break
                if outcome == "ask_human":
                    audit("recover_end", status="ask_human")
                    stop = write_traj("ask_human", action.reason or "human needed")
                    break
                # invalidate coords after any mutating action except wait
                if action.type != "wait":
                    if st.binding:
                        st.binding.valid = False
            if stop is not None:
                return stop
    except AskHumanError:
        raise
    except Exception as e:
        audit("recover_crash", error=type(e).__name__)
        return write_traj("failed", f"recover crashed: {type(e).__name__}")


def _build_messages(
    *,
    goal: str,
    stall: dict[str, Any],
    obs: Observation,
    feedback: str,
    remaining: float,
    allow_hosts: list[str],
    task_origin: str | None,
) -> list[dict[str, Any]]:
    clickable_brief = obs.clickables[:40]
    user = {
        "goal": goal,
        "stall": stall,
        "url": obs.url_safe,
        "title": obs.title,
        "viewport": {"width": obs.viewport[0], "height": obs.viewport[1]},
        "screenshot_id": obs.screenshot_id,
        "clickables": clickable_brief,
        "task_origin": task_origin,
        "allow_hosts": allow_hosts,
        "seconds_left": int(max(0, remaining)),
        "previous_feedback": feedback or None,
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(redact_any(user), ensure_ascii=False),
        },
    ]


def _execute_action(
    *,
    page: Any,
    action: RecoverAction,
    obs: Observation,
    cfg: LlmConfig,
    task_origin: str | None,
    remaining_ms: int,
    audit: Callable[..., None],
    st: _State,
) -> str:
    """Return done|fail|ask_human|ok|rejected."""
    timeout = max(1000, min(15000, remaining_ms))
    try:
        if action.type == "done":
            return "done"
        if action.type == "fail":
            return "fail"
        if action.type == "ask_human":
            return "ask_human"

        if action.type == "goto":
            ok, reason = url_allowed(
                action.url or "",
                task_origin=task_origin,
                allow_hosts=cfg.allow_hosts,
            )
            if not ok:
                st.last_feedback = f"rejected goto: {reason}"
                audit("action_reject", action="goto", reason=reason, url=action.url)
                return "rejected"
            page.goto(action.url, wait_until="domcontentloaded", timeout=timeout)
            audit("action_execute", action=action.public_dict())
            st.last_feedback = f"ok goto {origin_of(action.url)}"
            return "ok"

        if action.type == "click":
            if action.css:
                page.click(action.css, timeout=timeout)
                audit("action_execute", action=action.public_dict())
                st.last_feedback = f"ok click css={action.css}"
                return "ok"
            # coordinate click bound to current screenshot
            if not binding_still_valid(page, st.binding):
                st.last_feedback = "rejected click: coordinates invalidated (navigation/viewport/screenshot)"
                audit("action_reject", action="click", reason="coords invalidated")
                return "rejected"
            if action.screenshot_id and action.screenshot_id != obs.screenshot_id:
                st.last_feedback = "rejected click: screenshot_id does not match current observation"
                audit("action_reject", action="click", reason="screenshot_id mismatch")
                return "rejected"
            x, y = action.x, action.y
            w, h = obs.viewport
            if x is None or y is None or x < 0 or y < 0 or x >= w or y >= h:
                st.last_feedback = f"rejected click: coordinates out of bounds ({x},{y}) viewport={w}x{h}"
                audit("action_reject", action="click", reason="oob", x=x, y=y)
                return "rejected"
            page.mouse.click(x, y)
            audit("action_execute", action=action.public_dict())
            st.last_feedback = f"ok click coords=({x},{y})"
            return "ok"

        if action.type in ("type", "fill"):
            text = action.text or ""
            if action.css:
                if action.type == "fill":
                    page.fill(action.css, text, timeout=timeout)
                else:
                    page.click(action.css, timeout=timeout)
                    page.keyboard.type(text, delay=20)
            else:
                page.keyboard.type(text, delay=20)
            audit("action_execute", action={"action": action.type, "css": action.css, "text_len": len(text)})
            st.last_feedback = f"ok {action.type}"
            return "ok"

        if action.type == "scroll":
            if action.css:
                loc = page.locator(action.css).first
                loc.scroll_into_view_if_needed(timeout=timeout)
            else:
                page.mouse.wheel(action.delta_x, action.delta_y)
            audit("action_execute", action=action.public_dict())
            st.last_feedback = "ok scroll"
            return "ok"

        if action.type == "wait":
            page.wait_for_timeout(int(action.ms))
            audit("action_execute", action=action.public_dict())
            st.last_feedback = f"ok wait {action.ms}ms"
            return "ok"

        st.last_feedback = f"rejected unknown action {action.type}"
        audit("action_reject", action=action.type, reason="unknown")
        return "rejected"
    except ActionError as e:
        st.last_feedback = f"rejected: {e}"
        audit("action_reject", action=action.type, reason=str(e))
        return "rejected"
    except Exception as e:
        name = type(e).__name__
        st.last_feedback = f"action {action.type} error: {name}"
        audit("action_error", action=action.type, error=name)
        return "rejected"
