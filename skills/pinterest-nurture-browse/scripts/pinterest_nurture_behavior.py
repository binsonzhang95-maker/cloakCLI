#!/usr/bin/env python3
"""Pure Pinterest nurture behavior helpers (no CloakBrowser fingerprint knobs).

Continuous randomized mouse trails, inertial scroll, log-normal/gamma pauses,
and session personas. Used by run_pinterest_nurture_browse.py. Stdlib only.
"""
from __future__ import annotations

import math
import random
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
    """Pick persona, pin count (1–12), like probability (may be 0), session target.

    Always clamps target_sec into [min_sec, max_sec].
    """
    rng = _as_rng(rng)
    if max_sec < min_sec:
        min_sec, max_sec = max_sec, min_sec
    pname = choose_persona(persona, rng=rng)
    spec = PERSONAS[pname]
    if pins is not None and int(pins) > 0:
        n_pins = max(1, min(12, int(pins)))
    else:
        lo, hi = spec["pins"]
        n_pins = max(1, min(12, int(rng.randint(int(lo), int(hi)))))
    pr = spec.get("like_prob_range") or (0.0, 0.0)
    like_p = float(rng.uniform(float(pr[0]), float(pr[1])))
    span = max(0, int(max_sec) - int(min_sec))
    if spec.get("bounce"):
        target_sec = int(min_sec) + int(span * rng.uniform(0.0, 0.2))
    else:
        target_sec = int(min_sec) + int(span * rng.uniform(0.0, 1.0))
    target_sec = max(int(min_sec), min(int(max_sec), target_sec))
    return {
        "persona": pname,
        "pins": n_pins,
        "like_prob": like_p,
        "min_sec": int(min_sec),
        "max_sec": int(max_sec),
        "target_sec": target_sec,
        "bounce": bool(spec.get("bounce")),
        "view_scale": float(spec.get("view_scale") or 1.0),
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
) -> dict[str, Any]:
    """Focus via trail+click, then per-key delays. Never element.fill() for human fields."""
    rng = _as_rng(rng)
    out: dict[str, Any] = {"typed": 0, "typos": 0, "used_fill": False}
    if mouse is not None:
        click = human_click_locator(page, loc, mouse, rng=rng)
        out["focus"] = click.get("method")
        out["focus_ok"] = bool(click.get("ok"))
    else:
        try:
            loc.click(timeout=4000)
            out["focus"] = "locator_click"
            out["focus_ok"] = True
        except Exception:
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
