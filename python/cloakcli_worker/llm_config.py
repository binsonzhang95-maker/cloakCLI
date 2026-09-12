"""Load config/llm.json (API key via env var name only)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .paths import get_root

DEFAULT_RECOVER_TIMEOUT_SEC = 300
DEFAULT_MAX_ACTIONS = 120
DEFAULT_MAX_LOOPS = 60
DEFAULT_MAX_TOKENS = 200_000


@dataclass
class LlmConfig:
    enabled: bool = False
    base_url: str = ""
    model: str = ""
    api_key_env: str = "OPENAI_API_KEY"
    recover_timeout_sec: float = DEFAULT_RECOVER_TIMEOUT_SEC
    allow_hosts: list[str] = field(default_factory=list)
    max_actions: int = DEFAULT_MAX_ACTIONS
    max_loops: int = DEFAULT_MAX_LOOPS
    max_tokens_per_recover: int = DEFAULT_MAX_TOKENS
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
    base_url = str(data.get("base_url") or "").strip().rstrip("/")
    if base_url:
        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https"):
            base_url = ""
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

    api_key_env = str(data.get("api_key_env") or "OPENAI_API_KEY").strip()
    if not _valid_env_name(api_key_env):
        api_key_env = "OPENAI_API_KEY"

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
        path=path,
    )


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
