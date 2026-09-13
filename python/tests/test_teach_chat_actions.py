"""Teach Chat M2: unified action schema, execute whitelist, high-risk nav, cancel."""

from __future__ import annotations

import threading
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

    def test_goto_allowlist_only(self):
        page = FakePage()
        a = validate_action({"action": "goto", "url": "https://evil.example/x"})
        out = execute_action(page, a, allow_origins=["https://example.com"])
        self.assertEqual(out.status, "rejected")
        self.assertIn("allowlist", out.reason)
        self.assertEqual(page.gotos, [])

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

    def test_high_risk_cross_origin_needs_confirm(self):
        page = FakePage(url="https://example.com/")
        a = validate_action({"action": "goto", "url": "https://other.example/login"})
        risk, reason = goto_risk(
            a.url or "",
            allow_origins=["https://example.com", "https://other.example"],
            current_origin="https://example.com",
        )
        self.assertEqual(risk, "needs_confirm")
        self.assertIn("cross-origin", reason)
        out = execute_action(
            page,
            a,
            allow_origins=["https://example.com", "https://other.example"],
            current_origin="https://example.com",
            confirmed=False,
        )
        self.assertEqual(out.status, "needs_confirm")
        self.assertEqual(page.gotos, [])
        out2 = execute_action(
            page,
            a,
            allow_origins=["https://example.com", "https://other.example"],
            current_origin="https://example.com",
            confirmed=True,
        )
        self.assertEqual(out2.status, "ok")
        self.assertEqual(page.gotos, ["https://other.example/login"])

    def test_cancel_stops_in_flight_wait(self):
        page = FakePage()
        cancelled = threading.Event()
        cancelled.set()
        a = validate_action({"action": "wait", "ms": 5000})
        out = execute_action(page, a, cancel_check=cancelled.is_set)
        self.assertEqual(out.status, "cancelled")

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


if __name__ == "__main__":
    unittest.main()
