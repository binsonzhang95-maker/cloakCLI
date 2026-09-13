"""Teach Chat M4: skill draft export from merged Playwright steps."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cloakcli_worker.timeline import build_skill_draft, merge_timelines
from cloakcli_worker.normalize import is_raw_dom_event
from cloakcli_worker.runner import execute_skill
from fakes import FakePage

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = ROOT / "fixtures" / "teach" / "expected-skill.json"


class TeachExportTests(unittest.TestCase):
    def test_export_matches_expected_skill_fixture(self):
        expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
        steps = [
            {
                "action": "goto",
                "url": "https://example.com/login?token=leakme&next=/app",
                "source": "llm",
            },
            {
                "action": "fill",
                "selector": "#user",
                "text": "alice",
                "field_name": "username",
                "selectors": ["#user", 'input[name="username"]'],
                "source": "human",
            },
            {
                "action": "fill",
                "selector": "#pass",
                "text": "hunter2",
                "field_name": "password",
                "selectors": ["#pass", 'input[name="password"]', 'input[type="password"]'],
                "source": "human",
            },
            {"action": "click", "selector": "button.submit", "source": "human"},
            {"action": "done", "reason": "ok", "source": "llm"},
        ]
        draft = build_skill_draft(expected["name"], expected["goal"], steps)
        self.assertEqual(draft["name"], expected["name"])
        self.assertEqual(draft["goal"], expected["goal"])
        self.assertEqual(draft["params"], expected["params"])
        for got, exp in zip(draft["steps"], expected["steps"]):
            self.assertEqual(got["action"], exp["action"])
            if "url" in exp:
                self.assertEqual(got["url"], exp["url"])
            if "selector" in exp:
                self.assertEqual(got["selector"], exp["selector"])
            if "text" in exp:
                self.assertEqual(got["text"], exp["text"])
        self.assertEqual(draft["steps"][0]["source"], "agent")
        self.assertEqual(draft["steps"][1]["source"], "human")
        blob = json.dumps(draft)
        self.assertNotIn("hunter2", blob)
        self.assertNotIn("leakme", blob)
        self.assertTrue(all(not is_raw_dom_event(s) for s in draft["steps"]))

    def test_raw_dom_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            build_skill_draft(
                "x",
                None,
                [{"kind": "click", "selector": "#x"}],
            )
        self.assertIn("raw DOM", str(ctx.exception))

    def test_forbidden_action_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            build_skill_draft("x", None, [{"action": "eval", "code": "1"}])
        self.assertIn("forbidden", str(ctx.exception))

    def test_merged_timeline_then_export_runs(self):
        agent = [
            {
                "schema_version": 1,
                "action": "goto",
                "url": "https://example.com/login?next=/app",
                "source": "llm",
            }
        ]
        human = [
            {
                "action": "fill",
                "selector": "#user",
                "text": "alice",
                "source": "human",
            },
            {
                "action": "fill",
                "selector": "#pass",
                "text": "{{vars.PASSWORD}}",
                "field_name": "password",
                "source": "human",
            },
            {"action": "click", "selector": "button.submit", "source": "human"},
        ]
        tl = merge_timelines(agent, human)
        # Timeline redacts non-placeholder fills; export from original steps.
        draft = build_skill_draft("taught-login", "Sign in and open the dashboard", agent + human)
        self.assertTrue(tl.seq_is_monotonic())
        page = FakePage(url="https://example.com/")
        page.elements["#user"] = {"text": ""}
        page.elements["#pass"] = {"text": ""}
        page.elements["button.submit"] = {"text": "Sign in"}
        root = Path(tempfile.mkdtemp(prefix="cloakcli_m4_"))
        artifacts = root / "data" / "artifacts" / "taught-login"
        artifacts.mkdir(parents=True)
        public = {k: v for k, v in draft.items() if k != "_audit"}
        result = execute_skill(
            page=page,
            skill=public,
            skill_name="taught-login",
            variables={"PASSWORD": "from-var"},
            artifacts_dir=artifacts,
            project_root=root,
        )
        self.assertEqual(result["status"], "succeeded")
        dumped = (artifacts / "last_result.json").read_text(encoding="utf-8")
        self.assertNotIn("from-var", dumped)
        self.assertNotIn("hunter2", dumped)


if __name__ == "__main__":
    unittest.main()
