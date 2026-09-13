"""Teach Chat M3: local DOM → Playwright normalize."""

from __future__ import annotations

import unittest

from cloakcli_worker.normalize import (
    is_raw_dom_event,
    normalize_events,
    pick_selector,
)
from cloakcli_worker.timeline import Timeline, merge_timelines

from fakes import FakePage


def ev_click(**kw):
    base = {
        "kind": "click",
        "url": "https://example.com/app",
        "origin": "https://example.com",
        "tag": "button",
        "frame": "main",
        "ts": "2026-09-13T12:00:00Z",
    }
    base.update(kw)
    return base


class SelectorPriorityTests(unittest.TestCase):
    def test_testid_beats_css_and_id(self):
        ev = ev_click(
            selector="div > button:nth-of-type(3)",
            selector_candidates={
                "id": "#go",
                "testid": '[data-testid="submit"]',
                "css_path": "form > button:nth-of-type(1)",
            },
            candidate_unique={"testid": True, "id": True, "css": True},
        )
        sel, strategy, conf, fail = pick_selector(ev)
        self.assertEqual(sel, '[data-testid="submit"]')
        self.assertEqual(strategy, "testid")
        self.assertGreater(conf, 0.8)
        self.assertEqual(fail, "")

    def test_role_name_before_label_and_css(self):
        ev = ev_click(
            role="button",
            accessible_name="Save",
            label="Save",
            tag="button",
            selector="div > button:nth-of-type(2)",
            selector_candidates={
                "role_name": '[role="button"][aria-label="Save"]',
                "label": 'button[aria-label="Save"]',
                "css_path": "div > button:nth-of-type(2)",
            },
            candidate_unique={"role_name": True, "label": True},
        )
        sel, strategy, *_ = pick_selector(ev)
        self.assertEqual(strategy, "role_name")
        self.assertIn("role", sel)

    def test_label_before_text_and_css(self):
        ev = ev_click(
            tag="button",
            label="Continue",
            text="Continue",
            selector="div.x > span > button:nth-of-type(1)",
            selector_candidates={"label": 'button[aria-label="Continue"]'},
            candidate_unique={"label": True},
        )
        sel, strategy, *_ = pick_selector(ev)
        self.assertEqual(strategy, "label")
        self.assertEqual(sel, 'button[aria-label="Continue"]')

    def test_stable_text_before_css_path(self):
        ev = ev_click(
            tag="button",
            text="OK",
            selector="div > button:nth-of-type(4)",
            selector_candidates={},
            candidate_unique={"text": True},
        )
        sel, strategy, *_ = pick_selector(ev)
        self.assertEqual(strategy, "text")
        self.assertIn("has-text", sel)

    def test_page_uniqueness_skips_non_unique_testid(self):
        page = FakePage()
        page.selector_counts['[data-testid="dup"]'] = 3
        page.elements['button[aria-label="Only"]'] = {"text": "Only"}
        page.selector_counts['button[aria-label="Only"]'] = 1
        ev = ev_click(
            tag="button",
            label="Only",
            selector_candidates={
                "testid": '[data-testid="dup"]',
                "label": 'button[aria-label="Only"]',
            },
        )
        sel, strategy, *_ = pick_selector(ev, page=page)
        self.assertEqual(strategy, "label")
        self.assertEqual(sel, 'button[aria-label="Only"]')


class NormalizePipelineTests(unittest.TestCase):
    def test_click_fill_nav_to_playwright_source_human(self):
        events = [
            {
                "kind": "navigation",
                "url": "https://example.com/login?token=leakme&next=/app",
            },
            ev_click(
                selector="#user",
                selector_candidates={"id": "#user", "testid": '[data-testid="user"]'},
                candidate_unique={"testid": True},
            ),
            {
                "kind": "input",
                "selector": '[data-testid="user"]',
                "selector_candidates": {"testid": '[data-testid="user"]'},
                "candidate_unique": {"testid": True},
                "value": "alice",
                "field": {"type": "text", "name": "username", "id": "user"},
            },
            {
                "kind": "input",
                "selector": "#pass",
                "selector_candidates": {"id": "#pass"},
                "candidate_unique": {"css": True},
                "value": "hunter2-secret",
                "field": {
                    "type": "password",
                    "name": "password",
                    "id": "pass",
                    "autocomplete": "current-password",
                },
                "redacted": True,
            },
            ev_click(
                selector="button.submit",
                selector_candidates={"css_path": "button.submit"},
                candidate_unique={"css": True},
            ),
        ]
        r = normalize_events(events)
        kinds = [s["action"] for s in r.steps]
        self.assertEqual(kinds[0], "goto")
        self.assertEqual(r.steps[0]["source"], "human")
        self.assertNotIn("leakme", r.steps[0]["url"])
        self.assertTrue(r.steps[0]["url"].startswith("https://example.com/login"))
        self.assertIn("fill", kinds)
        pw = [s for s in r.steps if s.get("selector") == "#pass"][0]
        self.assertEqual(pw["text"], "{{vars.PASSWORD}}")
        self.assertNotIn("hunter2", str(r.to_public()))
        self.assertTrue(all(s.get("source") == "human" for s in r.steps))
        self.assertTrue(all(not is_raw_dom_event(s) for s in r.exportable_steps()))

    def test_consecutive_input_merged_final_value(self):
        events = [
            {
                "kind": "input",
                "selector": "#q",
                "selector_candidates": {"id": "#q"},
                "candidate_unique": {"css": True},
                "value": "a",
                "field": {"type": "text", "id": "q"},
            },
            {
                "kind": "input",
                "selector": "#q",
                "selector_candidates": {"id": "#q"},
                "candidate_unique": {"css": True},
                "value": "ab",
                "field": {"type": "text", "id": "q"},
            },
            {
                "kind": "input",
                "selector": "#q",
                "selector_candidates": {"id": "#q"},
                "candidate_unique": {"css": True},
                "value": "abc",
                "field": {"type": "text", "id": "q"},
            },
        ]
        r = normalize_events(events)
        fills = [s for s in r.steps if s["action"] == "fill"]
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0]["text"], "abc")

    def test_password_plaintext_not_in_public_payload(self):
        events = [
            {
                "kind": "input",
                "selector": "#pw",
                "value": "super-secret-password",
                "field": {"type": "password", "name": "password"},
                "selector_candidates": {"id": "#pw"},
                "candidate_unique": {"css": True},
            }
        ]
        r = normalize_events(events)
        pub = r.to_public()
        blob = str(pub)
        self.assertNotIn("super-secret-password", blob)
        self.assertEqual(r.steps[0]["text"], "{{vars.PASSWORD}}")

    def test_shadow_dom_non_exportable(self):
        r = normalize_events(
            [ev_click(shadow=True, selector="#x", selector_candidates={"id": "#x"})]
        )
        self.assertEqual(r.steps, [])
        self.assertTrue(any(x["reason"] == "shadow_dom" for x in r.non_exportable))
        self.assertFalse(any(is_raw_dom_event(x) and x.get("action") for x in r.non_exportable))

    def test_iframe_needs_confirm(self):
        r = normalize_events(
            [
                ev_click(
                    frame="iframe",
                    selector="#ok",
                    selector_candidates={"id": "#ok"},
                    candidate_unique={"css": True},
                )
            ]
        )
        self.assertEqual(r.steps, [])
        self.assertEqual(len(r.needs_confirm), 1)
        self.assertEqual(r.needs_confirm[0]["reason"], "iframe")
        self.assertEqual(r.needs_confirm[0]["proposed"]["action"], "click")
        self.assertFalse(r.needs_confirm[0]["exportable"])

    def test_unstable_css_needs_confirm(self):
        r = normalize_events(
            [
                ev_click(
                    selector="div > section > ul > li:nth-of-type(4) > button",
                    selector_candidates={
                        "css_path": "div > section > ul > li:nth-of-type(4) > button"
                    },
                    candidate_unique={"css": True},
                )
            ]
        )
        self.assertEqual(r.steps, [])
        self.assertEqual(r.needs_confirm[0]["reason"], "unstable_selector")
        self.assertEqual(r.needs_confirm[0]["proposed"]["selector_strategy"], "css")

    def test_coords_last_needs_confirm_with_observation(self):
        r = normalize_events(
            [
                ev_click(
                    x=40,
                    y=50,
                    observation_id="obs-1",
                    viewport={"width": 1280, "height": 720},
                    selector_candidates={},
                )
            ]
        )
        self.assertEqual(len(r.needs_confirm), 1)
        p = r.needs_confirm[0]["proposed"]
        self.assertEqual(p["selector_strategy"], "coords")
        self.assertEqual(p["x"], 40)
        self.assertEqual(p["observation_id"], "obs-1")
        self.assertEqual(r.needs_confirm[0]["reason"], "coords_fallback")

    def test_coords_without_observation_non_exportable(self):
        r = normalize_events([ev_click(x=1, y=2, selector_candidates={})])
        self.assertTrue(
            any(x["reason"] == "coords_missing_observation_id" for x in r.non_exportable)
        )
        self.assertEqual(r.exportable_steps(), [])

    def test_javascript_nav_rejected(self):
        r = normalize_events([{"kind": "navigation", "url": "javascript:alert(1)"}])
        self.assertEqual(r.steps, [])
        self.assertTrue(any("url" in x["reason"] for x in r.non_exportable))

    def test_any_https_goto_ok_no_allowlist(self):
        r = normalize_events(
            [{"kind": "navigation", "url": "https://paste.example/doc?q=1"}]
        )
        self.assertEqual(len(r.steps), 1)
        self.assertEqual(r.steps[0]["action"], "goto")
        self.assertTrue(r.steps[0]["url"].startswith("https://paste.example/doc"))

    def test_file_and_data_nav_rejected(self):
        for url in ("file:///etc/passwd", "data:text/html,hi"):
            r = normalize_events([{"kind": "navigation", "url": url}])
            self.assertEqual(r.steps, [], url)

    def test_press_and_select(self):
        r = normalize_events(
            [
                {
                    "kind": "select",
                    "tag": "select",
                    "selector": "#color",
                    "selector_candidates": {"id": "#color"},
                    "candidate_unique": {"css": True},
                    "value": "blue",
                },
                {"kind": "keypress", "key": "Enter"},
            ]
        )
        acts = [s["action"] for s in r.steps]
        self.assertIn("select", acts)
        self.assertIn("press", acts)
        self.assertEqual(r.steps[0]["source"], "human")

    def test_no_raw_dom_in_exportable(self):
        r = normalize_events(
            [
                ev_click(
                    selector="#ok",
                    selector_candidates={"id": "#ok"},
                    candidate_unique={"css": True},
                )
            ]
        )
        for s in r.exportable_steps():
            self.assertIn("action", s)
            self.assertNotIn("kind", s)
            self.assertFalse(is_raw_dom_event(s))

    def test_click_before_fill_same_selector_dropped(self):
        r = normalize_events(
            [
                ev_click(
                    selector="#user",
                    selector_candidates={"id": "#user"},
                    candidate_unique={"css": True},
                ),
                {
                    "kind": "input",
                    "selector": "#user",
                    "selector_candidates": {"id": "#user"},
                    "candidate_unique": {"css": True},
                    "value": "a",
                    "field": {"type": "text"},
                },
            ]
        )
        self.assertEqual([s["action"] for s in r.steps], ["fill"])

    def test_live_page_non_unique_falls_through(self):
        page = FakePage()
        page.selector_counts["#dup"] = 2
        page.elements['[data-testid="one"]'] = {"text": "x"}
        page.selector_counts['[data-testid="one"]'] = 1
        r = normalize_events(
            [
                ev_click(
                    selector="#dup",
                    selector_candidates={
                        "id": "#dup",
                        "testid": '[data-testid="one"]',
                    },
                )
            ],
            page=page,
        )
        self.assertEqual(r.steps[0]["selector"], '[data-testid="one"]')
        self.assertEqual(r.steps[0]["selector_strategy"], "testid")


class TimelineMergeTests(unittest.TestCase):
    def test_merge_agent_then_human_monotonic_seq(self):
        agent = [
            {
                "schema_version": 1,
                "action": "goto",
                "url": "https://example.com/",
                "source": "llm",
            },
            {"schema_version": 1, "action": "click", "selector": "#a", "source": "llm"},
        ]
        human = [
            {
                "schema_version": 1,
                "action": "fill",
                "selector": "#pw",
                "text": "hunter2",
                "source": "human",
            },
            {
                "schema_version": 1,
                "action": "click",
                "selector": "button.submit",
                "source": "human",
            },
        ]
        tl = merge_timelines(agent, human)
        self.assertTrue(tl.seq_is_monotonic())
        steps = tl.exportable_steps()
        self.assertEqual([s["source"] for s in steps], ["llm", "llm", "human", "human"])
        self.assertEqual(steps[0]["seq"] if "seq" in steps[0] else 1, 1)
        blob = str(tl.snapshot())
        self.assertNotIn("hunter2", blob)
        self.assertTrue(all(s.get("action") for s in steps))
        self.assertFalse(any(is_raw_dom_event(s) for s in steps))

    def test_raw_dom_never_exportable(self):
        tl = Timeline()
        tl.append("takeover_event", {"kind": "click", "selector": "#x"}, source="extension")
        rec = tl.append_action({"kind": "click", "selector": "#x"}, source="human")
        self.assertIsNone(rec)
        self.assertEqual(tl.exportable_steps(), [])

    def test_dedup_request_id_index(self):
        tl = Timeline()
        step = {"action": "click", "selector": "#a", "source": "human"}
        tl.merge_human_steps([step], request_id="t1")
        tl.merge_human_steps([step], request_id="t1")
        self.assertEqual(len(tl.exportable_steps()), 1)

    def test_fill_redacted_on_timeline(self):
        tl = Timeline()
        tl.append_action(
            {"action": "fill", "selector": "#pw", "text": "super-secret-password", "source": "human"},
            source="human",
        )
        blob = str(tl.snapshot())
        self.assertNotIn("super-secret-password", blob)
        self.assertEqual(tl.exportable_steps()[0]["text"], "[REDACTED]")


if __name__ == "__main__":
    unittest.main()
