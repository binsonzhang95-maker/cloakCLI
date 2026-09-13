"""Teach Chat M2: unified action schema, execute whitelist, high-risk nav, cancel."""

from __future__ import annotations

import threading
import time
import unittest

from cloakcli_worker.actions import (
    MAX_ACTIONS_PER_TURN,
    ActionError,
    execute_action,
    execute_actions,
    goto_risk,
    parse_actions_payload,
    parse_model_output,
    validate_action,
)

from fakes import FakePage


class TeachActionSchemaTests(unittest.TestCase):
    def test_selector_canonical_accepts_legacy_css(self):
        a = validate_action({"schema_version": 1, "action": "click", "selector": "#go"})
        self.assertEqual(a.selector, "#go")
        self.assertEqual(a.css, "#go")
        b = validate_action({"action": "click", "css": "button.submit"})
        self.assertEqual(b.selector, "button.submit")
        d = a.public_dict()
        self.assertEqual(d["selector"], "#go")
        self.assertNotIn("css", d)

    def test_max_three_actions_per_turn(self):
        self.assertEqual(MAX_ACTIONS_PER_TURN, 3)
        blob = {
            "schema_version": 1,
            "actions": [
                {"action": "wait", "ms": 10},
                {"action": "wait", "ms": 10},
                {"action": "wait", "ms": 10},
                {"action": "done", "reason": "too many"},
            ],
        }
        r = parse_model_output(__import__("json").dumps(blob))
        self.assertEqual(r.actions, [])
        self.assertTrue(any("too many" in e for e in r.errors))

    def test_three_actions_ok(self):
        r = parse_model_output(
            '{"schema_version":1,"actions":['
            '{"action":"click","selector":"a"},'
            '{"action":"wait","ms":10},'
            '{"action":"done","reason":"ok"}]}'
        )
        self.assertEqual([a.type for a in r.actions], ["click", "wait", "done"])
        self.assertEqual(r.errors, [])

    def test_raw_prose_not_executable(self):
        r = parse_model_output("please click the submit button and then fill the form")
        self.assertEqual(r.actions, [])
        self.assertTrue(r.errors)

    def test_payload_rejects_raw_text_field(self):
        r = parse_actions_payload({"text": "page.evaluate('1+1')"})
        self.assertEqual(r.actions, [])
        self.assertTrue(any("raw model text" in e for e in r.errors))

    def test_forbidden_actions_rejected(self):
        for name in (
            "shell",
            "exec",
            "eval",
            "evaluate",
            "python",
            "read_file",
            "write_file",
            "javascript",
            "bash",
        ):
            with self.assertRaises(ActionError, msg=name):
                validate_action({"action": name, "cmd": "id"})

    def test_javascript_and_file_urls_rejected(self):
        for url in (
            "javascript:alert(1)",
            "data:text/html,hi",
            "file:///etc/passwd",
            "vbscript:msgbox(1)",
        ):
            with self.assertRaises(ActionError, msg=url):
                validate_action({"action": "goto", "url": url})

    def test_selector_javascript_rejected(self):
        with self.assertRaises(ActionError):
            validate_action({"action": "click", "selector": "javascript:alert(1)"})

    def test_press_not_in_unified_schema(self):
        with self.assertRaises(ActionError):
            validate_action({"action": "press", "key": "Enter"})


class TeachExecuteTests(unittest.TestCase):
    def test_one_goal_at_most_three_actions(self):
        page = FakePage()
        page.elements["#submit"] = {"text": "Submit"}
        parsed = parse_model_output(
            '{"schema_version":1,"actions":['
            '{"action":"click","selector":"a"},'
            '{"action":"click","selector":"#submit"},'
            '{"action":"done","reason":"clicked"}]}'
        )
        self.assertEqual(len(parsed.actions), 3)
        results = execute_actions(page, parsed.actions, allow_origins=["https://example.com"])
        self.assertEqual([r.status for r in results], ["ok", "ok", "done"])
        self.assertEqual(page.clicked, ["a", "#submit"])

    def test_https_goto_any_origin_no_allowlist_or_confirm(self):
        page = FakePage(url="https://example.com/")
        a = validate_action({"action": "goto", "url": "https://evil.example/x"})
        out = execute_action(page, a, allow_origins=["https://example.com"])
        self.assertEqual(out.status, "ok")
        self.assertEqual(page.gotos, ["https://evil.example/x"])
        risk, _ = goto_risk(
            "https://paste.example/doc",
            allow_origins=["https://example.com"],
            current_origin="https://example.com",
        )
        self.assertEqual(risk, "ok")
        a2 = validate_action({"action": "goto", "url": "https://other.example/login"})
        out2 = execute_action(
            page,
            a2,
            allow_origins=["https://example.com"],
            current_origin="https://example.com",
            confirmed=False,
        )
        self.assertEqual(out2.status, "ok")
        self.assertIn("https://other.example/login", page.gotos)

    def test_same_origin_goto_ok(self):
        page = FakePage(url="https://example.com/app")
        a = validate_action({"action": "goto", "url": "https://example.com/next"})
        out = execute_action(
            page,
            a,
            allow_origins=["https://example.com"],
            current_origin="https://example.com",
        )
        self.assertEqual(out.status, "ok")
        self.assertEqual(page.gotos, ["https://example.com/next"])
        self.assertEqual(out.page["origin"], "https://example.com")

    def test_goto_still_rejects_dangerous_schemes(self):
        page = FakePage()
        for url in ("javascript:alert(1)", "file:///etc/passwd", "data:text/html,hi"):
            with self.assertRaises(ActionError):
                validate_action({"action": "goto", "url": url})
            risk, reason = goto_risk(url, allow_origins=["https://example.com"])
            self.assertEqual(risk, "reject")
            self.assertTrue(reason)
        self.assertEqual(page.gotos, [])

    def test_cancel_stops_in_flight_wait(self):
        page = FakePage()
        cancelled = threading.Event()
        cancelled.set()
        a = validate_action({"action": "wait", "ms": 5000})
        out = execute_action(page, a, cancel_check=cancelled.is_set)
        self.assertEqual(out.status, "cancelled")

    def test_cancel_during_wait_interrupts(self):
        from cloakcli_worker.actions import interrupt_playwright

        page = FakePage()
        cancelled = threading.Event()
        a = validate_action({"action": "wait", "ms": 5000})
        out: dict[str, str] = {}

        def run() -> None:
            result = execute_action(page, a, cancel_check=cancelled.is_set)
            out["status"] = result.status

        t = threading.Thread(target=run)
        t.start()
        self.assertTrue(page.entered_call.wait(1.0))
        cancelled.set()
        interrupt_playwright(page)
        t.join(2.0)
        self.assertFalse(t.is_alive())
        self.assertEqual(out.get("status"), "cancelled")

    def test_non_goto_does_not_block_https_off_allowlist(self):
        page = FakePage(url="https://evil.example/")
        a = validate_action({"action": "click", "selector": "a"})
        page.elements["a"] = {"text": "x"}
        out = execute_action(page, a, allow_origins=["https://example.com"])
        self.assertEqual(out.status, "ok")
        self.assertEqual(page.clicked, ["a"])

    def test_non_goto_rejects_file_page(self):
        page = FakePage(url="file:///etc/passwd")
        a = validate_action({"action": "click", "selector": "a"})
        page.elements["a"] = {"text": "x"}
        out = execute_action(page, a, allow_origins=["https://example.com"])
        self.assertEqual(out.status, "rejected")
        self.assertIn("scheme", out.reason)
        self.assertEqual(page.clicked, [])

    def test_executor_paused_rejects(self):
        page = FakePage()
        a = validate_action({"action": "click", "selector": "a"})
        out = execute_action(page, a, executor_paused=True)
        self.assertEqual(out.status, "rejected")
        self.assertEqual(out.reason, "executor_paused")
        self.assertEqual(page.clicked, [])

    def test_fill_text_redacted_in_outcome(self):
        page = FakePage()
        page.elements["#pw"] = {"text": ""}
        a = validate_action({"action": "fill", "selector": "#pw", "text": "super-secret-password"})
        out = execute_action(page, a)
        self.assertEqual(out.status, "ok")
        self.assertEqual(page.filled, [("#pw", "super-secret-password")])
        self.assertEqual(out.action["text"], "[REDACTED]")
        self.assertEqual(out.action["text_len"], len("super-secret-password"))
        self.assertNotIn("css", out.action)
        self.assertEqual(out.action["selector"], "#pw")
        self.assertNotIn("super-secret-password", str(out.action))

    def test_page_change_on_timeline_snapshot(self):
        page = FakePage()
        page.nav_on_click["a"] = "https://example.com/next"
        a = validate_action({"action": "click", "selector": "a"})
        out = execute_action(page, a)
        self.assertEqual(out.status, "ok")
        self.assertEqual(out.page["url"], "https://example.com/next")
        self.assertEqual(out.page["origin"], "https://example.com")

    def test_worker_handler_never_runs_raw_text(self):
        from cloakcli_worker.teach import _handle_action_request

        class _Client:
            def __init__(self):
                self.sent = []
                self.executor_paused = False

            def cancel_requested(self):
                return False

            def send(self, typ, data, request_id=None):
                self.sent.append((typ, data, request_id))

        page = FakePage()
        client = _Client()
        _handle_action_request(
            page,
            client,
            {
                "request_id": "r1",
                "data": {"text": "page.evaluate('1+1'); shell id"},
            },
        )
        self.assertEqual(page.clicked, [])
        self.assertEqual(client.sent[0][0], "action_result")
        self.assertFalse(client.sent[0][1]["ok"])

        _handle_action_request(
            page,
            client,
            {
                "request_id": "r2",
                "data": {
                    "actions": [
                        {"action": "click", "selector": "a"},
                        {"action": "done", "reason": "ok"},
                    ],
                    "allow_origins": ["https://example.com"],
                },
            },
        )
        self.assertEqual(page.clicked, ["a"])
        self.assertTrue(client.sent[-1][1]["ok"])

    def test_recv_thread_queues_action_and_cancel_is_immediate(self):
        from cloakcli_worker.teach_hub import TeachHubClient

        client = TeachHubClient("127.0.0.1", 1, "p", "c")
        started = threading.Event()
        finished = threading.Event()

        def exec_loop() -> None:
            env = client._action_queue.get(timeout=2)
            started.set()
            while not client.cancel_requested():
                time.sleep(0.01)
            finished.set()
            _ = env

        t = threading.Thread(target=exec_loop)
        t.start()
        t0 = time.monotonic()
        client._handle(
            {
                "type": "action_request",
                "request_id": "r1",
                "data": {"actions": [{"action": "wait", "ms": 5000}]},
            }
        )
        self.assertTrue(started.wait(2.0))
        client._handle({"type": "cancel", "request_id": "r1", "data": {}})
        self.assertTrue(client.cancel_requested())
        self.assertTrue(finished.wait(2.0))
        t.join(1.0)
        self.assertLess(time.monotonic() - t0, 1.5)
        # Recv thread must not invoke Playwright/on_action_request.
        self.assertIsNone(client.on_action_request)


    def test_takeover_start_pauses_and_stop_normalizes(self):
        from cloakcli_worker.teach import _handle_takeover_stop
        from cloakcli_worker.teach_hub import TeachHubClient

        client = TeachHubClient("127.0.0.1", 1, "p", "c")
        client._handle({"type": "takeover_start", "data": {"reason": "test"}})
        self.assertTrue(client.executor_paused)
        self.assertTrue(client.takeover_active)
        sent = []

        def capture(typ, data, request_id=None):
            sent.append((typ, data, request_id))

        client.send = capture  # type: ignore[method-assign]
        client._handle(
            {
                "type": "action_request",
                "request_id": "during",
                "data": {"actions": [{"action": "click", "selector": "a"}]},
            }
        )
        self.assertEqual(sent[0][0], "action_result")
        self.assertEqual(sent[0][1]["error"], "executor_paused")

        class _Client:
            def __init__(self):
                self.sent = []
                self.executor_paused = True

            def send(self, typ, data, request_id=None):
                self.sent.append((typ, data, request_id))

        page = FakePage()
        page.elements["#go"] = {"text": "Go"}
        page.selector_counts['[data-testid="go"]'] = 1
        page.elements['[data-testid="go"]'] = {"text": "Go"}
        page.elements["#pw"] = {"text": ""}
        page.selector_counts["#pw"] = 1
        c2 = _Client()
        _handle_takeover_stop(
            page,
            c2,
            {
                "request_id": "t1",
                "type": "takeover_stop",
                "data": {
                    "events": [
                        {
                            "kind": "click",
                            "selector_candidates": {"testid": '[data-testid="go"]'},
                            "candidate_unique": {"testid": True},
                            "tag": "button",
                            "frame": "main",
                        },
                        {
                            "kind": "input",
                            "selector": "#pw",
                            "selector_candidates": {"id": "#pw"},
                            "candidate_unique": {"css": True},
                            "value": "hunter2-secret",
                            "field": {"type": "password", "name": "password"},
                            "redacted": True,
                        },
                    ]
                },
            },
        )
        self.assertEqual(c2.sent[0][0], "normalize_result")
        payload = c2.sent[0][1]
        self.assertTrue(payload["ok"])
        steps = payload["steps"]
        self.assertTrue(all(s.get("source") == "human" for s in steps))
        self.assertTrue(all("action" in s for s in steps))
        self.assertFalse(any(s.get("kind") for s in payload.get("exportable_steps") or steps))
        blob = str(payload)
        self.assertNotIn("hunter2-secret", blob)
        self.assertTrue(any(s.get("action") == "fill" and "{{vars." in str(s.get("text")) for s in steps))

        client._handle({"type": "resume", "data": {}})
        self.assertFalse(client.executor_paused)


if __name__ == "__main__":
    unittest.main()
