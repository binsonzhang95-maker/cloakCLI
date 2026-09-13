"""Local DOM-event → Playwright action normalize (Teach Chat M3).

Zero-token post-process on takeover_stop. Never emits raw DOM events as
exportable skill steps. Canonical field is `selector` (css is read-compat).

Selector priority (plan C, locked):
  id → data-testid → name → aria/role → text → CSS path → coords.

Failures (non-unique, shadow, iframe, missing selector) go to confirm or
non-exportable. Silent raw-event save is forbidden.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .actions import (
    RECOVER_EXTRA_ACTIONS,
    ActionError,
    validate_action,
)
from .redact import redact_text
from .teach_hub import selector_from_obj

SCHEMA_VERSION = 1
MAX_TEXT_SEL = 48
SECRET_FIELD_MARKERS = (
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
SECRET_QUERY_KEYS = {
    "token",
    "access_token",
    "refresh_token",
    "id_token",
    "api_key",
    "apikey",
    "api-key",
    "auth",
    "authorization",
    "password",
    "passwd",
    "secret",
    "session",
    "sessionid",
    "jwt",
    "cookie",
    "client_secret",
    "code",
}
UNSTABLE_RE = re.compile(r":nth-(?:child|of-type)", re.I)
HUMAN_EXTRA = set(RECOVER_EXTRA_ACTIONS)

# Priority used when ranking candidate selectors (plan C / product lock).
# Coords are not CSS selectors; pick_selector falls through to them last.
STRATEGY_ORDER = (
    "id",
    "testid",
    "name",
    "aria",
    "text",
    "css",
    "coords",
)
UNIQUE_ALIASES = {
    "id": ("id",),
    "testid": ("testid", "data-testid", "data-test"),
    "name": ("name",),
    "aria": ("aria", "role_name", "label"),
    "text": ("text",),
    "css": ("css", "css_path"),
}


@dataclass
class NormalizeItem:
    status: str  # ok | needs_confirm | non_exportable
    reason: str = ""
    action: dict[str, Any] | None = None
    kind: str = ""
    exportable: bool = False


@dataclass
class NormalizeResult:
    steps: list[dict[str, Any]] = field(default_factory=list)
    needs_confirm: list[dict[str, Any]] = field(default_factory=list)
    non_exportable: list[dict[str, Any]] = field(default_factory=list)
    event_count: int = 0
    dropped_raw: int = 0

    def to_public(self) -> dict[str, Any]:
        """Wire payload: fill/type plaintext already stripped from steps."""
        return {
            "ok": True,
            "schema_version": SCHEMA_VERSION,
            "steps": [redact_step(s) for s in self.steps],
            "needs_confirm": self.needs_confirm,
            "non_exportable": self.non_exportable,
            "event_count": self.event_count,
            "dropped_raw": self.dropped_raw,
        }

    def exportable_steps(self) -> list[dict[str, Any]]:
        """Playwright actions only (source=human). No raw DOM."""
        out: list[dict[str, Any]] = []
        for s in self.steps:
            if is_raw_dom_event(s):
                continue
            if s.get("exportable") is False:
                continue
            out.append(s)
        return out


def is_raw_dom_event(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    if obj.get("action"):
        return False
    kind = str(obj.get("kind") or "").strip().lower()
    return kind in {
        "click",
        "input",
        "fill",
        "type",
        "select",
        "navigation",
        "nav",
        "goto",
        "keypress",
        "keydown",
        "press",
        "change",
    }


def redact_step(step: dict[str, Any]) -> dict[str, Any]:
    """Timeline/log copy: fill/type plaintext never included."""
    from .actions import Action

    try:
        a = validate_action(step, extra_allowed=HUMAN_EXTRA)
    except ActionError:
        out = {k: v for k, v in step.items() if k not in ("text", "value")}
        if step.get("action") in ("fill", "type") and "text" in step:
            raw = step.get("text")
            if isinstance(raw, str) and _is_placeholder(raw):
                out["text"] = raw
            else:
                out["text"] = "[REDACTED]"
                if isinstance(raw, str):
                    out["text_len"] = len(raw)
        return out
    d = a.public_dict()
    if step.get("selector_strategy"):
        d["selector_strategy"] = step["selector_strategy"]
    if step.get("confidence") is not None:
        d["confidence"] = step["confidence"]
    if step.get("source"):
        d["source"] = step["source"]
    d["exportable"] = True
    return d


def _is_placeholder(s: str) -> bool:
    t = s.strip()
    return t.startswith("{{vars.") and t.endswith("}}") and len(t) <= 80 and "\n" not in t


def css_attr(v: str) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"')


def looks_secret_field(field: dict[str, Any] | None, extra: str = "") -> bool:
    if not field and not extra:
        return False
    field = field or {}
    hay = " ".join(
        str(field.get(k) or "")
        for k in ("type", "name", "id", "autocomplete", "placeholder", "testid", "tag")
    )
    hay = f"{hay} {extra}".lower()
    return any(m in hay for m in SECRET_FIELD_MARKERS)


def var_name_for_field(field: dict[str, Any] | None) -> str:
    field = field or {}
    hay = " ".join(
        str(field.get(k) or "")
        for k in ("type", "name", "id", "autocomplete", "placeholder", "testid")
    ).lower()
    if "token" in hay or "jwt" in hay:
        return "TOKEN"
    if "pass" in hay:
        return "PASSWORD"
    if "cookie" in hay:
        return "COOKIE"
    if "auth" in hay or "secret" in hay:
        return "SECRET"
    if "api" in hay and "key" in hay:
        return "API_KEY"
    return "SECRET"


def sanitize_goto_url(raw: str) -> str | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    low = raw.lower()
    if low.startswith(("javascript:", "data:", "file:", "vbscript:", "blob:", "about:")):
        return None
    if "javascript:" in low:
        return None
    try:
        parts = urlsplit(raw)
    except Exception:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    if parts.username or parts.password:
        return None
    q = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in SECRET_QUERY_KEYS and not any(s in k.lower() for s in SECRET_QUERY_KEYS)
    ]
    query = urlencode(q) if q else ""
    host = (parts.hostname or "").lower()
    if not host:
        return None
    port = parts.port
    default = 80 if parts.scheme == "http" else 443
    netloc = host if not port or port == default else f"{host}:{port}"
    path = parts.path or "/"
    return urlunsplit((parts.scheme, netloc, path, query, ""))


def is_unstable_selector(sel: str) -> bool:
    if not sel:
        return True
    if UNSTABLE_RE.search(sel):
        return True
    if sel.count(">") >= 3:
        return True
    if len(sel) > 80:
        return True
    return False


def _count_on_page(page: Any, selector: str) -> int | None:
    if page is None or not selector:
        return None
    try:
        loc = page.locator(selector)
        if hasattr(loc, "count"):
            return int(loc.count())
        # FakeLocator: presence in page.elements
        els = getattr(page, "elements", None)
        if isinstance(els, dict):
            if selector in els:
                return 1
            n = getattr(page, "selector_counts", {}).get(selector)
            if n is not None:
                return int(n)
            return 0
    except Exception:
        return None
    return None


def _candidate_unique(event: dict[str, Any], key: str, selector: str, page: Any) -> bool | None:
    live = _count_on_page(page, selector)
    if live is not None:
        return live == 1
    uniq = event.get("candidate_unique")
    uniq = uniq if isinstance(uniq, dict) else {}
    cands = event.get("selector_candidates")
    if isinstance(cands, dict):
        for ck, cv in cands.items():
            cand_sel = _cand_str({ck: cv}, ck)
            if cand_sel != selector:
                continue
            if ck in uniq:
                v = uniq[ck]
                if isinstance(v, bool):
                    return v
            if isinstance(cv, dict) and "unique" in cv:
                return bool(cv["unique"])
            break
    for alias in UNIQUE_ALIASES.get(key, (key,)):
        if alias in uniq:
            v = uniq[alias]
            if isinstance(v, bool):
                return v
    if key in uniq:
        v = uniq[key]
        if isinstance(v, bool):
            return v
    return None


def _cand_str(cands: dict[str, Any], key: str) -> str:
    v = cands.get(key)
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, dict):
        s = v.get("selector") or v.get("css") or ""
        return str(s).strip()
    return ""


def _strategy_for_sel(sel: str) -> str:
    """Classify a raw selector string into a plan-C strategy bucket."""
    s = (sel or "").strip()
    if not s:
        return "css"
    if re.match(r"^#[^\s\[\]>+~.,#]+$", s):
        return "id"
    low = s.lower()
    if "data-testid=" in low or "data-test=" in low:
        return "testid"
    if re.search(r"\[name\s*=", low):
        return "name"
    if "aria-label=" in low or "[role=" in low:
        return "aria"
    if ":has-text(" in low:
        return "text"
    return "css"


def collect_candidates(event: dict[str, Any]) -> list[tuple[str, str]]:
    """Return (strategy, selector) in plan-C priority order, de-duplicated.

    Order: id → data-testid → name → aria/role → text → CSS path.
    Uniqueness is re-checked by pick_selector against the live page.
    """
    buckets: dict[str, list[str]] = {k: [] for k in STRATEGY_ORDER}
    seen: set[str] = set()

    def add(strategy: str, sel: str) -> None:
        sel = (sel or "").strip()
        if not sel or sel in seen:
            return
        if ";" in sel or "{" in sel or "}" in sel:
            return
        if len(sel) > 500:
            return
        if strategy not in buckets or strategy == "coords":
            strategy = "css"
        seen.add(sel)
        buckets[strategy].append(sel)

    cands = event.get("selector_candidates")
    if not isinstance(cands, dict):
        cands = {}

    field = event.get("field") if isinstance(event.get("field"), dict) else {}
    tag = str(event.get("tag") or (field or {}).get("tag") or "").strip().lower()

    # 1. id
    add("id", _cand_str(cands, "id"))
    el_id = str((field or {}).get("id") or event.get("id") or "")
    if el_id:
        add("id", "#" + _css_ident(el_id))

    # 2. data-testid / data-test
    add(
        "testid",
        _cand_str(cands, "testid")
        or _cand_str(cands, "data-testid")
        or _cand_str(cands, "data-test"),
    )
    testid = str(
        (field or {}).get("testid")
        or event.get("testid")
        or event.get("data-testid")
        or event.get("data-test")
        or ""
    )
    if testid:
        attr = "data-test" if _cand_str(cands, "data-test") and not (
            _cand_str(cands, "testid") or _cand_str(cands, "data-testid")
        ) else "data-testid"
        add("testid", f'[{attr}="{css_attr(testid)}"]')

    # 3. name + tag
    add("name", _cand_str(cands, "name"))
    fname = str((field or {}).get("name") or event.get("name") or "")
    if fname and tag:
        add("name", f'{tag}[name="{css_attr(fname)}"]')
    elif fname:
        add("name", f'[name="{css_attr(fname)}"]')

    # 4. aria-label / role combo
    add("aria", _cand_str(cands, "role_name"))
    role = str(event.get("role") or "").strip()
    acc_name = str(
        event.get("accessible_name")
        or event.get("aria_label")
        or event.get("label")
        or event.get("text")
        or ""
    ).strip()
    if role and acc_name and len(acc_name) <= MAX_TEXT_SEL:
        add("aria", f'[role="{css_attr(role)}"][aria-label="{css_attr(acc_name)}"]')
        if tag:
            add("aria", f'{tag}[aria-label="{css_attr(acc_name)}"]')
    add("aria", _cand_str(cands, "aria") or _cand_str(cands, "label"))
    aria = str(event.get("aria_label") or event.get("label") or "").strip()
    if aria and tag:
        add("aria", f'{tag}[aria-label="{css_attr(aria)}"]')
    if aria:
        add("aria", f'[aria-label="{css_attr(aria)}"]')

    # 5. stable text (never secret field values)
    add("text", _cand_str(cands, "text"))
    text = str(event.get("text") or "").strip()
    if text and 0 < len(text) <= MAX_TEXT_SEL and not looks_secret_field(field, text):
        tsel_tag = tag or "button"
        add("text", f'{tsel_tag}:has-text("{css_attr(text)}")')

    # 6. CSS path (id/name already emitted above)
    add("css", _cand_str(cands, "autocomplete"))
    add("css", _cand_str(cands, "css_path") or _cand_str(cands, "css"))
    primary = selector_from_obj(event) or str(event.get("selector") or "").strip()
    if primary:
        add(_strategy_for_sel(primary), primary)
    extras = event.get("selectors")
    if isinstance(extras, list):
        for s in extras:
            if isinstance(s, str):
                add(_strategy_for_sel(s), s)

    out: list[tuple[str, str]] = []
    for strategy in STRATEGY_ORDER:
        if strategy == "coords":
            continue
        for sel in buckets[strategy]:
            out.append((strategy, sel))
    return out


def _css_ident(ident: str) -> str:
    # Minimal CSS.escape for ids that are already sane; keep unicode.
    if re.match(r"^[A-Za-z_][\w-]*$", ident):
        return ident
    return css_attr(ident)


def pick_selector(
    event: dict[str, Any],
    *,
    page: Any = None,
) -> tuple[str | None, str, float, str]:
    """Return (selector, strategy, confidence, fail_reason)."""
    ranked = collect_candidates(event)
    best_unstable: tuple[str, str] | None = None
    for strategy, sel in ranked:
        unique = _candidate_unique(event, strategy, sel, page)
        if unique is False:
            continue
        if is_unstable_selector(sel):
            if best_unstable is None:
                best_unstable = (strategy, sel)
            continue
        conf = {
            "id": 0.98,
            "testid": 0.95,
            "name": 0.9,
            "aria": 0.88,
            "text": 0.75,
            "css": 0.7,
        }.get(strategy, 0.6)
        if unique is None:
            conf -= 0.1
        return sel, strategy, conf, ""
    if best_unstable is not None:
        strategy, sel = best_unstable
        return sel, strategy, 0.35, "unstable_selector"
    return None, "", 0.0, "no_selector"


def denoise_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        kind = str(ev.get("kind") or ev.get("type") or "").strip().lower()
        if kind in {
            "hover",
            "blur",
            "focus",
            "mouseover",
            "mouseout",
            "mousemove",
            "pointermove",
            "scroll",
            "keydown",
            "keyup",
            "goal",
        }:
            # keydown is captured as kind=keypress when it is a press we keep.
            if kind != "keydown":
                continue
        if kind == "click" and out:
            last = out[-1]
            if str(last.get("kind") or "").lower() == "click" and last.get("selector") == ev.get(
                "selector"
            ):
                continue
        out.append(ev)
    return out


def coalesce_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ev in events:
        kind = str(ev.get("kind") or "").strip().lower()
        if kind in {"input", "fill", "type", "change"}:
            if out:
                last = out[-1]
                lk = str(last.get("kind") or "").strip().lower()
                same_sel = (last.get("selector") == ev.get("selector")) or (
                    selector_from_obj(last) == selector_from_obj(ev)
                    and selector_from_obj(ev) is not None
                )
                if lk in {"input", "fill", "type", "change"} and same_sel:
                    out[-1] = ev
                    continue
        out.append(ev)
    return out


def _skip_click_before_fill(events: list[dict[str, Any]], i: int, sel: str | None) -> bool:
    if not sel:
        return False
    if i + 1 >= len(events):
        return False
    nxt = events[i + 1]
    nk = str(nxt.get("kind") or "").strip().lower()
    if nk not in {"input", "fill", "type", "change", "select"}:
        return False
    ns = selector_from_obj(nxt) or nxt.get("selector")
    return ns == sel


def _coords(event: dict[str, Any]) -> tuple[int | None, int | None]:
    x = event.get("x")
    y = event.get("y")
    coords = event.get("coords")
    if isinstance(coords, dict):
        x = coords.get("x", x)
        y = coords.get("y", y)
    try:
        xi = int(x) if x is not None else None
        yi = int(y) if y is not None else None
    except (TypeError, ValueError):
        return None, None
    return xi, yi


def _observation_id(event: dict[str, Any], fallback: str | None) -> str | None:
    oid = event.get("observation_id") or event.get("screenshot_id") or fallback
    if oid is None:
        return None
    s = str(oid).strip()
    return s[:80] or None


def _frame_issue(event: dict[str, Any]) -> str | None:
    if event.get("shadow") is True:
        return "shadow_dom"
    frame = str(event.get("frame") or "main").strip().lower()
    if frame in {"shadow", "shadow_dom", "closed-shadow"}:
        return "shadow_dom"
    if frame in {"iframe", "frame"}:
        return "iframe"
    if frame not in {"", "main", "top", "0"}:
        # named frame / unknown
        return "iframe"
    url = str(event.get("url") or "")
    low = url.lower()
    if low.startswith(("chrome:", "chrome-extension:", "about:", "devtools:")):
        return "internal_page"
    return None


def _fill_text(event: dict[str, Any]) -> tuple[str, bool]:
    field = event.get("field") if isinstance(event.get("field"), dict) else {}
    secret = bool(event.get("redacted")) or looks_secret_field(field, str(event.get("label") or ""))
    raw = event.get("value")
    if raw is None:
        raw = event.get("text")
    if raw is None:
        raw = ""
    if not isinstance(raw, str):
        raw = str(raw)
    if secret:
        return "{{vars." + var_name_for_field(field) + "}}", True
    return raw, False


def _validated_human(action: dict[str, Any]) -> dict[str, Any] | None:
    action = dict(action)
    action.setdefault("schema_version", SCHEMA_VERSION)
    action["source"] = "human"
    try:
        a = validate_action(action, extra_allowed=HUMAN_EXTRA)
    except ActionError:
        return None
    d = a.public_dict()
    # Keep plaintext on the internal step for later export; timeline redacts.
    if a.type in ("fill", "type") and a.text is not None:
        d["text"] = a.text
    if a.type == "goto" and action.get("url"):
        d["url"] = action["url"]
    if action.get("selector_strategy"):
        d["selector_strategy"] = action["selector_strategy"]
    if action.get("confidence") is not None:
        d["confidence"] = action["confidence"]
    d["source"] = "human"
    d["exportable"] = True
    if "css" in d:
        d.pop("css", None)
    return d


def normalize_one(
    event: dict[str, Any],
    *,
    page: Any = None,
    observation_id: str | None = None,
    viewport: dict[str, Any] | None = None,
) -> NormalizeItem:
    if not isinstance(event, dict):
        return NormalizeItem(status="non_exportable", reason="invalid_event")
    kind = str(event.get("kind") or event.get("type") or "").strip().lower()
    if kind in {"goal", "hover", "blur", "focus"}:
        return NormalizeItem(status="non_exportable", reason="ignored_kind", kind=kind)

    issue = _frame_issue(event)
    if issue == "shadow_dom":
        return NormalizeItem(
            status="non_exportable",
            reason="shadow_dom",
            kind=kind,
        )
    if issue == "internal_page":
        return NormalizeItem(status="non_exportable", reason="internal_page", kind=kind)

    iframe_confirm = issue == "iframe"
    vp = event.get("viewport") if isinstance(event.get("viewport"), dict) else viewport
    oid = _observation_id(event, observation_id)

    if kind in {"navigation", "nav", "goto"}:
        url = sanitize_goto_url(str(event.get("url") or ""))
        if not url:
            return NormalizeItem(
                status="non_exportable",
                reason="invalid_or_blocked_url",
                kind=kind,
            )
        action = {
            "schema_version": SCHEMA_VERSION,
            "action": "goto",
            "url": url,
            "source": "human",
            "selector_strategy": "css",
            "confidence": 1.0,
        }
        d = _validated_human(action)
        if not d:
            return NormalizeItem(status="non_exportable", reason="goto_validate_failed", kind=kind)
        if iframe_confirm:
            return NormalizeItem(
                status="needs_confirm",
                reason="iframe",
                action=d,
                kind=kind,
            )
        return NormalizeItem(status="ok", action=d, kind=kind, exportable=True, reason="")

    sel, strategy, conf, fail = pick_selector(event, page=page)
    x, y = _coords(event)

    if kind in {"keypress", "press", "keydown"}:
        key = str(event.get("key") or event.get("value") or event.get("text") or "").strip()
        if not key:
            return NormalizeItem(status="non_exportable", reason="press_missing_key", kind=kind)
        action = {
            "schema_version": SCHEMA_VERSION,
            "action": "press",
            "key": key,
            "source": "human",
            "confidence": 0.8,
            "selector_strategy": strategy or "css",
        }
        if sel:
            action["selector"] = sel
        d = _validated_human(action)
        if not d:
            return NormalizeItem(
                status="non_exportable",
                reason="unsupported_press_key",
                kind=kind,
            )
        if iframe_confirm:
            return NormalizeItem(status="needs_confirm", reason="iframe", action=d, kind=kind)
        return NormalizeItem(status="ok", action=d, kind=kind, exportable=True)

    if kind in {"select", "change"} and str(event.get("tag") or "").lower() == "select":
        kind = "select"

    if kind == "select":
        if not sel:
            return NormalizeItem(status="non_exportable", reason="select_no_selector", kind=kind)
        val = event.get("value")
        if val is None:
            val = event.get("text")
        if val is None:
            return NormalizeItem(status="non_exportable", reason="select_no_value", kind=kind)
        action = {
            "schema_version": SCHEMA_VERSION,
            "action": "select",
            "selector": sel,
            "value": str(val),
            "source": "human",
            "selector_strategy": strategy or "css",
            "confidence": conf,
        }
        d = _validated_human(action)
        if not d:
            return NormalizeItem(status="non_exportable", reason="select_validate_failed", kind=kind)
        if iframe_confirm or fail == "unstable_selector" or conf < 0.5:
            return NormalizeItem(
                status="needs_confirm",
                reason=fail or ("iframe" if iframe_confirm else "unstable_selector"),
                action=d,
                kind=kind,
            )
        return NormalizeItem(status="ok", action=d, kind=kind, exportable=True)

    if kind in {"input", "fill", "type", "change"}:
        if not sel:
            return NormalizeItem(status="non_exportable", reason="fill_no_selector", kind=kind)
        text, secret = _fill_text(event)
        action = {
            "schema_version": SCHEMA_VERSION,
            "action": "fill",
            "selector": sel,
            "text": text,
            "source": "human",
            "selector_strategy": strategy or "css",
            "confidence": conf,
        }
        d = _validated_human(action)
        if not d:
            return NormalizeItem(status="non_exportable", reason="fill_validate_failed", kind=kind)
        if secret:
            d["text"] = text  # placeholder
            d["redacted"] = True
        if iframe_confirm or fail == "unstable_selector":
            return NormalizeItem(
                status="needs_confirm",
                reason=fail or "iframe",
                action=d,
                kind=kind,
            )
        return NormalizeItem(status="ok", action=d, kind=kind, exportable=True)

    # click (default)
    if kind in {"click", "mousedown", ""}:
        if sel:
            action = {
                "schema_version": SCHEMA_VERSION,
                "action": "click",
                "selector": sel,
                "source": "human",
                "selector_strategy": strategy or "css",
                "confidence": conf,
            }
            d = _validated_human(action)
            if not d:
                sel = None
            else:
                if iframe_confirm or fail == "unstable_selector":
                    return NormalizeItem(
                        status="needs_confirm",
                        reason=fail or "iframe",
                        action=d,
                        kind=kind or "click",
                    )
                return NormalizeItem(
                    status="ok", action=d, kind=kind or "click", exportable=True
                )
        # coords last
        if x is None or y is None:
            return NormalizeItem(
                status="non_exportable",
                reason=fail or "no_selector",
                kind=kind or "click",
            )
        if not oid:
            return NormalizeItem(
                status="non_exportable",
                reason="coords_missing_observation_id",
                kind=kind or "click",
            )
        if vp:
            try:
                w = int(vp.get("width") or 0)
                h = int(vp.get("height") or 0)
            except (TypeError, ValueError):
                w, h = 0, 0
            if w and h and (x < 0 or y < 0 or x > w or y > h):
                return NormalizeItem(
                    status="non_exportable",
                    reason="coords_out_of_viewport",
                    kind=kind or "click",
                )
        action = {
            "schema_version": SCHEMA_VERSION,
            "action": "click",
            "x": x,
            "y": y,
            "observation_id": oid,
            "screenshot_id": oid,
            "source": "human",
            "selector_strategy": "coords",
            "confidence": 0.2,
        }
        d = _validated_human(action)
        if not d:
            return NormalizeItem(
                status="non_exportable",
                reason="coords_validate_failed",
                kind=kind or "click",
            )
        return NormalizeItem(
            status="needs_confirm",
            reason="coords_fallback",
            action=d,
            kind=kind or "click",
        )

    return NormalizeItem(
        status="non_exportable",
        reason="unsupported_kind",
        kind=kind,
    )


def normalize_events(
    events: list[Any] | None,
    *,
    page: Any = None,
    observation_id: str | None = None,
    viewport: dict[str, Any] | None = None,
) -> NormalizeResult:
    raw = [e for e in (events or []) if isinstance(e, dict)]
    cleaned = coalesce_events(denoise_events(raw))
    result = NormalizeResult(event_count=len(raw), dropped_raw=0)
    last_goto: str | None = None
    for i, ev in enumerate(cleaned):
        kind = str(ev.get("kind") or "").strip().lower()
        sel = selector_from_obj(ev) or ev.get("selector")
        if kind == "click" and _skip_click_before_fill(cleaned, i, sel if isinstance(sel, str) else None):
            result.dropped_raw += 1
            continue
        item = normalize_one(
            ev, page=page, observation_id=observation_id, viewport=viewport
        )
        if item.status == "ok" and item.action:
            if item.action.get("action") == "goto":
                url = item.action.get("url")
                if url and url == last_goto:
                    result.dropped_raw += 1
                    continue
                last_goto = url
            result.steps.append(item.action)
        elif item.status == "needs_confirm":
            rec = {
                "reason": item.reason,
                "kind": item.kind,
                "proposed": redact_step(item.action) if item.action else None,
                "exportable": False,
            }
            # Keep wire text placeholders; never leak secrets in confirm payload.
            if item.action and item.action.get("text"):
                t = item.action["text"]
                if _is_placeholder(str(t)):
                    rec["proposed"]["text"] = t
            result.needs_confirm.append(rec)
        else:
            result.non_exportable.append(
                {
                    "reason": item.reason,
                    "kind": item.kind,
                    "exportable": False,
                }
            )
            result.dropped_raw += 1
    return result


def human_steps_summary(steps: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for s in steps:
        act = str(s.get("action") or "")
        if act == "goto":
            parts.append(f"goto {redact_text(str(s.get('url') or ''))}")
        elif act in ("fill", "type"):
            parts.append(f"{act} {s.get('selector') or ''} [REDACTED]")
        elif act == "click":
            sel = s.get("selector") or "(coords)"
            parts.append(f"click {sel}")
        elif act == "press":
            parts.append(f"press {s.get('key') or ''}")
        elif act == "select":
            parts.append(f"select {s.get('selector') or ''}")
        else:
            parts.append(act)
    return " → ".join(parts) if parts else "(no human steps)"
