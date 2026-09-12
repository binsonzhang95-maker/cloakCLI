"""Redact secrets from logs, trajectories, and error strings.

Never emit API keys, cookies, full proxy userinfo, or Authorization headers.
"""

from __future__ import annotations

import os
import re
from typing import Any

_AUTH = re.compile(r"(?i)(authorization\s*[:=]\s*)(bearer\s+)?(\S+)")
_BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-+/=]+")
_COOKIE_HDR = re.compile(r"(?i)((?:set-)?cookie\s*[:=]\s*)([^\s,;]+)")
_API_KEY_JSON = re.compile(r'(?i)("(?:api[_-]?key|secret|token|password|passwd)"\s*:\s*")([^"]*)(")')
_SK = re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9]{8,}\b")
_PROXY_USERINFO = re.compile(r"(://)([^/@:\s]+):([^/@\s]+)@")
_ENV_ASSIGN = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:API_KEY|SECRET|TOKEN|PASSWORD|PASSWD|AUTHORIZATION)[A-Z0-9_]*)\s*[:=]\s*(\S+)"
)


def extra_secret_values() -> list[str]:
    """Collect values of likely-secret env vars (for stripping)."""
    out: list[str] = []
    for k, v in os.environ.items():
        if not v or len(v) < 6:
            continue
        ku = k.upper()
        if any(
            s in ku
            for s in ("API_KEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD", "AUTHORIZATION", "COOKIE")
        ):
            out.append(v)
    return out


def redact_text(text: str, extra: list[str] | None = None) -> str:
    if not isinstance(text, str) or not text:
        return text
    s = text
    extras = list(extra or [])
    extras.extend(extra_secret_values())
    # Longest first so partials don't leave remnants
    for v in sorted(set(extras), key=len, reverse=True):
        if v and len(v) >= 4:
            s = s.replace(v, "***")
    s = _AUTH.sub(r"\1***", s)
    s = _BEARER.sub("Bearer ***", s)
    s = _COOKIE_HDR.sub(r"\1***", s)
    s = _API_KEY_JSON.sub(r"\1***\3", s)
    s = _SK.sub("sk-***", s)
    s = _PROXY_USERINFO.sub(r"\1***:***@", s)
    s = _ENV_ASSIGN.sub(r"\1=***", s)
    return s


def redact_any(value: Any, extra: list[str] | None = None) -> Any:
    if isinstance(value, str):
        return redact_text(value, extra)
    if isinstance(value, list):
        return [redact_any(v, extra) for v in value]
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            lk = str(k).lower()
            if lk in {
                "api_key",
                "apikey",
                "authorization",
                "cookie",
                "cookies",
                "password",
                "secret",
                "token",
                "proxy",
            }:
                out[k] = "***"
            elif lk in {"api_key_env"}:
                out[k] = v  # name only
            else:
                out[k] = redact_any(v, extra)
        return out
    return value


def looks_secret_key(name: str) -> bool:
    n = (name or "").lower()
    return any(
        s in n
        for s in (
            "password",
            "passwd",
            "secret",
            "token",
            "api_key",
            "apikey",
            "authorization",
            "cookie",
            "proxy",
            "credential",
        )
    )
