"""Hubstudio Pinterest register skill (0.1.3) — selector CDP path, not vision."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
PKG = ROOT / "skills" / "hubstudio-pinterest-register"
RUNNER = PKG / "scripts" / "run_hubstudio_pinterest_register.py"
SHIM = ROOT / "scripts" / "run_hubstudio_pinterest_register.py"
BEHAVIOR = ROOT / "scripts" / "pinterest_nurture_behavior.py"
PKG_BEHAVIOR = PKG / "scripts" / "pinterest_nurture_behavior.py"
HOOK = PKG / "scripts" / "row_register_hook.py"


def load_mod(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeMouse:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def move(self, x: float, y: float, steps: int = 1) -> None:
        self.events.append(("move", float(x), float(y), int(steps)))

    def down(self) -> None:
        self.events.append(("down",))

    def up(self) -> None:
        self.events.append(("up",))

    def click(self, *a: object, **k: object) -> None:
        raise AssertionError("mouse.click teleport is forbidden")

    def wheel(self, dx: int, dy: int) -> None:
        self.events.append(("wheel", int(dx), int(dy)))


class FakeKeyboard:
    def __init__(self) -> None:
        self.events: list[tuple] = []
        self.value = ""

    def type(self, text: str, delay: int = 0) -> None:
        self.events.append(("type", text, delay))
        self.value += text

    def press(self, key: str) -> None:
        self.events.append(("press", key))


class FakeLocator:
    def __init__(self, box: dict | None, *, value: str = "") -> None:
        self._box = box
        self.clicks = 0
        self.fills = 0
        self.presses: list[str] = []
        self._value = value
        self.visible = box is not None

    def bounding_box(self) -> dict | None:
        return self._box

    def click(self, **k: object) -> None:
        self.clicks += 1
        raise AssertionError("locator.click must not run on Hub register Continue/fields")

    def hover(self, **k: object) -> None:
        return

    def focus(self) -> None:
        pass

    def press(self, key: str) -> None:
        self.presses.append(key)
        if key == "Control+A":
            self._value = ""
        elif key == "Backspace":
            self._value = self._value[:-1]

    def fill(self, text: str = "", **k: object) -> None:
        self.fills += 1
        self._value = text
        raise AssertionError("fill is forbidden for email/password/code/name")

    def input_value(self, timeout: int = 0) -> str:
        return self._value

    def wait_for(self, **k: object) -> None:
        return

    def is_visible(self, timeout: int = 0) -> bool:
        return bool(self.visible)

    def is_disabled(self) -> bool:
        return False

    def get_attribute(self, name: str) -> str:
        return ""

    def count(self) -> int:
        return 1 if self._box else 0

    @property
    def first(self) -> "FakeLocator":
        return self

    @property
    def page(self) -> "FakePage":
        return FakePage()


class FakePage:
    def __init__(self) -> None:
        self.mouse = FakeMouse()
        self.keyboard = FakeKeyboard()
        self.waits: list[int] = []
        self.url = "https://www.pinterest.com/signup/"
        self._locs: dict[str, FakeLocator] = {}
        self.body = "Log in Sign up Create a free account"

    def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(int(ms))

    def locator(self, sel: str) -> FakeLocator:
        if sel not in self._locs:
            self._locs[sel] = FakeLocator(
                {"x": 40.0, "y": 120.0, "width": 200.0, "height": 36.0}
            )
        return self._locs[sel]

    def inner_text(self, sel: str) -> str:
        return self.body


class HubstudioRegisterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = load_mod(RUNNER, "run_hubstudio_pinterest_register")

    def test_version_skill_id_and_catalog(self) -> None:
        self.assertEqual(self.mod.SKILL_ID, "hubstudio-pinterest-register")
        self.assertEqual(self.mod.VERSION, "0.1.3")
        self.assertIn("registered_ok", self.mod.SUCCESS_STATUSES)
        self.assertIn("oops_blocked", self.mod.ALLOWED_STATUSES)
        self.assertIn("env_protected", self.mod.ALLOWED_STATUSES)
        self.assertTrue(callable(self.mod.human_click_locator))
        self.assertTrue(callable(self.mod.human_type_text))
        self.assertTrue(callable(self.mod._hclick))
        self.assertTrue(callable(self.mod.connect_over_cdp))

    def test_manifest_and_skill_json_aligned(self) -> None:
        manifest = json.loads((PKG / "manifest.json").read_text(encoding="utf-8"))
        skill = json.loads((PKG / "skill.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "0.1.3")
        self.assertEqual(manifest["entry"]["kind"], "python_runner")
        self.assertEqual(manifest["entry"]["path"], "scripts/run_hubstudio_pinterest_register.py")
        ids = [s["id"] for s in manifest["statuses"]]
        self.assertEqual(ids[0], "registered_ok")
        self.assertTrue(manifest["statuses"][0]["success"])
        self.assertIn("oops_blocked", ids)
        self.assertIn("verify_soft_fail", ids)
        self.assertIn("0.1.3", skill["description"])
        self.assertIn("connect_over_cdp", skill["description"])
        self.assertIn("never a vision", skill["description"].lower())

    def test_source_has_no_teleport_cloak_or_vision_loop(self) -> None:
        src = RUNNER.read_text(encoding="utf-8")
        self.assertIn("human_click_locator", src)
        self.assertIn("human_type_text", src)
        self.assertIn("connect_over_cdp", src)
        self.assertIn("quiet_window", src)
        self.assertNotIn("force=True", src)
        self.assertNotIn("from cloakbrowser", src.lower())
        self.assertNotIn("import cloakbrowser", src.lower())
        self.assertNotIn("launch_persistent_context", src)
        self.assertNotIn("chat/completions", src)
        self.assertNotIn("image_url", src)
        self.assertNotIn("locator.click", src)
        self.assertNotIn("cont.click(", src)
        self.assertNotIn("signup_cont.click()", src)
        self.assertNotIn("press_sequentially", src)
        self.assertIn('page.fill("#birthdate"', src)
        self.assertIn("hub_api_success_code", src)
        self.assertIn("code is 0", src)
        self.assertIn('code == "0"', src)
        self.assertNotIn('not in (0, "0")', src)
        self.assertNotIn("not in (0, '0')", src)
        self.assertNotIn('in (0, "0")', src)
        self.assertNotIn("in (0, '0')", src)
        self.assertIn("1863073221", src)
        self.assertIn("1863107506", src)
        self.assertIn("1863108423", src)

    def test_behavior_copy_matches_repo(self) -> None:
        self.assertEqual(BEHAVIOR.read_bytes(), PKG_BEHAVIOR.read_bytes())
        self.assertTrue(SHIM.is_file())
        shim = SHIM.read_text(encoding="utf-8")
        self.assertIn("skills", shim)
        self.assertIn("hubstudio-pinterest-register", shim)

    def test_continue_uses_human_click_not_locator_click(self) -> None:
        page = FakePage()
        loc = FakeLocator({"x": 40.0, "y": 280.0, "width": 200.0, "height": 40.0})
        result = self.mod._hclick(page, loc)
        self.assertTrue(result["ok"])
        self.assertEqual(result["method"], "mouse_trail_down_up")
        self.assertEqual(loc.clicks, 0)
        kinds = [e[0] for e in page.mouse.events]
        self.assertNotIn("click", kinds)
        self.assertGreaterEqual(kinds.count("move"), 8)
        self.assertEqual(kinds.count("down"), 1)
        self.assertEqual(kinds.count("up"), 1)

        missing = FakeLocator(None)
        page2 = FakePage()
        failed = self.mod._hclick(page2, missing)
        self.assertFalse(failed["ok"])
        self.assertEqual(missing.clicks, 0)

    def test_code_mismatch_does_not_click_continue(self) -> None:
        page = FakePage()
        with (
            mock.patch.object(self.mod, "fill_code_react", return_value="000000") as fill,
            mock.patch.object(self.mod, "_hclick") as hclick,
            mock.patch.object(self.mod, "find_verify_continue") as find_cont,
            mock.patch.object(self.mod, "human_pause") as pause,
        ):
            result = self.mod.submit_verify_code(page, "654321")
        fill.assert_called_once_with(page, "654321")
        hclick.assert_not_called()
        find_cont.assert_not_called()
        pause.assert_not_called()
        self.assertEqual(result["status"], "verify_soft_fail")
        self.assertEqual(result.get("error_snip"), "code_value_mismatch")

    def test_parse_accounts_raw_line(self) -> None:
        line = "alice@example.com----outlookpwd----aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee----REFRESHTOKENVALUE----bob@example.com----pinpwd12"
        got = self.mod.parse_accounts_raw_line(line)
        self.assertEqual(got["OUTLOOK_EMAIL"], "alice@example.com")
        self.assertEqual(got["PINTEREST_EMAIL"], "bob@example.com")
        self.assertEqual(got["PINTEREST_PASSWORD"], "pinpwd12")
        self.assertEqual(got["OUTLOOK_CLIENT_ID"], "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        self.assertEqual(got["OUTLOOK_REFRESH_TOKEN"], "REFRESHTOKENVALUE")
        short = "only@x.com----p----uuid-uuid-uuid-uuid-uuiduuiduuid----tok"
        got2 = self.mod.parse_accounts_raw_line(short)
        self.assertEqual(got2["PINTEREST_EMAIL"], "only@x.com")
        self.assertEqual(got2["PINTEREST_PASSWORD"], "p")

    def test_parse_proxy_line(self) -> None:
        p = self.mod.parse_proxy_line("10.0.0.1:8080:user:passw")
        self.assertEqual(p["host"], "10.0.0.1")
        self.assertEqual(p["port"], 8080)
        self.assertEqual(p["account"], "user")
        self.assertEqual(p["asDynamicType"], 0)

    def test_protected_container_codes(self) -> None:
        for c in (1863073221, 1863107506, 1863108423):
            self.assertTrue(self.mod.is_protected_container(c))
        self.assertFalse(self.mod.is_protected_container(1863092952))
        self.assertIn(1863073221, self.mod.PROTECTED_CONTAINER_CODES)

    def test_env_name_pattern(self) -> None:
        state = Path("/home/box/.local/state/hubstudio-registration/20260930")
        self.assertEqual(self.mod.default_env_name("row-0041", state), "hub-reg-20260930-41")
        self.assertEqual(self.mod.default_env_name("row-0001", state), "hub-reg-20260930-01")

    def test_login_gate_onboarding_is_success(self) -> None:
        page = FakePage()
        page.url = "https://www.pinterest.com/"
        page.body = "Nice to meet you! What's your name?"
        page._locs[self.mod.UNAUTH_SELS] = FakeLocator(None)
        page._locs[self.mod.ACCT_SELS] = FakeLocator(None)
        page._locs[self.mod.PIN_LINK] = FakeLocator(None)
        page._locs["#code"] = FakeLocator(None)
        gate = self.mod.page_login_gate(page)
        self.assertTrue(gate["ok"])
        self.assertEqual(gate["reason"], "onboarding")

    def test_login_gate_unauth_cta(self) -> None:
        page = FakePage()
        page.url = "https://www.pinterest.com/"
        page.body = "Log in Sign up Create a free account"
        page._locs[self.mod.UNAUTH_SELS] = FakeLocator(
            {"x": 1.0, "y": 1.0, "width": 10.0, "height": 10.0}
        )
        page._locs[self.mod.ACCT_SELS] = FakeLocator(None)
        page._locs[self.mod.PIN_LINK] = FakeLocator(None)
        page._locs["#code"] = FakeLocator(None)
        gate = self.mod.page_login_gate(page)
        self.assertFalse(gate["ok"])
        self.assertEqual(gate["gate"], "not_logged_in")

    def test_redact_result_strips_secrets(self) -> None:
        bag = ["supersecretpassword", "alice@example.com"]
        out = self.mod.redact_result(
            {"password": "supersecretpassword", "reason": "fail alice@example.com", "ok": True},
            bag,
        )
        self.assertEqual(out["password"], "***")
        self.assertNotIn("alice@example.com", out["reason"])
        self.assertTrue(out["ok"])

    def test_map_internal_status(self) -> None:
        self.assertEqual(self.mod.map_internal_status("ok"), "registered_ok")
        self.assertEqual(self.mod.map_internal_status("oops_blocked"), "oops_blocked")
        self.assertEqual(self.mod.map_internal_status("verify_soft_oops"), "verify_soft_fail")
        self.assertEqual(self.mod.map_internal_status("nope"), "adapter_error")

    def test_skip_nurture_env(self) -> None:
        page = FakePage()
        args = mock.Mock(skip_nurture=False, nurture_pins=0, nurture_min_sec=1, nurture_max_sec=2)
        with mock.patch.dict(os.environ, {"HUB_SKIP_NURTURE": "1"}):
            out = self.mod.maybe_nurture(
                page, args, {"status": "registered_ok"}, profile="hub", art=Path("/tmp")
            )
        self.assertEqual(out["nurture_status"], "skipped")
        self.assertTrue(out["nurture_skip_warning"])

    def test_finish_report_identity(self) -> None:
        r = self.mod.finish_report(
            "registered_ok",
            digest="a" * 64,
            extra_secrets=["secretvalue12"],
            path="path_a_code",
            reason="ok secretvalue12",
        )
        self.assertEqual(r["skill_id"], "hubstudio-pinterest-register")
        self.assertEqual(r["version"], "0.1.3")
        self.assertEqual(r["status"], "registered_ok")
        self.assertEqual(r["digest"], "a" * 64)
        self.assertNotIn("secretvalue12", r["reason"])

    def test_plan_update_registered_ok(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            (state / "reports").mkdir()
            plan = {
                "records": [
                    {
                        "row_id": "row-0041",
                        "environment_id": "1863092952",
                        "environment_name": "hub-reg-20260930-41",
                        "status": "started",
                        "retries_used": 0,
                    }
                ]
            }
            (state / "plan.json").write_text(json.dumps(plan))
            self.mod.update_plan_from_report(
                state,
                "row-0041",
                {
                    "status": "registered_ok",
                    "email_verified": False,
                    "nurture_status": "skipped",
                    "path": "signup_ok_no_code_challenge",
                },
            )
            rec = json.loads((state / "plan.json").read_text())["records"][0]
            self.assertEqual(rec["status"], "ok")
            self.assertEqual(rec["skill_status"], "registered_ok")
            self.assertEqual(rec["registration_status"], "completed")
            self.assertFalse(rec["email_verified"])
            report = json.loads((state / "reports" / "row-0041-result.json").read_text())
            self.assertEqual(report["skill_status"], "registered_ok")
            self.assertNotIn("password", json.dumps(report))

    def test_hook_finds_package_runner(self) -> None:
        hook = load_mod(HOOK, "row_register_hook")
        with mock.patch.dict(os.environ, {"HUB_REGISTER_RUNNER": str(RUNNER)}):
            self.assertEqual(hook.find_runner(), RUNNER)

    def test_dry_run_last_line_is_report(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            (state / "secrets").mkdir()
            envf = state / "secrets" / "t.env"
            envf.write_text(
                "PINTEREST_EMAIL=a@example.com\n"
                "PINTEREST_PASSWORD=pw123456\n"
                "OUTLOOK_EMAIL=a@example.com\n"
                "OUTLOOK_CLIENT_ID=cid\n"
                "OUTLOOK_REFRESH_TOKEN=rtokenvalue\n"
            )
            import subprocess

            env = os.environ.copy()
            env["CLOAKCLI_HANG_BEFORE_CLOSE_MS"] = "0"
            proc = subprocess.run(
                [
                    os.environ.get("PYTHON", "python3"),
                    str(RUNNER),
                    "--row-id",
                    "row-0001",
                    "--state-dir",
                    str(state),
                    "--secrets",
                    str(envf),
                    "--dry-run",
                    "--container-code",
                    "1863092952",
                ],
                cwd=str(ROOT),
                env=env,
                text=True,
                capture_output=True,
            )
        lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        self.assertTrue(lines, proc.stderr)
        report = json.loads(lines[-1])
        self.assertEqual(report["skill_id"], "hubstudio-pinterest-register")
        self.assertEqual(report["status"], "not_logged_in")
        self.assertTrue(report.get("dry_run"))
        blob = proc.stdout + proc.stderr
        self.assertNotIn("pw123456", blob)
        self.assertNotIn("rtokenvalue", blob)

    def test_dry_run_refuses_protected_env(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            (state / "secrets").mkdir()
            envf = state / "secrets" / "t.env"
            envf.write_text(
                "PINTEREST_EMAIL=a@example.com\n"
                "PINTEREST_PASSWORD=pw123456\n"
                "OUTLOOK_EMAIL=a@example.com\n"
                "OUTLOOK_CLIENT_ID=cid\n"
                "OUTLOOK_REFRESH_TOKEN=rtokenvalue\n"
            )
            import subprocess

            env = os.environ.copy()
            env["CLOAKCLI_HANG_BEFORE_CLOSE_MS"] = "0"
            proc = subprocess.run(
                [
                    os.environ.get("PYTHON", "python3"),
                    str(RUNNER),
                    "--secrets",
                    str(envf),
                    "--dry-run",
                    "--container-code",
                    "1863073221",
                ],
                cwd=str(ROOT),
                env=env,
                text=True,
                capture_output=True,
            )
        report = json.loads([ln for ln in proc.stdout.splitlines() if ln.strip()][-1])
        self.assertEqual(report["status"], "env_protected")
        self.assertEqual(proc.returncode, 9)

    def _blank_page(self) -> FakePage:
        page = FakePage()
        page.url = "about:blank"
        page.body = ""
        page._locs[self.mod.UNAUTH_SELS] = FakeLocator(None)
        page._locs[self.mod.ACCT_SELS] = FakeLocator(None)
        page._locs[self.mod.PIN_LINK] = FakeLocator(None)
        page._locs["#code"] = FakeLocator(None)
        return page

    def test_login_gate_blank_page_not_success(self) -> None:
        gate = self.mod.page_login_gate(self._blank_page())
        self.assertFalse(gate["ok"])
        self.assertEqual(gate["gate"], "not_logged_in")
        self.assertEqual(gate["reason"], "no_login_signals")
        self.assertIsNotNone(self.mod.login_gate_fail_status(gate))
        self.assertNotEqual(self.mod.login_gate_fail_status(gate), "registered_ok")

    def test_login_gate_homefeed_positive_signal(self) -> None:
        page = FakePage()
        page.url = "https://www.pinterest.com/homefeed/"
        page.body = "Search Pinterest"
        page._locs[self.mod.UNAUTH_SELS] = FakeLocator(None)
        page._locs[self.mod.ACCT_SELS] = FakeLocator(None)
        page._locs[self.mod.PIN_LINK] = FakeLocator(None)
        page._locs["#code"] = FakeLocator(None)
        gate = self.mod.page_login_gate(page)
        self.assertTrue(gate["ok"])
        self.assertEqual(gate["reason"], "feed_no_unauth_cta")
        self.assertIsNone(self.mod.login_gate_fail_status(gate))

    def test_login_gate_empty_homefeed_or_today_url_not_success(self) -> None:
        for url in (
            "https://www.pinterest.com/homefeed/",
            "https://www.pinterest.com/today/",
        ):
            page = FakePage()
            page.url = url
            page.body = ""
            page._locs[self.mod.UNAUTH_SELS] = FakeLocator(None)
            page._locs[self.mod.ACCT_SELS] = FakeLocator(None)
            page._locs[self.mod.PIN_LINK] = FakeLocator(None)
            page._locs["#code"] = FakeLocator(None)
            gate = self.mod.page_login_gate(page)
            self.assertFalse(gate["ok"], url)
            self.assertEqual(gate["gate"], "not_logged_in", url)
            self.assertEqual(gate["reason"], "no_login_signals", url)
            fail = self.mod.login_gate_fail_status(gate)
            self.assertEqual(fail, "not_logged_in", url)
            self.assertNotEqual(fail, "registered_ok")

    def test_login_gate_fail_status_mapping(self) -> None:
        self.assertIsNone(self.mod.login_gate_fail_status({"ok": True, "gate": "logged_in"}))
        self.assertEqual(
            self.mod.login_gate_fail_status({"ok": False, "gate": "not_logged_in"}),
            "not_logged_in",
        )
        self.assertEqual(self.mod.login_gate_fail_status({"ok": False, "gate": "oops"}), "oops_blocked")
        self.assertEqual(
            self.mod.login_gate_fail_status({"ok": False, "gate": "account_deactivated"}),
            "account_deactivated",
        )
        self.assertEqual(
            self.mod.login_gate_fail_status({"ok": False, "gate": "code_ui"}),
            "verify_soft_fail",
        )

    def test_path_a_final_gate_fail_not_registered_ok(self) -> None:
        page = FakePage()
        args = mock.Mock(skip_nurture=True, nurture_pins=0, nurture_min_sec=1, nurture_max_sec=2)
        secrets = {
            "PINTEREST_EMAIL": "a@example.com",
            "PINTEREST_PASSWORD": "pw123456",
            "OUTLOOK_EMAIL": "a@example.com",
            "OUTLOOK_CLIENT_ID": "cidcidcidcid",
            "OUTLOOK_REFRESH_TOKEN": "rtokenvalue12",
        }
        start_gate = {"ok": False, "gate": "not_logged_in", "reason": "signup_url"}
        blank_gate = {"ok": False, "gate": "not_logged_in", "reason": "no_login_signals"}
        with (
            mock.patch.object(self.mod, "imap_max_uid", return_value=1),
            mock.patch.object(self.mod, "page_login_gate", side_effect=[start_gate, blank_gate]),
            mock.patch.object(self.mod, "fill_signup_form", return_value=None),
            mock.patch.object(self.mod, "click_signup_continue", return_value=True),
            mock.patch.object(self.mod, "settle_after_continue", return_value={"outcome": "code_ui"}),
            mock.patch.object(self.mod, "body_text", return_value="Enter the code we sent you"),
            mock.patch.object(self.mod, "_code_visible", return_value=True),
            mock.patch.object(self.mod, "maybe_verify_path_a", return_value={"status": "ok", "email_confirmed_toast": True}),
            mock.patch.object(self.mod, "finish_onboarding", return_value="James"),
            mock.patch.object(self.mod, "maybe_nurture") as nurture,
        ):
            out = self.mod.run_register_flow(page, args, secrets, Path("/tmp"), "hub")
        nurture.assert_not_called()
        self.assertEqual(out["status"], "not_logged_in")
        self.assertNotEqual(out["status"], "registered_ok")

    def test_send_new_code_captures_uid_before_wait(self) -> None:
        page = FakePage()
        secrets = {
            "OUTLOOK_EMAIL": "a@example.com",
            "OUTLOOK_CLIENT_ID": "cidcidcidcid",
            "OUTLOOK_REFRESH_TOKEN": "rtokenvalue12",
        }
        order: list = []

        def submit(_page, code, art=None):
            order.append(("submit", code))
            if code == "111111":
                return {"status": "verify_soft_oops"}
            return {"status": "ok"}

        def pause(*a, **k):
            label = k.get("label") or (a[3] if len(a) > 3 else "")
            order.append(("pause", label))
            return 1

        def max_uid(_s):
            order.append("uid")
            return 99

        def wait(_s, after, timeout=180):
            order.append(("wait", after))
            if after == 99:
                return {"code": "222222", "subject": "n"}
            return {"code": "111111", "subject": "o"}

        with (
            mock.patch.object(self.mod, "submit_verify_code", side_effect=submit),
            mock.patch.object(self.mod, "human_pause", side_effect=pause),
            mock.patch.object(self.mod, "imap_max_uid", side_effect=max_uid),
            mock.patch.object(self.mod, "imap_wait_code", side_effect=wait),
            mock.patch.object(self.mod, "click_send_new_code", return_value=True),
        ):
            out = self.mod.maybe_verify_path_a(page, secrets, 1, None)
        self.assertTrue(out.get("verify_retry"))
        uid_i = order.index("uid")
        pause_after = next(i for i, x in enumerate(order) if x == ("pause", "after_send_new_code"))
        wait_retry = next(i for i, x in enumerate(order) if x == ("wait", 99))
        self.assertLess(uid_i, pause_after)
        self.assertLess(uid_i, wait_retry)

    def test_proxy_update_fail_does_not_start_and_redacts_msg(self) -> None:
        proxy = {
            "host": "sensitive-host",
            "port": 8080,
            "account": "sensitive-user",
            "password": "sensitive-pass",
            "asDynamicType": 0,
        }
        buf = io.StringIO()
        with (
            mock.patch.object(
                self.mod,
                "proxy_update",
                return_value={"code": "E010008", "msg": "invalid sensitive-host sensitive-user sensitive-pass"},
            ),
            mock.patch.object(self.mod, "browser_start") as start,
            mock.patch.object(self.mod, "wait_cdp", return_value=True),
            contextlib.redirect_stdout(buf),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self.mod.start_hub_browser(1863092952, proxy)
        self.assertIn("proxy_update", str(ctx.exception))
        start.assert_not_called()
        out = buf.getvalue()
        self.assertNotIn("sensitive-pass", out)
        self.assertNotIn("sensitive-user", out)
        self.assertNotIn("sensitive-host", out)

    def test_proxy_update_missing_code_does_not_start(self) -> None:
        proxy = {
            "host": "sensitive-host",
            "port": 8080,
            "account": "sensitive-user",
            "password": "sensitive-pass",
            "asDynamicType": 0,
        }
        buf = io.StringIO()
        with (
            mock.patch.object(
                self.mod,
                "proxy_update",
                return_value={"msg": "update failed"},
            ),
            mock.patch.object(self.mod, "browser_start") as start,
            mock.patch.object(self.mod, "wait_cdp", return_value=True),
            contextlib.redirect_stdout(buf),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self.mod.start_hub_browser(1863092952, proxy)
        self.assertIn("proxy_update", str(ctx.exception))
        start.assert_not_called()
        out = buf.getvalue()
        self.assertNotIn("sensitive-pass", out)
        self.assertNotIn("sensitive-user", out)
        self.assertNotIn("sensitive-host", out)

    def test_proxy_update_none_false_float_do_not_start(self) -> None:
        proxy = {
            "host": "sensitive-host",
            "port": 8080,
            "account": "sensitive-user",
            "password": "sensitive-pass",
            "asDynamicType": 0,
        }
        for code in (None, False, 0.0):
            buf = io.StringIO()
            with (
                mock.patch.object(self.mod, "proxy_update", return_value={"code": code}),
                mock.patch.object(self.mod, "browser_start") as start,
                mock.patch.object(self.mod, "wait_cdp", return_value=True),
                contextlib.redirect_stdout(buf),
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    self.mod.start_hub_browser(1863092952, proxy)
            self.assertIn("proxy_update", str(ctx.exception), repr(code))
            start.assert_not_called()
            out = buf.getvalue()
            self.assertNotIn("sensitive-pass", out)
            self.assertNotIn("sensitive-user", out)
            self.assertNotIn("sensitive-host", out)

    def test_proxy_update_literal_zero_or_str_zero_may_start(self) -> None:
        proxy = {
            "host": "sensitive-host",
            "port": 8080,
            "account": "sensitive-user",
            "password": "sensitive-pass",
            "asDynamicType": 0,
        }
        for code in (0, "0"):
            buf = io.StringIO()
            with (
                mock.patch.object(self.mod, "proxy_update", return_value={"code": code}),
                mock.patch.object(
                    self.mod,
                    "browser_start",
                    return_value={"code": 0, "data": {"debuggingPort": 9222}},
                ) as start,
                mock.patch.object(self.mod, "wait_cdp", return_value=True),
                contextlib.redirect_stdout(buf),
            ):
                out_hub = self.mod.start_hub_browser(1863092952, proxy)
            start.assert_called_once_with(1863092952)
            self.assertEqual(out_hub["debugging_port"], 9222, repr(code))
            self.assertEqual(out_hub["cdp_endpoint"], "http://127.0.0.1:9222", repr(code))
            blob = buf.getvalue()
            self.assertNotIn("sensitive-pass", blob)
            self.assertNotIn("sensitive-user", blob)
            self.assertNotIn("sensitive-host", blob)

    def test_hub_api_success_code_rejects_falsey_zeros(self) -> None:
        self.assertTrue(self.mod.hub_api_success_code(0))
        self.assertTrue(self.mod.hub_api_success_code("0"))
        for bad in (None, False, 0.0, True, 1, "1", "", "00", -1):
            self.assertFalse(self.mod.hub_api_success_code(bad), repr(bad))

    def test_cdp_timeout_stops_browser(self) -> None:
        buf = io.StringIO()
        with (
            mock.patch.object(
                self.mod,
                "browser_start",
                return_value={"code": 0, "data": {"debuggingPort": 9222}},
            ),
            mock.patch.object(self.mod, "wait_cdp", return_value=False),
            mock.patch.object(self.mod, "browser_stop", return_value={"code": 0}) as stop,
            contextlib.redirect_stdout(buf),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self.mod.start_hub_browser(1863092952, None)
        self.assertIn("cdp_timeout", str(ctx.exception))
        stop.assert_called_once_with(1863092952)

    def test_main_cdp_timeout_stops_browser(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            (state / "secrets").mkdir()
            envf = state / "secrets" / "t.env"
            envf.write_text(
                "PINTEREST_EMAIL=a@example.com\n"
                "PINTEREST_PASSWORD=pw123456\n"
                "OUTLOOK_EMAIL=a@example.com\n"
                "OUTLOOK_CLIENT_ID=cid\n"
                "OUTLOOK_REFRESH_TOKEN=rtokenvalue\n"
            )
            buf = io.StringIO()
            with (
                mock.patch.object(self.mod, "start_hub_browser", side_effect=RuntimeError("cdp_timeout port=9222")),
                mock.patch.object(self.mod, "browser_stop", return_value={"code": 0}) as stop,
                mock.patch.dict(os.environ, {"CLOAKCLI_HANG_BEFORE_CLOSE_MS": "0"}),
                contextlib.redirect_stdout(buf),
            ):
                code = self.mod.main(
                    [
                        "--row-id",
                        "row-0001",
                        "--state-dir",
                        str(state),
                        "--secrets",
                        str(envf),
                        "--container-code",
                        "1863092952",
                    ]
                )
        stop.assert_called()
        self.assertEqual(code, 4)
        lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
        report = json.loads(lines[-1])
        self.assertEqual(report["skill_id"], "hubstudio-pinterest-register")
        self.assertEqual(report["status"], "adapter_error")
        self.assertEqual(report.get("path"), "cdp_timeout")

    def test_skip_nurture_cli_flag(self) -> None:
        parser = self.mod.build_parser()
        ns = parser.parse_args(["--skip-nurture"])
        self.assertTrue(ns.skip_nurture)
        ns2 = parser.parse_args([])
        self.assertFalse(ns2.skip_nurture)
        page = FakePage()
        args = mock.Mock(skip_nurture=True, nurture_pins=0, nurture_min_sec=1, nurture_max_sec=2)
        with mock.patch.dict(os.environ, {"HUB_SKIP_NURTURE": "0"}):
            out = self.mod.maybe_nurture(
                page, args, {"status": "registered_ok"}, profile="hub", art=Path("/tmp")
            )
        self.assertEqual(out["nurture_status"], "skipped")
        self.assertTrue(out["nurture_skip_warning"])

    def test_protected_env_refuses_start_stop_without_api(self) -> None:
        api = load_mod(PKG / "scripts" / "hubstudio_api.py", "hubstudio_api_prot")
        with mock.patch.object(api, "api_post") as post:
            for c in (1863073221, 1863107506, 1863108423):
                self.assertEqual(api.browser_start(c)["msg"], "protected_env_refused")
                self.assertEqual(api.browser_stop(c)["msg"], "protected_env_refused")
                self.assertEqual(
                    api.proxy_update(c, "h", 1, "u", "p")["msg"],
                    "protected_env_refused",
                )
            post.assert_not_called()
        with (
            mock.patch.object(self.mod, "browser_start") as start,
            mock.patch.object(self.mod, "browser_stop") as stop,
            mock.patch.object(self.mod, "proxy_update") as pu,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                self.mod.start_hub_browser(1863073221, None)
            self.assertIn("protected", str(ctx.exception))
            start.assert_not_called()
            stop.assert_not_called()
            pu.assert_not_called()

    def test_plan_update_err_does_not_append_json_after_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td)
            (state / "secrets").mkdir()
            envf = state / "secrets" / "t.env"
            envf.write_text(
                "PINTEREST_EMAIL=a@example.com\n"
                "PINTEREST_PASSWORD=pw123456\n"
                "OUTLOOK_EMAIL=a@example.com\n"
                "OUTLOOK_CLIENT_ID=cid\n"
                "OUTLOOK_REFRESH_TOKEN=rtokenvalue\n"
            )
            (state / "plan.json").write_text(
                json.dumps({"records": [{"row_id": "row-0001", "status": "started"}]}),
                encoding="utf-8",
            )
            buf = io.StringIO()
            err = io.StringIO()
            with (
                mock.patch.object(self.mod, "update_plan_from_report", side_effect=OSError("disk")),
                mock.patch.dict(os.environ, {"CLOAKCLI_HANG_BEFORE_CLOSE_MS": "0"}),
                contextlib.redirect_stdout(buf),
                contextlib.redirect_stderr(err),
            ):
                code = self.mod.main(
                    [
                        "--row-id",
                        "row-0001",
                        "--state-dir",
                        str(state),
                        "--secrets",
                        str(envf),
                        "--dry-run",
                        "--container-code",
                        "1863092952",
                    ]
                )
        self.assertEqual(code, 8)
        lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
        self.assertTrue(lines)
        report = json.loads(lines[-1])
        self.assertEqual(report["skill_id"], "hubstudio-pinterest-register")
        self.assertEqual(report["status"], "not_logged_in")
        self.assertTrue(report.get("dry_run"))
        self.assertNotIn("plan_update_err", buf.getvalue())
        for ln in lines:
            obj = json.loads(ln)
            if obj.get("status") == "plan_update_err":
                self.fail("identity-less plan_update_err JSON on stdout")
        self.assertIn("plan_update_err", err.getvalue())


if __name__ == "__main__":
    unittest.main()
