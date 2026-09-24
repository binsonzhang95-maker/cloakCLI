"""Persistent per-profile CloakBrowser fingerprint seed locking.

CloakBrowser's get_default_stealth_args() picks random.randint(10000, 99999)
for --fingerprint=<seed> on every launch. Same profile cookie jar + rotating
UA/GPU fingerprint looks like a new visitor. We mint one seed per profile,
persist it in profiles/<name>/profile.json as fingerprint_seed, and pass
args=["--fingerprint=<seed>"] so cloakbrowser build_args dedupes by flag key
and overrides the random default (platform=windows stealth stays).

Never log proxy URLs, passwords, emails, or cookie values.
"""

from __future__ import annotations

import json
import os
import random
import sys
import tempfile
from pathlib import Path
from typing import Any

SEED_MIN = 10000
SEED_MAX = 99999

_FIELD = "fingerprint_seed"


def mint_fingerprint_seed() -> int:
    """Return a new seed in [SEED_MIN, SEED_MAX] inclusive."""
    return random.randint(SEED_MIN, SEED_MAX)


def is_valid_fingerprint_seed(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return SEED_MIN <= value <= SEED_MAX
    if isinstance(value, float) and value.is_integer():
        return SEED_MIN <= int(value) <= SEED_MAX
    if isinstance(value, str) and value.strip().isdigit():
        n = int(value.strip())
        return SEED_MIN <= n <= SEED_MAX
    return False


def coerce_fingerprint_seed(value: Any) -> int | None:
    if not is_valid_fingerprint_seed(value):
        return None
    if isinstance(value, str):
        return int(value.strip())
    return int(value)


def fingerprint_chrome_args(seed: int) -> list[str]:
    """Chrome args that override cloakbrowser's random --fingerprint default."""
    if not is_valid_fingerprint_seed(seed):
        raise ValueError(f"fingerprint_seed out of range [{SEED_MIN}, {SEED_MAX}]: {seed!r}")
    return [f"--fingerprint={int(seed)}"]


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def ensure_fingerprint_seed(meta_path: Path, *, regenerate: bool = False) -> int:
    """Load profile.json; mint/persist fingerprint_seed if missing/invalid or regenerate.

    Preserves all other fields (proxy, notes, etc.). Returns the seed to use.
    """
    meta_path = Path(meta_path)
    data: dict[str, Any] = {}
    if meta_path.is_file():
        try:
            loaded = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}

    existing = coerce_fingerprint_seed(data.get(_FIELD))
    if existing is not None and not regenerate:
        return existing

    seed = mint_fingerprint_seed()
    data[_FIELD] = seed
    _atomic_write_json(meta_path, data)
    return seed


def find_meta_path_for_user_data_dir(
    profiles_root: Path,
    user_data_dir: str | Path,
) -> Path | None:
    """Best-effort match profiles/*/profile.json by user_data_dir field or path heuristics.

    Matching is by resolved path when possible, else by string equality / basename
    suffix (e.g. data/profiles/<name>-pinterest-run ↔ profiles/<name>/profile.json).
    Does not create files.
    """
    profiles_root = Path(profiles_root)
    if not profiles_root.is_dir():
        return None

    target = Path(user_data_dir)
    try:
        target_resolved = target.resolve()
    except OSError:
        target_resolved = target

    target_str = str(user_data_dir).replace("\\", "/").rstrip("/")
    target_name = target.name

    candidates: list[Path] = []
    try:
        entries = sorted(profiles_root.iterdir(), key=lambda p: p.name)
    except OSError:
        return None

    for entry in entries:
        if not entry.is_dir():
            continue
        meta = entry / "profile.json"
        if not meta.is_file():
            continue
        try:
            raw = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        ud = raw.get("user_data_dir")
        if isinstance(ud, str) and ud.strip():
            ud_path = Path(ud)
            if not ud_path.is_absolute():
                # Relative to CloakCLI root (parent of profiles/)
                ud_path = profiles_root.parent / ud
            try:
                if ud_path.resolve() == target_resolved:
                    return meta
            except OSError:
                pass
            if ud.replace("\\", "/").rstrip("/") == target_str:
                return meta
            if Path(ud).name == target_name:
                candidates.append(meta)
        # Heuristic: data/profiles/<name>-pinterest-run or <name>
        name = entry.name
        if target_name in (name, f"{name}-pinterest-run", f"{name}-run"):
            candidates.append(meta)

    if len(candidates) == 1:
        return candidates[0]
    return None


def resolve_fingerprint_seed(
    *,
    fingerprint_seed: int | None = None,
    profile_meta_path: str | Path | None = None,
    profiles_root: str | Path | None = None,
    user_data_dir: str | Path | None = None,
    regenerate: bool = False,
) -> int | None:
    """Resolve a seed from explicit value, meta path, or user_data_dir lookup.

    Returns None when no profile.json can be found (caller may launch without
    override; cloakbrowser will still pick a random seed for that launch).
    When fingerprint_seed is passed explicitly it wins (must be in range);
    regenerate only applies when loading/ensuring via profile.json.
    """
    if fingerprint_seed is not None:
        coerced = coerce_fingerprint_seed(fingerprint_seed)
        if coerced is None:
            raise ValueError(
                f"fingerprint_seed out of range [{SEED_MIN}, {SEED_MAX}]: {fingerprint_seed!r}"
            )
        return coerced

    if profile_meta_path is not None:
        return ensure_fingerprint_seed(Path(profile_meta_path), regenerate=regenerate)

    if profiles_root is not None and user_data_dir is not None:
        meta = find_meta_path_for_user_data_dir(Path(profiles_root), user_data_dir)
        if meta is not None:
            return ensure_fingerprint_seed(meta, regenerate=regenerate)

    return None


def log_fingerprint_seed(seed: int) -> None:
    """Safe stderr line — seed only, never proxy/email/cookies."""
    sys.stderr.write(f"[fingerprint] seed={int(seed)}\n")
    sys.stderr.flush()
