#!/usr/bin/env python3
"""Orchestrate the Teach Chat M1 headed Chrome smoke.

Starts two loopback origins (allow + deny), runs `cloakcli teach start` with
the real hub + MV3 extension + Python worker, then asserts:

- extension and worker each paired (same session_id)
- page_state has url/origin/title/viewport/observation_id
- deny origin: no content inject and no page_state
- extension service-worker restart reconnects (no new session)
- run logs contain no token/cookie/password values

Usage (from repo root):

    ./scripts/e2e-teach-m1-smoke.sh
    python3 scripts/teach_m1_headed_smoke.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]

LEAK_JSON = re.compile(
    r'(?i)"(?:session_token|token|cookie|cookies|password|passwd|secret|authorization)"\s*:\s*"(?!\[REDACTED\]|\*{3})[^"]+"'
)
LEAK_ASSIGN = re.compile(
    r"(?i)\b(?:password|passwd|cookie|authorization)\s*[:=]\s*(?!\[REDACTED\]|\*{3})(\S{4,})"
)

ALLOW_HTML = b"""<!doctype html>
<html><head><title>M1 Allow</title></head>
<body>
  <h1>allow</h1>
  <button id="go" type="button">Go</button>
</body></html>
"""

DENY_HTML = b"""<!doctype html>
<html><head><title>M1 Deny</title></head>
<body><h1>deny</h1></body></html>
"""


class _Handler(BaseHTTPRequestHandler):
    page = b""

    def do_GET(self) -> None:  # noqa: N802
        body = self.page
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        return


def _serve(html: bytes) -> tuple[ThreadingHTTPServer, str]:
    class H(_Handler):
        page = html

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    origin = f"http://127.0.0.1:{httpd.server_address[1]}"
    return httpd, origin


def _has_display() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _bin() -> Path:
    env = os.environ.get("CLOAKCLI_BIN", "").strip()
    if env:
        p = Path(env)
        if p.is_file() and os.access(p, os.X_OK):
            return p
    debug = ROOT / "target" / "debug" / "cloakcli"
    release = ROOT / "target" / "release" / "cloakcli"
    if debug.is_file():
        return debug
    if release.is_file():
        return release
    print("building cloakcli…", flush=True)
    subprocess.run(["cargo", "build", "--offline"], cwd=str(ROOT), check=True)
    if not debug.is_file():
        raise SystemExit("cloakcli binary missing after cargo build")
    return debug


def _ensure_profile(bin_path: Path, home: Path, name: str) -> None:
    env = os.environ.copy()
    env["CLOAKCLI_HOME"] = str(home)
    listed = subprocess.run(
        [str(bin_path), "profile", "list"],
        cwd=str(home),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if name in (listed.stdout or ""):
        return
    created = subprocess.run(
        [str(bin_path), "profile", "create", name],
        cwd=str(home),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if created.returncode != 0:
        raise SystemExit(f"profile create failed: {created.stdout}{created.stderr}")


def scan_logs_for_leaks(text: str) -> list[str]:
    hits: list[str] = []
    for m in LEAK_JSON.finditer(text):
        snippet = m.group(0)
        if "[REDACTED]" in snippet or '"***"' in snippet:
            continue
        hits.append(snippet[:160])
    for m in LEAK_ASSIGN.finditer(text):
        hits.append(m.group(0)[:160])
    return hits


def _load_events(path: Path) -> list[dict]:
    out: list[dict] = []
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _origin(url: str) -> str:
    p = urlparse(url)
    if p.port:
        return f"{p.scheme}://{p.hostname}:{p.port}"
    return f"{p.scheme}://{p.hostname}"


def main() -> int:
    if not _has_display():
        print(
            "FAIL: no DISPLAY/WAYLAND_DISPLAY. Run on a GUI host or under xvfb-run.",
            file=sys.stderr,
        )
        return 2

    bin_path = _bin()
    allow_httpd, allow_origin = _serve(ALLOW_HTML)
    deny_httpd, deny_origin = _serve(DENY_HTML)
    allow_url = allow_origin + "/"
    deny_url = deny_origin + "/"

    tmp = Path(tempfile.mkdtemp(prefix="cloakcli-m1-smoke-"))
    events_path = tmp / "hub-events.jsonl"
    log_path = tmp / "teach.log"
    profile = "m1smoke"
    home = ROOT

    env = os.environ.copy()
    env["CLOAKCLI_HOME"] = str(home)
    env["CLOAKCLI_TEACH_M1_SMOKE"] = "1"
    env["CLOAKCLI_TEACH_SMOKE_DENY_URL"] = deny_url
    env["CLOAKCLI_TEACH_HUB_EVENTS"] = str(events_path)
    env["CLOAKCLI_TEACH_SMOKE_SECONDS"] = os.environ.get("CLOAKCLI_TEACH_SMOKE_SECONDS", "90")
    env["PYTHONUNBUFFERED"] = "1"
    pp = str(ROOT / "python")
    if env.get("PYTHONPATH"):
        env["PYTHONPATH"] = pp + ":" + env["PYTHONPATH"]
    else:
        env["PYTHONPATH"] = pp

    udir = home / "data" / "profiles" / profile
    if udir.exists():
        shutil.rmtree(udir, ignore_errors=True)

    output = ""
    rc = 1
    try:
        _ensure_profile(bin_path, home, profile)
        cmd = [
            str(bin_path),
            "teach",
            "start",
            "--profile",
            profile,
            "--url",
            allow_url,
            "--no-smart-optimize",
        ]
        print(f"== teach start {allow_url} (deny {deny_url}) ==", flush=True)
        proc = subprocess.Popen(
            cmd,
            cwd=str(home),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        chunks: list[str] = []
        assert proc.stdout is not None
        deadline = time.time() + 120
        while True:
            if time.time() > deadline:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except OSError:
                    proc.terminate()
                chunks.append("\nSMOKE_TIMEOUT\n")
                break
            line = proc.stdout.readline()
            if line == "" and proc.poll() is not None:
                break
            if line:
                chunks.append(line)
                sys.stdout.write(line)
                sys.stdout.flush()
        rc = proc.wait(timeout=15)
        output = "".join(chunks)
        log_path.write_text(output, encoding="utf-8")
    finally:
        allow_httpd.shutdown()
        deny_httpd.shutdown()
        shutil.rmtree(home / "data" / "profiles" / profile, ignore_errors=True)
        shutil.rmtree(home / "profiles" / profile, ignore_errors=True)

    smoke_json = None
    for line in output.splitlines():
        if line.startswith("TEACH_M1_SMOKE_JSON "):
            try:
                smoke_json = json.loads(line[len("TEACH_M1_SMOKE_JSON ") :])
            except json.JSONDecodeError:
                smoke_json = None

    events = _load_events(events_path)
    failures: list[str] = []

    if smoke_json is None:
        failures.append("missing TEACH_M1_SMOKE_JSON from worker")
    elif not smoke_json.get("ok"):
        failures.append(f"worker smoke not ok: {smoke_json}")
    else:
        if not smoke_json.get("worker_paired"):
            failures.append("worker did not pair")
        if not smoke_json.get("duplicate_pairing_rejected"):
            failures.append("duplicate pairing was not rejected")
        if not smoke_json.get("worker_reconnect_same_session"):
            failures.append("worker reconnect did not reuse session")
        if not smoke_json.get("injected_allow"):
            failures.append("content script not injected on allow origin")
        if smoke_json.get("injected_deny"):
            failures.append("content script injected on deny origin")
        if not smoke_json.get("sw_stopped"):
            failures.append("extension service worker was not stopped")
        if not smoke_json.get("injected_allow_after_sw"):
            failures.append("content script not injected after SW restart")

    roles = {e.get("data", {}).get("role") for e in events if e.get("event") == "paired"}
    if "extension" not in roles:
        failures.append(f"hub did not pair extension (paired roles={sorted(roles)})")
    if "worker" not in roles:
        failures.append(f"hub did not pair worker (paired roles={sorted(roles)})")

    session_ids = []
    for e in events:
        sid = (e.get("data") or {}).get("session_id")
        if sid:
            session_ids.append(sid)
    unique_sess = sorted(set(session_ids))
    if len(unique_sess) != 1:
        failures.append(f"expected one session_id, got {unique_sess}")
    if smoke_json and smoke_json.get("session_id") and unique_sess:
        if smoke_json["session_id"] not in unique_sess:
            failures.append("worker session_id does not match hub events")

    page_states = [e for e in events if e.get("event") == "page_state"]
    allow_ps = [e for e in page_states if (e.get("data") or {}).get("origin") == allow_origin]
    deny_ps = [e for e in page_states if (e.get("data") or {}).get("origin") == deny_origin]
    if not allow_ps:
        failures.append("hub received no page_state for allow origin")
    else:
        ps = allow_ps[0].get("data") or {}
        for field in ("url", "origin", "title", "viewport", "observation_id"):
            if field not in ps:
                failures.append(f"page_state missing {field}")
        if ps.get("origin") != allow_origin:
            failures.append(f"page_state origin {ps.get('origin')!r} != {allow_origin}")
        if "M1 Allow" not in str(ps.get("title") or ""):
            failures.append(f"page_state title {ps.get('title')!r} missing M1 Allow")
        vp = ps.get("viewport") or {}
        if not isinstance(vp, dict) or not vp.get("width") or not vp.get("height"):
            failures.append(f"page_state viewport invalid: {vp}")
        obs = str(ps.get("observation_id") or "")
        if not obs.startswith("obs-"):
            failures.append(f"page_state observation_id {obs!r}")
        url = str(ps.get("url") or "")
        if _origin(url) != allow_origin:
            failures.append(f"page_state url origin mismatch: {url}")
    if deny_ps:
        failures.append(f"hub stored page_state for deny origin: {deny_ps}")

    reconnected = [
        e
        for e in events
        if e.get("event") == "reconnected" and (e.get("data") or {}).get("role") == "extension"
    ]
    if not reconnected:
        failures.append("hub has no extension reconnected event after SW restart")
    elif unique_sess and (reconnected[0].get("data") or {}).get("session_id") != unique_sess[0]:
        failures.append("extension reconnect used a different session_id")

    rejected = [
        e
        for e in events
        if e.get("event") == "pairing_rejected"
        and (e.get("data") or {}).get("error") == "pairing_consumed"
    ]
    if not rejected:
        failures.append("hub has no pairing_consumed event for duplicate pairing")

    leak_text = output
    if events_path.is_file():
        leak_text += "\n" + events_path.read_text(encoding="utf-8")
    leaks = scan_logs_for_leaks(leak_text)
    if leaks:
        failures.append("secret leakage in logs: " + "; ".join(leaks[:5]))

    print("== hub events ==", flush=True)
    for e in events:
        print(json.dumps(e, ensure_ascii=False), flush=True)
    if smoke_json:
        print("== worker smoke ==", flush=True)
        print(json.dumps(smoke_json, ensure_ascii=False), flush=True)

    if failures:
        print("FAIL:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        print(f"log: {log_path}", file=sys.stderr)
        print(f"events: {events_path}", file=sys.stderr)
        return 1

    print("E2E OK (teach M1 headed smoke)", flush=True)
    return rc if rc not in (0, None) and not smoke_json else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
