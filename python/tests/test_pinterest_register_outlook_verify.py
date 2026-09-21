"""Pinterest register outlook-verify runner (0.1.3) — human behavior wiring."""
from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "run_pinterest_register_outlook_verify.py"
BEHAVIOR = ROOT / "scripts" / "pinterest_nurture_behavior.py"


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
        self.hovered = 0
        self._value = value
        self.visible = box is not None

    def bounding_box(self) -> dict | None:
        return self._box

    def click(self, **k: object) -> None:
        self.clicks += 1
        raise AssertionError("locator.click must not run on register Continue/fields")

    def hover(self, **k: object) -> None:
        self.hovered += 1

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

    def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(int(ms))

    def locator(self, sel: str) -> FakeLocator:
        if sel not in self._locs:
            self._locs[sel] = FakeLocator(
                {"x": 40.0, "y": 120.0, "width": 200.0, "height": 36.0}
            )
        return self._locs[sel]


class OutlookVerifyBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ov = load_mod(RUNNER, "run_pinterest_register_outlook_verify")

    def test_version_and_imports(self) -> None:
        self.assertEqual(self.ov.VERSION, "0.1.3")
        self.assertTrue(callable(self.ov.human_click_locator))
        self.assertTrue(callable(self.ov.human_type_text))
        self.assertTrue(callable(self.ov._hclick))
        self.assertTrue(callable(self.ov.quiet_window))

    def test_source_has_no_teleport_click_or_uniform_type(self) -> None:
        src = RUNNER.read_text(encoding="utf-8")
        self.assertIn("human_click_locator", src)
        self.assertIn("human_type_text", src)
        self.assertIn("quiet_window", src)
        self.assertIn("sample_pause_ms", src)
        self.assertNotIn("force=True", src)
        self.assertNotIn('page.click("#email")', src)
        self.assertNotIn('page.click("#password")', src)
        self.assertNotIn("signup_cont.click()", src)
        self.assertNotIn("signup_cont.click(timeout", src)
        self.assertNotIn("cont.click(", src)
        self.assertNotIn("loc.click()", src)
        self.assertNotIn("loc.click(timeout", src)
        self.assertNotIn("name_loc.click()", src)
        self.assertNotIn("keyboard.type", src)
        self.assertNotIn("press_sequentially", src)
        self.assertIn('page.fill("#birthdate"', src)
        skill = json.loads(
            (ROOT / "skills/pinterest-register-outlook-verify/skill.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn("0.1.3", skill["description"])
        readme = (ROOT / "skills/pinterest-register-outlook-verify/README.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("trail click", readme.lower())

    def test_continue_uses_human_click_not_locator_click(self) -> None:
        page = FakePage()
        loc = FakeLocator({"x": 40.0, "y": 280.0, "width": 200.0, "height": 40.0})
        result = self.ov._hclick(page, loc)
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
        failed = self.ov._hclick(page2, missing)
        self.assertFalse(failed["ok"])
        self.assertEqual(missing.clicks, 0)
        self.assertNotIn("click", [e[0] for e in page2.mouse.events])

    def test_code_type_uses_human_type_text_not_fill(self) -> None:
        page = FakePage()
        loc = FakeLocator({"x": 40.0, "y": 160.0, "width": 180.0, "height": 32.0})
        page._locs["#code"] = loc

        def _input_value(timeout: int = 0) -> str:
            typed = [e[1] for e in page.keyboard.events if e[0] == "type"]
            return "".join(ch for ch in typed if len(ch) == 1)

        loc.input_value = _input_value  # type: ignore[method-assign]
        val = self.ov.fill_code_react(page, "654321")
        self.assertEqual(val, "654321")
        self.assertEqual(loc.clicks, 0)
        self.assertEqual(loc.fills, 0)
        typed = [e[1] for e in page.keyboard.events if e[0] == "type"]
        self.assertEqual("".join(ch for ch in typed if len(ch) == 1)[-6:], "654321")

    def test_human_pause_is_lognormal_not_uniform_randint(self) -> None:
        src = RUNNER.read_text(encoding="utf-8")
        pause_fn = src.split("def human_pause", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("sample_pause_ms", pause_fn)
        self.assertNotIn("random.randint", pause_fn)
        page = FakePage()
        ms = self.ov.human_pause(page, 800, 2500, "between_email_password", ambient=False)
        self.assertGreaterEqual(ms, 800)
        self.assertLessEqual(ms, 2500)
        self.assertTrue(page.waits)

    def test_quiet_window_helper(self) -> None:
        page = FakePage()
        ms = self.ov.quiet_window(page)
        self.assertGreaterEqual(ms, 1000)
        self.assertLessEqual(ms, 2300)
        self.assertTrue(page.waits)

    def test_behavior_module_is_the_nurture_helper(self) -> None:
        self.assertTrue(BEHAVIOR.is_file())
        self.assertIn("pinterest_nurture_behavior", RUNNER.read_text(encoding="utf-8"))

    def test_submit_verify_code_mismatch_does_not_click_continue(self) -> None:
        page = FakePage()
        shots: list[str] = []
        page.screenshot = lambda path="", **k: shots.append(str(path))  # type: ignore[method-assign]
        with (
            mock.patch.object(self.ov, "fill_code_react", return_value="000000") as fill,
            mock.patch.object(self.ov, "_hclick") as hclick,
            mock.patch.object(self.ov, "find_verify_continue") as find_cont,
            mock.patch.object(self.ov, "human_pause") as pause,
        ):
            result = self.ov.submit_verify_code(page, "654321")
        fill.assert_called_once_with(page, "654321")
        hclick.assert_not_called()
        find_cont.assert_not_called()
        pause.assert_not_called()
        self.assertEqual(shots, [])
        self.assertEqual(result["status"], "verify_soft_fail")
        self.assertEqual(result.get("error_snip"), "code_value_mismatch")
        self.assertFalse(result.get("value_match", True))
        self.assertTrue(result.get("still_code_ui"))
        self.assertEqual(result.get("code_value_len"), 6)

    def test_skill_docs_nurture_version_is_0_2_1(self) -> None:
        files = [
            ROOT / "skills/pinterest-register-visual/skill.json",
            ROOT / "skills/pinterest-register-visual/OPERATOR.md",
            ROOT / "skills/pinterest-register-outlook-verify/README.md",
        ]
        for path in files:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("0.1.7+", text, msg=str(path))
            self.assertIn("0.2.1+", text, msg=str(path))


if __name__ == "__main__":
    unittest.main()
