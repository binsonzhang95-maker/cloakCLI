#!/usr/bin/env python3
"""Pure Pinterest human-behavior helpers (no CloakBrowser fingerprint knobs).

Continuous randomized mouse trails, inertial scroll, log-normal/gamma pauses,
session personas, pin linger planning, visibility keepalive, micro reverse
scroll, and mixed close-path weights (nurture 0.2.3). Used by nurture browse
and register runners. Stdlib only. Every pause site must re-sample independently
(no fixed identical timing chains across steps/runs).
"""
from __future__ import annotations

import json
import math
import os
import random
import time
from typing import Any

PERSONA_NAMES = ("browse_only", "light_like", "deep_browse", "bounce_early")

PERSONAS: dict[str, dict[str, Any]] = {
    "browse_only": {
        "pins": (2, 6),
        "like_prob_range": (0.0, 0.0),
        "view_scale": 1.0,
        "bounce": False,
    },
    "light_like": {
        "pins": (2, 5),
        "like_prob_range": (0.15, 0.40),
        "view_scale": 0.9,
        "bounce": False,
    },
    "deep_browse": {
        "pins": (6, 12),
        "like_prob_range": (0.15, 0.40),
        "view_scale": 1.35,
        "bounce": False,
    },
    "bounce_early": {
        "pins": (1, 2),
        "like_prob_range": (0.15, 0.40),
        "view_scale": 0.55,
        "bounce": True,
    },
}

# Playwright cursor starts at (0, 0). Session-local; not a fingerprint surface.
_SESSION_MOUSE: dict[str, float] = {"x": 0.0, "y": 0.0}


def _as_rng(rng: Any) -> Any:
    return random if rng is None else rng


def reset_session_mouse(x: float = 0.0, y: float = 0.0) -> dict[str, float]:
    _SESSION_MOUSE["x"] = float(x)
    _SESSION_MOUSE["y"] = float(y)
    return _SESSION_MOUSE


def session_mouse() -> dict[str, float]:
    return _SESSION_MOUSE


def sample_lognormal_ms(
    lo_ms: int,
    hi_ms: int,
    *,
    mu: float | None = None,
    sigma: float = 0.5,
    rng: Any = None,
) -> int:
    """Truncated log-normal milliseconds. Not uniform randint."""
    rng = _as_rng(rng)
    lo = max(0, int(lo_ms))
    hi = max(lo, int(hi_ms))
    if hi <= lo:
        return lo
    if mu is None:
        mid = math.sqrt(max(1.0, float(lo)) * float(hi))
        mu = math.log(mid / 1000.0)
    val = int(rng.lognormvariate(mu, float(sigma)) * 1000.0)
    return max(lo, min(hi, val))


def sample_gamma_ms(
    lo_ms: int,
    hi_ms: int,
    *,
    alpha: float = 2.4,
    beta: float = 40.0,
    rng: Any = None,
) -> int:
    """Truncated gamma milliseconds. random.gammavariate mean = alpha * beta."""
    rng = _as_rng(rng)
    lo = max(0, int(lo_ms))
    hi = max(lo, int(hi_ms))
    if hi <= lo:
        return lo
    val = int(rng.gammavariate(float(alpha), float(beta)))
    return max(lo, min(hi, val))


def sample_pause_ms(
    lo_ms: int,
    hi_ms: int,
    *,
    rng: Any = None,
    kind: str = "lognormal",
) -> int:
    if kind == "gamma":
        return sample_gamma_ms(lo_ms, hi_ms, rng=rng)
    return sample_lognormal_ms(lo_ms, hi_ms, rng=rng)


def sample_key_delay_ms(rng: Any = None) -> int:
    """Inter-key delay ~80–180ms mean (gamma)."""
    return sample_gamma_ms(45, 240, alpha=4.0, beta=30.0, rng=rng)


def sample_quiet_window_ms(rng: Any = None) -> int:
    """1–2s quiet window after load before first interaction (TTI)."""
    return sample_lognormal_ms(1000, 2300, mu=0.45, sigma=0.32, rng=rng)


def choose_persona(name: str | None = None, *, rng: Any = None) -> str:
    rng = _as_rng(rng)
    raw = (name or "").strip().lower()
    if raw and raw not in ("auto", "*"):
        if raw not in PERSONAS:
            raise ValueError(f"unknown persona: {name}")
        return raw
    return str(rng.choice(PERSONA_NAMES))


def plan_nurture_session(
    *,
    persona: str | None = None,
    pins: int | None = None,
    min_sec: int = 120,
    max_sec: int = 180,
    rng: Any = None,
) -> dict[str, Any]:
    """Pick persona, pin count (0–12), like probability (may be 0), session target.

    Always clamps target_sec into [min_sec, max_sec].
    When pins is None/<=0 (auto), ~15–25% of sessions are feed-only (0 pins),
    mostly bounce_early / browse_only. Explicit pins>0 never forces zero-pin.
    """
    rng = _as_rng(rng)
    if max_sec < min_sec:
        min_sec, max_sec = max_sec, min_sec
    raw_persona = (persona or "").strip().lower()
    explicit_persona = bool(raw_persona) and raw_persona not in ("auto", "*")
    pname = choose_persona(persona, rng=rng)
    spec = PERSONAS[pname]
    feed_only = False
    if pins is not None and int(pins) > 0:
        n_pins = max(1, min(12, int(pins)))
    else:
        # Re-sample zero-pin session probability in [0.15, 0.25] each plan call.
        # Mostly bounce_early / browse_only; auto may reassign into those.
        # Explicit deep_browse / light_like keep their pin bands (no forced 0).
        zero_p = float(rng.uniform(0.15, 0.25))
        allow_zero = (not explicit_persona) or pname in ("bounce_early", "browse_only")
        if allow_zero and rng.random() < zero_p:
            feed_only = True
            n_pins = 0
            if pname not in ("bounce_early", "browse_only"):
                pname = str(rng.choice(("bounce_early", "browse_only")))
                spec = PERSONAS[pname]
        else:
            lo, hi = spec["pins"]
            n_pins = max(1, min(12, int(rng.randint(int(lo), int(hi)))))
    pr = spec.get("like_prob_range") or (0.0, 0.0)
    like_p = 0.0 if feed_only or n_pins == 0 else float(
        rng.uniform(float(pr[0]), float(pr[1]))
    )
    span = max(0, int(max_sec) - int(min_sec))
    if feed_only or spec.get("bounce"):
        target_sec = int(min_sec) + int(span * rng.uniform(0.0, 0.2))
    else:
        target_sec = int(min_sec) + int(span * rng.uniform(0.0, 1.0))
    target_sec = max(int(min_sec), min(int(max_sec), target_sec))
    # Feed-only dwell band (independent of min_sec clamp for gate accounting).
    feed_dwell_sec = 0
    feed_scroll_screens = 0
    if feed_only or n_pins == 0:
        feed_dwell_sec = int(
            round(
                sample_lognormal_ms(
                    25_000, 90_000, mu=math.log(45.0), sigma=0.30, rng=rng
                )
                / 1000.0
            )
        )
        feed_scroll_screens = int(rng.randint(3, 7))
    return {
        "persona": pname,
        "pins": n_pins,
        "like_prob": like_p,
        "min_sec": int(min_sec),
        "max_sec": int(max_sec),
        "target_sec": target_sec,
        "bounce": bool(spec.get("bounce")) or feed_only,
        "view_scale": float(spec.get("view_scale") or 1.0),
        "feed_only": bool(feed_only or n_pins == 0),
        "feed_dwell_sec": int(feed_dwell_sec),
        "feed_scroll_screens": int(feed_scroll_screens),
    }


def cubic_bezier(
    p0: tuple[float, float],
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
    t: float,
) -> tuple[float, float]:
    u = 1.0 - t
    x = (u**3) * p0[0] + 3 * (u**2) * t * p1[0] + 3 * u * (t**2) * p2[0] + (t**3) * p3[0]
    y = (u**3) * p0[1] + 3 * (u**2) * t * p1[1] + 3 * u * (t**2) * p2[1] + (t**3) * p3[1]
    return (x, y)


def _norm(dx: float, dy: float) -> tuple[float, float]:
    mag = math.hypot(dx, dy) or 1.0
    return (dx / mag, dy / mag)


def build_mouse_path(
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    rng: Any = None,
) -> list[tuple[float, float, int]]:
    """Many small steps: multi-segment bezier, jitter, wander, overshoot+correct.

    Not a single straight line or one simple Bézier to the target center.
    Returns [(x, y, dwell_ms), ...]. Dwell is gamma/log-normal so speed varies.
    """
    rng = _as_rng(rng)
    x0, y0 = float(start[0]), float(start[1])
    x1, y1 = float(end[0]), float(end[1])
    dx, dy = x1 - x0, y1 - y0
    dist = math.hypot(dx, dy)
    if dist < 2.0:
        pts = [(x0, y0, sample_gamma_ms(6, 28, alpha=2.0, beta=8.0, rng=rng))]
        for _ in range(int(rng.randint(4, 8))):
            pts.append(
                (
                    x1 + rng.uniform(-1.2, 1.2),
                    y1 + rng.uniform(-1.2, 1.2),
                    sample_gamma_ms(6, 24, alpha=2.0, beta=7.0, rng=rng),
                )
            )
        pts.append((x1, y1, sample_gamma_ms(8, 30, alpha=2.0, beta=8.0, rng=rng)))
        return pts

    ux, uy = _norm(dx, dy)
    px, py = -uy, ux
    n_seg = int(rng.randint(2, 4))
    waypoints: list[tuple[float, float]] = [(x0, y0)]
    for i in range(1, n_seg):
        t = i / n_seg + rng.uniform(-0.08, 0.08)
        t = min(0.9, max(0.1, t))
        lat = rng.uniform(-0.55, 0.55) * dist * rng.uniform(0.15, 0.45)
        wander = rng.uniform(-0.12, 0.12) * dist
        waypoints.append(
            (
                x0 + dx * t + px * lat + ux * wander,
                y0 + dy * t + py * lat + uy * wander,
            )
        )
    overshoot_px = rng.uniform(10.0, 28.0) + 0.02 * dist
    ang = rng.uniform(-0.7, 0.7)
    ox = x1 + (ux * math.cos(ang) - uy * math.sin(ang)) * overshoot_px
    oy = y1 + (ux * math.sin(ang) + uy * math.cos(ang)) * overshoot_px
    waypoints.append((ox, oy))
    waypoints.append((x1, y1))

    points: list[tuple[float, float, int]] = [
        (x0, y0, sample_gamma_ms(5, 18, alpha=2.0, beta=5.0, rng=rng))
    ]
    for si in range(len(waypoints) - 1):
        a = waypoints[si]
        b = waypoints[si + 1]
        sdx, sdy = b[0] - a[0], b[1] - a[1]
        slen = math.hypot(sdx, sdy) or 1.0
        sux, suy = sdx / slen, sdy / slen
        spx, spy = -suy, sux
        c1off = rng.uniform(0.2, 0.45)
        c2off = rng.uniform(0.55, 0.85)
        c1lat = rng.uniform(-0.5, 0.5) * slen * rng.uniform(0.2, 0.6)
        c2lat = rng.uniform(-0.5, 0.5) * slen * rng.uniform(0.2, 0.6)
        if rng.random() < 0.7:
            c2lat = -abs(c2lat) if c1lat > 0 else abs(c2lat)
        p1 = (a[0] + sux * slen * c1off + spx * c1lat, a[1] + suy * slen * c1off + spy * c1lat)
        p2 = (a[0] + sux * slen * c2off + spx * c2lat, a[1] + suy * slen * c2off + spy * c2lat)
        n_steps = max(8, min(36, int(slen / rng.uniform(4.5, 9.0))))
        for k in range(1, n_steps + 1):
            t = k / n_steps
            bx, by = cubic_bezier(a, p1, p2, b, t)
            jamp = rng.uniform(0.6, 1.8)
            jx = rng.gauss(0.0, jamp)
            jy = rng.gauss(0.0, jamp)
            if rng.random() < 0.12:
                jx += rng.uniform(-3.5, 3.5)
                jy += rng.uniform(-3.5, 3.5)
            ease = 1.55 - math.sin(t * math.pi)
            dwell = sample_gamma_ms(
                4,
                70,
                alpha=2.2,
                beta=max(6.0, 14.0 * ease),
                rng=rng,
            )
            points.append((bx + jx, by + jy, dwell))
    points.append((x1, y1, sample_gamma_ms(12, 40, alpha=2.5, beta=10.0, rng=rng)))
    return points


def max_step_px(path: list[tuple[float, float, int]]) -> float:
    if len(path) < 2:
        return 0.0
    return max(
        math.hypot(path[i][0] - path[i - 1][0], path[i][1] - path[i - 1][1])
        for i in range(1, len(path))
    )


def path_is_continuous(
    path: list[tuple[float, float, int]],
    *,
    max_step: float = 36.0,
) -> bool:
    return max_step_px(path) <= max_step


def path_length(path: list[tuple[float, float, int]]) -> float:
    total = 0.0
    for i in range(1, len(path)):
        total += math.hypot(path[i][0] - path[i - 1][0], path[i][1] - path[i - 1][1])
    return total


def line_deviation_px(
    path: list[tuple[float, float, int]],
    start: tuple[float, float],
    end: tuple[float, float],
) -> float:
    """Max distance from path points to the chord start→end."""
    x0, y0 = start
    x1, y1 = end
    dx, dy = x1 - x0, y1 - y0
    denom = dx * dx + dy * dy
    if denom <= 1e-9:
        return max(math.hypot(p[0] - x0, p[1] - y0) for p in path) if path else 0.0
    best = 0.0
    for x, y, *_rest in path:
        t = ((x - x0) * dx + (y - y0) * dy) / denom
        qx, qy = x0 + t * dx, y0 + t * dy
        best = max(best, math.hypot(x - qx, y - qy))
    return best


def path_overshot(
    path: list[tuple[float, float, int]],
    start: tuple[float, float],
    end: tuple[float, float],
) -> bool:
    """True if some point projects past the target along the chord (t > 1)."""
    x0, y0 = start
    x1, y1 = end
    dx, dy = x1 - x0, y1 - y0
    denom = dx * dx + dy * dy
    if denom <= 1e-9 or len(path) < 3:
        return False
    return any(((p[0] - x0) * dx + (p[1] - y0) * dy) / denom > 1.02 for p in path[:-1])


def build_ambient_drift(
    origin: tuple[float, float],
    *,
    rng: Any = None,
    n_moves: int | None = None,
) -> list[tuple[float, float, int]]:
    rng = _as_rng(rng)
    ox, oy = float(origin[0]), float(origin[1])
    n = int(n_moves) if n_moves is not None else int(rng.randint(3, 9))
    pts: list[tuple[float, float, int]] = []
    x, y = ox, oy
    for _ in range(max(1, n)):
        x += rng.gauss(0.0, 5.5)
        y += rng.gauss(0.0, 5.5)
        x = ox + max(-42.0, min(42.0, x - ox))
        y = oy + max(-42.0, min(42.0, y - oy))
        pts.append((x, y, sample_gamma_ms(25, 180, alpha=2.5, beta=28.0, rng=rng)))
    return pts


def inertial_scroll_plan(
    *,
    rng: Any = None,
    direction: int = 1,
) -> list[tuple[int, int]]:
    """[(delta_y, wait_ms), ...] with v(t)=v0 e^{-kt}, a pause, then slight reverse."""
    rng = _as_rng(rng)
    sign = 1 if direction >= 0 else -1
    v0 = rng.uniform(320.0, 980.0) * sign
    k = rng.uniform(1.7, 3.4)
    dt = 0.018
    min_v = rng.uniform(14.0, 32.0)
    steps: list[tuple[int, int]] = []
    t = 0.0
    while len(steps) < 48:
        v = v0 * math.exp(-k * t)
        if abs(v) < min_v:
            break
        delta = v * rng.uniform(0.85, 1.15) + rng.gauss(0.0, 10.0)
        wait = sample_gamma_ms(8, 42, alpha=2.4, beta=8.0, rng=rng)
        steps.append((int(delta), wait))
        t += dt
    steps.append((0, sample_lognormal_ms(160, 900, mu=-1.2, sigma=0.45, rng=rng)))
    if rng.random() < 0.9:
        n_rev = int(rng.randint(2, 5))
        rev0 = -0.10 * v0 * rng.uniform(0.6, 1.3)
        for i in range(n_rev):
            rv = rev0 * math.exp(-2.2 * i * 0.05) + rng.gauss(0.0, 5.0)
            steps.append(
                (int(rv), sample_gamma_ms(10, 40, alpha=2.2, beta=8.0, rng=rng))
            )
    return steps


def play_mouse_path(page: Any, path: list[tuple[float, float, int]], mouse: dict[str, float]) -> int:
    """Issue many mouse.move steps (no teleport, no mouse.click). Returns move count."""
    n = 0
    for x, y, dwell in path:
        page.mouse.move(float(x), float(y))
        mouse["x"] = float(x)
        mouse["y"] = float(y)
        n += 1
        if dwell and int(dwell) > 0:
            page.wait_for_timeout(int(dwell))
    return n


def play_ambient_drift(
    page: Any,
    mouse: dict[str, float],
    *,
    rng: Any = None,
    budget_ms: int = 400,
) -> int:
    origin = (float(mouse.get("x") or 0.0), float(mouse.get("y") or 0.0))
    path = build_ambient_drift(origin, rng=rng)
    spent = 0
    n = 0
    for x, y, dwell in path:
        if spent >= int(budget_ms):
            break
        page.mouse.move(float(x), float(y))
        mouse["x"] = float(x)
        mouse["y"] = float(y)
        d = int(dwell)
        page.wait_for_timeout(d)
        spent += d
        n += 1
    return n


# Default hang band before ctx.close: ~60–180s lognormal (geometric mean ~104s).
HANG_BEFORE_CLOSE_LO_MS = 60_000
HANG_BEFORE_CLOSE_HI_MS = 180_000
# mu = ln(geo_mean_seconds); sigma keeps most mass inside the clamp band.
HANG_BEFORE_CLOSE_MU = math.log(104.0)  # ~4.644 ln(seconds); sample_lognormal_ms multiplies by 1000
HANG_BEFORE_CLOSE_SIGMA = 0.35
HANG_BEFORE_CLOSE_ENV = "CLOAKCLI_HANG_BEFORE_CLOSE_MS"


def resolve_hang_before_close_ms(
    *,
    lo_ms: int = HANG_BEFORE_CLOSE_LO_MS,
    hi_ms: int = HANG_BEFORE_CLOSE_HI_MS,
    rng: Any = None,
) -> int:
    """Sample hang duration. Env CLOAKCLI_HANG_BEFORE_CLOSE_MS overrides (0=skip)."""
    raw = os.environ.get(HANG_BEFORE_CLOSE_ENV)
    if raw is not None and str(raw).strip() != "":
        try:
            return max(0, int(str(raw).strip()))
        except ValueError:
            pass
    return sample_lognormal_ms(
        lo_ms,
        hi_ms,
        mu=HANG_BEFORE_CLOSE_MU,
        sigma=HANG_BEFORE_CLOSE_SIGMA,
        rng=rng,
    )


def hang_before_close(
    page: Any,
    mouse: dict[str, float] | None = None,
    *,
    lo_ms: int = HANG_BEFORE_CLOSE_LO_MS,
    hi_ms: int = HANG_BEFORE_CLOSE_HI_MS,
    rng: Any = None,
    log_fn: Any = None,
) -> int:
    """Ambient idle hang before ctx.close. Returns ms spent (0 if skipped).

    Samples lognormal clamped to [lo_ms, hi_ms] (default 60–180s) unless
    CLOAKCLI_HANG_BEFORE_CLOSE_MS is set (use 0 in unit tests to skip).
    During the hang: ambient mouse drift + quiet waits (not pure sleep).
    """
    if mouse is None:
        mouse = session_mouse()
    rng = _as_rng(rng)
    target = int(resolve_hang_before_close_ms(lo_ms=lo_ms, hi_ms=hi_ms, rng=rng))
    emit = log_fn if callable(log_fn) else (
        lambda payload: print(json.dumps(payload, ensure_ascii=False), flush=True)
    )
    emit({"status": "hang_before_close", "ms": target})
    if target <= 0:
        return 0
    t0 = time.monotonic()
    spent = 0
    while spent < target:
        remain = target - spent
        drift_budget = min(
            remain,
            sample_lognormal_ms(250, 900, mu=-0.8, sigma=0.4, rng=rng),
        )
        try:
            play_ambient_drift(page, mouse, rng=rng, budget_ms=int(drift_budget))
        except Exception:
            try:
                page.wait_for_timeout(int(drift_budget))
            except Exception:
                pass
        spent += int(drift_budget)
        if spent >= target:
            break
        quiet = min(
            target - spent,
            sample_lognormal_ms(400, 1800, mu=-0.2, sigma=0.35, rng=rng),
        )
        try:
            page.wait_for_timeout(int(quiet))
        except Exception:
            pass
        spent += int(quiet)
    elapsed = int(max(spent, (time.monotonic() - t0) * 1000.0))
    emit({"status": "hang_before_close_done", "ms": target, "elapsed_ms": elapsed})
    return elapsed


def human_move_to(
    page: Any,
    x: float,
    y: float,
    mouse: dict[str, float] | None = None,
    *,
    rng: Any = None,
) -> int:
    if mouse is None:
        mouse = session_mouse()
    start = (float(mouse.get("x") or 0.0), float(mouse.get("y") or 0.0))
    path = build_mouse_path(start, (float(x), float(y)), rng=rng)
    return play_mouse_path(page, path, mouse)


def _locator_box(loc: Any) -> dict[str, float] | None:
    try:
        box = loc.bounding_box()
    except Exception:
        return None
    if not isinstance(box, dict):
        return None
    try:
        w = float(box.get("width") or 0)
        h = float(box.get("height") or 0)
        if w <= 1 or h <= 1:
            return None
        return {
            "x": float(box["x"]),
            "y": float(box["y"]),
            "width": w,
            "height": h,
        }
    except Exception:
        return None


def human_click_locator(
    page: Any,
    loc: Any,
    mouse: dict[str, float] | None = None,
    *,
    rng: Any = None,
) -> dict[str, Any]:
    """Trail → hover → mousedown/mouseup. Never locator.click / teleport."""
    rng = _as_rng(rng)
    if mouse is None:
        mouse = session_mouse()
    box = _locator_box(loc)
    if box is None:
        try:
            loc.hover(timeout=4000)
            page.wait_for_timeout(sample_pause_ms(350, 1100, rng=rng))
            box = _locator_box(loc)
        except Exception:
            box = None
    if box is None:
        return {"ok": False, "method": "no_box"}
    tx = box["x"] + box["width"] * rng.uniform(0.22, 0.78)
    ty = box["y"] + box["height"] * rng.uniform(0.22, 0.78)
    n_moves = human_move_to(page, tx, ty, mouse, rng=rng)
    hover_ms = sample_lognormal_ms(450, 1300, mu=-0.15, sigma=0.4, rng=rng)
    page.wait_for_timeout(hover_ms)
    try:
        page.mouse.down()
        page.wait_for_timeout(sample_gamma_ms(35, 140, alpha=3.0, beta=18.0, rng=rng))
        page.mouse.up()
        return {
            "ok": True,
            "method": "mouse_trail_down_up",
            "hover_ms": hover_ms,
            "n_moves": n_moves,
            "target": (tx, ty),
        }
    except Exception as e:
        return {
            "ok": False,
            "method": "mouse_down_up_failed",
            "hover_ms": hover_ms,
            "n_moves": n_moves,
            "error": type(e).__name__,
        }


def human_type_text(
    page: Any,
    loc: Any,
    text: str,
    *,
    mouse: dict[str, float] | None = None,
    rng: Any = None,
    skip_focus: bool = False,
) -> dict[str, Any]:
    """Focus via trail+click, then per-key delays. Never element.fill() for human fields.

    skip_focus=True when the caller already trail-clicked. If a trail focus is
    attempted and fails, return without typing (never unfocused keyboard.type).
    """
    rng = _as_rng(rng)
    out: dict[str, Any] = {"typed": 0, "typos": 0, "used_fill": False}
    if skip_focus:
        out["focus"] = "already_focused"
        out["focus_ok"] = True
    elif mouse is not None:
        click = human_click_locator(page, loc, mouse, rng=rng)
        out["focus"] = click.get("method")
        out["focus_ok"] = bool(click.get("ok"))
        if not out["focus_ok"]:
            return out
    else:
        try:
            loc.focus()
            out["focus"] = "focus"
            out["focus_ok"] = True
        except Exception as e:
            out["focus_ok"] = False
            out["error"] = type(e).__name__
            return out
    try:
        loc.press("Control+A")
        page.wait_for_timeout(sample_gamma_ms(40, 120, alpha=2.0, beta=20.0, rng=rng))
        loc.press("Backspace")
        page.wait_for_timeout(sample_gamma_ms(40, 140, alpha=2.0, beta=22.0, rng=rng))
    except Exception:
        pass
    kb = page.keyboard
    for ch in text:
        if ch.isalpha() and rng.random() < 0.008:
            wrong = rng.choice("abcdefghijklmnopqrstuvwxyz")
            kb.type(wrong, delay=0)
            page.wait_for_timeout(sample_key_delay_ms(rng=rng))
            kb.press("Backspace")
            page.wait_for_timeout(sample_key_delay_ms(rng=rng))
            out["typos"] += 1
        kb.type(ch, delay=0)
        page.wait_for_timeout(sample_key_delay_ms(rng=rng))
        out["typed"] += 1
    return out


def inertial_scroll(
    page: Any,
    *,
    rng: Any = None,
    direction: int = 1,
) -> list[tuple[int, int]]:
    plan = inertial_scroll_plan(rng=rng, direction=direction)
    for dy, wait in plan:
        if dy:
            page.mouse.wheel(0, int(dy))
        if wait:
            page.wait_for_timeout(int(wait))
    return plan


# --- nurture 0.2.3: linger / visibility / reverse / close weights / browsed_ok ---

# browsed_ok gate mins (feed-only zero-pin success). Documented in README.
BROWSED_OK_MIN_FEED_DWELL_SEC = 25
BROWSED_OK_MIN_SCROLL_PX = 1500

# Close-path weights: button / Esc / history back / backdrop.
CLOSE_PATH_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("button", 0.50),
    ("escape", 0.30),
    ("history_back", 0.15),
    ("backdrop", 0.05),
)

# Persona-weighted micro-reverse probability after a downward feed scroll.
REVERSE_SCROLL_P: dict[str, float] = {
    "deep_browse": 0.18,
    "light_like": 0.15,
    "browse_only": 0.14,
    "bounce_early": 0.07,
}

# Min seconds between feed scroll direction flips (anti oscillation).
REVERSE_FLIP_MIN_SEC = 1.5


def browsed_ok(
    *,
    pins_opened: int,
    feed_dwell_sec: float,
    scroll_distance_px: float,
    min_feed_dwell_sec: int = BROWSED_OK_MIN_FEED_DWELL_SEC,
    min_scroll_px: int = BROWSED_OK_MIN_SCROLL_PX,
) -> bool:
    """Success if any pin opened OR feed dwell+scroll mins met (zero-pin bounce)."""
    if int(pins_opened) >= 1:
        return True
    return (
        float(feed_dwell_sec) >= float(min_feed_dwell_sec)
        and float(scroll_distance_px) >= float(min_scroll_px)
    )


def plan_pin_linger(
    *,
    persona: str | None = None,
    bounce: bool = False,
    rng: Any = None,
) -> dict[str, Any]:
    """Plan one pin closeup linger; every field independently re-sampled.

    Sequence intended by runner:
      gaze quiet → optional peek scroll → (like at like_frac of total) → pre-exit → close
    Like must never be immediate after open (mid/late 60–85% of planned dwell).
    """
    rng = _as_rng(rng)
    pname = (persona or "").strip().lower() or "light_like"
    is_bounce = bool(bounce) or pname == "bounce_early"
    if is_bounce:
        total_ms = sample_lognormal_ms(
            2000, 4500, mu=math.log(3.2), sigma=0.28, rng=rng
        )
    else:
        total_ms = sample_lognormal_ms(
            4500, 22000, mu=math.log(9.0), sigma=0.40, rng=rng
        )
    # Independent gaze; clamp so bounce still fits inside total.
    gaze_ms = sample_lognormal_ms(2500, 5500, mu=math.log(3.6), sigma=0.28, rng=rng)
    if is_bounce:
        gaze_ms = min(gaze_ms, max(800, int(total_ms * 0.55)))
    else:
        gaze_ms = min(gaze_ms, max(1200, int(total_ms * 0.55)))
    # Optional ~50% peek; independently allow skip.
    do_peek = bool(rng.random() < 0.50)
    peek_px = int(rng.randint(300, 600)) if do_peek else 0
    peek_ms = (
        sample_gamma_ms(350, 1200, alpha=2.2, beta=220.0, rng=rng) if do_peek else 0
    )
    like_frac = float(rng.uniform(0.60, 0.85))
    pre_exit_ms = sample_lognormal_ms(1000, 3000, mu=math.log(1.8), sigma=0.35, rng=rng)
    return {
        "persona": pname,
        "bounce": is_bounce,
        "total_ms": int(total_ms),
        "gaze_ms": int(gaze_ms),
        "do_peek": do_peek,
        "peek_px": int(peek_px),
        "peek_ms": int(peek_ms),
        "like_frac": like_frac,
        "like_at_ms": int(total_ms * like_frac),
        "pre_exit_ms": int(pre_exit_ms),
    }


def ensure_page_visible(
    page: Any,
    *,
    rng: Any = None,
    log_fn: Any = None,
    force: bool = False,
) -> dict[str, Any]:
    """Soft page/window focus keepalive (no fingerprint/launch changes).

    If visibilityState != visible or !hasFocus, bring_to_front + focus dispatch,
    short independently sampled wait, re-check. Logs visibility_keepalive.
    """
    rng = _as_rng(rng)
    emit = log_fn if callable(log_fn) else (
        lambda payload: print(json.dumps(payload, ensure_ascii=False), flush=True)
    )
    out: dict[str, Any] = {
        "event": "visibility_keepalive",
        "acted": False,
        "ok": True,
    }
    try:
        state = page.evaluate(
            """() => ({
              visibilityState: String(document.visibilityState || ''),
              hasFocus: !!document.hasFocus(),
            })"""
        )
    except Exception as e:
        out["ok"] = False
        out["error"] = type(e).__name__
        emit(out)
        return out
    if not isinstance(state, dict):
        state = {}
    vis = str(state.get("visibilityState") or "")
    focused = bool(state.get("hasFocus"))
    out["visibilityState"] = vis
    out["hasFocus"] = focused
    need = force or (vis != "visible") or (not focused)
    if not need:
        emit(out)
        return out
    out["acted"] = True
    try:
        bring = getattr(page, "bring_to_front", None)
        if callable(bring):
            bring()
    except Exception:
        pass
    try:
        page.evaluate(
            """() => {
              try { window.focus(); } catch (e) {}
              try {
                if (document.body && document.body.focus) document.body.focus();
              } catch (e) {}
              try { window.dispatchEvent(new Event('focus')); } catch (e) {}
              try {
                document.dispatchEvent(new Event('visibilitychange'));
              } catch (e) {}
            }"""
        )
    except Exception as e:
        out["focus_error"] = type(e).__name__
    wait_ms = sample_gamma_ms(80, 420, alpha=2.5, beta=60.0, rng=rng)
    out["wait_ms"] = int(wait_ms)
    try:
        page.wait_for_timeout(int(wait_ms))
    except Exception:
        pass
    try:
        state2 = page.evaluate(
            """() => ({
              visibilityState: String(document.visibilityState || ''),
              hasFocus: !!document.hasFocus(),
            })"""
        )
        if isinstance(state2, dict):
            out["visibilityState_after"] = str(state2.get("visibilityState") or "")
            out["hasFocus_after"] = bool(state2.get("hasFocus"))
            out["ok"] = (
                out["visibilityState_after"] == "visible"
                or bool(out["hasFocus_after"])
            )
    except Exception as e:
        out["recheck_error"] = type(e).__name__
    emit(out)
    return out


def scroll_plan_down_magnitude(plan: list[tuple[int, int]]) -> int:
    """Sum of positive (downward) delta_y in an inertial plan."""
    return int(sum(max(0, int(dy)) for dy, _w in plan))


def reverse_scroll_probability(persona: str | None, *, rng: Any = None) -> float:
    """Persona-weighted P(micro reverse | after down scroll), ~12–18% typical."""
    rng = _as_rng(rng)
    base = float(REVERSE_SCROLL_P.get((persona or "").strip().lower(), 0.14))
    # Tiny independent jitter so runs don't share identical thresholds.
    return max(0.05, min(0.22, base + float(rng.uniform(-0.02, 0.02))))


def maybe_micro_reverse_scroll(
    page: Any,
    down_magnitude_px: int,
    *,
    persona: str | None = None,
    last_flip_mono: float | None = None,
    rng: Any = None,
    log_fn: Any = None,
) -> dict[str, Any]:
    """Occasional micro reverse after a downward feed scroll.

    Reverse delta ≈ 20–45% of previous down magnitude. Pause 1–3s after.
    Skips if direction flipped within REVERSE_FLIP_MIN_SEC. Net session scroll
    should remain downward (caller tracks). Returns updated last_flip_mono.
    """
    rng = _as_rng(rng)
    emit = log_fn if callable(log_fn) else (
        lambda payload: print(json.dumps(payload, ensure_ascii=False), flush=True)
    )
    now = time.monotonic()
    out: dict[str, Any] = {
        "event": "micro_reverse_scroll",
        "did": False,
        "down_magnitude_px": int(down_magnitude_px),
        "last_flip_mono": last_flip_mono,
    }
    if int(down_magnitude_px) < 80:
        emit(out)
        return out
    if last_flip_mono is not None and (now - float(last_flip_mono)) < REVERSE_FLIP_MIN_SEC:
        out["skipped"] = "flip_cooldown"
        emit(out)
        return out
    p = reverse_scroll_probability(persona, rng=rng)
    out["p"] = round(p, 4)
    if rng.random() >= p:
        out["skipped"] = "bernoulli"
        emit(out)
        return out
    frac = float(rng.uniform(0.20, 0.45))
    reverse_px = max(40, int(abs(down_magnitude_px) * frac))
    out["frac"] = round(frac, 3)
    out["reverse_px"] = int(reverse_px)
    # Soft upward inertial burst: a few decaying negative wheel steps (not PageDown).
    remain = float(reverse_px)
    steps = 0
    while remain > 12 and steps < 14:
        chunk = max(8.0, remain * float(rng.uniform(0.18, 0.42)))
        chunk = min(chunk, remain)
        dy = -int(round(chunk + rng.gauss(0.0, 4.0)))
        try:
            page.mouse.wheel(0, dy)
        except Exception as e:
            out["error"] = type(e).__name__
            break
        wait = sample_gamma_ms(10, 48, alpha=2.2, beta=10.0, rng=rng)
        try:
            page.wait_for_timeout(int(wait))
        except Exception:
            pass
        remain -= abs(dy)
        steps += 1
    out["steps"] = steps
    out["did"] = steps > 0
    pause_ms = sample_lognormal_ms(1000, 3000, mu=math.log(1.8), sigma=0.35, rng=rng)
    out["pause_ms"] = int(pause_ms)
    try:
        page.wait_for_timeout(int(pause_ms))
    except Exception:
        pass
    out["last_flip_mono"] = time.monotonic()
    emit(out)
    return out


def choose_close_path(rng: Any = None) -> str:
    """Weighted close path: button 50% / escape 30% / history_back 15% / backdrop 5%."""
    rng = _as_rng(rng)
    names = [n for n, _w in CLOSE_PATH_WEIGHTS]
    weights = [float(w) for _n, w in CLOSE_PATH_WEIGHTS]
    # random.choices available 3.6+
    return str(rng.choices(names, weights=weights, k=1)[0])


def close_path_fallback_order(primary: str, *, rng: Any = None) -> list[str]:
    """Primary first, then remaining paths shuffled (fallback if chosen fails)."""
    rng = _as_rng(rng)
    names = [n for n, _w in CLOSE_PATH_WEIGHTS]
    if primary not in names:
        primary = "button"
    rest = [n for n in names if n != primary]
    rng.shuffle(rest)
    return [primary] + rest


def sample_esc_key_hold_ms(rng: Any = None) -> int:
    """Real keydown→keyup hold for Esc (independent sample each press)."""
    return sample_gamma_ms(60, 120, alpha=3.0, beta=28.0, rng=rng)
