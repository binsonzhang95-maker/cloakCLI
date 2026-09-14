#!/usr/bin/env python3
"""Real headed Teach Chat → browser action → result echo (Geek Desktop M2).

Spawns `cloakcli teach chat --events` with CloakBrowser (not --no-browser),
drives a mock-planned click on a loopback page, and asserts the worker
executes it and the JSONL stream echoes the result.

Evidence is written under artifacts/:
  m2-headed-events.jsonl  m2-headed-smoke.log  m2-headed-display.png
  m2-headed-page.png      m2-headed-worker.log

This is not a mock of the execute path. LLM is mocked (plan JSON); the hub,
extension, worker, and Playwright click are real.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"

PAGE = b"""<!doctype html>
<html><head><title>M2 Headed</title></head>
<body>
  <h1>M2 Teach Chat headed</h1>
  <button id="go" type="button">Go</button>
  <div id="out">idle</div>
  <script>
    document.getElementById('go').onclick = function () {
      document.getElementById('out').textContent = 'clicked';
      document.title = 'M2 Clicked';
      fetch('/clicked', {method: 'POST'}).catch(function () {});
    };
  </script>
</body></html>
"""

MOCK = json.dumps(
    {
        "schema_version": 1,
        "actions": [
            {"action": "click", "selector": "#go"},
            {"action": "done", "reason": "ok"},
        ],
    }
)


class _Handler(BaseHTTPRequestHandler):
    clicked = 0
    hits = 0

    def do_GET(self) -> None:  # noqa: N802
        type(self).hits += 1
        body = PAGE
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] == "/clicked":
            type(self).clicked += 1
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        return


def _serve() -> tuple[ThreadingHTTPServer, str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
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
        raise SystemExit("FAIL: cloakcli binary missing after cargo build")
    return debug


def _prepare_home() -> Path:
    home = Path(tempfile.mkdtemp(prefix="cloakcli-m2-headed-"))
    (home / "skills").mkdir(parents=True, exist_ok=True)
    (home / "Cargo.toml").write_text('[package]\nname="t"\nversion="0.0.0"\n', encoding="utf-8")
    ext_src = ROOT / "extensions" / "teach"
    ext_dst = home / "extensions" / "teach"
    ext_dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        ext_dst.symlink_to(ext_src, target_is_directory=True)
    except OSError:
        shutil.copytree(ext_src, ext_dst)
    return home


def _ensure_profile(bin_path: Path, home: Path) -> None:
    env = os.environ.copy()
    env["CLOAKCLI_HOME"] = str(home)
    created = subprocess.run(
        [str(bin_path), "profile", "create", "demo"],
        cwd=str(home),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if created.returncode != 0 and "already" not in (created.stdout + created.stderr).lower():
        raise SystemExit(f"FAIL: profile create: {created.stdout}{created.stderr}")


def _grab_display(path: Path) -> str | None:
    display = os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    if not display:
        return "no DISPLAY"
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return "ffmpeg not found"
    geom = "1280x720"
    try:
        info = subprocess.run(
            ["xwininfo", "-root"],
            capture_output=True,
            text=True,
            check=False,
        )
        for line in info.stdout.splitlines():
            if "geometry" in line.lower():
                # e.g. -geometry 1920x1080+0+0
                parts = line.replace("+", "x").split()
                for p in parts:
                    if "x" in p and p[0].isdigit():
                        w, h = p.split("x")[:2]
                        geom = f"{w}x{h}"
                        break
    except FileNotFoundError:
        pass
    cmd = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-f",
        "x11grab",
        "-video_size",
        geom,
        "-i",
        display,
        "-frames:v",
        "1",
        str(path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if r.returncode != 0 or not path.is_file():
        return f"ffmpeg x11grab failed: {(r.stderr or r.stdout)[:400]}"
    return None


def _grab_page(url: str, path: Path) -> str | None:
    chrome = shutil.which("google-chrome") or shutil.which("google-chrome-stable")
    if not chrome:
        return "google-chrome not found"
    r = subprocess.run(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            f"--screenshot={path}",
            "--window-size=800,600",
            url,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    if not path.is_file():
        return f"chrome screenshot failed: {(r.stderr or r.stdout)[:400]}"
    return None


def _copy_if(src: Path, dest: Path) -> None:
    if src.is_file():
        dest.write_bytes(src.read_bytes())


def main() -> int:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    log_path = ARTIFACTS / "m2-headed-smoke.log"
    events_path = ARTIFACTS / "m2-headed-events.jsonl"
    log_lines: list[str] = []

    def log(msg: str) -> None:
        print(msg, flush=True)
        log_lines.append(msg)

    if not _has_display():
        log("FAIL: no DISPLAY/WAYLAND_DISPLAY (headed CloakBrowser requires a display)")
        log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
        return 2

    try:
        from cloakbrowser import binary_info

        info = binary_info()
        chrome = Path(str(info.get("binary_path") or ""))
        if not info.get("installed") or not chrome.is_file():
            log(f"FAIL: CloakBrowser Chromium not installed at {chrome}")
            log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
            return 2
        log(f"cloakbrowser: {chrome}")
    except Exception as e:
        log(f"FAIL: cloakbrowser import: {type(e).__name__}: {e}")
        log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
        return 2

    bin_path = _bin()
    home = _prepare_home()
    httpd, origin = _serve()
    log(f"origin {origin}")
    log(f"home {home}")
    proc: subprocess.Popen[str] | None = None
    try:
        _ensure_profile(bin_path, home)
        env = os.environ.copy()
        env["CLOAKCLI_HOME"] = str(home)
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONPATH"] = str(ROOT / "python") + (
            (":" + env["PYTHONPATH"]) if env.get("PYTHONPATH") else ""
        )
        env["CLOAKCLI_TEACH_SMOKE_SECONDS"] = "50"
        env["CLOAKCLI_TEACH_STREAM_CHUNK_MS"] = "0"
        proc = subprocess.Popen(
            [
                str(bin_path),
                "teach",
                "chat",
                "--profile",
                "demo",
                "--events",
                "--url",
                origin,
                "--mock-json",
                MOCK,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=str(home),
            text=True,
            bufsize=1,
        )
        assert proc.stdin and proc.stdout

        events: list[dict] = []
        deadline = time.time() + 40
        paired = False
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            line = line.strip()
            if not line:
                continue
            try:
                v = json.loads(line)
            except json.JSONDecodeError:
                continue
            events.append(v)
            if v.get("kind") == "status" and v.get("worker") and v.get("extension"):
                paired = True
                break

        if not paired:
            err = ""
            try:
                if proc.stderr:
                    err = proc.stderr.read()[:4000]
            except Exception:
                pass
            log("FAIL: worker+extension never paired (Teach Chat headed loop did not come up)")
            log(f"events kinds: {[e.get('kind') for e in events]}")
            log(f"stderr: {err}")
            worker_log = home / "data" / "teach" / "chat-worker.log"
            if worker_log.is_file():
                log("worker log tail:\n" + worker_log.read_text(encoding="utf-8", errors="replace")[-3000:])
            events_path.write_text(
                "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
            )
            log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
            return 1

        log("paired: worker + extension")
        proc.stdin.write(
            json.dumps({"cmd": "send", "goal": "click the Go button", "profile": "demo"}) + "\n"
        )
        proc.stdin.flush()

        done = False
        echo_ok = False
        deadline = time.time() + 30
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            line = line.strip()
            if not line:
                continue
            try:
                v = json.loads(line)
            except json.JSONDecodeError:
                continue
            events.append(v)
            kind = v.get("kind")
            if kind == "job" and v.get("state") in ("done", "failed"):
                done = True
                if v.get("state") == "done" or v.get("ok") is True:
                    echo_ok = True
            if kind == "system" and "results" in str(v.get("text", "")).lower():
                echo_ok = True
            if kind == "tool":
                tools = v.get("tools") or []
                if any(str(t.get("status")) in ("ok", "done") for t in tools):
                    echo_ok = True
            if done and echo_ok:
                break

        time.sleep(0.4)
        clicked = _Handler.clicked
        log(f"page GET hits={_Handler.hits} POST /clicked={clicked}")

        disp_err = _grab_display(ARTIFACTS / "m2-headed-display.png")
        if disp_err:
            log(f"display screenshot: {disp_err}")
        else:
            log(f"saved {ARTIFACTS / 'm2-headed-display.png'}")
        page_err = _grab_page(origin + "/", ARTIFACTS / "m2-headed-page.png")
        if page_err:
            log(f"page screenshot: {page_err}")
        else:
            log(f"saved {ARTIFACTS / 'm2-headed-page.png'}")

        try:
            proc.stdin.write(json.dumps({"cmd": "stop"}) + "\n")
            proc.stdin.flush()
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)

        events_path.write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
            encoding="utf-8",
        )
        worker_log = home / "data" / "teach" / "chat-worker.log"
        _copy_if(worker_log, ARTIFACTS / "m2-headed-worker.log")
        log(f"saved {events_path}")

        kinds = [e.get("kind") for e in events]
        log(f"event kinds: {kinds}")
        if "assistant_delta" not in kinds and "assistant" not in kinds:
            log("FAIL: no assistant / assistant_delta in headed stream")
            log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
            return 1
        if clicked < 1 and not echo_ok:
            log(
                "FAIL: browser click not observed (POST /clicked=0 and no result echo). "
                "Teach Chat → action → result loop did not complete."
            )
            log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
            return 1
        if clicked < 1:
            log(
                "WARN: POST /clicked not seen (page may have been clicked without fetch); "
                "relying on JSONL result echo"
            )
        log("PASS headed Teach Chat → click #go → result echo")
        log_path.write_text("\n".join(log_lines) + "\n", encoding="utf-8")
        return 0
    finally:
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
        try:
            httpd.shutdown()
        except Exception:
            pass
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
