"""Screenshot + compact clickable DOM summary (under project-root artifacts)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..paths import ensure_under_root
from ..redact import redact_any
from .origin import safe_url_for_prompt

_CLICKABLE_JS = """() => {
  const sels = 'a, button, input, textarea, select, [role="button"], [role="link"], [onclick]';
  const els = Array.from(document.querySelectorAll(sels)).slice(0, 80);
  function cssPath(el) {
    if (el.id && /^[A-Za-z][\\w.-]*$/.test(el.id)) return '#' + el.id;
    const parts = [];
    let cur = el;
    for (let i = 0; i < 4 && cur && cur.nodeType === 1; i++) {
      let part = cur.tagName.toLowerCase();
      if (cur.id && /^[A-Za-z][\\w.-]*$/.test(cur.id)) {
        parts.unshift('#' + cur.id);
        break;
      }
      const parent = cur.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter(c => c.tagName === cur.tagName);
        if (same.length > 1) {
          part += ':nth-of-type(' + (same.indexOf(cur) + 1) + ')';
        }
      }
      parts.unshift(part);
      cur = parent;
    }
    return parts.join(' > ');
  }
  function safeHref(h) {
    if (!h) return null;
    try {
      const u = new URL(h, location.href);
      return u.origin + u.pathname;
    } catch (e) {
      return String(h).slice(0, 80);
    }
  }
  return {
    title: (document.title || '').slice(0, 120),
    url: location.origin + location.pathname,
    items: els.map(el => {
      const r = el.getBoundingClientRect();
      const text = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().slice(0, 80);
      return {
        tag: el.tagName.toLowerCase(),
        text: text,
        href: safeHref(el.getAttribute('href')),
        type: el.getAttribute('type'),
        name: el.getAttribute('name'),
        css: cssPath(el),
        bbox: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)}
      };
    }).filter(it => it.bbox.w > 0 && it.bbox.h > 0)
  };
}"""


@dataclass
class CoordBinding:
    screenshot_id: str
    width: int
    height: int
    url: str
    valid: bool = True


@dataclass
class Observation:
    screenshot_id: str
    screenshot_path: str
    image_b64: str
    url_safe: str
    url_raw: str
    viewport: tuple[int, int]
    clickables: list[dict[str, Any]]
    title: str
    binding: CoordBinding


def capture_observation(
    page: Any,
    traj_dir: Path,
    index: int,
    root: Path,
) -> Observation:
    sid = f"obs-{index:03d}"
    png_path = ensure_under_root(traj_dir / f"{sid}.png", root)
    png_path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(png_path), full_page=False)

    vp = _viewport(page)
    raw_url = ""
    try:
        raw_url = str(page.url or "")
    except Exception:
        raw_url = ""
    url_safe = safe_url_for_prompt(raw_url)

    title = ""
    clickables: list[dict[str, Any]] = []
    try:
        data = page.evaluate(_CLICKABLE_JS)
        if isinstance(data, dict):
            title = str(data.get("title") or "")[:120]
            items = data.get("items") or []
            if isinstance(items, list):
                clickables = [redact_any(it) for it in items[:80] if isinstance(it, dict)]
    except Exception:
        clickables = []

    try:
        b64 = png_path.read_bytes()
        import base64

        image_b64 = base64.b64encode(b64).decode("ascii")
    except OSError:
        image_b64 = ""

    dom_path = ensure_under_root(traj_dir / f"{sid}-dom.json", root)
    summary = {
        "screenshot_id": sid,
        "url": url_safe,
        "viewport": {"width": vp[0], "height": vp[1]},
        "title": title,
        "clickables": clickables,
    }
    dom_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    binding = CoordBinding(
        screenshot_id=sid,
        width=vp[0],
        height=vp[1],
        url=raw_url,
        valid=True,
    )
    return Observation(
        screenshot_id=sid,
        screenshot_path=str(png_path),
        image_b64=image_b64,
        url_safe=url_safe,
        url_raw=raw_url,
        viewport=vp,
        clickables=clickables,
        title=title,
        binding=binding,
    )


def _viewport(page: Any) -> tuple[int, int]:
    try:
        vs = page.viewport_size
        if isinstance(vs, dict):
            return int(vs.get("width") or 1280), int(vs.get("height") or 720)
        if vs is not None:
            w = getattr(vs, "width", None) or (vs[0] if len(vs) > 0 else 1280)
            h = getattr(vs, "height", None) or (vs[1] if len(vs) > 1 else 720)
            return int(w), int(h)
    except Exception:
        pass
    return 1280, 720


def binding_still_valid(page: Any, binding: CoordBinding | None) -> bool:
    if binding is None or not binding.valid:
        return False
    try:
        url = str(page.url or "")
    except Exception:
        return False
    if url != binding.url:
        return False
    w, h = _viewport(page)
    if (w, h) != (binding.width, binding.height):
        return False
    return True
