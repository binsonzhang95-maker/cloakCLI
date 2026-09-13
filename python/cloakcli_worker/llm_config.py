"""Load config/llm.json (API key via env var name only)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from .paths import get_root

DEFAULT_RECOVER_TIMEOUT_SEC = 90
ADVANCED_RECOVER_TIMEOUT_SEC = 300
DEFAULT_MAX_ACTIONS = 120
DEFAULT_MAX_LOOPS = 60
DEFAULT_MAX_TOKENS = 200_000
DEFAULT_MAX_MODEL_ROUNDS = 3
DEFAULT_TEACH_SMART_OPTIMIZE = True
DEFAULT_API_KEY_ENV = "CLOAKCLI_LLM_API_KEY"
COMPAT_API_KEY_ENV = "OPENAI_API_KEY"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
MAX_MODELS_BODY = 1_048_576
MAX_MODELS = 256
MAX_MODEL_ID_LEN = 128
MAX_MODELS_PAGES = 5
MODELS_CONNECT_TIMEOUT_SEC = 10
MODELS_READ_TIMEOUT_SEC = 20


@dataclass
class LlmConfig:
    enabled: bool = False
    base_url: str = ""
    model: str = ""
    api_key_env: str = DEFAULT_API_KEY_ENV
    recover_timeout_sec: float = DEFAULT_RECOVER_TIMEOUT_SEC
    allow_hosts: list[str] = field(default_factory=list)
    max_actions: int = DEFAULT_MAX_ACTIONS
    max_loops: int = DEFAULT_MAX_LOOPS
    max_tokens_per_recover: int = DEFAULT_MAX_TOKENS
    max_model_rounds: int = DEFAULT_MAX_MODEL_ROUNDS
    teach_smart_optimize: bool = DEFAULT_TEACH_SMART_OPTIMIZE
    path: str = ""

    def public_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "base_url": self.base_url,
            "model": self.model,
            "api_key_env": self.api_key_env,
            "recover_timeout_sec": int(self.recover_timeout_sec)
            if float(self.recover_timeout_sec).is_integer()
            else self.recover_timeout_sec,
            "allow_hosts": list(self.allow_hosts),
            "max_actions": self.max_actions,
            "max_loops": self.max_loops,
            "max_tokens_per_recover": self.max_tokens_per_recover,
            "max_model_rounds": self.max_model_rounds,
            "teach_smart_optimize": self.teach_smart_optimize,
        }


def config_path(root: Path | None = None) -> Path:
    r = root or get_root()
    return r / "config" / "llm.json"


def load_llm_config(root: Path | None = None) -> LlmConfig | None:
    path = config_path(root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return parse_llm_config(data, path=str(path))


def parse_llm_config(data: dict[str, Any], *, path: str = "") -> LlmConfig:
    base_url = normalize_base_url(str(data.get("base_url") or ""))
    timeout = data.get("recover_timeout_sec", DEFAULT_RECOVER_TIMEOUT_SEC)
    try:
        timeout_f = float(timeout)
    except (TypeError, ValueError):
        timeout_f = DEFAULT_RECOVER_TIMEOUT_SEC
    timeout_f = max(5.0, min(3600.0, timeout_f))

    hosts_raw = data.get("allow_hosts") or []
    hosts: list[str] = []
    if isinstance(hosts_raw, str):
        hosts_raw = [h.strip() for h in hosts_raw.split(",")]
    if isinstance(hosts_raw, list):
        for h in hosts_raw:
            n = _normalize_host(str(h))
            if n and n not in hosts:
                hosts.append(n)

    def _int(key: str, default: int, lo: int, hi: int) -> int:
        try:
            v = int(data.get(key, default))
        except (TypeError, ValueError):
            v = default
        return max(lo, min(hi, v if v else default))

    api_key_env = str(data.get("api_key_env") or DEFAULT_API_KEY_ENV).strip()
    if not _valid_env_name(api_key_env):
        api_key_env = DEFAULT_API_KEY_ENV

    return LlmConfig(
        enabled=bool(data.get("enabled", False)),
        base_url=base_url,
        model=str(data.get("model") or "").strip(),
        api_key_env=api_key_env,
        recover_timeout_sec=timeout_f,
        allow_hosts=hosts,
        max_actions=_int("max_actions", DEFAULT_MAX_ACTIONS, 1, 500),
        max_loops=_int("max_loops", DEFAULT_MAX_LOOPS, 1, 200),
        max_tokens_per_recover=_int(
            "max_tokens_per_recover", DEFAULT_MAX_TOKENS, 1000, 5_000_000
        ),
        max_model_rounds=_int(
            "max_model_rounds", DEFAULT_MAX_MODEL_ROUNDS, 1, 8
        ),
        teach_smart_optimize=bool(
            data["teach_smart_optimize"]
            if "teach_smart_optimize" in data
            else DEFAULT_TEACH_SMART_OPTIMIZE
        ),
        path=path,
    )


def _parse_http_url(url: str, *, allow_query: bool = False):
    """http(s) URL parser. Base URLs reject credentials, query, and fragment."""
    s = (url or "").strip()
    if not s:
        return None
    lower = s.lower()
    if lower.startswith(("file:", "javascript:", "data:")):
        return None
    if any(c.isspace() or ord(c) < 32 for c in s):
        return None
    try:
        parsed = urlparse(s)
    except Exception:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    if parsed.username or parsed.password or "@" in (parsed.netloc or ""):
        return None
    if not parsed.hostname:
        return None
    if parsed.query and not allow_query:
        return None
    if parsed.fragment:
        return None
    return parsed


def _path_segments(path: str) -> list[str]:
    return [s for s in (path or "").split("/") if s]


def _drop_endpoint_suffixes(segs: list[str]) -> list[str]:
    segs = list(segs)
    while segs:
        last = segs[-1].lower()
        if len(segs) >= 2 and segs[-2].lower() == "chat" and last == "completions":
            segs.pop()
            segs.pop()
            continue
        if last in ("models", "completions"):
            segs.pop()
            continue
        break
    return segs


def _collapse_duplicate_v1(segs: list[str]) -> list[str]:
    """Collapse consecutive `/v1` path segments only (`/v1/v1` → `/v1`, not `/v1/v10`)."""
    out: list[str] = []
    for s in segs:
        if s.lower() == "v1" and out and out[-1].lower() == "v1":
            continue
        out.append(s)
    return out


def _netloc_no_userinfo(parsed) -> str:
    host = parsed.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    if parsed.port:
        return f"{host}:{parsed.port}"
    return host


def _rebuild(parsed, segs: list[str], *, query: str = "") -> str:
    path = "/" + "/".join(segs) if segs else ""
    out = urlunparse((parsed.scheme, _netloc_no_userinfo(parsed), path, "", query, ""))
    return out.rstrip("/")


def normalize_base_url(url: str) -> str:
    """http(s) only, strip trailing slash, drop endpoint suffixes, collapse /v1/v1."""
    parsed = _parse_http_url(url, allow_query=False)
    if parsed is None:
        return ""
    segs = _collapse_duplicate_v1(_drop_endpoint_suffixes(_path_segments(parsed.path)))
    return _rebuild(parsed, segs)


def join_openai_path(base: str, path: str) -> str:
    """Build `{base}/{path}` without duplicating a trailing `/v1` path segment."""
    base = normalize_base_url(base)
    if not base:
        return ""
    path = (path or "").strip().lstrip("/")
    if not path:
        return base
    parsed = _parse_http_url(base, allow_query=False)
    if parsed is None:
        return ""
    segs = _path_segments(parsed.path)
    add = _path_segments(path)
    if segs and segs[-1].lower() == "v1" and add and add[0].lower() == "v1":
        add = add[1:]
    return _rebuild(parsed, segs + add)


def accept_model_id(mid: str, api_key: str | None = None) -> str | None:
    """Return a usable model id, or None. Never keeps ids that contain the API key."""
    mid = (mid or "").strip()
    if not mid:
        return None
    if any(ord(c) < 32 for c in mid):
        return None
    if len(mid) > MAX_MODEL_ID_LEN:
        return None
    if api_key and len(api_key) >= 4 and api_key in mid:
        return None
    return mid


def models_url(base: str) -> str:
    return join_openai_path(base, "models")


def chat_completions_url(base: str) -> str:
    return join_openai_path(base, "chat/completions")


def resolve_api_key(cfg: LlmConfig) -> str:
    """Env named in api_key_env, with OPENAI_API_KEY fallback for the default name."""
    name = cfg.api_key_env or DEFAULT_API_KEY_ENV
    v = os.environ.get(name) or ""
    if v:
        return v
    if name == DEFAULT_API_KEY_ENV:
        return os.environ.get(COMPAT_API_KEY_ENV) or ""
    return ""


def _valid_env_name(name: str) -> bool:
    if not name or len(name) > 64 or "-" in name:
        return False
    if not (name[0].isalpha() or name[0] == "_"):
        return False
    return all(c.isalnum() or c == "_" for c in name)


def _normalize_host(raw: str) -> str:
    s = raw.strip().lower()
    if not s:
        return ""
    if "://" in s:
        try:
            p = urlparse(s if "://" in s else f"https://{s}")
            s = (p.hostname or "").lower()
        except Exception:
            return ""
    s = s.strip(".")
    if not s or "/" in s or " " in s or "\\" in s:
        return ""
    return s
