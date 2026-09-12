"""Teach export fixture → existing skill runner mapping (click / fill / goto)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cloakcli_worker.runner import execute_skill
from cloakcli_worker.teach import _require_headed

from fakes import FakePage

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "fixtures" / "teach" / "expected-skill.json"
MANIFEST = ROOT / "extensions" / "teach" / "manifest.json"


class TeachRoundTripTests(unittest.TestCase):
    def test_manifest_has_no_all_urls(self):
        text = MANIFEST.read_text(encoding="utf-8")
        self.assertNotIn("<all_urls>", text)
        data = json.loads(text)
        self.assertEqual(data["manifest_version"], 3)
        self.assertEqual(data["name"], "CloakCLI Teach")
        self.assertNotIn("content_scripts", data)
        hosts = data.get("host_permissions") or []
        self.assertIn("http://127.0.0.1/*", hosts)
        self.assertTrue(all(h != "<all_urls>" for h in hosts))

    def test_headless_hard_error(self):
        with self.assertRaises(SystemExit) as ctx:
            _require_headed(False)
        self.assertIn("headed", str(ctx.exception))
        self.assertIn("headless", str(ctx.exception))

    def test_expected_skill_runs_click_input_nav(self):
        skill = json.loads(FIXTURE.read_text(encoding="utf-8"))
        page = FakePage(url="https://example.com/")
        page.elements["#user"] = {"text": ""}
        page.elements["#pass"] = {"text": ""}
        page.elements["button.submit"] = {"text": "Sign in"}
        root = Path(tempfile.mkdtemp(prefix="cloakcli_teach_rt_"))
        artifacts = root / "data" / "artifacts" / "taught-login"
        artifacts.mkdir(parents=True)
        result = execute_skill(
            page=page,
            skill=skill,
            skill_name="taught-login",
            variables={"PASSWORD": "from-var"},
            artifacts_dir=artifacts,
            project_root=root,
        )
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(page.gotos, ["https://example.com/login?next=/app"])
        self.assertEqual(page.filled[0], ("#user", "alice"))
        self.assertEqual(page.filled[1], ("#pass", "from-var"))
        self.assertIn("button.submit", page.clicked)
        dumped = (artifacts / "last_result.json").read_text(encoding="utf-8")
        self.assertNotIn("from-var", dumped)
        self.assertNotIn("hunter2", dumped)


if __name__ == "__main__":
    unittest.main()
