"""Entry: python -m cloakcli_worker [serve --root R --socket S]

JSONL protocol over stdin/stdout OR unix domain socket:
  → {"id":"1","cmd":"open","profile":"work","url":"...","headed":false,...}
  ← {"id":"1","ok":true,"data":{...}} | {"id":"1","ok":false,"error":"..."}
"""

from __future__ import annotations

import argparse
import os
import signal
import socket
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

from . import protocol
from .browser import InvalidCookieError, apply_cookie_file, get_page, launch_context
from .paths import PathTrustError, ensure_under_root, get_root, set_root
from .runner import run_skill

# In-process sessions for open/close within a long-lived worker
_sessions: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()
_protocol_errors = 0
_MAX_PROTOCOL_ERRORS = 50


def _handle(req: dict[str, Any]) -> dict[str, Any]:
    req_id = str(req.get("id", "0"))
    cmd = (req.get("cmd") or "").strip().lower()

    try:
        if cmd in ("_skip",):
            return protocol.ok(req_id, {"skipped": True})

        if cmd in ("ping", "noop"):
            return protocol.ok(req_id, {"pong": True, "version": "0.1.0"})

        if cmd == "shutdown":
            _close_all()
            return protocol.ok(req_id, {"shutdown": True})

        if cmd == "open":
            return _cmd_open(req_id, req)

        if cmd == "close":
            return _cmd_close(req_id, req)

        if cmd == "list_sessions":
            with _lock:
                data = [
                    {
                        "id": sid,
                        "profile": s.get("profile"),
                        "url": s.get("url"),
                        "headed": s.get("headed"),
                        "cookies_applied": bool(s.get("cookies_applied")),
                    }
                    for sid, s in _sessions.items()
                ]
            return protocol.ok(req_id, {"sessions": data})

        if cmd == "run_skill":
            return _cmd_run_skill(req_id, req)

        return protocol.err(req_id, f"unknown cmd: {cmd}")
    except PathTrustError as e:
        return protocol.err(req_id, f"path trust: {e}")
    except InvalidCookieError as e:
        return protocol.err(req_id, str(e))
    except Exception as e:
        tb = traceback.format_exc()
        sys.stderr.write(tb)
        return protocol.err(req_id, str(e))


def _trusted_path(req: dict[str, Any], key: str) -> str | None:
    """Resolve and validate a path field under project root."""
    val = req.get(key)
    if not val:
        return None
    root = get_root()
    return str(ensure_under_root(val, root))


def _cmd_open(req_id: str, req: dict[str, Any]) -> dict[str, Any]:
    user_data_dir = _trusted_path(req, "user_data_dir")
    if not user_data_dir:
        return protocol.err(req_id, "user_data_dir required (under project root)")
    Path(user_data_dir).mkdir(parents=True, exist_ok=True)
    headed = bool(req.get("headed", False))
    proxy = req.get("proxy")
    url = req.get("url") or "about:blank"
    profile = req.get("profile") or "default"

    ctx = launch_context(user_data_dir=user_data_dir, headed=headed, proxy=proxy)

    cookie_meta = None
    cookie_file = _trusted_path(req, "cookie_file")
    if cookie_file:
        try:
            cookie_meta = apply_cookie_file(ctx, cookie_file)
        except Exception:
            try:
                ctx.close()
            except Exception:
                pass
            raise

    page = get_page(ctx)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
    except Exception as e:
        goto_err = str(e)
    else:
        goto_err = None

    session_id = uuid.uuid4().hex[:10]
    with _lock:
        _sessions[session_id] = {
            "ctx": ctx,
            "profile": profile,
            "url": url,
            "headed": headed,
            "proxy": proxy,
            "cookies_applied": bool(cookie_meta),
        }
    data: dict[str, Any] = {
        "session": session_id,
        "profile": profile,
        "url": url,
        "headed": headed,
    }
    if cookie_meta:
        # metadata only — no values
        data["cookies"] = cookie_meta
    if goto_err:
        data["goto_warning"] = goto_err
    return protocol.ok(req_id, data)


def _cmd_close(req_id: str, req: dict[str, Any]) -> dict[str, Any]:
    target = req.get("session") or "all"
    closed: list[str] = []
    with _lock:
        keys = list(_sessions.keys())
        for sid in keys:
            s = _sessions[sid]
            match = (
                target == "all"
                or sid == target
                or sid.startswith(str(target))
                or s.get("profile") == target
            )
            if match:
                try:
                    s["ctx"].close()
                except Exception:
                    pass
                del _sessions[sid]
                closed.append(sid)
    return protocol.ok(req_id, {"closed": closed})


def _close_all() -> None:
    with _lock:
        for sid, s in list(_sessions.items()):
            try:
                s["ctx"].close()
            except Exception:
                pass
        _sessions.clear()


def _cmd_run_skill(req_id: str, req: dict[str, Any]) -> dict[str, Any]:
    root = get_root()
    skill_path = req.get("skill_path")
    if skill_path:
        skill_path = str(ensure_under_root(skill_path, root))
    else:
        skill = req.get("skill")
        if not skill:
            return protocol.err(req_id, "skill_path or skill required")
        found = None
        skills_root = root / "skills"
        for p in skills_root.rglob("skill.json"):
            try:
                import json

                data = json.loads(p.read_text(encoding="utf-8"))
                if data.get("name") == skill or p.parent.name == skill:
                    found = str(p)
                    break
            except Exception:
                continue
        if not found:
            return protocol.err(req_id, f"skill not found: {skill}")
        skill_path = found

    user_data_dir = _trusted_path(req, "user_data_dir")
    if not user_data_dir:
        return protocol.err(req_id, "user_data_dir required (under project root)")

    cookie_file = _trusted_path(req, "cookie_file")

    # Ignore client-supplied root; use daemon root
    result = run_skill(
        skill_path=skill_path,
        user_data_dir=user_data_dir,
        headed=bool(req.get("headed", False)),
        proxy=req.get("proxy"),
        vars=req.get("vars") if isinstance(req.get("vars"), dict) else {},
        root=str(root),
        cookie_file=cookie_file,
    )
    return protocol.ok(req_id, result)


def main_stdio() -> None:
    """Legacy stdin/stdout JSONL loop (oneshot skill runs)."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    # Ensure root from env
    root = os.environ.get("CLOAKCLI_ROOT") or "."
    set_root(root)

    global _protocol_errors
    while True:
        try:
            req = protocol.read_request()
        except Exception as e:
            _protocol_errors += 1
            protocol.write_response(protocol.err("0", f"bad json: {e}"))
            if _protocol_errors >= _MAX_PROTOCOL_ERRORS:
                sys.stderr.write("too many protocol errors; exiting\n")
                break
            continue
        if req is None:
            break
        if (req.get("cmd") or "") == "_skip":
            continue
        resp = _handle(req)
        protocol.write_response(resp)
        if (req.get("cmd") or "").lower() == "shutdown":
            break
        time.sleep(0)


def _handle_client(conn: socket.socket) -> bool:
    """Handle one client connection (may send multiple JSONL lines).
    Returns True if shutdown was requested.
    """
    global _protocol_errors
    f = conn.makefile("rwb")
    shutdown = False
    try:
        while True:
            raw = f.readline()
            if not raw:
                break
            try:
                line = raw.decode("utf-8")
                req = protocol.read_request_line(line)
            except Exception as e:
                _protocol_errors += 1
                err = protocol.err("0", f"bad json: {e}")
                f.write((__import__("json").dumps(err) + "\n").encode("utf-8"))
                f.flush()
                if _protocol_errors >= _MAX_PROTOCOL_ERRORS:
                    shutdown = True
                    break
                continue
            if req is None:
                continue
            resp = _handle(req)
            f.write((__import__("json").dumps(resp, ensure_ascii=False) + "\n").encode("utf-8"))
            f.flush()
            if (req.get("cmd") or "").lower() == "shutdown":
                shutdown = True
                break
    finally:
        try:
            f.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass
    return shutdown


def main_serve(root: str, sock_path: str) -> None:
    """Unix-domain socket daemon."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    root_path = set_root(root)
    sock_path = str(Path(sock_path))
    Path(sock_path).parent.mkdir(parents=True, exist_ok=True)
    if Path(sock_path).exists():
        Path(sock_path).unlink()

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(16)
    # Restrict socket perms
    try:
        os.chmod(sock_path, 0o600)
    except Exception:
        pass

    sys.stderr.write(f"cloakcli_worker serve root={root_path} sock={sock_path}\n")
    sys.stderr.flush()

    try:
        while True:
            conn, _ = srv.accept()
            # Serialize request handling for MVP (sessions are shared)
            stop = _handle_client(conn)
            if stop:
                break
    finally:
        _close_all()
        try:
            srv.close()
        except Exception:
            pass
        try:
            Path(sock_path).unlink(missing_ok=True)
        except Exception:
            pass


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] == "serve":
        parser = argparse.ArgumentParser(prog="cloakcli_worker serve")
        parser.add_argument("--root", required=True)
        parser.add_argument("--socket", required=True)
        args = parser.parse_args(argv[1:])
        main_serve(args.root, args.socket)
    else:
        main_stdio()


if __name__ == "__main__":
    main()
