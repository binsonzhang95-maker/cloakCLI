"""Headed CloakBrowser launcher for CloakCLI Teach.

Loads the staged MV3 extension (path supplied by the Rust CLI after it
resolved the bundled install/repo copy). Headless is a hard error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .browser import launch_context
from .paths import PathTrustError, ensure_under_root, set_root
from .redact import redact_any, redact_text
from .teach_hub import TeachHubClient, TYPE_ACTION_RESULT


def _require_headed(headed: bool) -> None:
    if not headed:
        raise SystemExit("teach requires a headed CloakBrowser; headless is not supported")


def _preflight_binary() -> Path:
    try:
        from cloakbrowser import binary_info
    except Exception as e:
        raise SystemExit(
            f"teach: cloakbrowser is not importable: {type(e).__name__}: {e}"
        ) from e
    info = binary_info()
    path = Path(str(info.get("binary_path") or ""))
    if not info.get("installed") or not path.is_file():
        raise SystemExit(
            f"teach: CloakBrowser Chromium binary not found at {path}. "
            "Download it with: python3 -c 'from cloakbrowser import ensure_binary; print(ensure_binary())'"
        )
    return path


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
    _run_until_closed(ctx, None, None, smoke_seconds)


def _run_until_closed(ctx, page: Any, hub_client: Any, smoke_seconds: float) -> None:
    """Playwright-owning loop: drain queued action_request while the browser lives.

    Recv/hub thread never calls Playwright; this path does, and can be interrupted
    via hub cancel → interrupt_playwright.
    """
    deadline = time.time() + smoke_seconds if smoke_seconds > 0 else None
    while True:
        if deadline is not None and time.time() >= deadline:
            try:
                ctx.close()
            except Exception:
                pass
            return
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
        if hub_client is not None:
            env = hub_client.pop_action_request(timeout=0.15)
            if env is not None:
                try:
                    _handle_action_request(page, hub_client, env)
                except Exception as e:
                    try:
                        hub_client.send(
                            TYPE_ACTION_RESULT,
                            {"ok": False, "error": type(e).__name__, "results": []},
                            request_id=env.get("request_id"),
                        )
                    except Exception:
                        pass
                continue
        else:
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
    p.add_argument("--hub", help="loopback teach hub host:port")
    p.add_argument("--pairing-id")
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
    _preflight_binary()

    smoke = 0.0
    raw = os.environ.get("CLOAKCLI_TEACH_SMOKE_SECONDS", "").strip()
    if raw:
        try:
            smoke = float(raw)
        except ValueError:
            smoke = 0.0

    hub_client = _maybe_hub_client(args)
    hub_thread: threading.Thread | None = None

    m1_smoke = os.environ.get("CLOAKCLI_TEACH_M1_SMOKE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )

    ctx = launch_context(
        user_data_dir=str(user_data),
        headed=True,
        proxy=args.proxy,
        extension_paths=[str(ext)],
    )
    try:
        from .browser import get_page

        page = get_page(ctx)
        if hub_client is not None:
            hub_client.bind_playwright_page(page)
            hub_thread = threading.Thread(target=hub_client.run, name="teach-hub", daemon=True)
            hub_thread.start()
            hub_client.wait_paired(timeout=5.0)
        if m1_smoke:
            from .teach_m1_smoke import run_headed_smoke

            result = run_headed_smoke(ctx, page, args.url, hub_client)
            print("TEACH_M1_SMOKE_JSON " + json.dumps(result, ensure_ascii=False), flush=True)
            return 0 if result.get("ok") else 1
        if args.url:
            try:
                page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:
                print(f"teach goto warning: {type(e).__name__}", file=sys.stderr)
        _run_until_closed(ctx, page, hub_client, smoke)
    finally:
        if hub_client is not None:
            hub_client.stop()
        try:
            ctx.close()
        except Exception:
            pass
    return 0


def _handle_action_request(page: Any, client: TeachHubClient, env: dict) -> None:
    """Re-validate inbound payload then execute. Never runs raw model text."""
    from .actions import ActionError, execute_actions, parse_actions_payload, validate_action

    request_id = env.get("request_id")
    data = env.get("data") if isinstance(env.get("data"), dict) else {}
    # Refuse a raw LLM string even if a caller stuffed it into data.text.
    parsed = parse_actions_payload(data)
    if parsed.errors:
        client.send(
            TYPE_ACTION_RESULT,
            redact_any(
                {
                    "ok": False,
                    "error": "; ".join(parsed.errors)[:300],
                    "results": [],
                }
            ),
            request_id=request_id,
        )
        return
    raw_items: list[Any] = []
    if isinstance(data, dict):
        if isinstance(data.get("actions"), list):
            raw_items = data["actions"]
        elif "action" in data:
            raw_items = [data]
    for i, item in enumerate(raw_items):
        try:
            validate_action(item)
        except ActionError as e:
            client.send(
                TYPE_ACTION_RESULT,
                redact_any(
                    {
                        "ok": False,
                        "error": f"actions[{i}]: {e}"[:300],
                        "results": [],
                    }
                ),
                request_id=request_id,
            )
            return

    allow = data.get("allow_origins") if isinstance(data.get("allow_origins"), list) else []
    allow = [str(o) for o in allow if isinstance(o, str)]
    confirmed = bool(data.get("confirmed"))
    results = execute_actions(
        page,
        parsed.actions,
        allow_origins=allow,
        cancel_check=client.cancel_requested,
        confirmed=confirmed,
        executor_paused=client.executor_paused,
    )
    payload_results = []
    page_out = None
    ok = True
    for r in results:
        rec = {
            "status": r.status,
            "reason": redact_text(r.reason or ""),
            "action": r.action,
            "page": r.page,
        }
        payload_results.append(rec)
        if r.page:
            page_out = r.page
        if r.status not in ("ok", "done"):
            ok = False
    client.send(
        TYPE_ACTION_RESULT,
        redact_any(
            {
                "ok": ok,
                "results": payload_results,
                "page": page_out,
                "errors": parsed.errors,
            }
        ),
        request_id=request_id,
    )


def _maybe_hub_client(args: argparse.Namespace) -> TeachHubClient | None:
    addr = (args.hub or os.environ.get("CLOAKCLI_TEACH_HUB") or "").strip()
    pairing_id = (args.pairing_id or os.environ.get("CLOAKCLI_TEACH_PAIRING_ID") or "").strip()
    code = os.environ.get("CLOAKCLI_TEACH_PAIRING_CODE", "").strip()
    if not addr or not pairing_id or not code:
        return None
    host, sep, port_s = addr.rpartition(":")
    if not sep or host not in ("127.0.0.1", "localhost") or not port_s.isdigit():
        print("teach hub: ignoring non-loopback hub address", file=sys.stderr)
        return None
    return TeachHubClient(host, int(port_s), pairing_id, code, role="worker")


if __name__ == "__main__":
    raise SystemExit(main())
