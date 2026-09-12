"""Origin / host policy for recover goto."""

from __future__ import annotations

from urllib.parse import urlparse

BLOCKED_SCHEMES = {
    "file",
    "javascript",
    "data",
    "vbscript",
    "about",
    "blob",
    "chrome",
    "chrome-extension",
    "view-source",
}


def origin_of(url: str | None) -> str | None:
    if not url:
        return None
    try:
        p = urlparse(url)
    except Exception:
        return None
    if p.scheme not in ("http", "https") or not p.hostname:
        return None
    host = p.hostname.lower().rstrip(".")
    if p.port:
        return f"{p.scheme}://{host}:{p.port}"
    return f"{p.scheme}://{host}"


def safe_url_for_prompt(url: str | None) -> str:
    """Origin + path only (drop query/fragment — may contain tokens)."""
    if not url:
        return ""
    try:
        p = urlparse(url)
    except Exception:
        return ""
    origin = origin_of(url) or ""
    path = p.path or "/"
    return f"{origin}{path}"


def host_matches(hostname: str, pattern: str) -> bool:
    h = hostname.lower().rstrip(".")
    p = pattern.lower().strip().rstrip(".")
    if not h or not p:
        return False
    if p.startswith("*."):
        suffix = p[1:]  # .example.com
        return h.endswith(suffix) or h == p[2:]
    return h == p


def url_allowed(
    url: str,
    *,
    task_origin: str | None,
    allow_hosts: list[str] | None,
) -> tuple[bool, str]:
    """Return (ok, reason). Rejects dangerous schemes; same-origin or allow_hosts."""
    try:
        p = urlparse(url)
    except Exception:
        return False, "invalid url"
    scheme = (p.scheme or "").lower()
    if scheme in BLOCKED_SCHEMES or scheme not in ("http", "https"):
        return False, f"blocked scheme: {scheme or '(none)'}"
    if p.username or p.password:
        return False, "url must not contain credentials"
    if not p.hostname:
        return False, "url missing host"
    dest = origin_of(url)
    if not dest:
        return False, "could not parse origin"
    if task_origin and dest == task_origin:
        return True, "same origin"
    host = p.hostname.lower().rstrip(".")
    for pat in allow_hosts or []:
        if host_matches(host, pat):
            return True, "allow_hosts"
    if not task_origin and not (allow_hosts or []):
        return False, "no task origin and host not in allow_hosts"
    return False, "cross-origin goto requires allow_hosts"
