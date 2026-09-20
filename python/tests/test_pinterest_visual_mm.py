"""Product multimodal Pinterest register runner (0.2.4) — parse, LLM discover, dry-run."""
from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "run_pinterest_register_visual_mm.py"

_LLM_ENV = (
    "CLOAKCLI_LLM_BASE_URL",
    "CLOAKCLI_LLM_MODEL",
    "CLOAKCLI_LLM_VISION_MODEL",
)


class IsolatedLlmEnv:
    """Clear overlay env so discovery tests do not depend on the host shell."""

    def setUp(self) -> None:
        super().setUp()
        self._llm_env = {k: os.environ.get(k) for k in _LLM_ENV}
        for k in _LLM_ENV:
            os.environ.pop(k, None)

    def tearDown(self) -> None:
        for k, v in self._llm_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()


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
        code_bound = self.mm.substitute_secrets(f, secrets)
        self.assertEqual(code_bound["text"], "654321")
        self.assertEqual(code_bound["selector"], "#code")
        leaked = self.mm.redact_result_strings(
            {"reason": "typed user@example.com / super-secret-pass"},
            extra_secrets=[secrets["PASSWORD"], secrets["EMAIL"]],
        )
        leaked_blob = json.dumps(leaked)
        self.assertNotIn("user@example.com", leaked_blob)
        self.assertNotIn("super-secret-pass", leaked_blob)

    def test_fail_status_heuristic(self) -> None:
        self.assertEqual(
            self.mm.map_fail_status({"reason": "full page Oops"}, "oops"),
            "oops_blocked",
        )
        self.assertEqual(
            self.mm.map_fail_status({"reason": "stuck"}, "unknown"),
            "visual_stuck",
        )
        self.assertEqual(self.mm.map_done_status({}), "visual_stuck")
        self.assertEqual(self.mm.map_done_status({"status": "invented"}), "visual_stuck")
        self.assertEqual(self.mm.map_done_status({"status": "browsed_ok"}), "browsed_ok")
        self.assertEqual(self.mm.map_fail_status({"status": "registered_ok"}), "visual_stuck")

    def test_status_catalog_loaded_from_manifest_file(self) -> None:
        mm = self.mm
        tmp = Path(tempfile.mkdtemp(prefix="cloakcli_visual_man_")) / "manifest.json"
        tmp.write_text(
            json.dumps(
                {
                    "version": "9.9.9",
                    "statuses": [
                        {
                            "id": "alpha_ok",
                            "success": True,
                            "retryable": False,
                            "label": "Alpha",
                            "exit": 0,
                        },
                        {
                            "id": "visual_stuck",
                            "success": False,
                            "retryable": True,
                            "label": "Stuck",
                            "exit": 4,
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        cat = mm.load_skill_status_catalog(tmp)
        self.assertEqual(cat["version"], "9.9.9")
        self.assertEqual(cat["success"], frozenset({"alpha_ok"}))
        self.assertEqual(cat["allowed"], frozenset({"alpha_ok", "visual_stuck"}))
        self.assertNotIn("registered_ok", cat["success"])
        self.assertNotIn("browsed_ok", cat["allowed"])
        self.assertEqual(cat["exits"]["alpha_ok"], 0)
        missing = Path(tempfile.mkdtemp(prefix="cloakcli_visual_noman_")) / "nope.json"
        safe = mm.load_skill_status_catalog(missing)
        self.assertEqual(safe["success"], frozenset())
        self.assertIn("visual_stuck", safe["allowed"])


class LlmDiscoverTests(IsolatedLlmEnv, unittest.TestCase):
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
            os.environ.pop("CLOAKCLI_LLM_VISION_MODEL", None)
            os.environ.pop("XAI_API_KEY", None)
            if old_xai is not None:
                os.environ["XAI_API_KEY"] = old_xai
            if old_def is not None:
                os.environ["CLOAKCLI_LLM_API_KEY"] = old_def
            if old_oa is not None:
                os.environ["OPENAI_API_KEY"] = old_oa

    def test_default_model_grok_46_when_unset(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="cloakcli_visual_llm_default_"))
        (root / "config").mkdir()
        (root / "config" / "llm.json").write_text(
            json.dumps(
                {
                    "enabled": True,
                    "base_url": "https://api.x.ai/v1",
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
        self.assertEqual(disc["model"], "grok-4.6")
        self.assertEqual(disc["text_model"], "grok-4.6")
        self.assertEqual(disc["vision_model"], "grok-4.6")
        self.assertTrue(disc["base_url"])
        self.assertTrue(disc["key_present"])
        self.assertNotIn("xai-secret-should-not-leak", json.dumps(disc))

    def test_vision_model_prefers_env_then_file_then_text(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="cloakcli_visual_llm_vision_"))
        (root / "config").mkdir()
        cfg_path = root / "config" / "llm.json"
        cfg_path.write_text(
            json.dumps(
                {
                    "enabled": True,
                    "base_url": "https://api.x.ai/v1",
                    "model": "text-model",
                    "vision_model": "file-vision",
                    "api_key_env": "CLOAKCLI_LLM_API_KEY",
                }
            ),
            encoding="utf-8",
        )
        os.environ["CLOAKCLI_LLM_API_KEY"] = "xai-secret-should-not-leak"
        try:
            disc = self.mm.discover_llm(root)
            self.assertEqual(disc["text_model"], "text-model")
            self.assertEqual(disc["model"], "file-vision")
            self.assertEqual(disc["vision_model"], "file-vision")
            os.environ["CLOAKCLI_LLM_VISION_MODEL"] = "env-vision"
            disc = self.mm.discover_llm(root)
            self.assertEqual(disc["model"], "env-vision")
            self.assertEqual(disc["vision_source"], "env:CLOAKCLI_LLM_VISION_MODEL")
            os.environ.pop("CLOAKCLI_LLM_VISION_MODEL", None)
            os.environ["CLOAKCLI_LLM_MODEL"] = "env-text"
            disc = self.mm.discover_llm(root)
            self.assertEqual(disc["text_model"], "env-text")
            self.assertEqual(disc["model"], "file-vision")
            disc = self.mm.discover_llm(root, model_cli="cli-vision")
            self.assertEqual(disc["model"], "cli-vision")
            cfg_path.write_text(
                json.dumps(
                    {
                        "enabled": True,
                        "base_url": "https://api.x.ai/v1",
                        "model": "text-model",
                        "api_key_env": "CLOAKCLI_LLM_API_KEY",
                    }
                ),
                encoding="utf-8",
            )
            disc = self.mm.discover_llm(root)
            self.assertEqual(disc["model"], "env-text")
        finally:
            os.environ.pop("CLOAKCLI_LLM_API_KEY", None)
            os.environ.pop("CLOAKCLI_LLM_MODEL", None)
            os.environ.pop("CLOAKCLI_LLM_VISION_MODEL", None)


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
        self.assertEqual(report["version"], "0.2.4")
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

    def test_skill_manifest_python_runner_0_2_4(self) -> None:
        man = json.loads(
            (ROOT / "skills/pinterest-register-visual/manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(man["version"], "0.2.4")
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
        mm = load_mm()
        success = {s["id"] for s in man["statuses"] if s.get("success")}
        self.assertEqual(mm.ALLOWED_STATUSES, ids)
        self.assertEqual(mm.SUCCESS_STATUSES, success)
        self.assertEqual(mm.VERSION, man["version"])
        self.assertIn("registered_ok", mm.SUCCESS_STATUSES)
        self.assertIn("browsed_ok", mm.SUCCESS_STATUSES)
        self.assertEqual(mm.process_exit_code("registered_ok"), 0)
        self.assertEqual(mm.process_exit_code("browsed_ok"), 0)
        self.assertEqual(mm.process_exit_code("oops_blocked"), 2)
        self.assertEqual(mm.process_exit_code("verify_soft_fail"), 5)
        self.assertEqual(mm.process_exit_code("account_deactivated"), 7)
        self.assertEqual(mm.process_exit_code("not_logged_in"), 8)
        self.assertEqual(mm.process_exit_code("visual_stuck"), 4)
        self.assertEqual(mm.process_exit_code("invented"), 4)


class ProviderMimeTests(unittest.TestCase):
    def test_jpeg_mime_normalized(self) -> None:
        from cloakcli_worker.recover.provider import _normalize_image_mime

        self.assertEqual(_normalize_image_mime("image/jpg"), "image/jpeg")
        self.assertEqual(_normalize_image_mime("image/jpeg"), "image/jpeg")
        self.assertEqual(_normalize_image_mime("nope"), "image/png")


class _GatePage:
    def __init__(
        self,
        body: str = "",
        url: str = "https://www.pinterest.com/",
        counts: dict[str, int] | None = None,
        code_visible: bool = False,
    ) -> None:
        self._body = body
        self.url = url
        self.counts = counts or {}
        self.code_visible = code_visible
        self.viewport_size = {"width": 1280, "height": 720}

    def inner_text(self, sel: str = "body") -> str:
        return self._body

    def locator(self, sel: str) -> "_GateLoc":
        return _GateLoc(self, sel)


class _GateLoc:
    def __init__(self, page: _GatePage, sel: str) -> None:
        self.page = page
        self.sel = sel

    @property
    def first(self) -> "_GateLoc":
        return self

    def count(self) -> int:
        if self.sel in self.page.counts:
            return int(self.page.counts[self.sel])
        if self.sel == "#code":
            return 1 if self.page.code_visible else 0
        return 0

    def is_visible(self, timeout: int = 0) -> bool:
        if self.sel == "#code":
            return self.page.code_visible
        return self.count() > 0


def _loop_args(**over: object) -> argparse.Namespace:
    ns = argparse.Namespace(
        profile="geo02",
        max_steps=12,
        timeout_sec=20.0,
        skip_nurture=False,
        nurture_pins=1,
        nurture_min_sec=1,
        nurture_max_sec=1,
        digest="",
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def _subst() -> dict[str, str]:
    return {
        "EMAIL": "user@example.com",
        "PASSWORD": "super-secret-pass",
        "BIRTHDAY": "1995-04-12",
        "DISPLAY_NAME": "Nora",
        "CODE": "",
    }


class LoginGateAndActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mm = load_mm()

    def test_register_success_threshold_selectors(self) -> None:
        mm = self.mm
        unauth = mm.UNAUTH_SELS
        acct = mm.ACCT_SELS
        pins = mm.PIN_LINK
        logged = mm.page_login_gate(
            _GatePage(
                body="Search Pinterest header-accounts",
                url="https://www.pinterest.com/homefeed/",
                counts={acct: 1, unauth: 0, pins: 4},
            )
        )
        self.assertTrue(logged["ok"])
        self.assertEqual(logged["reason"], "account_menu")
        feed = mm.page_login_gate(
            _GatePage(
                body="Search Pinterest",
                url="https://www.pinterest.com/homefeed/",
                counts={acct: 0, unauth: 0, pins: 4},
            )
        )
        self.assertTrue(feed["ok"])
        self.assertEqual(feed["reason"], "pin_feed")
        no_cta = mm.page_login_gate(
            _GatePage(
                body="Search Pinterest",
                url="https://www.pinterest.com/today/",
                counts={acct: 0, unauth: 0, pins: 0},
            )
        )
        self.assertTrue(no_cta["ok"])
        self.assertEqual(no_cta["reason"], "feed_no_unauth_cta")
        cta = mm.page_login_gate(
            _GatePage(
                body="Log in  Sign up  Welcome to Pinterest",
                url="https://www.pinterest.com/",
                counts={acct: 0, unauth: 1, pins: 0},
            )
        )
        self.assertFalse(cta["ok"])
        self.assertEqual(cta["gate"], "not_logged_in")
        signup = mm.page_login_gate(
            _GatePage(
                body="Create your account Continue",
                url="https://www.pinterest.com/signup/",
                counts={acct: 0, unauth: 1, pins: 0},
            )
        )
        self.assertFalse(signup["ok"])
        code = mm.page_login_gate(
            _GatePage(
                body="Enter the code we sent you",
                url="https://www.pinterest.com/signup/",
                code_visible=True,
            )
        )
        self.assertFalse(code["ok"])
        self.assertEqual(code["gate"], "code_ui")

    def test_execute_browser_actions_on_mock_page(self) -> None:
        page = self.mm.DryRunPage()
        click = self.mm.execute_browser_action(
            page, {"action": "click", "selector": "#email"}, screenshot_id="obs-000"
        )
        self.assertTrue(click["ok"])
        self.assertEqual(page.clicked, ["#email"])
        xy = self.mm.execute_browser_action(
            page,
            {"action": "click", "x": 40, "y": 80, "screenshot_id": "obs-000"},
            screenshot_id="obs-000",
        )
        self.assertTrue(xy["ok"])
        self.assertEqual(page.coord_clicks, [(40, 80)])
        stale = self.mm.execute_browser_action(
            page,
            {"action": "click", "x": 1, "y": 1, "screenshot_id": "old"},
            screenshot_id="obs-000",
        )
        self.assertFalse(stale["ok"])
        typed = self.mm.execute_browser_action(
            page,
            {"action": "type", "selector": "#email", "text": "user@example.com"},
            screenshot_id="obs-000",
        )
        self.assertTrue(typed["ok"])
        self.assertTrue(page.typed or page.filled)
        press = self.mm.execute_browser_action(
            page, {"action": "press", "key": "Tab"}, screenshot_id="obs-000"
        )
        self.assertTrue(press["ok"])
        self.assertEqual(page.pressed, ["Tab"])
        scroll = self.mm.execute_browser_action(
            page, {"action": "scroll", "delta_x": 0, "delta_y": 200}, screenshot_id="obs-000"
        )
        self.assertTrue(scroll["ok"])
        self.assertEqual(page.scrolls, [(0, 200)])
        wait = self.mm.execute_browser_action(
            page, {"action": "wait", "ms": 50}, screenshot_id="obs-000"
        )
        self.assertTrue(wait["ok"])

    def test_provider_complete_mocked_http(self) -> None:
        from cloakcli_worker.llm_config import parse_llm_config
        from cloakcli_worker.recover.provider import OpenAICompatProvider

        class _Resp:
            def read(self) -> bytes:
                return json.dumps(
                    {
                        "choices": [{"message": {"content": '{"action":"wait","ms":200}'}}],
                        "usage": {"total_tokens": 11},
                    }
                ).encode()

            def __enter__(self) -> "_Resp":
                return self

            def __exit__(self, *a: object) -> bool:
                return False

        cfg = parse_llm_config(
            {
                "enabled": True,
                "base_url": "https://api.x.ai/v1",
                "model": "grok-4.6",
                "api_key_env": "CLOAKCLI_LLM_API_KEY",
            }
        )
        old = os.environ.get("CLOAKCLI_LLM_API_KEY")
        os.environ["CLOAKCLI_LLM_API_KEY"] = "xai-secret-should-not-leak"
        try:
            with mock.patch(
                "cloakcli_worker.recover.provider.urllib.request.urlopen",
                return_value=_Resp(),
            ) as urlopen:
                text, tokens = OpenAICompatProvider().complete(
                    cfg,
                    [{"role": "user", "content": "observe"}],
                    image_b64="aaa",
                    image_mime="image/jpeg",
                )
        finally:
            if old is None:
                os.environ.pop("CLOAKCLI_LLM_API_KEY", None)
            else:
                os.environ["CLOAKCLI_LLM_API_KEY"] = old
        self.assertIn("wait", text)
        self.assertEqual(tokens, 11)
        req = urlopen.call_args[0][0]
        self.assertIn("chat/completions", req.full_url)
        header_names = {str(k).lower() for k in req.headers}
        self.assertIn("user-agent", header_names)
        self.assertNotIn("xai-secret-should-not-leak", text)

    def _run_loop(self, page, vision, ud: Path, *, dry_run: bool, **args_over: object):
        mm = self.mm
        disc = {
            "base_url": "https://api.x.ai/v1",
            "model": "grok-4.6",
            "api_key_env": "CLOAKCLI_LLM_API_KEY",
        }
        return mm.run_loop(
            page=page,
            ctx=None,
            ud=ud,
            args=_loop_args(**args_over),
            secrets=_subst(),
            secrets_path=str(ROOT / "data/secrets/pinterest-outlook-01.env"),
            disc=disc,
            extra_secrets=["super-secret-pass", "user@example.com"],
            dry_run=dry_run,
            vision=vision,
            api_key="",
            root=ROOT,
        )

    def test_model_registered_ok_without_login_does_not_write_session_ok(self) -> None:
        mm = self.mm
        ud = Path(tempfile.mkdtemp(prefix="cloakcli_visual_ud_gate_"))
        page = mm.DryRunPage()
        vision = mm.MockVision(
            [
                {
                    "schema_version": 1,
                    "action": "done",
                    "status": "registered_ok",
                    "reason": "model guess",
                }
            ]
        )
        report = self._run_loop(page, vision, ud, dry_run=True)
        self.assertNotEqual(report["status"], "registered_ok")
        self.assertIn(report["status"], ("not_logged_in", "visual_stuck"))
        self.assertFalse((ud / ".cloak_session_ok").exists())
        self.assertEqual(report.get("nurture_status"), "skipped")

    def test_finish_result_json_redacts_model_reason_secrets(self) -> None:
        """Model done/fail reason with email/password must not appear in result JSON."""
        mm = self.mm
        email = "user@example.com"
        password = "super-secret-pass"
        leak = f"could not type {email} password={password}"

        ud_fail = Path(tempfile.mkdtemp(prefix="cloakcli_visual_ud_reason_fail_"))
        fail_report = self._run_loop(
            mm.DryRunPage(),
            mm.MockVision(
                [
                    {
                        "schema_version": 1,
                        "action": "fail",
                        "status": "visual_stuck",
                        "reason": leak,
                        "path": f"form {email}",
                    }
                ]
            ),
            ud_fail,
            dry_run=True,
        )
        fail_blob = json.dumps(fail_report)
        self.assertNotIn(email, fail_blob)
        self.assertNotIn(password, fail_blob)
        self.assertIn("reason", fail_report)
        self.assertIn("***", str(fail_report.get("reason")))
        self.assertNotIn(email, str(fail_report.get("path", "")))

        ud_done = Path(tempfile.mkdtemp(prefix="cloakcli_visual_ud_reason_done_"))
        done_report = self._run_loop(
            mm.DryRunPage(persist_logged_in=True),
            mm.MockVision(
                [
                    {
                        "schema_version": 1,
                        "action": "done",
                        "status": "registered_ok",
                        "reason": leak,
                        "path": f"signed in {email}",
                    }
                ]
            ),
            ud_done,
            dry_run=True,
        )
        done_blob = json.dumps(done_report)
        self.assertNotIn(email, done_blob)
        self.assertNotIn(password, done_blob)
        self.assertEqual(done_report["status"], "registered_ok")
        self.assertIn("***", str(done_report.get("reason")))
        self.assertNotIn(email, str(done_report.get("path", "")))

    def test_model_browsed_ok_on_register_page_is_rejected(self) -> None:
        """done:browsed_ok on signup/login must not fake success or write .cloak_session_ok."""
        mm = self.mm
        ud = Path(tempfile.mkdtemp(prefix="cloakcli_visual_ud_browse_"))
        page = mm.DryRunPage()
        vision = mm.MockVision(
            [
                {
                    "schema_version": 1,
                    "action": "done",
                    "status": "browsed_ok",
                    "reason": "model guess on register form",
                }
            ]
        )
        report = self._run_loop(page, vision, ud, dry_run=True)
        self.assertNotEqual(report["status"], "browsed_ok")
        self.assertNotEqual(report["status"], "registered_ok")
        self.assertFalse(mm.is_success_status(report["status"]))
        self.assertIn(report["status"], ("not_logged_in", "visual_stuck"))
        self.assertFalse((ud / ".cloak_session_ok").exists())
        self.assertEqual(report.get("nurture_status"), "skipped")

    def test_model_browsed_ok_with_login_is_success(self) -> None:
        mm = self.mm
        ud = Path(tempfile.mkdtemp(prefix="cloakcli_visual_ud_browse_ok_"))
        page = mm.DryRunPage(persist_logged_in=True)
        vision = mm.MockVision(
            [
                {
                    "schema_version": 1,
                    "action": "done",
                    "status": "browsed_ok",
                    "path": "already_logged_in",
                }
            ]
        )
        report = self._run_loop(page, vision, ud, dry_run=True)
        self.assertTrue(mm.is_success_status(report["status"]))
        self.assertIn(report["status"], ("browsed_ok", "registered_ok"))
        self.assertTrue((ud / ".cloak_session_ok").exists())

    def test_nurture_action_does_not_set_registered_without_login(self) -> None:
        mm = self.mm
        ud = Path(tempfile.mkdtemp(prefix="cloakcli_visual_ud_nurture_"))
        page = mm.DryRunPage()
        vision = mm.MockVision(
            [{"schema_version": 1, "action": "nurture"} for _ in range(6)]
        )
        report = self._run_loop(page, vision, ud, dry_run=True, max_steps=8)
        self.assertNotEqual(report["status"], "registered_ok")
        self.assertFalse((ud / ".cloak_session_ok").exists())

    def test_confirmed_login_writes_session_ok(self) -> None:
        mm = self.mm
        ud = Path(tempfile.mkdtemp(prefix="cloakcli_visual_ud_ok_"))
        page = mm.DryRunPage(persist_logged_in=True)
        vision = mm.MockVision(
            [
                {
                    "schema_version": 1,
                    "action": "done",
                    "status": "registered_ok",
                    "path": "already_logged_in",
                }
            ]
        )
        report = self._run_loop(page, vision, ud, dry_run=True)
        self.assertEqual(report["status"], "registered_ok")
        self.assertTrue((ud / ".cloak_session_ok").exists())

    def test_nurture_fail_does_not_negate_register(self) -> None:
        mm = self.mm
        ud = Path(tempfile.mkdtemp(prefix="cloakcli_visual_ud_nf_"))
        page = mm.DryRunPage(persist_logged_in=True)
        vision = mm.MockVision(
            [
                {
                    "schema_version": 1,
                    "action": "done",
                    "status": "registered_ok",
                    "path": "already_logged_in",
                }
            ]
        )

        def _fail_nurture(*_a: object, **_k: object) -> dict:
            return {
                "nurture_status": "like_failed",
                "nurture_elapsed_s": 3,
                "nurture_liked": False,
            }

        with mock.patch.object(mm, "imap_max_uid", return_value=0):
            with mock.patch.object(mm, "chain_nurture", side_effect=_fail_nurture):
                report = self._run_loop(page, vision, ud, dry_run=False)
        self.assertEqual(report["status"], "registered_ok")
        self.assertEqual(report["nurture_status"], "like_failed")
        self.assertTrue((ud / ".cloak_session_ok").exists())

    def test_keep_alive_probe_ok_and_lost(self) -> None:
        mm = self.mm
        ud = Path(tempfile.mkdtemp(prefix="cloakcli_visual_ud_ka_"))
        page = mm.DryRunPage(persist_logged_in=True)

        class _OkMod:
            def session_keepalive_probe(self, _page: object) -> dict:
                return {"ok": True, "gate": "ok", "url": "https://www.pinterest.com/", "probe": "session_keepalive_probe"}

            def run_nurture_session(self, _page: object, **_k: object) -> dict:
                return {"status": "browsed_ok", "elapsed_sec": 2, "liked": True, "pins_opened": 3}

        class _LostMod:
            def session_keepalive_probe(self, _page: object) -> dict:
                return {
                    "ok": False,
                    "gate": "not_logged_in",
                    "url": "https://www.pinterest.com/",
                    "probe": "session_keepalive_probe",
                }

            def run_nurture_session(self, *_a: object, **_k: object) -> dict:
                raise AssertionError("nurture must not run after failed keep-alive")

        with mock.patch.object(mm, "flush_session", return_value={"storage_state": True, "home_nav": True, "wait_ms": 0}):
            with mock.patch.object(mm, "load_nurture_mod", return_value=_OkMod()):
                ok = mm.chain_nurture(
                    page, None, ud, "geo02", ROOT, pins=1, min_sec=1, max_sec=1, dry_run=False
                )
            with mock.patch.object(mm, "load_nurture_mod", return_value=_LostMod()):
                lost = mm.chain_nurture(
                    page, None, ud, "geo02", ROOT, pins=1, min_sec=1, max_sec=1, dry_run=False
                )
        self.assertEqual(ok["nurture_status"], "browsed_ok")
        self.assertTrue(ok["session_keepalive_probe"]["ok"])
        self.assertEqual(lost["nurture_status"], "session_lost_before_nurture")

    def test_chain_nurture_and_imap_stderr_redacted(self) -> None:
        mm = self.mm
        ud = Path(tempfile.mkdtemp(prefix="cloakcli_visual_ud_redact_"))
        page = mm.DryRunPage(persist_logged_in=True)
        secret = "sk-secretTEST99abc"
        extra = [secret, "super-secret-pass"]

        class _BoomMod:
            def session_keepalive_probe(self, _page: object) -> dict:
                raise RuntimeError(
                    f"Authorization: Bearer {secret} password=super-secret-pass"
                )

        buf = io.StringIO()
        with mock.patch.object(mm, "flush_session", return_value={"storage_state": False, "home_nav": False, "wait_ms": 0}):
            with mock.patch.object(mm, "load_nurture_mod", return_value=_BoomMod()):
                with mock.patch.object(mm, "log", side_effect=lambda obj: buf.write(json.dumps(obj) + "\n")):
                    fields = mm.chain_nurture(
                        page,
                        None,
                        ud,
                        "geo02",
                        ROOT,
                        pins=1,
                        min_sec=1,
                        max_sec=1,
                        dry_run=False,
                        extra_secrets=extra,
                    )
        blob = buf.getvalue() + json.dumps(fields)
        self.assertNotIn(secret, blob)
        self.assertNotIn("super-secret-pass", blob)
        self.assertTrue(str(fields.get("nurture_status", "")).startswith("error:"))

        class _Proc:
            returncode = 1
            stderr = f"AUTH failed token={secret} Authorization: Bearer {secret}\n"
            stdout = ""

        err = io.StringIO()
        with mock.patch("subprocess.run", return_value=_Proc()):
            with mock.patch.object(sys, "stderr", err):
                with mock.patch.object(mm, "log", side_effect=lambda obj: err.write(json.dumps(obj) + "\n")):
                    hit = mm.imap_wait_code("secrets.env", 0, 10, ROOT, extra_secrets=extra)
        self.assertIsNone(hit)
        dumped = err.getvalue()
        self.assertNotIn(secret, dumped)


class SignupDeadLoopTests(unittest.TestCase):
    """0.2.3+: field→selector bind, type focuses input, anti-loop, vision retry."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mm = load_mm()

    def test_field_binds_selector(self) -> None:
        secrets = _subst()
        secrets["CODE"] = "654321"
        expected = {
            "email": "#email",
            "password": "#password",
            "birthday": "#birthdate",
            "birthdate": "#birthdate",
            "code": "#code",
        }
        for field, sel in expected.items():
            a = self.mm.parse_mm_action(json.dumps({"action": "type", "field": field}))
            bound = self.mm.bind_type_selector(a)
            self.assertEqual(bound.get("selector"), sel, field)
            sub = self.mm.substitute_secrets(a, secrets)
            self.assertEqual(sub.get("selector"), sel, field)
            self.assertTrue(sub.get("text"))
            self.assertNotIn("field", sub)
        name = self.mm.bind_type_selector(
            self.mm.parse_mm_action('{"action":"type","field":"name"}')
        )
        self.assertIn("name", str(name.get("selector") or "").lower())
        ph = self.mm.bind_type_selector(
            {"action": "type", "text": "{{EMAIL}}"}
        )
        self.assertEqual(ph.get("selector"), "#email")
        icon = self.mm.bind_type_selector(
            {
                "action": "type",
                "field": "birthday",
                "selector": "button.calendar-icon",
                "text": "{{BIRTHDAY}}",
            }
        )
        self.assertEqual(icon.get("selector"), "#birthdate")

    def test_type_without_selector_focuses_email(self) -> None:
        page = self.mm.DryRunPage()
        self.assertIsNone(page.focused)
        result = self.mm.execute_browser_action(
            page,
            {"action": "type", "field": "email", "text": "user@example.com"},
            screenshot_id="obs-000",
        )
        self.assertTrue(result["ok"])
        self.assertIn("#email", page.clicked)
        self.assertEqual(page.focused, "#email")
        self.assertEqual(page.fields.get("#email"), "user@example.com")
        self.assertTrue(page.typed)
        bday = self.mm.execute_browser_action(
            page,
            {"action": "type", "field": "birthday", "text": "1995-04-12"},
            screenshot_id="obs-000",
        )
        self.assertTrue(bday["ok"])
        self.assertIn("fill-date", str(bday.get("detail") or ""))
        self.assertIn(("#birthdate", "1995-04-12"), page.filled)

    def test_type_without_selector_or_focus_is_not_silent_keyboard_ok(self) -> None:
        page = self.mm.DryRunPage()
        missing = self.mm.execute_browser_action(
            page,
            {"action": "type", "text": "hello-unfocused"},
            screenshot_id="obs-000",
        )
        self.assertFalse(missing["ok"])
        self.assertIn("selector", str(missing.get("detail") or "").lower())
        self.assertNotIn("hello-unfocused", page.typed)
        self.assertFalse(page.filled)

        class _DeadFocus:
            def __init__(self) -> None:
                self.typed: list[str] = []
                self.filled: list[tuple[str, str]] = []
                self.keyboard = self

            def click(self, *_a: object, **_k: object) -> None:
                raise RuntimeError("no such element")

            def locator(self, _sel: str) -> object:
                class _Loc:
                    @property
                    def first(self) -> "_Loc":
                        return self

                    def click(self, *_a: object, **_k: object) -> None:
                        raise RuntimeError("no such element")

                return _Loc()

            def type(self, text: str, delay: int = 0) -> None:
                self.typed.append(text)

            def fill(self, sel: str, text: str, timeout: int = 0) -> None:
                self.filled.append((sel, text))

        dead = _DeadFocus()
        failed = self.mm.execute_browser_action(
            dead,
            {"action": "type", "selector": "#email", "text": "user@example.com"},
            screenshot_id="obs-000",
        )
        self.assertFalse(failed["ok"])
        self.assertIn("focus", str(failed.get("detail") or "").lower())
        self.assertNotIn("user@example.com", dead.typed)
        self.assertFalse(dead.filled)

        bday_dead = self.mm.execute_browser_action(
            dead,
            {"action": "type", "field": "birthday", "text": "1995-04-12"},
            screenshot_id="obs-000",
        )
        self.assertFalse(bday_dead["ok"])
        self.assertNotIn("1995-04-12", dead.typed)
        self.assertFalse(dead.filled)

    def test_anti_loop_rejects_duplicate_field_type(self) -> None:
        d = self.mm.signup_type_decision(
            heuristic="signup_form",
            action={"action": "type", "field": "email"},
            filled={"email"},
            redundant_streak=0,
        )
        self.assertTrue(d["skip"])
        self.assertFalse(d["recover_continue"])
        self.assertIn("already filled email", d["feedback"])
        self.assertIn("password", d["feedback"])
        self.assertEqual(d["streak"], 1)
        fresh = self.mm.signup_type_decision(
            heuristic="signup_form",
            action={"action": "type", "field": "password"},
            filled={"email"},
            redundant_streak=0,
        )
        self.assertFalse(fresh["skip"])

    def test_after_three_fills_continue_nudge_and_recovery(self) -> None:
        filled = {"email", "password", "birthday"}
        nudge = self.mm.signup_type_decision(
            heuristic="signup_form",
            action={"action": "type", "field": "email"},
            filled=filled,
            redundant_streak=0,
        )
        self.assertTrue(nudge["skip"])
        self.assertIn("button:has-text('Continue')", nudge["feedback"])
        self.assertIn("not Continue with Google", nudge["feedback"])
        third = self.mm.signup_type_decision(
            heuristic="signup_form",
            action={"action": "type", "field": "password"},
            filled=filled,
            redundant_streak=2,
        )
        self.assertTrue(third["skip"])
        self.assertTrue(third["recover_continue"])
        self.assertEqual(third["streak"], 3)
        self.assertIn("recovery", third["feedback"])

        ud = Path(tempfile.mkdtemp(prefix="cloakcli_visual_ud_loop_"))
        page = self.mm.DryRunPage()
        vision = self.mm.MockVision(
            [
                {"schema_version": 1, "action": "type", "field": "email"},
                {"schema_version": 1, "action": "type", "field": "password"},
                {"schema_version": 1, "action": "type", "field": "birthday"},
                {"schema_version": 1, "action": "type", "field": "email"},
                {"schema_version": 1, "action": "type", "field": "password"},
                {"schema_version": 1, "action": "type", "field": "email"},
                {"schema_version": 1, "action": "imap_fetch_code"},
                {"schema_version": 1, "action": "type", "field": "code"},
                {
                    "schema_version": 1,
                    "action": "click",
                    "selector": "button:has-text('Continue')",
                },
                {"schema_version": 1, "action": "nurture"},
                {
                    "schema_version": 1,
                    "action": "done",
                    "status": "registered_ok",
                    "path": "code_ui",
                },
            ]
        )
        logs: list[dict] = []
        orig_log = self.mm.log

        def _cap(obj: dict) -> None:
            logs.append(obj)
            orig_log(obj)

        with mock.patch.object(self.mm, "log", side_effect=_cap):
            report = self.mm.run_loop(
                page=page,
                ctx=None,
                ud=ud,
                args=_loop_args(max_steps=16),
                secrets=_subst(),
                secrets_path=str(ROOT / "data/secrets/pinterest-outlook-01.env"),
                disc={"base_url": "https://api.x.ai/v1", "model": "grok-4.6"},
                extra_secrets=["super-secret-pass", "user@example.com"],
                dry_run=True,
                vision=vision,
                api_key="",
                root=ROOT,
            )
        skipped = [e for e in logs if e.get("status") == "signup_type_skipped"]
        recovered = [e for e in logs if e.get("status") == "signup_continue_recovery"]
        self.assertTrue(skipped, "duplicate field types must be skipped")
        self.assertTrue(recovered and recovered[0].get("ok"), "one-shot Continue recovery")
        self.assertEqual(report["status"], "registered_ok")
        self.assertTrue((ud / ".cloak_session_ok").exists())
        blob = json.dumps(report)
        self.assertNotIn("user@example.com", blob)
        self.assertNotIn("super-secret-pass", blob)

    def test_vision_timeout_retries_then_succeeds(self) -> None:
        mm = self.mm

        class Flaky:
            def __init__(self) -> None:
                self.n = 0

            def complete(self, *_a: object, **_k: object) -> tuple[str, int]:
                self.n += 1
                if self.n < 3:
                    raise TimeoutError("model request timed out")
                return '{"schema_version":1,"action":"wait","ms":50}', 4

        sleeps: list[float] = []
        flaky = Flaky()
        text, tokens = mm.complete_vision_resilient(
            flaky,
            {},
            [],
            image_b64="",
            image_mime="image/jpeg",
            timeout_sec=8,
            sleep_fn=lambda s: sleeps.append(s),
            dry_run=False,
        )
        self.assertEqual(flaky.n, 3)
        self.assertEqual(len(sleeps), 2)
        self.assertIn("wait", text)
        self.assertEqual(tokens, 4)

        class AlwaysTimeout:
            def __init__(self) -> None:
                self.n = 0

            def complete(self, *_a: object, **_k: object) -> tuple[str, int]:
                self.n += 1
                raise TimeoutError("model request timed out")

        boom = AlwaysTimeout()
        with self.assertRaises(TimeoutError):
            mm.complete_vision_resilient(
                boom,
                {},
                [],
                image_b64="",
                image_mime="image/jpeg",
                timeout_sec=8,
                sleep_fn=lambda _s: None,
                dry_run=False,
            )
        self.assertEqual(boom.n, mm.VISION_RETRY_ATTEMPTS)

        class AuthFail:
            def __init__(self) -> None:
                self.n = 0

            def complete(self, *_a: object, **_k: object) -> tuple[str, int]:
                self.n += 1
                raise RuntimeError("HTTP 401 unauthorized")

        auth = AuthFail()
        with self.assertRaises(RuntimeError):
            mm.complete_vision_resilient(
                auth,
                {},
                [],
                image_b64="",
                image_mime="image/jpeg",
                timeout_sec=8,
                sleep_fn=lambda _s: None,
                dry_run=False,
            )
        self.assertEqual(auth.n, 1)
        self.assertTrue(mm.is_transient_model_error(TimeoutError("timed out")))
        self.assertTrue(mm.is_transient_model_error(RuntimeError("HTTP 503 bad gateway")))
        self.assertFalse(mm.is_transient_model_error(RuntimeError("HTTP 401 unauthorized")))

        ud = Path(tempfile.mkdtemp(prefix="cloakcli_visual_ud_to_"))
        page = self.mm.DryRunPage(persist_logged_in=True)

        class FlakyThenDone:
            def __init__(self) -> None:
                self.calls = 0

            def complete(self, *_a: object, **_k: object) -> tuple[str, int]:
                self.calls += 1
                if self.calls < 3:
                    raise TimeoutError("model request timed out")
                return (
                    json.dumps(
                        {
                            "schema_version": 1,
                            "action": "done",
                            "status": "registered_ok",
                            "path": "already_logged_in",
                        }
                    ),
                    4,
                )

        vision = FlakyThenDone()
        report = self.mm.run_loop(
            page=page,
            ctx=None,
            ud=ud,
            args=_loop_args(max_steps=6),
            secrets=_subst(),
            secrets_path=str(ROOT / "data/secrets/pinterest-outlook-01.env"),
            disc={"base_url": "https://api.x.ai/v1", "model": "grok-4.6"},
            extra_secrets=["super-secret-pass", "user@example.com"],
            dry_run=True,
            vision=vision,
            api_key="",
            root=ROOT,
        )
        self.assertEqual(report["status"], "registered_ok")
        self.assertEqual(vision.calls, 3)
        self.assertEqual(report["steps"], 0)


if __name__ == "__main__":
    unittest.main()
