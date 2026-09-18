#!/usr/bin/env python3
"""Fixture: emit a declared terminal status as the last stdout line (JSON object)."""
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
    status = "logged_in"
    if isinstance(vars_, dict) and vars_.get("status"):
        status = str(vars_.get("status"))
    # Skill contract: email_confirmed implies login. Callers must not request
    # email_confirmed unless login already succeeded this run.
    report = {
        "skill_id": payload.get("skill_id"),
        "version": payload.get("version"),
        "digest": payload.get("digest"),
        "status": status,
    }
    json.dump(report, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
