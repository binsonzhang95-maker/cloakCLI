"""Nurture 0.2.6 empty-feed / NUX helpers: gate reclassify + picker detect (no live Pinterest)."""
from __future__ import annotations

import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "run_pinterest_nurture_browse.py"


def load_mod(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    # Behavior import needs scripts/ on path; runner inserts it.
    spec.loader.exec_module(mod)
    return mod


nb = load_mod(RUNNER, "run_pinterest_nurture_browse_empty_feed")


class FakeLocator:
    def __init__(self, count: int = 0, *, text: str = "", visible: bool = True):
        self._count = count
        self._text = text
        self._visible = visible
        self.first = self

    def count(self) -> int:
        return self._count

    def is_visible(self, timeout: int = 0) -> bool:
        return self._visible and self._count > 0

    def is_disabled(self, timeout: int = 0) -> bool:
        return False

    def inner_text(self, timeout: int = 0) -> str:
        return self._text

    def nth(self, i: int) -> "FakeLocator":
        if i >= self._count:
            return FakeLocator(0)
        return FakeLocator(1, text=self._text or f"tile_{i}", visible=True)

    def scroll_into_view_if_needed(self, timeout: int = 0) -> None:
        return None

    def filter(self, **k):
        return self


class FakePage:
    """Minimal page: body text + selector→count map for gate/picker helpers."""

    def __init__(
        self,
        *,
        body: str = "",
        counts: dict[str, int] | None = None,
        url: str = "https://www.pinterest.com/",
    ) -> None:
        self._body = body
        self._counts = counts or {}
        self.url = url
        self.waits: list[int] = []
        self.reloads = 0

    def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(int(ms))

    def inner_text(self, sel: str) -> str:
        if sel == "body":
            return self._body
        return ""

    def locator(self, sel: str) -> FakeLocator:
        # text=/.../i style
        if sel.startswith("text="):
            raw = sel[5:]
            if raw.startswith("/") and raw.rstrip("i").endswith("/"):
                body_pat = raw.strip("i").strip("/")
                # strip trailing flags already handled
                if body_pat.endswith("/"):
                    body_pat = body_pat[:-1]
                try:
                    matched = bool(re.search(body_pat, self._body, re.I))
                except re.error:
                    matched = body_pat.lower() in self._body.lower()
                return FakeLocator(1 if matched else 0, text=body_pat)
            needle = raw.strip("'\"")
            return FakeLocator(1 if needle.lower() in self._body.lower() else 0)

        # Exact / substring match against configured counts
        if sel in self._counts:
            return FakeLocator(self._counts[sel])
        for key, n in self._counts.items():
            if key in sel or sel in key:
                return FakeLocator(n)
        # Multi-selector strings used by UNAUTH_SELS / ACCT_SELS
        total = 0
        for part in re.split(r"\s*,\s*", sel):
            part = part.strip()
            if part in self._counts:
                total += self._counts[part]
            else:
                for key, n in self._counts.items():
                    if key == part or key in part:
                        total += n
                        break
        return FakeLocator(total)

    def get_by_role(self, role: str, name=None):
        return FakeLocator(0)

    def reload(self, **k) -> None:
        self.reloads += 1


class VersionTests(unittest.TestCase):
    def test_version_bumped(self) -> None:
        self.assertEqual(nb.VERSION, "0.2.6")


class BlankPaintTests(unittest.TestCase):
    def test_short_body_is_blank(self) -> None:
        page = FakePage(body="  \n  ", counts={nb.PIN_LINK: 0})
        out = nb.page_looks_blank(page)
        self.assertTrue(out["blank"])
        self.assertEqual(out["pins"], 0)

    def test_feed_with_pins_not_blank(self) -> None:
        page = FakePage(
            body="Home Feed ideas for you",
            counts={nb.PIN_LINK: 12},
        )
        out = nb.page_looks_blank(page)
        self.assertFalse(out["blank"])


class LoginGateTests(unittest.TestCase):
    def test_unauth_cta_log_in_sign_up(self) -> None:
        body = (
            "Welcome to Pinterest\n"
            "Log in\nSign up\n"
            "Find new ideas to try\n"
        )
        page = FakePage(
            body=body,
            counts={
                '[data-test-id="unauth-header"]': 1,
                '[data-test-id="simple-login-button"]': 1,
                '[data-test-id="simple-signup-button"]': 1,
                nb.PIN_LINK: 0,
            },
        )
        self.assertEqual(nb.detect_login_state(page), "not_logged_in")

    def test_marketing_wall_without_test_ids(self) -> None:
        # i10-021 class: Log in / Sign up visible, no pin links, no acct chrome
        body = (
            "Log in  Sign up\n"
            "When's your birthday?\n"
            "Continue\n"
            "Create a free account\n"
        )
        page = FakePage(body=body, counts={nb.PIN_LINK: 0})
        self.assertEqual(nb.detect_login_state(page), "not_logged_in")

    def test_logged_in_with_acct_chrome(self) -> None:
        page = FakePage(
            body="Home Search Create",
            counts={
                '[data-test-id="header-accounts-options-button"]': 1,
                nb.PIN_LINK: 8,
            },
        )
        self.assertEqual(nb.detect_login_state(page), "ok")

    def test_blank_defers_ok_for_recovery(self) -> None:
        # Blank must not hard-fail as not_logged_in before reload recovery.
        page = FakePage(body="", counts={nb.PIN_LINK: 0})
        self.assertEqual(nb.detect_login_state(page), "ok")
        self.assertTrue(nb.page_looks_blank(page)["blank"])

    def test_deactivated(self) -> None:
        page = FakePage(
            body="Your account has been deactivated. Contact support.",
            counts={nb.PIN_LINK: 0},
        )
        self.assertEqual(nb.detect_login_state(page), "account_deactivated")


class UseCasePickerDetectTests(unittest.TestCase):
    def test_test_id_picker(self) -> None:
        page = FakePage(
            body="What are you in the mood to do?",
            counts={nb.USE_CASE_PICKER: 1, nb.USE_CASE_TILE: 6},
        )
        self.assertTrue(nb.use_case_picker_visible(page))

    def test_text_fallback_mood(self) -> None:
        page = FakePage(
            body="What are you in the mood to do? Pick 3 or more to continue to your feed",
            counts={nb.PIN_LINK: 0},
        )
        self.assertTrue(nb.use_case_picker_visible(page))

    def test_absent(self) -> None:
        page = FakePage(
            body="Home ideas for you Saved",
            counts={nb.PIN_LINK: 20, '[data-test-id="header-profile"]': 1},
        )
        self.assertFalse(nb.use_case_picker_visible(page))


class RecoverEmptyFeedTests(unittest.TestCase):
    def test_reclassify_unauth_before_like_failed(self) -> None:
        body = "Log in\nSign up\nFind your next idea\n"
        page = FakePage(
            body=body,
            counts={
                '[data-test-id="simple-login-button"]': 1,
                '[data-test-id="simple-signup-button"]': 1,
                nb.PIN_LINK: 0,
            },
        )
        out = nb.recover_empty_feed(page, run_art=None)
        self.assertEqual(out["gate"], "not_logged_in")
        self.assertEqual(out["detail"], "not_logged_in")
        self.assertEqual(out["pin_count"], 0)

    def test_blank_reloads_once(self) -> None:
        page = FakePage(body="", counts={nb.PIN_LINK: 0})
        real_pause = nb.pause
        real_quiet = nb.quiet_window

        def fast_pause(page, lo, hi, label="", *, ambient=False):
            page.wait_for_timeout(1)
            return 1

        def fast_quiet(page):
            page.wait_for_timeout(1)
            return 1

        nb.pause = fast_pause  # type: ignore
        nb.quiet_window = fast_quiet  # type: ignore
        try:
            out = nb.recover_empty_feed(page, run_art=None)
        finally:
            nb.pause = real_pause  # type: ignore
            nb.quiet_window = real_quiet  # type: ignore
        self.assertEqual(page.reloads, 1)
        self.assertTrue(out.get("reloaded"))
        self.assertTrue(out.get("blank"))
        self.assertIn(out.get("detail"), {"blank_paint", "no_pin_links"})


if __name__ == "__main__":
    unittest.main()
