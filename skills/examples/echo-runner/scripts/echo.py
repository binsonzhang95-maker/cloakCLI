#!/usr/bin/env python3
"""Fixture python_runner. No secrets, no shell, no network, no job-supplied commands."""
from __future__ import annotations

import json
import sys

SECRET_MARKERS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
)


def _looks_secret_key(k: str) -> bool:
    lk = k.lower()
    return any(m in lk for m in SECRET_MARKERS)


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
    if isinstance(vars_, dict):
        for k, v in vars_.items():
            if _looks_secret_key(str(k)) and isinstance(v, str) and v and not str(v).startswith("{{"):
                json.dump(
                    {"ok": False, "error": "secret values forbidden in job vars"},
                    sys.stdout,
                )
                sys.stdout.write("\n")
                return 2
            if isinstance(v, str) and (
                "bearer " in v.lower() or v.startswith("sk-") or "cookie=" in v.lower()
            ):
                json.dump(
                    {"ok": False, "error": "secret values forbidden in job vars"},
                    sys.stdout,
                )
                sys.stdout.write("\n")
                return 2
    out = {
        "ok": True,
        "echo": True,
        "skill_id": payload.get("skill_id"),
        "version": payload.get("version"),
        "digest": payload.get("digest"),
        "profile": payload.get("profile"),
    }
    json.dump(out, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
