"""Path trust: only allow paths under the configured project root."""

from __future__ import annotations

import os
from pathlib import Path


class PathTrustError(ValueError):
    pass


def set_root(root: str | Path) -> Path:
    p = Path(root).resolve()
    os.environ["CLOAKCLI_ROOT"] = str(p)
    return p


def get_root() -> Path:
    env = os.environ.get("CLOAKCLI_ROOT")
    if not env:
        raise PathTrustError("CLOAKCLI_ROOT not set")
    return Path(env).resolve()


def ensure_under_root(path: str | Path, root: Path | None = None) -> Path:
    root = (root or get_root()).resolve()
    p = Path(path)
    if not p.is_absolute():
        p = root / p
    # Resolve; Path.resolve() always works; strict=False is 3.9+ default behavior via absolute+norm
    try:
        resolved = p.resolve()
    except Exception as e:
        raise PathTrustError(f"cannot resolve path: {path}: {e}") from e

    root_s = str(root)
    res_s = str(resolved)
    if res_s != root_s and not res_s.startswith(root_s + os.sep):
        raise PathTrustError(f"path escapes project root: {resolved} (root={root})")
    return resolved
