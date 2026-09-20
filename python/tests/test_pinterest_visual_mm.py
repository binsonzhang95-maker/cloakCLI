"""Product multimodal Pinterest register runner (0.2.0) — parse, LLM discover, dry-run."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "run_pinterest_register_visual_mm.py"


def load_mm():
    spec = importlib.util.spec_from_file_location("run_pinterest_register_visual_mm", RUNNER)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ParseActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mm = load_mm()

    def test_click_selector_and_xy_require_sid(self) -> None:
        a = self.mm.parse_mm_action('{"action":"click","selector":"#email"}')
        self.assertEqual(a["action"], "click")
        self.assertEqual(a["selector"], "#email")
        with self.assertRaises(self.mm.ActionError):
            self.mm.parse_mm_action('{"action":"click","x":10,"y":20}')
        b = self.mm.parse_mm_action(
            '{"action":"click","x":10,"y":20,"screenshot_id":"obs-000"}'
        )
        self.assertEqual(b["screenshot_id"], "obs-000")

    def test_type_placeholder_and_field(self) -> None:
        a = self.mm.parse_mm_action(
            '{"action":"type","selector":"#email","text":"{{EMAIL}}"}'
        )
        self.assertEqual(a["text"], "{{EMAIL}}")
        b = self.mm.parse_mm_action('{"action":"type","field":"password"}')
        self.assertEqual(b["field"], "password")
        fill = self.mm.parse_mm_action(
            '{"action":"fill","selector":"#x","text":"{{BIRTHDAY}}"}'
        )
        self.assertEqual(fill["action"], "type")

    def test_forbidden_and_unknown(self) -> None:
        with self.assertRaises(self.mm.ActionError):
            self.mm.parse_mm_action('{"action":"shell","text":"rm -rf /"}')
        with self.assertRaises(self.mm.ActionError):
            self.mm.parse_mm_action('{"action":"goto","url":"https://evil.example"}')
        with self.assertRaises(self.mm.ActionError):
            self.mm.parse_mm_action("not json")

    def test_imap_nurture_done_fail(self) -> None:
        self.assertEqual(self.mm.parse_mm_action('{"action":"imap_fetch_code"}')["action"], "imap_fetch_code")
        self.assertEqual(self.mm.parse_mm_action('{"action":"nurture"}')["action"], "nurture")
        d = self.mm.parse_mm_action(
            '{"action":"done","status":"registered_ok","path":"code_ui"}'
        )
        self.assertEqual(d["status"], "registered_ok")
        f = self.mm.parse_mm_action(
            '{"action":"fail","status":"oops_blocked","reason":"Oops"}'
        )
        self.assertEqual(f["status"], "oops_blocked")
        with self.assertRaises(self.mm.ActionError):
            self.mm.parse_mm_action('{"action":"fail","status":"nope"}')

    def test_press_and_scroll_bounds(self) -> None:
        p = self.mm.parse_mm_action('{"action":"press","key":"Escape"}')
        self.assertEqual(p["key"], "Escape")
        s = self.mm.parse_mm_action('{"action":"scroll","delta_y":200}')
        self.assertEqual(s["delta_y"], 200)
        with self.assertRaises(self.mm.ActionError):
            self.mm.parse_mm_action('{"action":"scroll","delta_y":9999}')

    def test_substitute_and_redact(self) -> None:
        secrets = {
            "EMAIL": "user@example.com",
            "PASSWORD": "super-secret-pass",
            "BIRTHDAY": "1995-04-12",
            "DISPLAY_NAME": "Nora",
            "CODE": "654321",
        }
        a = self.mm.parse_mm_action(
            '{"action":"type","selector":"#email","text":"{{EMAIL}}"}'
        )
        bound = self.mm.substitute_secrets(a, secrets)
        self.assertEqual(bound["text"], "user@example.com")
        pub = self.mm.public_action(bound, extra_secrets=[secrets["PASSWORD"], secrets["EMAIL"]])
        self.assertEqual(pub["text"], "[REDACTED]")
        blob = json.dumps(pub)
        self.assertNotIn("user@example.com", blob)
        self.assertNotIn("super-secret-pass", blob)
        f = self.mm.parse_mm_action('{"action":"type","field":"code"}')
        self.assertEqual(self.mm.substitute_secrets(f, secrets)["text"], "654321")

    def test_fail_status_heuristic(self) -> None:
        self.assertEqual(
            self.mm.map_fail_status({"reason": "full page Oops"}, "oops"),
            "oops_blocked",
        )
        self.assertEqual(
            self.mm.map_fail_status({"reason": "stuck"}, "unknown"),
            "visual_stuck",
        )
        self.assertEqual(self.mm.map_done_status({}), "registered_ok")


class LlmDiscoverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mm = load_mm()

    def test_discover_from_config_no_key_leak(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="cloakcli_visual_llm_"))
        (root / "config").mkdir()
        (root / "config" / "llm.json").write_text(
            json.dumps(
                {
                    "enabled": True,
                    "base_url": "https://api.x.ai/v1",
                    "model": "grok-2-vision-1212",
                    "api_key_env": "CLOAKCLI_LLM_API_KEY",
                }
            ),
            encoding="utf-8",
        )
        old = os.environ.get("CLOAKCLI_LLM_API_KEY")
        os.environ["CLOAKCLI_LLM_API_KEY"] = "xai-secret-should-not-leak"
        try:
            disc = self.mm.discover_llm(root)
        finally:
            if old is None:
                os.environ.pop("CLOAKCLI_LLM_API_KEY", None)
            else:
                os.environ["CLOAKCLI_LLM_API_KEY"] = old
        self.assertEqual(disc["base_url"], "https://api.x.ai/v1")
        self.assertEqual(disc["model"], "grok-2-vision-1212")
        self.assertTrue(disc["key_present"])
        self.assertTrue(disc["grok_compat"])
        blob = json.dumps(disc)
        self.assertNotIn("xai-secret-should-not-leak", blob)
        self.assertEqual(disc["grok_docs"]["base_url"], "https://api.x.ai/v1")
        self.assertIn("chat/completions", disc["chat_completions"])

    def test_env_overlay_and_xai_fallback(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="cloakcli_visual_llm2_"))
        os.environ["CLOAKCLI_LLM_BASE_URL"] = "https://api.openai.com/v1/"
        os.environ["CLOAKCLI_LLM_MODEL"] = "gpt-4o"
        old_xai = os.environ.pop("XAI_API_KEY", None)
        old_def = os.environ.pop("CLOAKCLI_LLM_API_KEY", None)
        old_oa = os.environ.pop("OPENAI_API_KEY", None)
        os.environ["XAI_API_KEY"] = "xai-fallback-key"
        try:
            disc = self.mm.discover_llm(root)
            self.assertEqual(disc["base_url"], "https://api.openai.com/v1")
            self.assertEqual(disc["model"], "gpt-4o")
            self.assertTrue(disc["key_present"])
            self.assertEqual(disc["key_source"], "env:XAI_API_KEY")
        finally:
            os.environ.pop("CLOAKCLI_LLM_BASE_URL", None)
            os.environ.pop("CLOAKCLI_LLM_MODEL", None)
            os.environ.pop("XAI_API_KEY", None)
            if old_xai is not None:
                os.environ["XAI_API_KEY"] = old_xai
            if old_def is not None:
                os.environ["CLOAKCLI_LLM_API_KEY"] = old_def
            if old_oa is not None:
                os.environ["OPENAI_API_KEY"] = old_oa


class DryRunSmokeTests(unittest.TestCase):
    def test_dry_run_subprocess_report(self) -> None:
        env = os.environ.copy()
        env["CLOAKCLI_ROOT"] = str(ROOT)
        env["PYTHONPATH"] = str(ROOT / "python") + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.run(
            [sys.executable, str(RUNNER), "--dry-run", "--profile", "geo02"],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            env=env,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr[-2000:] + proc.stdout[-2000:])
        lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        self.assertTrue(lines)
        report = json.loads(lines[-1])
        self.assertEqual(report["skill_id"], "pinterest-register-visual")
        self.assertEqual(report["version"], "0.2.0")
        self.assertEqual(report["status"], "registered_ok")
        self.assertEqual(report["nurture_status"], "browsed_ok")
        self.assertTrue(report.get("dry_run"))
        self.assertNotIn("dry-run-password", proc.stdout)
        self.assertNotIn("dry-run-password", proc.stderr)

    def test_refuse_api_key_argv(self) -> None:
        env = os.environ.copy()
        env["CLOAKCLI_ROOT"] = str(ROOT)
        proc = subprocess.run(
            [sys.executable, str(RUNNER), "--api-key", "sk-leak-me", "--dry-run"],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            env=env,
            timeout=15,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("sk-leak-me", proc.stdout)
        self.assertNotIn("sk-leak-me", proc.stderr)
        report = json.loads([ln for ln in proc.stdout.splitlines() if ln.strip()][-1])
        self.assertEqual(report["status"], "visual_stuck")

    def test_skill_manifest_python_runner_0_2_0(self) -> None:
        man = json.loads(
            (ROOT / "skills/pinterest-register-visual/manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(man["version"], "0.2.0")
        self.assertEqual(man["entry"]["kind"], "python_runner")
        self.assertEqual(man["entry"]["path"], "scripts/run_pinterest_register_visual_mm.py")
        ids = {s["id"] for s in man["statuses"]}
        self.assertEqual(
            ids,
            {
                "registered_ok",
                "browsed_ok",
                "oops_blocked",
                "verify_soft_fail",
                "account_deactivated",
                "not_logged_in",
                "visual_stuck",
            },
        )
        pkg_runner = ROOT / "skills/pinterest-register-visual/scripts/run_pinterest_register_visual_mm.py"
        self.assertTrue(pkg_runner.is_file())
        self.assertTrue((ROOT / "scripts/run_pinterest_register_visual_mm.py").is_file())


class ProviderMimeTests(unittest.TestCase):
    def test_jpeg_mime_normalized(self) -> None:
        from cloakcli_worker.recover.provider import _normalize_image_mime

        self.assertEqual(_normalize_image_mime("image/jpg"), "image/jpeg")
        self.assertEqual(_normalize_image_mime("image/jpeg"), "image/jpeg")
        self.assertEqual(_normalize_image_mime("nope"), "image/png")


if __name__ == "__main__":
    unittest.main()
