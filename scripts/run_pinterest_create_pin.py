#!/usr/bin/env python3
"""Forward to skills/pinterest-create-pin/scripts/run_pinterest_create_pin.py.

Same pattern as scripts/run_instagram_register_soft.py. The skill package
entry is the product path (manifest python_runner).
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

TARGET = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "pinterest-create-pin"
    / "scripts"
    / "run_pinterest_create_pin.py"
)


def main() -> None:
    sys.argv[0] = str(TARGET)
    runpy.run_path(str(TARGET), run_name="__main__")


if __name__ == "__main__":
    main()
