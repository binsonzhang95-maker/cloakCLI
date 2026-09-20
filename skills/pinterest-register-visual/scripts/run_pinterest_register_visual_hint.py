#!/usr/bin/env python3
"""Preflight for pinterest-register-visual (0.2.3).

Prints the product MM-loop checklist and validates that profile.json + secrets
env exist. Does NOT launch a browser. No secrets values are printed.

Product path: scripts/run_pinterest_register_visual_mm.py (not computerUse).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
if _HERE.parent.name == "scripts" and _HERE.parents[1].name == "pinterest-register-visual":
    ROOT = _HERE.parents[3]
else:
    ROOT = _HERE.parents[1]

VERSION = "0.2.3"
SKILL_ID = "pinterest-register-visual"

REQUIRED_SECRET_KEYS = (
    "PINTEREST_EMAIL",
    "PINTEREST_PASSWORD",
    "OUTLOOK_EMAIL",
    "OUTLOOK_CLIENT_ID",
    "OUTLOOK_REFRESH_TOKEN",
)

CHECKLIST = [
    "PRODUCT PATH: python3 scripts/run_pinterest_register_visual_mm.py (Bot must call this; computerUse is not the product path)",
    "DISPLAY / headed CloakBrowser only (NEVER system Chrome; no fingerprint knobs)",
    "Load proxy from profiles/<id>/profile.json (udeal/geo as bound)",
    "Persistent user_data_dir: data/profiles/<id>-pinterest-run",
    "LLM: config/llm.json + CLOAKCLI_LLM_API_KEY (default grok-4.6; vision: CLOAKCLI_LLM_VISION_MODEL / llm.json vision_model); never --api-key",
    "Pacing: 800–2500ms fields; 2–5s before Continue; 3–8s settle after (no click storms)",
    "MM loop: screenshot → vision JSON click|type|press|wait|scroll|imap_fetch_code|nurture|done|fail",
    "IMAP 6-digit via scripts/outlook_imap_pinterest_code.py + secrets env",
    "On logged-in: same-session nurture BEFORE close; probe+flush; nurture 0.1.7+",
    "Never fresh-profile after success; touch .cloak_session_ok; concurrency ≤2–3",
    "Report status/path/nurture_status/elapsed version 0.2.3 from skill manifest (see OPERATOR.md)",
    "Smoke: --dry-run mock vision (no CloakBrowser)",
]


def load_env_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _ = line.split("=", 1)
        keys.add(k.strip())
    return keys


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="geo02")
    ap.add_argument(
        "--secrets",
        default=str(ROOT / "data/secrets/pinterest-outlook-01.env"),
    )
    args = ap.parse_args()

    profile_path = ROOT / "profiles" / args.profile / "profile.json"
    secrets_path = Path(args.secrets)
    if not secrets_path.is_absolute():
        secrets_path = (ROOT / secrets_path).resolve()

    display = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY") or ""
    issues: list[str] = []

    if not profile_path.is_file():
        issues.append(f"missing_profile:{profile_path}")
        proxy_present = False
        proxy_scheme = None
    else:
        try:
            meta = json.loads(profile_path.read_text(encoding="utf-8"))
        except Exception as e:
            issues.append(f"bad_profile_json:{type(e).__name__}")
            meta = {}
        proxy = meta.get("proxy") or ""
        proxy_present = bool(proxy)
        proxy_scheme = proxy.split("://", 1)[0] if "://" in proxy else (proxy[:12] or None)
        if not proxy_present:
            issues.append("profile_missing_proxy")

    if not secrets_path.is_file():
        issues.append(f"missing_secrets:{secrets_path}")
        missing_keys: list[str] = list(REQUIRED_SECRET_KEYS)
    else:
        keys = load_env_keys(secrets_path)
        missing_keys = [k for k in REQUIRED_SECRET_KEYS if k not in keys]
        if missing_keys:
            issues.append("secrets_missing_keys:" + ",".join(missing_keys))

    imap_helper = ROOT / "scripts" / "outlook_imap_pinterest_code.py"
    nurture = ROOT / "scripts" / "run_pinterest_nurture_browse.py"
    mm_runner = ROOT / "scripts" / "run_pinterest_register_visual_mm.py"
    if not imap_helper.is_file():
        issues.append("missing_imap_helper")
    if not nurture.is_file():
        issues.append("missing_nurture_runner")
    if not mm_runner.is_file():
        issues.append("missing_mm_runner")

    report = {
        "skill_id": SKILL_ID,
        "version": VERSION,
        "mode": "preflight_hint",
        "profile": args.profile,
        "profile_path_ok": profile_path.is_file(),
        "proxy_present": proxy_present,
        "proxy_scheme": proxy_scheme,
        "secrets_path_ok": secrets_path.is_file(),
        "secrets_keys_missing": missing_keys,
        "display_set": bool(display),
        "display_hint": display[:32] if display else "",
        "checklist": CHECKLIST,
        "operator_doc": "skills/pinterest-register-visual/OPERATOR.md",
        "product_runner": "scripts/run_pinterest_register_visual_mm.py",
        "not_product_path": "computerUse",
        "ok": not issues,
        "issues": issues,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if not issues else 2


if __name__ == "__main__":
    sys.exit(main())
