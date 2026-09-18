#!/usr/bin/env python3
"""Fixture: status set is shipped|returned — not interchangeable with pin-statuses."""
from __future__ import annotations

import json
import sys


def main() -> int:
    raw = sys.stdin.read() or "{}"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        json.dump({"ok": False, "error": f"invalid stdin json: {e}"}, sys.stdout)
        sys.stdout.write("\n")
        return 2
    if not isinstance(payload, dict):
        json.dump({"ok": False, "error": "stdin json must be an object"}, sys.stdout)
        sys.stdout.write("\n")
        return 2
    vars_ = payload.get("vars") or {}
    status = "shipped"
    if isinstance(vars_, dict) and vars_.get("status"):
        status = str(vars_.get("status"))
    json.dump(
        {
            "skill_id": payload.get("skill_id"),
            "version": payload.get("version"),
            "digest": payload.get("digest"),
            "status": status,
        },
        sys.stdout,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
