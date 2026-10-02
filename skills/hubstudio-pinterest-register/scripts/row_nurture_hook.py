#!/usr/bin/env python3
"""Drop-in for STATE/bin/row_nurture_hook.py

Usage (cwd = STATE, invoked by bin/run_api_row.py --nurture-only):
  python3 bin/row_nurture_hook.py row-00NN http://127.0.0.1:PORT
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _roots_from_marker() -> list[Path]:
    out: list[Path] = []
    for marker in (HERE / "hub_cloakcli_root", Path.cwd() / "bin" / "hub_cloakcli_root"):
        if marker.is_file():
            raw = marker.read_text(encoding="utf-8").strip()
            if raw:
                out.append(Path(raw))
    return out


def find_runner() -> Path:
    env = os.environ.get("HUB_REGISTER_RUNNER")
    if env:
        p = Path(env)
        if p.is_file():
            return p
    env_root = os.environ.get("HUB_CLOAKCLI_ROOT") or os.environ.get("CLOAKCLI_ROOT")
    cands: list[Path] = []
    roots = []
    if env_root:
        roots.append(Path(env_root))
    roots.extend(_roots_from_marker())
    for root in roots:
        cands.append(root / "skills/hubstudio-pinterest-register/scripts/run_hubstudio_pinterest_register.py")
        cands.append(root / "scripts/run_hubstudio_pinterest_register.py")
    cands.append(HERE / "run_hubstudio_pinterest_register.py")
    cands.extend(
        [
            Path("/workspace/CloakCLI/skills/hubstudio-pinterest-register/scripts/run_hubstudio_pinterest_register.py"),
            Path("/workspace/CloakCLI/scripts/run_hubstudio_pinterest_register.py"),
        ]
    )
    for p in cands:
        if p.is_file():
            return p
    raise SystemExit("hubstudio_register_runner_missing (set HUB_REGISTER_RUNNER or HUB_CLOAKCLI_ROOT)")


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: row_nurture_hook.py <row_id> <cdp_endpoint>", file=sys.stderr)
        return 2
    row_id = sys.argv[1]
    cdp = sys.argv[2]
    state = Path.cwd().resolve()
    runner = find_runner()
    cmd = [
        sys.executable,
        str(runner),
        "--row-id",
        row_id,
        "--state-dir",
        str(state),
        "--cdp-endpoint",
        cdp,
        "--external-lifecycle",
        "--nurture-only",
    ]
    env = os.environ.copy()
    print(f"row_nurture_hook runner={runner} row={row_id} cdp_host=127.0.0.1", flush=True)
    return subprocess.call(cmd, cwd=str(state), env=env)


if __name__ == "__main__":
    raise SystemExit(main())
