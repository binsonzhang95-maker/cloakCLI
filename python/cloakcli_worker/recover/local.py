"""RECOVER PATH (not teach): local selector cascade before any model call.

Order: recorded selector → backup chain → unique role/text/label match
against the clickable DOM summary. Success stops the cascade (0 tokens).
Teach recording/export lives in the Rust CLI + extensions/teach/.
"""

from __future__ import annotations

from typing import Any

from .observe import clickable_summary


def selector_chain(stall: dict[str, Any]) -> list[str]:
    out: list[str] = []
    primary = stall.get("selector") or stall.get("css")
    if isinstance(primary, str) and primary.strip():
        out.append(primary.strip())
    alts = stall.get("selectors") or []
    if isinstance(alts, list):
        for s in alts:
            if isinstance(s, str) and s.strip() and s.strip() not in out:
                out.append(s.strip())
    return out


def try_local_recover(
    page: Any,
    stall: dict[str, Any],
    *,
    timeout_ms: int = 2500,
) -> tuple[bool, dict[str, Any]]:
    """Return (ok, detail). Never raises; failures fall through to text/vision."""
    action = str(stall.get("action") or "").strip().lower()
    if action in ("type",):
        action = "fill"
    detail: dict[str, Any] = {"stage": "local", "tried": [], "action": action}
    chain = selector_chain(stall)
    last_err = ""
    for sel in chain:
        detail["tried"].append(sel)
        try:
            if _apply(page, action, sel, stall, timeout_ms):
                detail["matched"] = sel
                detail["how"] = "selector"
                return True, detail
        except Exception as e:
            last_err = type(e).__name__
    if last_err:
        detail["last_error"] = last_err

    # Unique text / name / aria match from a compact clickable list (no screenshot).
    try:
        items = clickable_summary(page)
    except Exception:
        items = []
    needle = _needles(stall)
    unique = _unique_match(items, needle)
    if unique:
        css = str(unique.get("css") or "").strip()
        if css:
            detail["tried"].append(css)
            try:
                if _apply(page, action, css, stall, timeout_ms):
                    detail["matched"] = css
                    detail["how"] = "text_or_name"
                    return True, detail
            except Exception as e:
                detail["last_error"] = type(e).__name__
    detail["ok"] = False
    return False, detail


def _apply(page: Any, action: str, sel: str, stall: dict[str, Any], timeout_ms: int) -> bool:
    if action == "click":
        page.click(sel, timeout=timeout_ms)
        return True
    if action in ("fill", "type"):
        text = stall.get("intended_text")
        if text is None:
            text = stall.get("text") or stall.get("value") or ""
        if text == "(redacted)":
            return False
        if action == "fill" or hasattr(page, "fill"):
            page.fill(sel, str(text), timeout=timeout_ms)
        else:
            page.click(sel, timeout=timeout_ms)
            page.keyboard.type(str(text), delay=20)
        return True
    if action == "select":
        value = stall.get("intended_text") or stall.get("value") or stall.get("text") or ""
        if hasattr(page, "select_option"):
            page.select_option(sel, str(value), timeout=timeout_ms)
            return True
        return False
    if action == "press":
        key = stall.get("key") or stall.get("intended_text") or "Enter"
        if hasattr(page, "keyboard") and hasattr(page.keyboard, "press"):
            page.keyboard.press(str(key))
            return True
        return False
    return False


def _needles(stall: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for k in ("field_name", "name", "label", "text"):
        v = stall.get(k)
        if isinstance(v, str) and v.strip() and v != "(redacted)":
            out.append(v.strip().lower())
    sel = str(stall.get("selector") or "")
    if sel.startswith("#") and len(sel) > 1:
        out.append(sel[1:].lower())
    return out


def _unique_match(items: list[dict[str, Any]], needles: list[str]) -> dict[str, Any] | None:
    if not needles:
        return None
    hits: list[dict[str, Any]] = []
    for it in items:
        blob = " ".join(
            str(it.get(k) or "")
            for k in ("text", "name", "css", "type")
        ).lower()
        if any(n and n in blob for n in needles):
            hits.append(it)
    if len(hits) == 1:
        return hits[0]
    return None
