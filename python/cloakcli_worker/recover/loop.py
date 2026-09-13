"""RECOVER PATH (not teach): local selectors → text+DOM → one compressed vision shot.

Teach recording/export is the Rust CLI + extensions/teach/. This module only
runs after a skill step stalls. Success at any stage stops the cascade.
Default wall-clock ~90s, max 3 model rounds. Telemetry is observational.
"""

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
from .local import try_local_recover
from .observe import (
    CoordBinding,
    Observation,
    binding_still_valid,
    capture_observation,
    clip_around_selector,
)
from .origin import origin_of, url_allowed
from .provider import OpenAICompatProvider, ProviderError

TEXT_PROMPT = """You recover a stalled browser automation skill on the EXISTING page.
This is the TEXT stage: you get URL, failed action, and a structured clickable DOM summary.
NO screenshot is attached. Prefer css from the clickable list.
Form whitelist: click, fill, press, select, small scroll. type is fill-like.
You CANNOT read local files, run shell/host code, or execute arbitrary JavaScript.
Return JSON only, schema_version 1:
{"schema_version":1,"action":"click|type|fill|press|select|scroll|wait|goto|done|fail|ask_human"}
click: {"css":"..."}
press: {"key":"Enter|Tab|Escape|ArrowDown|..."}
select: {"css":"...","value":"..."}
fill/type: {"css":"...","text":"..."}  login passwords are allowed; never API keys/Authorization/cookies.
scroll: {"delta_y":int} small only (|delta|<=800) or {"css":"..."}
wait: {"ms":int}
goto: {"url":"..."} same origin unless allow_hosts; never file:/javascript:/data:
done/fail/ask_human: {"reason":"..."}
If you cannot decide from the DOM, return {"action":"fail","reason":"need vision"}.
"""

SYSTEM_PROMPT = """You recover a stalled browser automation skill on the EXISTING page.
This is the VISION stage: ONE compressed/crop screenshot is attached (never a full-page original).
Form whitelist: click, fill, press, select, small scroll. type is fill-like.
You CANNOT read local files, run shell/host code, or execute arbitrary JavaScript.
Return a JSON object only, schema_version 1, one action:
{"schema_version":1,"action":"click|type|fill|press|select|scroll|wait|goto|done|fail|ask_human"}
click: {"css":"..."} OR {"x":int,"y":int,"screenshot_id":"<current screenshot_id>"}
  Coordinate clicks REQUIRE screenshot_id equal to this observation's screenshot_id.
  Omitting it, or using a previous id after navigation/viewport change, is rejected.
  Coords are CSS pixels of the CURRENT screenshot.
press: {"key":"Enter|Tab|Escape|ArrowDown|..."}
select: {"css":"...","value":"..."}
type: {"css":"...","text":"..."}  click the field then keyboard.type
fill: {"css":"...","text":"..."}  Playwright page.fill (replace the input value)
  You MAY type into username/password form fields when the goal requires login.
  Do not invent API keys or paste Authorization headers / cookie values.
scroll: {"delta_y":int} small only (|delta|<=800) or css
wait: {"ms":int}
goto: {"url":"..."} same origin unless allow_hosts; never file:/javascript:/data:
done: {"reason":"..."} when the goal is achieved
fail: {"reason":"..."} when the goal cannot be achieved
ask_human: {"reason":"..."} when a human must decide
Prefer css selectors from the clickable list over coordinates.
Do not ask for another screenshot.
"""


@dataclass
class RecoverResult:
    status: str
    reason: str
    trajectory_path: str
    run_id: str
    loops: int = 0
    actions: int = 0
    tokens: int = 0
    model_rounds: int = 0
    screenshot_bytes: int = 0
    latency_ms: int = 0
    stage: str = ""

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
    model_rounds: int = 0
    screenshot_bytes: int = 0
    screenshot_count: int = 0
    screenshot_index: int = 0
    latency_ms: int = 0
    stage: str = "local"
    need_vision: bool = False
    vision_attached: bool = False


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
            tokens=st.tokens,
            model_rounds=st.model_rounds,
            screenshot_bytes=st.screenshot_bytes,
            latency_ms=st.latency_ms,
            stage=st.stage,
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
            "telemetry": {
                "tokens": st.tokens,
                "latency_ms": st.latency_ms,
                "model_rounds": st.model_rounds,
                "screenshot_bytes": st.screenshot_bytes,
                "screenshot_count": st.screenshot_count,
                "stage": st.stage,
                "status": status,
            },
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
        max_model_rounds=int(getattr(cfg, "max_model_rounds", 3) or 3),
    )

    try:
        local_ok, local_detail = try_local_recover(page, stall)
        audit("local_attempt", **local_detail)
        if local_ok:
            st.stage = "local"
            st.actions += 1
            audit("recover_end", status="done", stage="local")
            return write_traj("done", "local selectors recovered the stall")

        max_rounds = int(getattr(cfg, "max_model_rounds", 3) or 3)

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
            if st.model_rounds >= max_rounds:
                audit("recover_end", status="failed")
                return write_traj("failed", "max model rounds exceeded")

            st.loops += 1
            stage = "vision" if st.need_vision else "text"
            st.stage = stage
            attach = stage == "vision"
            clip = None
            if attach:
                sel = stall.get("selector") if isinstance(stall, dict) else None
                clip = clip_around_selector(page, sel if isinstance(sel, str) else None)
                st.screenshot_index += 1
                obs_index = st.screenshot_index
            else:
                obs_index = st.loops
            try:
                obs = capture_observation(
                    page,
                    traj_dir,
                    obs_index,
                    root,
                    attach_image=attach,
                    clip=clip,
                )
            except Exception as e:
                audit("observe_error", error=type(e).__name__)
                return write_traj("failed", f"observe failed: {type(e).__name__}")
            st.binding = obs.binding
            if attach:
                st.screenshots.append(obs.screenshot_path)
                st.screenshot_bytes += int(obs.screenshot_bytes or 0)
                st.screenshot_count += 1
                st.vision_attached = True
            audit(
                "observe",
                screenshot_id=obs.screenshot_id,
                path=obs.screenshot_path,
                url=obs.url_safe,
                viewport={"width": obs.viewport[0], "height": obs.viewport[1]},
                clickable_count=len(obs.clickables),
                stage=stage,
                image_attached=attach,
                screenshot_bytes=obs.screenshot_bytes,
                compressed=obs.compressed,
                full_page=False,
            )

            messages = _build_messages(
                goal=goal,
                stall=stall_safe,
                obs=obs,
                feedback=st.last_feedback,
                remaining=remaining,
                allow_hosts=cfg.allow_hosts,
                task_origin=task_origin,
                stage=stage,
            )
            audit("model_request", loop=st.loops, model=cfg.model, stage=stage, image=attach)
            t_req = time.monotonic()
            try:
                raw, usage = provider.complete(
                    cfg,
                    messages,
                    image_b64=obs.image_b64 if attach else None,
                    timeout_sec=min(60.0, max(8.0, remaining)),
                )
            except ProviderError as e:
                audit("model_error", error=str(e))
                return write_traj("failed", f"model error: {e}")
            except Exception as e:
                audit("model_error", error=type(e).__name__)
                return write_traj("failed", f"model error: {type(e).__name__}")
            st.latency_ms += int((time.monotonic() - t_req) * 1000)
            st.model_rounds += 1

            st.tokens += int(usage or 0)
            parsed = parse_model_output(raw)
            audit(
                "model_response",
                actions=[a.public_dict() for a in parsed.actions],
                errors=parsed.errors,
                tokens=st.tokens,
                stage=stage,
            )
            if _should_escalate(parsed, stage):
                st.need_vision = True
                st.last_feedback = "escalating to vision (text stage could not resolve)"
                audit("escalate_vision", round=st.model_rounds)
                continue
            if not parsed.actions:
                st.consecutive_rejects += 1
                st.last_feedback = "rejected: " + ("; ".join(parsed.errors) or "no actions")
                if st.consecutive_rejects >= 5:
                    return write_traj("failed", "too many invalid model outputs")
                if stage == "text":
                    st.need_vision = True
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
                    if stage == "text" and (
                        action.x is not None or (action.reason or "").find("vision") >= 0
                    ):
                        st.need_vision = True
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


def _should_escalate(parsed: Any, stage: str) -> bool:
    """Text stage → vision when the model says it cannot act from DOM alone.

    Coordinate clicks still execute (and reject) in text so tests/audit see the
    reject; vision is requested via the rejected-action path.
    """
    if stage != "text":
        return False
    if not parsed.actions:
        return True
    for a in parsed.actions:
        reason = (a.reason or "").lower()
        if a.type == "fail" and any(
            w in reason for w in ("vision", "screenshot", "can't see", "cannot see")
        ):
            return True
    return False


def _build_messages(
    *,
    goal: str,
    stall: dict[str, Any],
    obs: Observation,
    feedback: str,
    remaining: float,
    allow_hosts: list[str],
    task_origin: str | None,
    stage: str = "text",
) -> list[dict[str, Any]]:
    clickable_brief = obs.clickables[:12] if stage == "vision" else obs.clickables[:40]
    user = {
        "goal": goal,
        "stall": stall,
        "url": obs.url_safe,
        "title": obs.title,
        "viewport": {"width": obs.viewport[0], "height": obs.viewport[1]},
        "screenshot_id": obs.screenshot_id if stage == "vision" else None,
        "clickables": clickable_brief,
        "task_origin": task_origin,
        "allow_hosts": allow_hosts,
        "seconds_left": int(max(0, remaining)),
        "previous_feedback": feedback or None,
        "stage": stage,
        "image_attached": stage == "vision",
    }
    prompt = SYSTEM_PROMPT if stage == "vision" else TEXT_PROMPT
    return [
        {"role": "system", "content": prompt},
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
            # Coordinate click MUST bind to the current observation screenshot.
            if not action.screenshot_id:
                st.last_feedback = "rejected click: coordinate click requires screenshot_id"
                audit("action_reject", action="click", reason="screenshot_id required")
                return "rejected"
            if action.screenshot_id != obs.screenshot_id:
                st.last_feedback = "rejected click: screenshot_id does not match current observation"
                audit("action_reject", action="click", reason="screenshot_id mismatch")
                return "rejected"
            if st.binding and action.screenshot_id != st.binding.screenshot_id:
                st.last_feedback = "rejected click: screenshot_id does not match current binding"
                audit("action_reject", action="click", reason="screenshot_id mismatch")
                return "rejected"
            if not binding_still_valid(page, st.binding):
                st.last_feedback = "rejected click: coordinates invalidated (navigation/viewport/screenshot)"
                audit("action_reject", action="click", reason="coords invalidated")
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

        if action.type == "press":
            key = action.key or "Enter"
            page.keyboard.press(key)
            audit("action_execute", action=action.public_dict())
            st.last_feedback = f"ok press {key}"
            return "ok"

        if action.type == "select":
            if not action.css:
                st.last_feedback = "rejected select: css required"
                audit("action_reject", action="select", reason="css required")
                return "rejected"
            value = action.value if action.value is not None else (action.text or "")
            if hasattr(page, "select_option"):
                page.select_option(action.css, value, timeout=timeout)
            else:
                page.fill(action.css, value, timeout=timeout)
            audit("action_execute", action=action.public_dict())
            st.last_feedback = "ok select"
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
