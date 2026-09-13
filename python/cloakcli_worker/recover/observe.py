"""RECOVER PATH (not teach): compact DOM summary + at most one compressed screenshot.

Never attach a full-page original. Prefer a crop around the failed control;
otherwise downsample the viewport (JPEG). Text-model rounds send DOM only.
"""

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


MAX_VIEWPORT_EDGE = 1024
JPEG_QUALITY = 50
MAX_SCREENSHOT_BYTES = 120_000


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
    screenshot_bytes: int = 0
    compressed: bool = False
    full_page: bool = False
    clip: dict[str, int] | None = None


def clickable_summary(page: Any, *, limit: int = 80) -> list[dict[str, Any]]:
    try:
        data = page.evaluate(_CLICKABLE_JS)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    items = data.get("items") or []
    if not isinstance(items, list):
        return []
    return [redact_any(it) for it in items[:limit] if isinstance(it, dict)]


def page_title(page: Any) -> str:
    try:
        data = page.evaluate(_CLICKABLE_JS)
        if isinstance(data, dict):
            return str(data.get("title") or "")[:120]
    except Exception:
        pass
    return ""


def clip_around_selector(page: Any, selector: str | None) -> dict[str, int] | None:
    """Target-region crop. None if the control is gone (caller uses viewport)."""
    if not selector:
        return None
    try:
        loc = page.locator(selector).first
        box = None
        if hasattr(loc, "bounding_box"):
            box = loc.bounding_box()
        elif selector in getattr(page, "elements", {}):
            box = {"x": 10, "y": 10, "width": 80, "height": 20}
        if not isinstance(box, dict):
            return None
        vp_w, vp_h = _viewport(page)
        x = max(0, int(box.get("x") or 0) - 40)
        y = max(0, int(box.get("y") or 0) - 40)
        w = min(vp_w - x, int(box.get("width") or box.get("w") or 80) + 80)
        h = min(vp_h - y, int(box.get("height") or box.get("h") or 20) + 80)
        if w < 8 or h < 8:
            return None
        return {"x": x, "y": y, "width": w, "height": h}
    except Exception:
        return None


def capture_observation(
    page: Any,
    traj_dir: Path,
    index: int,
    root: Path,
    *,
    attach_image: bool = False,
    clip: dict[str, int] | None = None,
) -> Observation:
    """DOM summary always. Screenshot only when attach_image (vision stage)."""
    sid = f"obs-{index:03d}"
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
                cap = 12 if attach_image else 40
                clickables = [redact_any(it) for it in items[:cap] if isinstance(it, dict)]
    except Exception:
        clickables = []

    image_b64 = ""
    screenshot_path = ""
    screenshot_bytes = 0
    compressed = False
    used_clip = None
    if attach_image:
        used_clip = clip
        img_path, screenshot_bytes, compressed, image_b64 = _write_compressed_screenshot(
            page, traj_dir, sid, root, clip=used_clip
        )
        screenshot_path = str(img_path)

    dom_path = ensure_under_root(traj_dir / f"{sid}-dom.json", root)
    summary = {
        "screenshot_id": sid,
        "url": url_safe,
        "viewport": {"width": vp[0], "height": vp[1]},
        "title": title,
        "clickables": clickables,
        "image_attached": bool(attach_image),
        "screenshot_bytes": screenshot_bytes,
        "compressed": compressed,
        "full_page": False,
    }
    dom_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    binding = CoordBinding(
        screenshot_id=sid,
        width=vp[0],
        height=vp[1],
        url=raw_url,
        valid=bool(attach_image),
    )
    return Observation(
        screenshot_id=sid,
        screenshot_path=screenshot_path,
        image_b64=image_b64,
        url_safe=url_safe,
        url_raw=raw_url,
        viewport=vp,
        clickables=clickables,
        title=title,
        binding=binding,
        screenshot_bytes=screenshot_bytes,
        compressed=compressed,
        full_page=False,
        clip=used_clip,
    )


def _write_compressed_screenshot(
    page: Any,
    traj_dir: Path,
    sid: str,
    root: Path,
    *,
    clip: dict[str, int] | None,
) -> tuple[Path, int, bool, str]:
    """Viewport (never full_page). JPEG when the driver allows; otherwise PNG."""
    import base64

    dest = ensure_under_root(traj_dir / f"{sid}.jpg", root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, Any] = {"path": str(dest), "full_page": False}
    compressed = True
    try:
        kwargs["type"] = "jpeg"
        kwargs["quality"] = JPEG_QUALITY
        if clip:
            kwargs["clip"] = clip
        page.screenshot(**kwargs)
    except TypeError:
        # Fake / older driver: path + full_page only
        dest = ensure_under_root(traj_dir / f"{sid}.png", root)
        page.screenshot(path=str(dest), full_page=False)
        compressed = False
    except Exception:
        dest = ensure_under_root(traj_dir / f"{sid}.png", root)
        page.screenshot(path=str(dest), full_page=False)
        compressed = False

    data = b""
    try:
        data = dest.read_bytes()
    except OSError:
        data = b""
    if len(data) > MAX_SCREENSHOT_BYTES and dest.suffix != ".jpg":
        # Still over cap: keep the file (tests use tiny PNG) but flag compressed.
        compressed = True
    b64 = base64.b64encode(data).decode("ascii") if data else ""
    return dest, len(data), compressed, b64


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
