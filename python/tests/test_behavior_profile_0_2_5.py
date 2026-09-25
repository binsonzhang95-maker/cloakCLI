"""Nurture 0.2.5 P0 — BehaviorProfile persistence, split RNG, idle-off, budgets."""
from __future__ import annotations

import importlib.util
import json
import os
import random
import statistics
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))
sys.path.insert(0, str(ROOT / "scripts"))

from cloakcli_worker.behavior_profile import (  # noqa: E402
    GENERATOR_VERSION,
    BehaviorProfile,
    BehaviorProfileError,
    ProfileSessionLock,
    SessionBudget,
    bump_session_seq,
    effective_params_hash,
    ensure_behavior_profile,
    idle_wander_enabled,
    make_behavior_streams,
    resolve_behavior_config,
)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # required before @dataclass exec
    spec.loader.exec_module(mod)
    return mod


bh = _load(ROOT / "scripts" / "pinterest_nurture_behavior.py", "pinterest_nurture_behavior_025")


class BehaviorProfilePersistenceTests(unittest.TestCase):
    def test_ensure_stable_across_restart(self) -> None:
        td = Path(tempfile.mkdtemp(prefix="bp_stable_"))
        meta = td / "profile.json"
        meta.write_text(
            json.dumps({"name": "a", "fingerprint_seed": 42424, "notes": "keep"}) + "\n",
            encoding="utf-8",
        )
        p1 = ensure_behavior_profile(meta)
        p2 = ensure_behavior_profile(meta)
        self.assertEqual(p1, p2)
        self.assertEqual(p1.behavior_seed, p2.behavior_seed)
        self.assertEqual(effective_params_hash(p1), effective_params_hash(p2))
        data = json.loads(meta.read_text(encoding="utf-8"))
        self.assertEqual(data["fingerprint_seed"], 42424)
        self.assertEqual(data["notes"], "keep")
        self.assertIn("behavior_profile", data)

    def test_two_profiles_can_differ_fingerprint_unchanged(self) -> None:
        td = Path(tempfile.mkdtemp(prefix="bp_two_"))
        a = td / "a" / "profile.json"
        b = td / "b" / "profile.json"
        a.parent.mkdir(); b.parent.mkdir()
        a.write_text(json.dumps({"name": "a", "fingerprint_seed": 11111}) + "\n", encoding="utf-8")
        b.write_text(json.dumps({"name": "b", "fingerprint_seed": 11111}) + "\n", encoding="utf-8")
        pa = ensure_behavior_profile(a, rng=random.Random(1))
        pb = ensure_behavior_profile(b, rng=random.Random(2))
        self.assertEqual(json.loads(a.read_text())["fingerprint_seed"], 11111)
        self.assertEqual(json.loads(b.read_text())["fingerprint_seed"], 11111)
        # Different mint rng => different behavior seeds/params (almost surely)
        self.assertTrue(
            pa.behavior_seed != pb.behavior_seed
            or effective_params_hash(pa) != effective_params_hash(pb)
        )

    def test_effective_hash_ignores_session_seq(self) -> None:
        td = Path(tempfile.mkdtemp(prefix="bp_hash_"))
        meta = td / "profile.json"
        meta.write_text(json.dumps({"name": "h"}) + "\n", encoding="utf-8")
        p0 = ensure_behavior_profile(meta, rng=random.Random(9))
        h0 = effective_params_hash(p0)
        p1 = bump_session_seq(meta)
        self.assertEqual(p1.session_seq, p0.session_seq + 1)
        self.assertEqual(effective_params_hash(p1), h0)


class SplitRngTests(unittest.TestCase):
    def test_profile_a_actions_do_not_affect_b(self) -> None:
        sa = make_behavior_streams(101, 1)
        sb = make_behavior_streams(202, 1)
        # Drain A heavily
        for _ in range(50):
            sa.pause_rng.random()
            sa.session_rng.random()
        b_before = [sb.pause_rng.random() for _ in range(10)]
        sb2 = make_behavior_streams(202, 1)
        b_fresh = [sb2.pause_rng.random() for _ in range(10)]
        self.assertEqual(b_before, b_fresh)

    def test_session_seq_changes_prefix(self) -> None:
        s0 = make_behavior_streams(55, 0)
        s1 = make_behavior_streams(55, 1)
        a = [s0.pause_rng.random() for _ in range(8)]
        b = [s1.pause_rng.random() for _ in range(8)]
        self.assertNotEqual(a, b)

    def test_pause_and_scroll_streams_independent(self) -> None:
        s = make_behavior_streams(7, 3)
        # Same number of draws from each — sequences differ (different streams)
        p = [s.pause_rng.random() for _ in range(20)]
        s2 = make_behavior_streams(7, 3)
        sc = [s2.scroll_rng.random() for _ in range(20)]
        self.assertNotEqual(p, sc)


class ParamScaleTests(unittest.TestCase):
    def test_pause_scale_shifts_samples(self) -> None:
        from dataclasses import dataclass

        @dataclass
        class Cfg:
            pause_scale: float
            pause_dispersion: float = 1.0
            scroll_step_scale: float = 1.0
            scroll_decay: float = 1.0

            def optional_weight(self, key: str, default: float = 1.0) -> float:
                return default

        bh.set_behavior_context(None)
        base = [
            bh.sample_pause_ms(400, 6000, rng=random.Random(i)) for i in range(60)
        ]
        bh.set_behavior_context(
            bh.BehaviorContext(config=Cfg(pause_scale=1.35), idle_wander_enabled=False)
        )
        scaled = [
            bh.sample_pause_ms(400, 6000, rng=random.Random(i)) for i in range(60)
        ]
        bh.set_behavior_context(None)
        self.assertGreater(statistics.mean(scaled), statistics.mean(base) * 1.05)

    def test_scroll_step_scale_shifts_plan_magnitude(self) -> None:
        from dataclasses import dataclass

        @dataclass
        class Cfg:
            pause_scale: float = 1.0
            pause_dispersion: float = 1.0
            scroll_step_scale: float = 1.0
            scroll_decay: float = 1.0

            def optional_weight(self, key: str, default: float = 1.0) -> float:
                return default

        def mag(plan):
            return sum(abs(dy) for dy, _w in plan if dy)

        bh.set_behavior_context(bh.BehaviorContext(config=Cfg(scroll_step_scale=1.0)))
        base = [
            mag(bh.inertial_scroll_plan(rng=random.Random(i), direction=1))
            for i in range(40)
        ]
        bh.set_behavior_context(bh.BehaviorContext(config=Cfg(scroll_step_scale=1.25)))
        big = [
            mag(bh.inertial_scroll_plan(rng=random.Random(i), direction=1))
            for i in range(40)
        ]
        bh.set_behavior_context(None)
        self.assertGreater(statistics.mean(big), statistics.mean(base) * 1.05)


class IdleDefaultOffTests(unittest.TestCase):
    def test_env_and_flag_default_off(self) -> None:
        prev = os.environ.pop("CLOAKCLI_IDLE_WANDER", None)
        try:
            self.assertFalse(idle_wander_enabled())
            self.assertFalse(idle_wander_enabled(flag=False))
            self.assertTrue(idle_wander_enabled(flag=True))
            self.assertTrue(idle_wander_enabled(env={"CLOAKCLI_IDLE_WANDER": "1"}))
        finally:
            if prev is not None:
                os.environ["CLOAKCLI_IDLE_WANDER"] = prev

    def test_idle_fill_is_quiet_when_disabled(self) -> None:
        class Page:
            def __init__(self) -> None:
                self.waits: list[int] = []
                self.mouse = type("M", (), {"move": lambda *a, **k: None})()

            def wait_for_timeout(self, ms: int) -> None:
                self.waits.append(int(ms))

        bh.set_behavior_context(bh.BehaviorContext(idle_wander_enabled=False))
        page = Page()
        mouse = {"x": 10.0, "y": 20.0}
        st = bh.IdleWanderState(rng=random.Random(1))
        spent = bh.idle_wander_fill(page, mouse, st, budget_ms=1500, rng=random.Random(1))
        bh.set_behavior_context(None)
        self.assertEqual(spent, 1500)
        self.assertEqual(sum(page.waits), 1500)
        self.assertEqual(st.small_count + st.large_count, 0)


class BudgetTerminateTests(unittest.TestCase):
    def test_max_actions_sets_end_reason(self) -> None:
        budget = SessionBudget(
            max_session_elapsed_sec=9999, max_actions=3, max_state_visits=100
        )
        self.assertIsNone(budget.check())
        self.assertIsNone(budget.record_action())  # 1
        self.assertIsNone(budget.record_action())  # 2
        self.assertIsNone(budget.record_action())  # 3 allowed
        self.assertEqual(budget.actions, 3)
        self.assertEqual(budget.record_action(), "budget_actions")  # 4th rejected
        self.assertEqual(budget.actions, 3)  # rejected does not consume
        self.assertEqual(budget.end_reason, "budget_actions")

    def test_elapsed_budget(self) -> None:
        budget = SessionBudget(
            max_session_elapsed_sec=0.01, max_actions=100, max_state_visits=100
        )
        time.sleep(0.03)
        self.assertEqual(budget.check(), "budget_elapsed")

    def test_profile_lock_mutual_exclusion(self) -> None:
        td = Path(tempfile.mkdtemp(prefix="bp_lock_"))
        meta = td / "profile.json"
        meta.write_text("{}\n", encoding="utf-8")
        lock1 = ProfileSessionLock(meta)
        lock2 = ProfileSessionLock(meta)
        self.assertTrue(lock1.acquire(blocking=False))
        self.assertFalse(lock2.acquire(blocking=False))
        lock1.release()
        self.assertTrue(lock2.acquire(blocking=False))
        lock2.release()


class VersionAndMirrorTests(unittest.TestCase):
    def test_runner_version_0_2_5(self) -> None:
        runner = _load(
            ROOT / "scripts" / "run_pinterest_nurture_browse.py",
            "run_pinterest_nurture_browse_025",
        )
        self.assertEqual(runner.VERSION, "0.2.5")
        ns = runner.build_arg_parser().parse_args(["--profile", "geo46"])
        self.assertFalse(getattr(ns, "idle_wander", True))
        ns2 = runner.build_arg_parser().parse_args(
            ["--profile", "geo46", "--idle-wander"]
        )
        self.assertTrue(ns2.idle_wander)

    def test_skill_mirror_and_manifest(self) -> None:
        skill = ROOT / "skills" / "pinterest-nurture-browse"
        self.assertEqual(
            (ROOT / "scripts" / "run_pinterest_nurture_browse.py").read_bytes(),
            (skill / "scripts" / "run_pinterest_nurture_browse.py").read_bytes(),
        )
        self.assertEqual(
            (ROOT / "scripts" / "pinterest_nurture_behavior.py").read_bytes(),
            (skill / "scripts" / "pinterest_nurture_behavior.py").read_bytes(),
        )
        manifest = json.loads((skill / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "0.2.5")
        self.assertEqual(GENERATOR_VERSION, "0.2.5")



class BudgetPermitSemanticsTests(unittest.TestCase):
    """max_actions=N / max_state_visits=N must allow N real executions."""

    def test_max_actions_1_and_2_execution_counts(self) -> None:
        for n in (1, 2):
            budget = SessionBudget(
                max_session_elapsed_sec=9999, max_actions=n, max_state_visits=100
            )
            bh.set_behavior_context(bh.BehaviorContext(budget=budget))
            executed = 0
            for _ in range(n + 3):
                if bh.budget_allows(action=True):
                    executed += 1
                else:
                    break
            bh.set_behavior_context(None)
            self.assertEqual(executed, n, f"max_actions={n}")
            self.assertEqual(budget.actions, n)
            self.assertEqual(budget.end_reason, "budget_actions")

    def test_max_state_visits_1_and_2_execution_counts(self) -> None:
        for n in (1, 2):
            budget = SessionBudget(
                max_session_elapsed_sec=9999, max_actions=100, max_state_visits=n
            )
            bh.set_behavior_context(bh.BehaviorContext(budget=budget))
            executed = 0
            for i in range(n + 3):
                if bh.budget_allows(state=f"s{i}"):
                    executed += 1
                else:
                    break
            bh.set_behavior_context(None)
            self.assertEqual(executed, n, f"max_state_visits={n}")
            self.assertEqual(sum(budget.state_visits.values()), n)
            self.assertEqual(budget.end_reason, "budget_state_visits")

    def test_state_cap_rejects_without_counting(self) -> None:
        budget = SessionBudget(
            max_session_elapsed_sec=9999, max_actions=100, max_state_visits=100
        )
        self.assertTrue(budget.visit_state("hover", cap=1))
        self.assertFalse(budget.visit_state("hover", cap=1))
        self.assertEqual(budget.state_visits.get("hover"), 1)


class CorruptProfilePreservedTests(unittest.TestCase):
    def test_corrupt_json_leaves_file_unchanged(self) -> None:
        td = Path(tempfile.mkdtemp(prefix="bp_corrupt_"))
        meta = td / "profile.json"
        raw = '{"name":"x","fingerprint_seed":77777,NOT_JSON'
        meta.write_text(raw, encoding="utf-8")
        with self.assertRaises(BehaviorProfileError):
            ensure_behavior_profile(meta)
        self.assertEqual(meta.read_text(encoding="utf-8"), raw)
        with self.assertRaises(BehaviorProfileError):
            bump_session_seq(meta)
        self.assertEqual(meta.read_text(encoding="utf-8"), raw)

    def test_non_object_json_leaves_file_unchanged(self) -> None:
        td = Path(tempfile.mkdtemp(prefix="bp_nonobj_"))
        meta = td / "profile.json"
        raw = "[1,2,3]\n"
        meta.write_text(raw, encoding="utf-8")
        with self.assertRaises(BehaviorProfileError):
            ensure_behavior_profile(meta)
        self.assertEqual(meta.read_text(encoding="utf-8"), raw)


class IdleRngIsolationTests(unittest.TestCase):
    def test_idle_scheduling_does_not_consume_session_or_pause_streams(self) -> None:
        streams = make_behavior_streams(4242, 7)
        bh.set_behavior_context(
            bh.BehaviorContext(
                streams=streams,
                idle_wander_enabled=True,
                budget=SessionBudget(
                    max_session_elapsed_sec=9999, max_actions=100, max_state_visits=100
                ),
            )
        )

        class Page:
            def __init__(self) -> None:
                self.mouse = type("M", (), {"move": lambda *a, **k: None})()

            def wait_for_timeout(self, ms: int) -> None:
                return None

        # Snapshot sequences before idle drain
        pause_before = [streams.pause_rng.random() for _ in range(5)]
        # Rebuild fresh streams for comparison baseline
        streams_a = make_behavior_streams(4242, 7)
        streams_b = make_behavior_streams(4242, 7)
        bh.set_behavior_context(
            bh.BehaviorContext(
                streams=streams_a,
                idle_wander_enabled=True,
                budget=SessionBudget(
                    max_session_elapsed_sec=9999, max_actions=100, max_state_visits=100
                ),
            )
        )
        # Drain idle scheduling heavily on A
        st = bh.IdleWanderState(rng=streams_a.optional_rng, now_ms=0.0)
        page = Page()
        mouse = {"x": 100.0, "y": 100.0}
        for _ in range(30):
            bh.idle_wander_tick(
                page, mouse, st, now_ms=float(st.next_small_at_ms), remain_ms=5000, rng=None
            )
            st.schedule_next_small(st.next_small_at_ms)
        # Session/pause/scroll draws from A after idle must match fresh B (idle used optional only)
        sess_a = [streams_a.session_rng.random() for _ in range(12)]
        pause_a = [streams_a.pause_rng.random() for _ in range(12)]
        scroll_a = [streams_a.scroll_rng.random() for _ in range(12)]
        sess_b = [streams_b.session_rng.random() for _ in range(12)]
        pause_b = [streams_b.pause_rng.random() for _ in range(12)]
        scroll_b = [streams_b.scroll_rng.random() for _ in range(12)]
        bh.set_behavior_context(None)
        self.assertEqual(sess_a, sess_b)
        self.assertEqual(pause_a, pause_b)
        self.assertEqual(scroll_a, scroll_b)
        # Silence unused
        self.assertEqual(len(pause_before), 5)

    def test_no_tremor_gauss_in_idle_circle_source(self) -> None:
        src = (ROOT / "scripts" / "pinterest_nurture_behavior.py").read_text(encoding="utf-8")
        # Narrow: build_idle_circle_wander body must not contain gauss jitter / overshoot promo
        start = src.index("def build_idle_circle_wander")
        end = src.index("\ndef _path_play_budget_ms", start)
        body = src[start:end]
        self.assertNotIn("rng.gauss", body)
        self.assertNotIn("jamp", body)
        self.assertNotIn("with tremor", body.lower())
        self.assertNotIn("overshoot then correct", body.lower())
        self.assertNotIn("human-like", body.lower())
        # build_mouse_path must not reintroduce overshoot waypoints / gauss tremor
        m_start = src.index("def build_mouse_path")
        m_end = src.index("\ndef max_step_px", m_start)
        m_body = src[m_start:m_end]
        self.assertNotIn("overshoot_px", m_body)
        self.assertNotIn("rng.gauss", m_body)
        self.assertNotIn("jamp", m_body)


class SessionWaitBudgetTests(unittest.TestCase):
    def test_idle_fill_disabled_clips_to_session_remaining(self) -> None:
        class Clock:
            def __init__(self) -> None:
                self.t = 1000.0

            def __call__(self) -> float:
                return self.t

        clock = Clock()
        budget = SessionBudget(
            max_session_elapsed_sec=1.0,
            max_actions=100,
            max_state_visits=100,
            t0=1000.0,
            _clock=clock,
        )
        # Already near exhaust: 0.2s left
        clock.t = 1000.8
        bh.set_behavior_context(bh.BehaviorContext(budget=budget, idle_wander_enabled=False))

        class Page:
            def __init__(self) -> None:
                self.waits: list[int] = []

            def wait_for_timeout(self, ms: int) -> None:
                self.waits.append(int(ms))

        page = Page()
        st = bh.IdleWanderState(rng=random.Random(1))
        spent = bh.idle_wander_fill(page, {"x": 0.0, "y": 0.0}, st, budget_ms=5000)
        # clip to ~200ms remaining
        self.assertLessEqual(spent, 250)
        self.assertGreater(spent, 0)
        self.assertEqual(sum(page.waits), spent)
        bh.set_behavior_context(None)

    def test_hang_skips_when_session_exhausted(self) -> None:
        class Clock:
            def __init__(self) -> None:
                self.t = 0.0

            def __call__(self) -> float:
                return self.t

        clock = Clock()
        budget = SessionBudget(
            max_session_elapsed_sec=0.01,
            max_actions=100,
            max_state_visits=100,
            t0=0.0,
            _clock=clock,
        )
        clock.t = 1.0  # exhausted
        bh.set_behavior_context(bh.BehaviorContext(budget=budget, idle_wander_enabled=False))
        prev = os.environ.get("CLOAKCLI_HANG_BEFORE_CLOSE_MS")
        os.environ["CLOAKCLI_HANG_BEFORE_CLOSE_MS"] = "5000"

        class Page:
            def __init__(self) -> None:
                self.waits: list[int] = []

            def wait_for_timeout(self, ms: int) -> None:
                self.waits.append(int(ms))

        try:
            elapsed = bh.hang_before_close(Page(), {"x": 0.0, "y": 0.0}, rng=random.Random(1))
            self.assertEqual(elapsed, 0)
        finally:
            if prev is None:
                os.environ.pop("CLOAKCLI_HANG_BEFORE_CLOSE_MS", None)
            else:
                os.environ["CLOAKCLI_HANG_BEFORE_CLOSE_MS"] = prev
            bh.set_behavior_context(None)



if __name__ == "__main__":
    unittest.main()
