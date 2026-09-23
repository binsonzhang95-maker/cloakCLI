"""pinterest-create-pin 0.1.0: board gate, pin evidence, dry-run, banned clicks."""
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
RUNNER = ROOT / "skills" / "pinterest-create-pin" / "scripts" / "run_pinterest_create_pin.py"
SHIM = ROOT / "scripts" / "run_pinterest_create_pin.py"
MANIFEST = ROOT / "skills" / "pinterest-create-pin" / "manifest.json"
IMAGE = ROOT / "artifacts" / "pinterest" / "create-pin" / "cw-10048-forest-friend-gift-sq.jpg"


def load_mod():
    spec = importlib.util.spec_from_file_location("run_pinterest_create_pin", RUNNER)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = load_mod()


class FakeLoc:
    def __init__(self, count: int, visible: bool) -> None:
        self._count = count
        self._visible = visible

    def count(self) -> int:
        return self._count

    @property
    def first(self) -> "FakeLoc":
        return self

    def is_visible(self) -> bool:
        return self._visible


class FakePage:
    def __init__(self, count: int, visible: bool) -> None:
        self.count = count
        self.visible = visible
        self.selectors: list[str] = []

    def locator(self, sel: str) -> FakeLoc:
        self.selectors.append(sel)
        return FakeLoc(self.count, self.visible)


class SelectorAndGateTests(unittest.TestCase):
    def test_verified_selectors(self) -> None:
        sel = mod.VERIFIED_SELECTORS
        self.assertEqual(sel["title"], "#storyboard-selector-title")
        self.assertEqual(sel["description_container"], '[data-test-id="storyboard-description-field-container"]')
        self.assertEqual(sel["description_editor"], '[data-test-id="editor-with-mentions"]')
        self.assertEqual(sel["board_button"], '[data-test-id="board-dropdown-select-button"]')
        self.assertEqual(sel["board_placeholder"], '[data-test-id="board-dropdown-placeholder"]')
        self.assertEqual(sel["board_form_submit"], '[data-test-id="board-form-submit-button"]')
        self.assertEqual(sel["publish"], '[data-test-id="storyboard-creation-nav-done"]')
        self.assertEqual(sel["create_tab"], '[data-test-id="create-tab"]')
        self.assertIn("pin-creation-tool", sel["pin_tool"])

    def test_board_scopes_are_not_loose_text(self) -> None:
        joined = "\n".join(mod.BOARD_OPTION_SCOPES)
        self.assertNotIn("div:has-text", joined)
        self.assertNotIn("Classic World", joined)
        for sel in mod.BOARD_OPTION_SCOPES:
            self.assertTrue(
                "option" in sel or "board-row" in sel or "boardFromList" in sel or "boardWithoutSection" in sel
            )
        publish = "\n".join((mod.SEL_PUBLISH,))
        self.assertNotIn("Publish", publish)
        self.assertEqual(mod.SEL_PUBLISH, '[data-test-id="storyboard-creation-nav-done"]')

    def test_board_name_match_is_exact_first_line(self) -> None:
        self.assertTrue(mod.board_name_matches("Classic World", "Classic World"))
        self.assertTrue(mod.board_name_matches("Classic World\n12 Pins", "classic world"))
        self.assertFalse(mod.board_name_matches("Forest Friend Baby Gift Set | Classic World", "Classic World"))
        self.assertFalse(mod.board_name_matches("Classic World Toys", "Classic World"))
        self.assertFalse(mod.board_name_matches("", "Classic World"))
        self.assertFalse(mod.board_name_matches("Classic World", ""))

    def test_option_y_skips_header_not_publish_button(self) -> None:
        self.assertFalse(mod.option_y_ok(93))
        self.assertFalse(mod.option_y_ok(119))
        self.assertTrue(mod.option_y_ok(140))
        self.assertTrue(mod.option_y_ok(None))

    def test_placeholder_gate(self) -> None:
        self.assertTrue(mod.publish_gate_open(placeholder_visible=False, picked_name="Classic World"))
        self.assertFalse(mod.publish_gate_open(placeholder_visible=True, picked_name="Classic World"))
        self.assertFalse(mod.publish_gate_open(placeholder_visible=False, picked_name=""))
        self.assertFalse(mod.publish_gate_open(placeholder_visible=False, picked_name=None))
        self.assertEqual(
            mod.board_block_status(placeholder_visible=True, picked_name="Classic World"),
            "board_missing",
        )
        self.assertIsNone(mod.board_block_status(placeholder_visible=False, picked_name="Classic World"))

        visible = FakePage(1, True)
        self.assertTrue(mod.board_placeholder_visible(visible))
        self.assertEqual(visible.selectors, [mod.SEL_BOARD_PLACEHOLDER])
        self.assertFalse(mod.board_placeholder_visible(FakePage(0, True)))
        self.assertFalse(mod.board_placeholder_visible(FakePage(1, False)))

    def test_publish_poll_budget_extends_when_complete_without_pin(self) -> None:
        self.assertEqual(mod.publish_poll_budget(publish_complete=False, pin_id=None), mod.PUBLISH_POLLS)
        self.assertEqual(
            mod.publish_poll_budget(publish_complete=True, pin_id=None),
            mod.PUBLISH_POLLS + mod.PUBLISH_EXTRA_POLLS_IF_COMPLETE,
        )
        self.assertEqual(mod.publish_poll_budget(publish_complete=True, pin_id="1152217885972567993"), mod.PUBLISH_POLLS)


class PinEvidenceTests(unittest.TestCase):
    PIN = "1152217885972567993"
    URL = f"https://www.pinterest.com/pin/{PIN}/"

    def test_parse_pin_ref(self) -> None:
        pin_id, url = mod.parse_pin_ref(f"/pin/{self.PIN}?x=1")
        self.assertEqual(pin_id, self.PIN)
        self.assertEqual(url, self.URL)
        pin_id, url = mod.parse_pin_ref(self.URL)
        self.assertEqual((pin_id, url), (self.PIN, self.URL))
        self.assertEqual(mod.parse_pin_ref("https://www.pinterest.com/pin-creation-tool/"), (None, None))
        self.assertEqual(mod.parse_pin_ref(""), (None, None))

    def test_toast_pin_counts_without_waiting_for_sidebar(self) -> None:
        chosen = mod.choose_published_pin(
            before_ids=set(),
            page_url="https://www.pinterest.com/pin-creation-tool/",
            toast_href=f"/pin/{self.PIN}/",
            draft_hrefs=[],
            page_hrefs=[],
            publish_complete=False,
        )
        self.assertEqual(chosen["pin_id"], self.PIN)
        self.assertEqual(chosen["source"], "toast")
        self.assertEqual(chosen["pin_url"], self.URL)

    def test_creation_tool_url_is_not_a_redirect(self) -> None:
        chosen = mod.choose_published_pin(
            before_ids=set(),
            page_url="https://www.pinterest.com/pin-creation-tool/",
            toast_href="",
            draft_hrefs=[],
            page_hrefs=[],
            publish_complete=True,
        )
        self.assertIsNone(chosen["pin_id"])

    def test_redirect_and_draft_and_complete_link(self) -> None:
        redir = mod.choose_published_pin(
            before_ids=set(),
            page_url=self.URL,
            toast_href="",
            draft_hrefs=[],
            page_hrefs=[],
            publish_complete=False,
        )
        self.assertEqual(redir["source"], "redirect")
        draft = mod.choose_published_pin(
            before_ids=set(),
            page_url="https://www.pinterest.com/pin-creation-tool/",
            toast_href="",
            draft_hrefs=[f"/pin/{self.PIN}"],
            page_hrefs=[],
            publish_complete=True,
        )
        self.assertEqual(draft["source"], "draft")
        loose = mod.choose_published_pin(
            before_ids=set(),
            page_url="https://www.pinterest.com/pin-creation-tool/",
            toast_href="",
            draft_hrefs=[],
            page_hrefs=[f"/pin/{self.PIN}/"],
            publish_complete=False,
        )
        self.assertIsNone(loose["pin_id"])
        paired = mod.choose_published_pin(
            before_ids=set(),
            page_url="https://www.pinterest.com/pin-creation-tool/",
            toast_href="",
            draft_hrefs=[],
            page_hrefs=[f"/pin/{self.PIN}/"],
            publish_complete=True,
        )
        self.assertEqual(paired["source"], "publish_complete_link")

    def test_preexisting_pin_ids_are_ignored(self) -> None:
        chosen = mod.choose_published_pin(
            before_ids={self.PIN},
            page_url=self.URL,
            toast_href=self.URL,
            draft_hrefs=[self.URL],
            page_hrefs=[self.URL],
            publish_complete=True,
        )
        self.assertIsNone(chosen["pin_id"])


class ProfileResolutionTests(unittest.TestCase):
    def test_prefers_pinterest_run_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nurture = root / "data" / "profiles" / "geo46-pinterest-run"
            nurture.mkdir(parents=True)
            meta_ud = root / "data" / "profiles" / "geo46"
            meta_ud.mkdir(parents=True)
            (root / "profiles" / "geo46").mkdir(parents=True)
            (root / "profiles" / "geo46" / "profile.json").write_text(
                json.dumps({"user_data_dir": "data/profiles/geo46", "proxy": "http://user:pass@127.0.0.1:1"}),
                encoding="utf-8",
            )
            got = mod.resolve_user_data_dir(root, "geo46")
            self.assertEqual(got, nurture)
            self.assertNotEqual(got, meta_ud)

    def test_falls_back_to_profile_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ud = root / "data" / "profiles" / "geo46"
            ud.mkdir(parents=True)
            (root / "profiles" / "geo46").mkdir(parents=True)
            (root / "profiles" / "geo46" / "profile.json").write_text(
                json.dumps({"user_data_dir": "data/profiles/geo46"}),
                encoding="utf-8",
            )
            self.assertEqual(mod.resolve_user_data_dir(root, "geo46"), ud)
            self.assertIsNone(mod.resolve_user_data_dir(root, "missing"))


class SourceBanTests(unittest.TestCase):
    def test_runner_has_no_teleport_fill_or_loose_board_text(self) -> None:
        src = RUNNER.read_text(encoding="utf-8")
        # The module docstring names the round-1 selector so operators can see the ban.
        code = src.split('"""', 2)[-1]
        self.assertNotIn("locator.click", code)
        self.assertNotIn("loc.click", code)
        self.assertNotIn(".fill(", code)
        self.assertNotIn("force=True", code)
        self.assertNotIn("div:has-text(", code)
        self.assertNotIn('button:has-text("Publish")', src)
        self.assertNotIn("not_implemented", src)
        self.assertNotIn("google-chrome", src)
        self.assertNotIn("Toys", src)
        self.assertIn("human_click_locator", src)
        self.assertIn("human_type_text", src)
        self.assertIn("launch_persistent_context", src)
        self.assertIn("storyboard-creation-nav-done", src)
        self.assertIn("board-dropdown-placeholder", src)

    def test_manifest_matches_runner_catalog(self) -> None:
        man = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(man["version"], "0.1.0")
        self.assertEqual(man["entry"]["kind"], "python_runner")
        self.assertEqual(man["entry"]["path"], "scripts/run_pinterest_create_pin.py")
        ids = [s["id"] for s in man["statuses"]]
        self.assertEqual(ids, list(mod.STATUS_EXIT))
        self.assertNotIn("not_implemented", ids)
        for row in man["statuses"]:
            self.assertEqual(row["exit"], mod.STATUS_EXIT[row["id"]])
            self.assertEqual(row["success"], row["id"] in mod.SUCCESS_STATUSES)
            self.assertTrue(row["label"])


class DryRunTests(unittest.TestCase):
    def _run(self, extra: list[str], image: Path | None = None) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as tmp:
            cmd = [
                sys.executable,
                str(RUNNER),
                "--dry-run",
                "--profile",
                "geo46",
                "--image",
                str(image or IMAGE),
                "--title",
                "Forest Friend Baby Gift Set | Classic World",
                "--description",
                "A sweet wooden forest friend baby gift from Classic World.",
                "--board",
                "Classic World",
                "--out",
                tmp,
                *extra,
            ]
            env = os.environ.copy()
            env["CLOAKCLI_HANG_BEFORE_CLOSE_MS"] = "0"
            proc = subprocess.run(
                cmd,
                cwd=str(ROOT),
                text=True,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                env=env,
                timeout=30,
            )
            disk = Path(tmp) / "result.json"
            proc.disk_text = disk.read_text(encoding="utf-8") if disk.is_file() else ""  # type: ignore[attr-defined]
            return proc

    def test_dry_run_exit_0(self) -> None:
        if not IMAGE.is_file():
            self.skipTest("explore image missing")
        proc = self._run([])
        self.assertEqual(proc.returncode, 0, msg=proc.stderr[-1500:] + proc.stdout[-1500:])
        lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
        self.assertEqual(len(lines), 1)
        report = json.loads(lines[-1])
        self.assertEqual(report["skill_id"], "pinterest-create-pin")
        self.assertEqual(report["version"], "0.1.0")
        self.assertEqual(report["status"], "dry_run_ok")
        self.assertTrue(report["success"])
        self.assertTrue(report["dry_run"])
        self.assertEqual(report["exit"], 0)
        self.assertTrue(report["create_board_if_missing"])
        self.assertTrue(report["headed"])
        self.assertTrue(report["publish"])
        self.assertNotIn("0.1.0-draft", proc.stdout)
        self.assertTrue(proc.disk_text)  # type: ignore[attr-defined]
        disk = json.loads(proc.disk_text)  # type: ignore[attr-defined]
        self.assertEqual(disk["status"], "dry_run_ok")

    def test_shim_dry_run(self) -> None:
        if not IMAGE.is_file():
            self.skipTest("explore image missing")
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SHIM),
                    "--dry-run",
                    "--profile",
                    "geo46",
                    "--image",
                    str(IMAGE),
                    "--title",
                    "Forest Friend",
                    "--description",
                    "Wooden baby gift from Classic World.",
                    "--board",
                    "Classic World",
                    "--out",
                    tmp,
                ],
                cwd=str(ROOT),
                text=True,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=30,
            )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr[-1500:] + proc.stdout[-1500:])
        report = json.loads([ln for ln in proc.stdout.splitlines() if ln.strip()][-1])
        self.assertEqual(report["status"], "dry_run_ok")
        self.assertEqual(report["version"], "0.1.0")

    def test_missing_image_is_upload_fail(self) -> None:
        proc = self._run([], image=Path("/tmp/does-not-exist-create-pin.jpg"))
        self.assertEqual(proc.returncode, 3, msg=proc.stdout[-800:])
        report = json.loads([ln for ln in proc.stdout.splitlines() if ln.strip()][-1])
        self.assertEqual(report["status"], "upload_fail")
        self.assertFalse(report["success"])

    def test_empty_title_parks_without_browser(self) -> None:
        if not IMAGE.is_file():
            self.skipTest("explore image missing")
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [
                    sys.executable,
                    str(RUNNER),
                    "--dry-run",
                    "--profile",
                    "geo46",
                    "--image",
                    str(IMAGE),
                    "--title",
                    "   ",
                    "--description",
                    "desc",
                    "--board",
                    "Classic World",
                    "--out",
                    tmp,
                ],
                cwd=str(ROOT),
                text=True,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=30,
            )
        self.assertEqual(proc.returncode, 4, msg=proc.stdout[-800:])
        report = json.loads([ln for ln in proc.stdout.splitlines() if ln.strip()][-1])
        self.assertEqual(report["status"], "ui_unknown_park")

    def test_stdin_digest_and_overrides(self) -> None:
        if not IMAGE.is_file():
            self.skipTest("explore image missing")
        with tempfile.TemporaryDirectory() as tmp:
            payload = {
                "skill_id": "pinterest-create-pin",
                "version": "0.1.0",
                "digest": "abc123",
                "profile": "from-stdin",
                "vars": {
                    "IMAGE_PATH": str(IMAGE),
                    "TITLE": "From stdin",
                    "DESCRIPTION": "From stdin description",
                    "BOARD": "Classic World",
                    "DRY_RUN": "true",
                    "PUBLISH": "false",
                },
            }
            proc = subprocess.run(
                [sys.executable, str(RUNNER), "--out", tmp],
                cwd=str(ROOT),
                text=True,
                capture_output=True,
                input=json.dumps(payload),
                timeout=30,
            )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr[-1500:] + proc.stdout[-1500:])
        report = json.loads([ln for ln in proc.stdout.splitlines() if ln.strip()][-1])
        self.assertEqual(report["status"], "dry_run_ok")
        self.assertEqual(report["digest"], "abc123")
        self.assertEqual(report["profile"], "from-stdin")
        self.assertFalse(report["publish"])
        self.assertEqual(report["title"], "From stdin")


if __name__ == "__main__":
    unittest.main()
