"""Headed CloakBrowser launcher for CloakCLI Teach.

Loads the staged MV3 extension (path supplied by the Rust CLI after it
resolved the bundled install/repo copy). Headless is a hard error.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from .browser import launch_context
from .paths import PathTrustError, ensure_under_root, set_root


def _require_headed(headed: bool) -> None:
    if not headed:
        raise SystemExit("teach requires a headed CloakBrowser; headless is not supported")


def _validate_extension(path: Path) -> Path:
    man = path / "manifest.json"
    if not man.is_file():
        raise SystemExit(f"teach extension missing manifest.json: {path}")
    text = man.read_text(encoding="utf-8")
    if "<all_urls>" in text:
        raise SystemExit("teach extension manifest must not use <all_urls>")
    if "CloakCLI Teach" not in text:
        raise SystemExit(f"refusing unknown extension at {path}")
    return path


def _wait_closed(ctx, smoke_seconds: float) -> None:
    if smoke_seconds > 0:
        deadline = time.time() + smoke_seconds
        while time.time() < deadline:
            try:
                _ = list(ctx.pages)
            except Exception:
                return
            time.sleep(0.2)
        try:
            ctx.close()
        except Exception:
            pass
        return
    while True:
        try:
            pages = list(ctx.pages)
        except Exception:
            return
        if not pages:
            time.sleep(0.4)
            try:
                if not ctx.pages:
                    return
            except Exception:
                return
            continue
        try:
            pages[0].wait_for_timeout(400)
        except Exception:
            return


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cloakcli_worker.teach")
    p.add_argument("--root", required=True)
    p.add_argument("--user-data-dir", required=True)
    p.add_argument("--extension", required=True)
    p.add_argument("--url")
    p.add_argument("--proxy")
    p.add_argument("--headed", action="store_true")
    p.add_argument("--headless", action="store_true")
    args = p.parse_args(argv)

    if args.headless or not args.headed:
        _require_headed(False)

    root = set_root(args.root)
    try:
        user_data = ensure_under_root(args.user_data_dir, root)
        ext = ensure_under_root(args.extension, root)
    except PathTrustError as e:
        print(f"path trust: {e}", file=sys.stderr)
        return 2

    user_data.mkdir(parents=True, exist_ok=True)
    ext = _validate_extension(ext)

    smoke = 0.0
    raw = os.environ.get("CLOAKCLI_TEACH_SMOKE_SECONDS", "").strip()
    if raw:
        try:
            smoke = float(raw)
        except ValueError:
            smoke = 0.0

    ctx = launch_context(
        user_data_dir=str(user_data),
        headed=True,
        proxy=args.proxy,
        extension_paths=[str(ext)],
    )
    try:
        from .browser import get_page

        page = get_page(ctx)
        if args.url:
            try:
                page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                print(f"teach goto warning: {type(e).__name__}", file=sys.stderr)
        _wait_closed(ctx, smoke)
    finally:
        try:
            ctx.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
