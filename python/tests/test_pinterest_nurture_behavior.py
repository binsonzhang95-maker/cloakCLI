"""Nurture 0.2.6 behavior helpers: linger, reverse scroll, close paths, browsed_ok."""
from __future__ import annotations

import importlib.util
import math
import random
import statistics
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BEHAVIOR = ROOT / "scripts" / "pinterest_nurture_behavior.py"
RUNNER = ROOT / "scripts" / "run_pinterest_nurture_browse.py"


def load_mod(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bh = load_mod(BEHAVIOR, "pinterest_nurture_behavior")


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

    def type(self, text: str, delay: int = 0) -> None:
        self.events.append(("type", text, delay))

    def press(self, key: str) -> None:
        self.events.append(("press", key))


class FakeLocator:
    def __init__(self, box: dict | None) -> None:
        self._box = box
        self.clicks = 0
        self.fills = 0
        self.presses: list[str] = []
        self.hovered = 0

    def bounding_box(self) -> dict | None:
        return self._box

    def click(self, **k: object) -> None:
        self.clicks += 1
        raise AssertionError("locator.click must not run when bounding_box exists")

    def hover(self, **k: object) -> None:
        self.hovered += 1

    def focus(self) -> None:
        pass

    def press(self, key: str) -> None:
        self.presses.append(key)

    def fill(self, text: str) -> None:
        self.fills += 1
        raise AssertionError("fill is forbidden for human fields")


class FakePage:
    def __init__(self) -> None:
        self.mouse = FakeMouse()
        self.keyboard = FakeKeyboard()
        self.waits: list[int] = []

    def wait_for_timeout(self, ms: int) -> None:
        self.waits.append(int(ms))


class PauseDistributionTests(unittest.TestCase):
    def test_samples_stay_in_range_and_are_not_uniform(self) -> None:
        rng = random.Random(7)
        xs = [bh.sample_pause_ms(500, 8000, rng=rng) for _ in range(400)]
        self.assertTrue(all(500 <= x <= 8000 for x in xs))
        self.assertGreater(len(set(xs)), 80)
        self.assertLess(statistics.median(xs), statistics.mean(xs))

    def test_gamma_key_delay_human_range(self) -> None:
        rng = random.Random(3)
        xs = [bh.sample_key_delay_ms(rng=rng) for _ in range(200)]
        self.assertTrue(all(45 <= x <= 240 for x in xs))
        self.assertGreater(statistics.mean(xs), 70)
        self.assertLess(statistics.mean(xs), 180)

    def test_quiet_window_is_about_one_to_two_seconds(self) -> None:
        rng = random.Random(11)
        xs = [bh.sample_quiet_window_ms(rng=rng) for _ in range(80)]
        self.assertTrue(all(1000 <= x <= 2300 for x in xs))

    def test_different_seeds_differ(self) -> None:
        a = [bh.sample_pause_ms(400, 4000, rng=random.Random(1)) for _ in range(40)]
        b = [bh.sample_pause_ms(400, 4000, rng=random.Random(2)) for _ in range(40)]
        self.assertNotEqual(a, b)


class PersonaTests(unittest.TestCase):
    def test_named_personas_and_auto(self) -> None:
        self.assertEqual(
            set(bh.PERSONA_NAMES),
            {"browse_only", "light_like", "deep_browse", "bounce_early"},
        )
        self.assertEqual(bh.choose_persona("browse_only"), "browse_only")
        picks = {bh.choose_persona("auto", rng=random.Random(i)) for i in range(40)}
        self.assertGreaterEqual(len(picks), 3)

    def test_browse_only_never_likes(self) -> None:
        plan = bh.plan_nurture_session(persona="browse_only", rng=random.Random(1))
        self.assertEqual(plan["like_prob"], 0.0)
        self.assertGreaterEqual(plan["pins"], 1)
        self.assertLessEqual(plan["pins"], 12)

    def test_like_prob_range_and_zero_likes_allowed(self) -> None:
        for name in ("light_like", "deep_browse", "bounce_early"):
            plan = bh.plan_nurture_session(persona=name, rng=random.Random(9))
            self.assertGreaterEqual(plan["like_prob"], 0.15)
            self.assertLessEqual(plan["like_prob"], 0.40)

    def test_pin_counts_and_min_max_sec(self) -> None:
        deep = bh.plan_nurture_session(
            persona="deep_browse", min_sec=120, max_sec=180, rng=random.Random(4)
        )
        bounce = bh.plan_nurture_session(
            persona="bounce_early", min_sec=120, max_sec=180, rng=random.Random(4)
        )
        self.assertGreaterEqual(deep["pins"], 6)
        self.assertLessEqual(deep["pins"], 12)
        self.assertLessEqual(bounce["pins"], 2)
        for plan in (deep, bounce):
            self.assertGreaterEqual(plan["target_sec"], 120)
            self.assertLessEqual(plan["target_sec"], 180)
        self.assertLessEqual(bounce["target_sec"], deep["target_sec"])
        locked = bh.plan_nurture_session(
            persona="deep_browse", pins=3, min_sec=10, max_sec=20, rng=random.Random(1)
        )
        self.assertEqual(locked["pins"], 3)
        auto = bh.plan_nurture_session(pins=0, min_sec=30, max_sec=90, rng=random.Random(8))
        self.assertGreaterEqual(auto["pins"], 0)
        self.assertLessEqual(auto["pins"], 12)
        locked0 = bh.plan_nurture_session(
            persona="deep_browse", pins=5, min_sec=10, max_sec=20, rng=random.Random(1)
        )
        self.assertEqual(locked0["pins"], 5)
        self.assertFalse(locked0.get("feed_only"))


class MousePathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.start = (40.0, 50.0)
        self.end = (620.0, 410.0)

    def test_continuous_many_steps_not_straight(self) -> None:
        rng = random.Random(21)
        path = bh.build_mouse_path(self.start, self.end, rng=rng)
        self.assertGreaterEqual(len(path), 24)
        self.assertLessEqual(bh.max_step_px(path), 36.0)
        self.assertTrue(bh.path_is_continuous(path, max_step=36.0))
        self.assertLess(math.hypot(path[0][0] - self.start[0], path[0][1] - self.start[1]), 6.0)
        self.assertLess(math.hypot(path[-1][0] - self.end[0], path[-1][1] - self.end[1]), 4.0)
        chord = math.hypot(self.end[0] - self.start[0], self.end[1] - self.start[1])
        self.assertGreater(bh.path_length(path) / chord, 1.08)
        self.assertGreater(bh.line_deviation_px(path, self.start, self.end), 8.0)
        self.assertTrue(bh.path_overshot(path, self.start, self.end))
        dwells = [p[2] for p in path]
        self.assertGreater(len(set(dwells)), 5)

    def test_randomness_every_run_differs(self) -> None:
        a = bh.build_mouse_path(self.start, self.end, rng=random.Random(1))
        b = bh.build_mouse_path(self.start, self.end, rng=random.Random(2))
        self.assertNotEqual([(round(p[0], 2), round(p[1], 2)) for p in a], [(round(p[0], 2), round(p[1], 2)) for p in b])
        same_a = bh.build_mouse_path(self.start, self.end, rng=random.Random(1))
        self.assertEqual(len(a), len(same_a))

    def test_ambient_drift_stays_near_origin(self) -> None:
        origin = (200.0, 180.0)
        drift = bh.build_ambient_drift(origin, rng=random.Random(5), n_moves=6)
        self.assertEqual(len(drift), 6)
        for x, y, d in drift:
            self.assertLessEqual(abs(x - origin[0]), 42.1)
            self.assertLessEqual(abs(y - origin[1]), 42.1)
            self.assertGreater(d, 0)

    def test_human_click_is_trail_then_down_up(self) -> None:
        page = FakePage()
        loc = FakeLocator({"x": 300.0, "y": 240.0, "width": 80.0, "height": 40.0})
        mouse = {"x": 12.0, "y": 18.0}
        result = bh.human_click_locator(page, loc, mouse, rng=random.Random(6))
        self.assertTrue(result["ok"])
        self.assertEqual(result["method"], "mouse_trail_down_up")
        kinds = [e[0] for e in page.mouse.events]
        self.assertNotIn("click", kinds)
        self.assertGreaterEqual(kinds.count("move"), 20)
        self.assertEqual(kinds.count("down"), 1)
        self.assertEqual(kinds.count("up"), 1)
        self.assertLess(kinds.index("down"), kinds.index("up"))
        moves = [e for e in page.mouse.events if e[0] == "move"]
        for i in range(1, len(moves)):
            step = math.hypot(moves[i][1] - moves[i - 1][1], moves[i][2] - moves[i - 1][2])
            self.assertLessEqual(step, 36.0)
        self.assertEqual(loc.clicks, 0)
        self.assertGreater(result["hover_ms"], 0)

    def test_human_click_never_falls_back_to_locator_click(self) -> None:
        class CountingLocator(FakeLocator):
            def click(self, **k: object) -> None:
                self.clicks += 1

        page_missing = FakePage()
        loc_missing = CountingLocator(None)
        missing = bh.human_click_locator(
            page_missing, loc_missing, {"x": 4.0, "y": 6.0}, rng=random.Random(1)
        )
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["method"], "no_box")
        self.assertEqual(loc_missing.clicks, 0)
        self.assertNotIn("click", [e[0] for e in page_missing.mouse.events])
        self.assertEqual([e[0] for e in page_missing.mouse.events].count("down"), 0)

        class BoomMouse(FakeMouse):
            def down(self) -> None:
                self.events.append(("down",))
                raise RuntimeError("pointer down failed")

        page_boom = FakePage()
        page_boom.mouse = BoomMouse()
        loc_boom = CountingLocator({"x": 120.0, "y": 80.0, "width": 48.0, "height": 22.0})
        boom = bh.human_click_locator(
            page_boom, loc_boom, {"x": 8.0, "y": 10.0}, rng=random.Random(2)
        )
        self.assertFalse(boom["ok"])
        self.assertEqual(boom["method"], "mouse_down_up_failed")
        self.assertEqual(loc_boom.clicks, 0)
        kinds = [e[0] for e in page_boom.mouse.events]
        self.assertNotIn("click", kinds)
        self.assertGreaterEqual(kinds.count("move"), 1)
        self.assertEqual(kinds.count("down"), 1)
        self.assertEqual(kinds.count("up"), 0)

    def test_human_type_never_fills(self) -> None:
        page = FakePage()
        loc = FakeLocator({"x": 10.0, "y": 10.0, "width": 120.0, "height": 24.0})
        out = bh.human_type_text(page, loc, "Nora", mouse={"x": 1.0, "y": 1.0}, rng=random.Random(2))
        self.assertFalse(out["used_fill"])
        self.assertEqual(loc.fills, 0)
        typed = [e[1] for e in page.keyboard.events if e[0] == "type"]
        self.assertEqual("".join(ch for ch in typed if len(ch) == 1)[-4:], "Nora")
        self.assertGreaterEqual(out["typed"], 4)

    def test_human_type_skips_keys_when_focus_fails(self) -> None:
        class CountingLocator(FakeLocator):
            def click(self, **k: object) -> None:
                self.clicks += 1

        page = FakePage()
        loc = CountingLocator(None)
        out = bh.human_type_text(
            page, loc, "Nora", mouse={"x": 1.0, "y": 1.0}, rng=random.Random(1)
        )
        self.assertFalse(out.get("focus_ok"))
        self.assertEqual(out["typed"], 0)
        self.assertEqual(loc.clicks, 0)
        self.assertFalse(page.keyboard.events)


class ScrollTests(unittest.TestCase):
    def test_inertial_decay_pause_reverse(self) -> None:
        plan = bh.inertial_scroll_plan(rng=random.Random(12), direction=1)
        zeros = [i for i, (dy, _w) in enumerate(plan) if dy == 0]
        self.assertTrue(zeros)
        pause_i = zeros[0]
        head = plan[:pause_i]
        tail = plan[pause_i + 1 :]
        self.assertGreaterEqual(len(head), 4)
        self.assertTrue(all(dy > -40 for dy, _w in head[:3]))
        first = statistics.mean(abs(dy) for dy, _w in head[:3])
        last = statistics.mean(abs(dy) for dy, _w in head[-3:])
        self.assertGreater(first, last)
        self.assertTrue(any(dy < 0 for dy, _w in tail))
        self.assertTrue(all(w > 0 for _dy, w in plan))

    def test_inertial_scroll_emits_many_wheel_events(self) -> None:
        page = FakePage()
        plan = bh.inertial_scroll(page, rng=random.Random(3), direction=1)
        wheels = [e for e in page.mouse.events if e[0] == "wheel"]
        self.assertGreaterEqual(len(wheels), 4)
        self.assertEqual(len(wheels), sum(1 for dy, _w in plan if dy))
        self.assertNotEqual(len({e[2] for e in wheels}), 1)

    def test_scroll_plans_differ_by_seed(self) -> None:
        a = bh.inertial_scroll_plan(rng=random.Random(1))
        b = bh.inertial_scroll_plan(rng=random.Random(2))
        self.assertNotEqual(a, b)


class RunnerCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        parent = str(RUNNER.parent)
        if parent not in sys.path:
            sys.path.insert(0, parent)
        cls.runner = load_mod(RUNNER, "run_pinterest_nurture_browse")

    def test_default_headed_and_persona_pins(self) -> None:
        self.assertEqual(self.runner.VERSION, "0.2.6")
        ns = self.runner.build_arg_parser().parse_args(["--profile", "geo46"])
        self.assertFalse(ns.headless)
        self.assertEqual(ns.pins, 0)
        self.assertEqual(ns.persona, "auto")
        ns_h = self.runner.build_arg_parser().parse_args(
            ["--profile", "geo46", "--headless", "--pins", "4", "--persona", "browse_only"]
        )
        self.assertTrue(ns_h.headless)
        self.assertEqual(ns_h.pins, 4)
        self.assertEqual(ns_h.persona, "browse_only")

    def test_dual_copies_and_manifest_version(self) -> None:
        import json

        skill_scripts = ROOT / "skills/pinterest-nurture-browse/scripts"
        self.assertEqual(
            (ROOT / "scripts/run_pinterest_nurture_browse.py").read_bytes(),
            (skill_scripts / "run_pinterest_nurture_browse.py").read_bytes(),
        )
        self.assertEqual(
            (ROOT / "scripts/pinterest_nurture_behavior.py").read_bytes(),
            (skill_scripts / "pinterest_nurture_behavior.py").read_bytes(),
        )
        manifest = json.loads(
            (ROOT / "skills/pinterest-nurture-browse/manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["version"], "0.2.6")
        self.assertEqual(manifest["entry"]["path"], "scripts/run_pinterest_nurture_browse.py")
        behavior_src = (ROOT / "scripts/pinterest_nurture_behavior.py").read_text(encoding="utf-8")
        click_fn = behavior_src.split("def human_click_locator", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn("loc.click", click_fn)
        type_fn = behavior_src.split("def human_type_text", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn("loc.click", type_fn)
        runner_src = (ROOT / "scripts/run_pinterest_nurture_browse.py").read_text(encoding="utf-8")
        self.assertNotIn("force=True", runner_src)
        self.assertNotIn(".click(timeout=5000, force=True)", runner_src)



class HangBeforeCloseTests(unittest.TestCase):
    def test_samples_clamped_to_default_band(self) -> None:
        import os

        prev = os.environ.pop(bh.HANG_BEFORE_CLOSE_ENV, None)
        try:
            lo = bh.HANG_BEFORE_CLOSE_LO_MS
            hi = bh.HANG_BEFORE_CLOSE_HI_MS
            samples = [
                bh.resolve_hang_before_close_ms(rng=random.Random(i))
                for i in range(80)
            ]
            self.assertTrue(all(lo <= s <= hi for s in samples))
            self.assertGreater(len(set(samples)), 5)
        finally:
            if prev is not None:
                os.environ[bh.HANG_BEFORE_CLOSE_ENV] = prev

    def test_env_override_zero_skips(self) -> None:
        import os

        prev = os.environ.get(bh.HANG_BEFORE_CLOSE_ENV)
        os.environ[bh.HANG_BEFORE_CLOSE_ENV] = "0"
        try:
            self.assertEqual(bh.resolve_hang_before_close_ms(rng=random.Random(1)), 0)
            page = FakePage()
            mouse = {"x": 10.0, "y": 20.0}
            logs: list[dict] = []
            spent = bh.hang_before_close(page, mouse, rng=random.Random(1), log_fn=logs.append)
            self.assertEqual(spent, 0)
            self.assertEqual(logs[0]["status"], "hang_before_close")
            self.assertEqual(logs[0]["ms"], 0)
            self.assertFalse(page.mouse.events)
        finally:
            if prev is None:
                os.environ.pop(bh.HANG_BEFORE_CLOSE_ENV, None)
            else:
                os.environ[bh.HANG_BEFORE_CLOSE_ENV] = prev

    def test_hang_runs_ambient_drift_within_budget(self) -> None:
        import os

        prev = os.environ.get(bh.HANG_BEFORE_CLOSE_ENV)
        os.environ[bh.HANG_BEFORE_CLOSE_ENV] = "1200"
        try:
            page = FakePage()
            mouse = {"x": 40.0, "y": 50.0}
            logs: list[dict] = []
            spent = bh.hang_before_close(page, mouse, rng=random.Random(9), log_fn=logs.append)
            self.assertGreaterEqual(spent, 1000)
            self.assertLessEqual(spent, 2000)
            self.assertTrue(any(e[0] == "move" for e in page.mouse.events))
            self.assertTrue(page.waits)
            self.assertEqual(logs[0]["ms"], 1200)
            self.assertEqual(logs[-1]["status"], "hang_before_close_done")
        finally:
            if prev is None:
                os.environ.pop(bh.HANG_BEFORE_CLOSE_ENV, None)
            else:
                os.environ[bh.HANG_BEFORE_CLOSE_ENV] = prev

    def test_runners_wire_hang_before_close(self) -> None:
        nurture_src = (ROOT / "scripts/run_pinterest_nurture_browse.py").read_text(encoding="utf-8")
        outlook_src = (ROOT / "scripts/run_pinterest_register_outlook_verify.py").read_text(encoding="utf-8")
        visual_src = (ROOT / "scripts/run_pinterest_register_visual_mm.py").read_text(encoding="utf-8")
        for src, label in (
            (nurture_src, "nurture"),
            (outlook_src, "outlook"),
            (visual_src, "visual"),
        ):
            self.assertIn("hang_before_close(", src, msg=label)
            self.assertIn("hang_before_close", src, msg=label)



class LingerPlanTests(unittest.TestCase):
    def test_like_not_immediate_mid_late_frac(self) -> None:
        for seed in range(40):
            plan = bh.plan_pin_linger(
                persona="deep_browse", bounce=False, rng=random.Random(seed)
            )
            self.assertGreaterEqual(plan["like_frac"], 0.60)
            self.assertLessEqual(plan["like_frac"], 0.85)
            self.assertGreaterEqual(plan["like_at_ms"], int(plan["total_ms"] * 0.60) - 1)
            self.assertGreaterEqual(plan["gaze_ms"], 800)
            # Gaze completes before like_at in the intended ordering.
            self.assertLess(plan["gaze_ms"], plan["like_at_ms"])

    def test_bounce_shorter_band(self) -> None:
        xs = [
            bh.plan_pin_linger(persona="bounce_early", bounce=True, rng=random.Random(i))[
                "total_ms"
            ]
            for i in range(60)
        ]
        self.assertTrue(all(2000 <= x <= 4500 for x in xs))

    def test_independent_resamples_differ(self) -> None:
        a = [bh.plan_pin_linger(persona="light_like", rng=random.Random(1)) for _ in range(5)]
        # Same seed sequence for plan_pin_linger alone is deterministic per call with new rng
        b = bh.plan_pin_linger(persona="light_like", rng=random.Random(2))
        self.assertNotEqual(a[0]["total_ms"], b["total_ms"])


class ReverseScrollTests(unittest.TestCase):
    def test_reverse_bounds_and_cooldown(self) -> None:
        page = FakePage()
        logs: list[dict] = []
        # Force reverse by patching probability via high persona + many trials
        did = 0
        mags = []
        last = None
        for i in range(80):
            # Use deep_browse (~18%); inject large down magnitude
            out = bh.maybe_micro_reverse_scroll(
                page,
                800,
                persona="deep_browse",
                last_flip_mono=last,
                rng=random.Random(i),
                log_fn=logs.append,
            )
            if out.get("did"):
                did += 1
                mags.append(out["reverse_px"])
                self.assertGreaterEqual(out["reverse_px"], int(800 * 0.20) - 1)
                self.assertLessEqual(out["reverse_px"], int(800 * 0.45) + 1)
                last = out.get("last_flip_mono")
                # Immediate retry should hit cooldown
                skip = bh.maybe_micro_reverse_scroll(
                    page,
                    800,
                    persona="deep_browse",
                    last_flip_mono=last,
                    rng=random.Random(999),
                    log_fn=logs.append,
                )
                self.assertFalse(skip.get("did"))
                self.assertEqual(skip.get("skipped"), "flip_cooldown")
                last = None  # reset for next outer trial diversity
        self.assertGreater(did, 5)
        self.assertTrue(mags)

    def test_down_magnitude_helper(self) -> None:
        plan = [(100, 10), (-20, 10), (50, 10), (0, 200)]
        self.assertEqual(bh.scroll_plan_down_magnitude(plan), 150)


class ClosePathTests(unittest.TestCase):
    def test_weights_smoke(self) -> None:
        from collections import Counter

        c = Counter(bh.choose_close_path(rng=random.Random(i)) for i in range(2000))
        self.assertGreater(c["button"], c["escape"])
        self.assertGreater(c["escape"], c["history_back"])
        self.assertGreater(c["history_back"], c["backdrop"])
        self.assertEqual(set(c), {"button", "escape", "history_back", "backdrop"})
        order = bh.close_path_fallback_order("escape", rng=random.Random(3))
        self.assertEqual(order[0], "escape")
        self.assertEqual(len(order), 4)
        self.assertEqual(len(set(order)), 4)


class BrowsedOkGateTests(unittest.TestCase):
    def test_pin_open_or_feed_mins(self) -> None:
        self.assertTrue(
            bh.browsed_ok(pins_opened=1, feed_dwell_sec=0, scroll_distance_px=0)
        )
        self.assertTrue(
            bh.browsed_ok(pins_opened=0, feed_dwell_sec=25, scroll_distance_px=1500)
        )
        self.assertTrue(
            bh.browsed_ok(pins_opened=0, feed_dwell_sec=40, scroll_distance_px=2000)
        )
        self.assertFalse(
            bh.browsed_ok(pins_opened=0, feed_dwell_sec=24, scroll_distance_px=5000)
        )
        self.assertFalse(
            bh.browsed_ok(pins_opened=0, feed_dwell_sec=60, scroll_distance_px=1499)
        )

    def test_zero_pin_plan_share_and_fields(self) -> None:
        zeros = 0
        for i in range(400):
            plan = bh.plan_nurture_session(pins=0, rng=random.Random(i))
            if plan["pins"] == 0:
                zeros += 1
                self.assertTrue(plan["feed_only"])
                self.assertIn(plan["persona"], {"bounce_early", "browse_only"})
                self.assertGreaterEqual(plan["feed_dwell_sec"], 25)
                self.assertLessEqual(plan["feed_dwell_sec"], 90)
                self.assertGreaterEqual(plan["feed_scroll_screens"], 3)
                self.assertLessEqual(plan["feed_scroll_screens"], 7)
                self.assertEqual(plan["like_prob"], 0.0)
        rate = zeros / 400
        self.assertGreaterEqual(rate, 0.10)
        self.assertLessEqual(rate, 0.30)


class VisibilityKeepaliveTests(unittest.TestCase):
    def test_logs_without_acting_when_focused(self) -> None:
        class VisPage(FakePage):
            def evaluate(self, script: str) -> dict:
                return {"visibilityState": "visible", "hasFocus": True}

            def bring_to_front(self) -> None:
                raise AssertionError("should not bring_to_front when visible+focused")

        logs: list[dict] = []
        out = bh.ensure_page_visible(VisPage(), rng=random.Random(1), log_fn=logs.append)
        self.assertFalse(out["acted"])
        self.assertEqual(logs[0]["event"], "visibility_keepalive")

    def test_acts_when_hidden(self) -> None:
        class VisPage(FakePage):
            def __init__(self) -> None:
                super().__init__()
                self.brought = 0
                self._n = 0

            def evaluate(self, script: str):
                self._n += 1
                if "window.focus" in script or "visibilitychange" in script:
                    return None
                if self._n == 1:
                    return {"visibilityState": "hidden", "hasFocus": False}
                return {"visibilityState": "visible", "hasFocus": True}

            def bring_to_front(self) -> None:
                self.brought += 1

        page = VisPage()
        logs: list[dict] = []
        out = bh.ensure_page_visible(page, rng=random.Random(2), log_fn=logs.append)
        self.assertTrue(out["acted"])
        self.assertEqual(page.brought, 1)
        self.assertTrue(page.waits)



if __name__ == "__main__":
    unittest.main()
