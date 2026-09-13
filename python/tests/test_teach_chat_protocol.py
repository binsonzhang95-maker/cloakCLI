"""Teach Chat M1: Envelope, selector alias, pairing, origin allowlist."""

from __future__ import annotations

import json
import socket
import threading
import time
import unittest
from pathlib import Path

from cloakcli_worker.teach_hub import (
    MAX_MESSAGE_BYTES,
    TeachHubClient,
    envelope,
    origin_allowed,
    origin_of,
    parse_envelope,
    selector_from_obj,
)

ROOT = Path(__file__).resolve().parents[2]
BG = ROOT / "extensions" / "teach" / "background.js"
PAIRING = ROOT / "extensions" / "teach" / "pairing.js"
MANIFEST = ROOT / "extensions" / "teach" / "manifest.json"


class ProtocolUnitTests(unittest.TestCase):
    def test_envelope_round_trip(self):
        env = envelope("page_state", {"url": "https://example.com/"}, session_id="sess-1")
        parsed = parse_envelope(json.dumps(env) + "\n")
        self.assertEqual(parsed["v"], 1)
        self.assertEqual(parsed["type"], "page_state")
        self.assertEqual(parsed["session_id"], "sess-1")

    def test_rejects_wrong_version_and_oversize(self):
        with self.assertRaises(ValueError) as ctx:
            parse_envelope('{"v":2,"type":"heartbeat","data":{}}')
        self.assertEqual(str(ctx.exception), "protocol_version")
        with self.assertRaises(ValueError) as ctx:
            parse_envelope(b"x" * (MAX_MESSAGE_BYTES + 1))
        self.assertEqual(str(ctx.exception), "message_too_large")

    def test_selector_canonical_and_css_alias(self):
        self.assertEqual(selector_from_obj({"selector": "#ok", "css": "#legacy"}), "#ok")
        self.assertEqual(selector_from_obj({"css": "button.submit"}), "button.submit")
        self.assertIsNone(selector_from_obj({"css": "div{color:red}"}))
        self.assertIsNone(selector_from_obj({"selector": ""}))

    def test_origin_allowlist(self):
        self.assertIsNone(origin_of("file:///etc/passwd"))
        self.assertIsNone(origin_of("javascript:alert(1)"))
        self.assertIsNone(origin_of("data:text/html,hi"))
        self.assertEqual(origin_of("https://Example.COM/app?q=1"), "https://example.com")
        allow = ["https://example.com"]
        self.assertTrue(origin_allowed("https://example.com", allow))
        self.assertFalse(origin_allowed("https://evil.example", allow))
        self.assertFalse(origin_allowed("file://ok", allow))


class _FakeHub:
    def __init__(self) -> None:
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.session_id = "sess-fake"
        self.token = "tok-fake-not-for-logs"
        self.code = "AB12CD"
        self.pairing_id = "pair-1"
        self.seen_roles: list[str] = []
        self.page_states: list[dict] = []
        self.stop = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.stop = True
        try:
            self.sock.close()
        except OSError:
            pass

    def _run(self) -> None:
        while not self.stop:
            try:
                self.sock.settimeout(0.2)
                conn, _ = self.sock.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            threading.Thread(target=self._conn, args=(conn,), daemon=True).start()

    def _conn(self, conn: socket.socket) -> None:
        buf = b""
        try:
            conn.settimeout(2)
            while not self.stop:
                try:
                    chunk = conn.recv(4096)
                except TimeoutError:
                    continue
                if not chunk:
                    return
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    msg = json.loads(line.decode("utf-8"))
                    self._handle(conn, msg)
        except OSError:
            return
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _send(self, conn: socket.socket, env: dict) -> None:
        conn.sendall((json.dumps(env) + "\n").encode("utf-8"))

    def _handle(self, conn: socket.socket, msg: dict) -> None:
        t = msg.get("type")
        data = msg.get("data") or {}
        if t == "pairing_accept":
            if data.get("session_token") == self.token:
                self._send(
                    conn,
                    {
                        "v": 1,
                        "type": "pairing_result",
                        "data": {
                            "ok": True,
                            "session_id": self.session_id,
                            "session_token": self.token,
                            "resumed": True,
                            "role": data.get("role"),
                        },
                    },
                )
                return
            if data.get("code") != self.code or data.get("pairing_id") != self.pairing_id:
                self._send(
                    conn,
                    {
                        "v": 1,
                        "type": "pairing_result",
                        "data": {"ok": False, "error": "pairing_bad_code"},
                    },
                )
                return
            role = str(data.get("role") or "")
            self.seen_roles.append(role)
            self._send(
                conn,
                {
                    "v": 1,
                    "type": "pairing_result",
                    "data": {
                        "ok": True,
                        "session_id": self.session_id,
                        "session_token": self.token,
                        "resumed": False,
                        "role": role,
                    },
                },
            )
            return
        if t == "page_state":
            origin = data.get("origin")
            if origin != "https://example.com":
                self._send(
                    conn,
                    {
                        "v": 1,
                        "type": "error",
                        "data": {"code": "origin_not_allowed", "retryable": False},
                    },
                )
                return
            self.page_states.append(data)
            self._send(
                conn,
                {
                    "v": 1,
                    "type": "page_state",
                    "session_id": self.session_id,
                    "seq": len(self.page_states),
                    "data": data,
                },
            )
        if t == "heartbeat":
            self._send(conn, {"v": 1, "type": "heartbeat", "data": {"ok": True}})


class HubClientTests(unittest.TestCase):
    def test_worker_pairs_and_reconnects_same_session(self):
        hub = _FakeHub()
        try:
            c = TeachHubClient("127.0.0.1", hub.port, hub.pairing_id, hub.code, role="worker")
            t = threading.Thread(target=c.run, daemon=True)
            t.start()
            self.assertTrue(c.wait_paired(3), c.last_error)
            self.assertEqual(c.session_id, hub.session_id)
            token = c.session_token
            self.assertTrue(token)
            c.stop()
            time.sleep(0.2)

            c2 = TeachHubClient("127.0.0.1", hub.port, hub.pairing_id, "WRONG", role="worker")
            c2.session_token = token
            t2 = threading.Thread(target=c2.run, daemon=True)
            t2.start()
            self.assertTrue(c2.wait_paired(3), c2.last_error)
            self.assertEqual(c2.session_id, hub.session_id)
            c2.stop()
        finally:
            hub.close()

    def test_pairing_bad_code_fails(self):
        hub = _FakeHub()
        try:
            c = TeachHubClient("127.0.0.1", hub.port, hub.pairing_id, "NOPE01", role="worker")
            t = threading.Thread(target=c.run, daemon=True)
            t.start()
            self.assertFalse(c.wait_paired(1.5))
            self.assertIn(c.last_error, ("pairing_failed", "pairing_bad_code"))
            c.stop()
        finally:
            hub.close()


class ExtensionSafetyTests(unittest.TestCase):
    def test_no_all_urls_and_pairing_present(self):
        man = MANIFEST.read_text(encoding="utf-8")
        self.assertNotIn("<all_urls>", man)
        hosts = json.loads(man)["host_permissions"]
        self.assertIn("ws://127.0.0.1/*", hosts)
        self.assertIn("http://127.0.0.1/*", hosts)
        bg = BG.read_text(encoding="utf-8")
        self.assertIn("importScripts(\"pairing.js\")", bg)
        self.assertIn("forwardPageState", bg)
        pairing = PAIRING.read_text(encoding="utf-8")
        self.assertIn("pairing_accept", pairing)
        self.assertIn("session_token", pairing)
        self.assertIn("page_state", pairing)


if __name__ == "__main__":
    unittest.main()
