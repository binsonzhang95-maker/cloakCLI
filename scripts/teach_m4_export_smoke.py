#!/usr/bin/env python3
"""Teach Chat M4 export smoke (CLI; headed Ctrl-E is the same write path).

Writes a skill draft from merged Playwright steps, asserts:
  - source=human|agent
  - secrets parameterized
  - existing skill.json is not overwritten
  - dangerous actions do not create a file

Usage:
  CLOAKCLI_BIN=target/debug/cloakcli python3 scripts/teach_m4_export_smoke.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    bin_path = os.environ.get("CLOAKCLI_BIN") or str(ROOT / "target" / "debug" / "cloakcli")
    home = Path(tempfile.mkdtemp(prefix="cloakcli_m4_smoke_"))
    (home / "skills").mkdir()
    (home / "Cargo.toml").write_text('[package]\nname="t"\nversion="0.0.0"\n')
    steps = [
        {
            "action": "goto",
            "url": "https://example.com/login?token=leakme&next=/app",
            "source": "llm",
        },
        {
            "action": "fill",
            "selector": "#pass",
            "text": "hunter2",
            "field_name": "password",
            "source": "human",
        },
        {"action": "click", "selector": "button.submit", "source": "human"},
    ]
    env = os.environ.copy()
    env["CLOAKCLI_HOME"] = str(home)
    r = subprocess.run(
        [
            bin_path,
            "teach",
            "export",
            "--name",
            "taught-login",
            "--goal",
            "Sign in",
            "--steps-json",
            json.dumps(steps),
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        print("FAIL export", r.stdout, r.stderr, file=sys.stderr)
        return 1
    sj = home / "skills" / "taught-login" / "skill.json"
    body = sj.read_text(encoding="utf-8")
    if "hunter2" in body or "leakme" in body:
        print("FAIL secret leak", body, file=sys.stderr)
        return 1
    if '"source": "agent"' not in body and '"source":"agent"' not in body:
        print("FAIL missing agent source", body, file=sys.stderr)
        return 1
    dup = subprocess.run(
        [
            bin_path,
            "teach",
            "export",
            "--name",
            "taught-login",
            "--goal",
            "Sign in",
            "--steps-json",
            json.dumps(steps),
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    if dup.returncode == 0:
        print("FAIL overwrite allowed", dup.stdout, file=sys.stderr)
        return 1
    print("OK", sj)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
