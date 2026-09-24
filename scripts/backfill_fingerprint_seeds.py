#!/usr/bin/env python3
"""Backfill fingerprint_seed into profiles/*/profile.json without regenerating valid seeds.

Never prints proxy, email, password, or cookie values — only profile names and counts.

Examples:
  PYTHONPATH=python python3 scripts/backfill_fingerprint_seeds.py --prefix cell8-
  PYTHONPATH=python python3 scripts/backfill_fingerprint_seeds.py --profiles-dir /workspace/CloakCLI/profiles --prefix cell8-
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
ROOT = _HERE.parents[1]
_py = ROOT / "python"
if str(_py) not in sys.path:
    sys.path.insert(0, str(_py))

from cloakcli_worker.fingerprint import (  # noqa: E402
    SEED_MAX,
    SEED_MIN,
    coerce_fingerprint_seed,
    ensure_fingerprint_seed,
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--profiles-dir",
        type=Path,
        default=ROOT / "profiles",
        help="Directory containing profiles/<name>/profile.json",
    )
    ap.add_argument(
        "--prefix",
        default="",
        help="Only profiles whose directory name starts with this prefix (e.g. cell8-)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing",
    )
    args = ap.parse_args()
    profiles_dir: Path = args.profiles_dir
    if not profiles_dir.is_dir():
        print(json.dumps({"ok": False, "error": "profiles_dir_missing", "path": str(profiles_dir)}))
        return 2

    minted = 0
    already = 0
    skipped = 0
    errors = 0
    minted_names: list[str] = []
    already_names: list[str] = []

    entries = sorted(
        [p for p in profiles_dir.iterdir() if p.is_dir()],
        key=lambda p: p.name,
    )
    for entry in entries:
        name = entry.name
        if args.prefix and not name.startswith(args.prefix):
            continue
        meta = entry / "profile.json"
        if not meta.is_file():
            skipped += 1
            continue
        try:
            raw = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            errors += 1
            continue
        if not isinstance(raw, dict):
            errors += 1
            continue
        existing = coerce_fingerprint_seed(raw.get("fingerprint_seed"))
        if existing is not None:
            already += 1
            already_names.append(name)
            continue
        if args.dry_run:
            minted += 1
            minted_names.append(name)
            continue
        seed = ensure_fingerprint_seed(meta, regenerate=False)
        # ensure always returns valid; count as minted if we just wrote
        check = coerce_fingerprint_seed(
            json.loads(meta.read_text(encoding="utf-8")).get("fingerprint_seed")
        )
        if check is None or not (SEED_MIN <= check <= SEED_MAX):
            errors += 1
            continue
        minted += 1
        minted_names.append(name)
        # Avoid unused var lint
        _ = seed

    report = {
        "ok": True,
        "profiles_dir": str(profiles_dir),
        "prefix": args.prefix or None,
        "dry_run": bool(args.dry_run),
        "minted": minted,
        "already_had": already,
        "skipped_no_meta": skipped,
        "errors": errors,
        "minted_names": minted_names,
        "already_names_sample": already_names[:10],
        "seed_range": [SEED_MIN, SEED_MAX],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
