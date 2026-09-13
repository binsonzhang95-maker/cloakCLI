"""Thin Teach Hub client (M1): JSONL Envelope over loopback TCP.

Pairs with a short code, heartbeats, and reconnects without creating a
new session. Does not log tokens, cookies, or passwords.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import uuid
from typing import Any, Callable
from urllib.parse import urlsplit

TEACH_PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 256 * 1024
MAX_SELECTOR_LEN = 500

TYPE_PAIRING_ACCEPT = "pairing_accept"
TYPE_PAIRING_RESULT = "pairing_result"
TYPE_PAGE_STATE = "page_state"
TYPE_HEARTBEAT = "heartbeat"
TYPE_ERROR = "error"


def selector_from_obj(obj: dict[str, Any] | None) -> str | None:
    """Canonical field is `selector`; accept legacy Recover `css` when reading."""
    if not isinstance(obj, dict):
        return None
    raw = obj.get("selector")
    if raw is None:
        raw = obj.get("css")
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s or len(s) > MAX_SELECTOR_LEN:
        return None
    if ";" in s or "{" in s or "}" in s:
        return None
    return s


def origin_of(url: str) -> str | None:
    try:
        parts = urlsplit(url.strip())
    except Exception:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    host = parts.hostname
    if not host:
        return None
    host = host.lower()
    port = parts.port
    default = 80 if parts.scheme == "http" else 443
    if port and port != default:
        return f"{parts.scheme}://{host}:{port}"
    return f"{parts.scheme}://{host}"


def is_http_origin(origin: str) -> bool:
    try:
        parts = urlsplit(origin.strip())
    except Exception:
        return False
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return False
    if parts.username or parts.password:
        return False
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        return False
    return True


def origin_allowed(origin: str, allowlist: list[str]) -> bool:
    if not is_http_origin(origin):
        return False
    return origin in allowlist


def envelope(msg_type: str, data: dict[str, Any] | None = None, session_id: str | None = None) -> dict[str, Any]:
    env: dict[str, Any] = {
        "v": TEACH_PROTOCOL_VERSION,
        "type": msg_type,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "data": data if data is not None else {},
    }
    if session_id:
        env["session_id"] = session_id
    return env


def parse_envelope(raw: bytes | str) -> dict[str, Any]:
    if isinstance(raw, bytes):
        if len(raw) > MAX_MESSAGE_BYTES:
            raise ValueError("message_too_large")
        text = raw.decode("utf-8")
    else:
        if len(raw) > MAX_MESSAGE_BYTES:
            raise ValueError("message_too_large")
        text = raw
    text = text.strip()
    if not text:
        raise ValueError("empty message")
    env = json.loads(text)
    if not isinstance(env, dict):
        raise ValueError("invalid_json")
    if env.get("v") != TEACH_PROTOCOL_VERSION:
        raise ValueError("protocol_version")
    if not env.get("type"):
        raise ValueError("invalid_type")
    data = env.get("data", {})
    if data is None:
        env["data"] = {}
    elif not isinstance(data, dict):
        raise ValueError("invalid_data")
    return env


def _redact(text: str) -> str:
    s = text
    for key in ("session_token", "token", "cookie", "password", "secret", "authorization"):
        needle = f'"{key}"'
        lower = s.lower()
        start = 0
        out = []
        while True:
            pos = lower.find(needle, start)
            if pos < 0:
                out.append(s[start:])
                break
            out.append(s[start:pos])
            colon = s.find(":", pos + len(needle))
            if colon < 0:
                out.append(s[pos:])
                break
            q = s.find('"', colon)
            if q < 0:
                out.append(s[pos:colon + 1])
                out.append("[REDACTED]")
                start = colon + 1
                continue
            end = s.find('"', q + 1)
            if end < 0:
                end = len(s) - 1
            out.append(s[pos:q + 1])
            out.append("[REDACTED]")
            out.append('"')
            start = end + 1
            lower = s.lower()
        s = "".join(out)
    return s


class TeachHubClient:
    """JSONL client for the Rust teach hub. Safe to run in a daemon thread."""

    def __init__(
        self,
        host: str,
        port: int,
        pairing_id: str,
        pairing_code: str,
        role: str = "worker",
        on_page_state: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.pairing_id = pairing_id
        self.pairing_code = pairing_code
        self.role = role
        self.on_page_state = on_page_state
        self.session_id: str | None = None
        self.session_token: str | None = None
        self.paired = threading.Event()
        self._stop = threading.Event()
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self.last_error: str | None = None

    def stop(self) -> None:
        self._stop.set()
        self.drop_connection()

    def drop_connection(self) -> None:
        """Close the socket so run() reconnects with the stored session token."""
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def wait_paired(self, timeout: float = 5.0) -> bool:
        return self.paired.wait(timeout)

    def run(self) -> None:
        backoff = 0.25
        while not self._stop.is_set():
            try:
                self._session(backoff)
                backoff = 0.25
            except Exception as e:
                if not self.last_error:
                    self.last_error = type(e).__name__
                self.paired.clear()
            if self._stop.is_set():
                return
            time.sleep(backoff)
            backoff = min(backoff * 2, 15.0)

    def send(self, msg_type: str, data: dict[str, Any] | None = None) -> None:
        env = envelope(msg_type, data, self.session_id)
        self._write(env)

    def _session(self, _backoff: float) -> None:
        sock = socket.create_connection((self.host, self.port), timeout=5)
        sock.settimeout(20)
        self._sock = sock
        try:
            self._pair(sock)
            next_beat = time.time() + 10
            buf = b""
            while not self._stop.is_set():
                now = time.time()
                if now >= next_beat:
                    self.send(TYPE_HEARTBEAT, {"ok": True})
                    next_beat = now + 10
                try:
                    chunk = sock.recv(4096)
                except TimeoutError:
                    continue
                except socket.timeout:
                    continue
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    if len(line) > MAX_MESSAGE_BYTES:
                        continue
                    try:
                        env = parse_envelope(line)
                    except Exception:
                        continue
                    self._handle(env)
        finally:
            try:
                sock.close()
            except OSError:
                pass
            self._sock = None
            self.paired.clear()

    def _pair(self, sock: socket.socket) -> None:
        nonce = uuid.uuid4().hex
        data: dict[str, Any] = {"nonce": nonce, "role": self.role}
        if self.session_token:
            data["session_token"] = self.session_token
            data["resume_from"] = 0
        else:
            data["pairing_id"] = self.pairing_id
            data["code"] = self.pairing_code
        self._write(envelope(TYPE_PAIRING_ACCEPT, data, self.session_id), sock=sock)
        deadline = time.time() + 5
        buf = b""
        while time.time() < deadline:
            sock.settimeout(max(0.1, deadline - time.time()))
            try:
                chunk = sock.recv(4096)
            except (TimeoutError, socket.timeout):
                continue
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                env = parse_envelope(line)
                if env.get("type") == TYPE_PAIRING_RESULT:
                    data = env.get("data") or {}
                    if data.get("ok") and data.get("session_id") and data.get("session_token"):
                        self.session_id = str(data["session_id"])
                        self.session_token = str(data["session_token"])
                        self.paired.set()
                        sock.settimeout(20)
                        return
                    self.last_error = str(data.get("error") or "pairing_failed")
                    raise RuntimeError("pairing_failed")
        raise RuntimeError("pairing_timeout")

    def _handle(self, env: dict[str, Any]) -> None:
        t = env.get("type")
        if t == TYPE_PAGE_STATE and self.on_page_state:
            self.on_page_state(env.get("data") or {})
        elif t == TYPE_PAIRING_RESULT:
            data = env.get("data") or {}
            if data.get("ok") and data.get("session_id"):
                self.session_id = str(data["session_id"])
                if data.get("session_token"):
                    self.session_token = str(data["session_token"])
                self.paired.set()

    def _write(self, env: dict[str, Any], sock: socket.socket | None = None) -> None:
        raw = (json.dumps(env, ensure_ascii=False) + "\n").encode("utf-8")
        if len(raw) > MAX_MESSAGE_BYTES:
            return
        target = sock or self._sock
        if target is None:
            return
        with self._lock:
            target.sendall(raw)


def hub_from_env() -> TeachHubClient | None:
    addr = os.environ.get("CLOAKCLI_TEACH_HUB", "").strip()
    code = os.environ.get("CLOAKCLI_TEACH_PAIRING_CODE", "").strip()
    pairing_id = os.environ.get("CLOAKCLI_TEACH_PAIRING_ID", "").strip()
    if not addr or not code or not pairing_id:
        return None
    host, _, port_s = addr.rpartition(":")
    if not host or not port_s.isdigit():
        return None
    if host not in ("127.0.0.1", "localhost"):
        return None
    return TeachHubClient(host, int(port_s), pairing_id, code)
