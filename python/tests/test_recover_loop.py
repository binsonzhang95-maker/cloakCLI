import json
import tempfile
import unittest
from pathlib import Path

from cloakcli_worker.llm_config import LlmConfig
from cloakcli_worker.recover.loop import SYSTEM_PROMPT, run_recover
from cloakcli_worker.recover.observe import CoordBinding, binding_still_valid
from cloakcli_worker.runner import SkillRunError, execute_skill, resolve_on_stall, step_goal

from fakes import FakePage, ScriptedProvider


def _cfg(**kw) -> LlmConfig:
    d = dict(
        enabled=True,
        base_url="https://api.example.com/v1",
        model="vision",
        api_key_env="OPENAI_API_KEY",
        recover_timeout_sec=30,
        allow_hosts=[],
        max_actions=120,
        max_loops=60,
    )
    d.update(kw)
    return LlmConfig(**d)


def _root():
    root = Path(tempfile.mkdtemp(prefix="cloakcli_rec_"))
    (root / "data" / "artifacts" / "demo").mkdir(parents=True)
    (root / "config").mkdir(parents=True)
    return root


class RecoverLoopTests(unittest.TestCase):
    def test_selector_fail_recover_click_continue(self):
        root = _root()
        artifacts = root / "data" / "artifacts" / "demo"
        page = FakePage()
        # skill will fail on #missing then recover clicks a, then extract h1
        llm_path = root / "config" / "llm.json"
        llm_path.write_text(
            json.dumps(
                {
                    "enabled": True,
                    "base_url": "https://api.example.com/v1",
                    "model": "vision",
                    "api_key_env": "OPENAI_API_KEY",
                    "recover_timeout_sec": 300,
                }
            ),
            encoding="utf-8",
        )
        skill = {
            "name": "demo",
            "on_stall": "recover",
            "steps": [
                {"action": "goto", "url": "https://example.com/"},
                {
                    "action": "click",
                    "selector": "#does-not-exist",
                    "timeout": 50,
                    "goal": "Click the More information link",
                    "on_stall": "recover",
                },
                {"action": "extract_text", "css": "h1", "as": "title"},
            ],
        }
        provider = ScriptedProvider(
            ['{"schema_version":1,"action":"click","css":"a"}', '{"schema_version":1,"action":"done","reason":"clicked"}']
        )
        result = execute_skill(
            page=page,
            skill=skill,
            skill_name="demo",
            variables={},
            artifacts_dir=artifacts,
            project_root=root,
            provider=provider,
        )
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["extracts"]["title"], "Example Domain")
        self.assertIn("a", page.clicked)
        self.assertTrue(result["recover"])
        self.assertEqual(result["recover"][0]["status"], "done")
        traj = Path(result["recover"][0]["trajectory_path"])
        self.assertTrue(traj.is_file())

    def test_illegal_actions_rejected_then_done(self):
        root = _root()
        artifacts = root / "data" / "artifacts" / "demo"
        page = FakePage()
        provider = ScriptedProvider(
            [
                '{"schema_version":1,"action":"shell","cmd":"id"}',
                '{"schema_version":1,"action":"read_file","path":"/etc/passwd"}',
                '{"schema_version":1,"action":"goto","url":"file:///etc/passwd"}',
                '{"schema_version":1,"action":"goto","url":"https://evil.test/"}',
                '{"schema_version":1,"action":"click","x":99999,"y":10,"screenshot_id":"obs-001"}',
                '{"schema_version":1,"action":"done","reason":"gave up illegal"}',
            ]
        )
        out = run_recover(
            page=page,
            goal="stay safe",
            stall={"error": "timeout"},
            artifacts_dir=artifacts,
            skill_name="demo",
            task_origin="https://example.com",
            cfg=_cfg(max_model_rounds=8),
            root=root,
            provider=provider,
        )
        self.assertEqual(out.status, "done")
        self.assertEqual(page.gotos, [])  # illegal gotos not executed
        text = Path(out.trajectory_path).read_text(encoding="utf-8")
        self.assertIn("action_reject", text)
        self.assertNotIn("/etc/passwd", "".join(page.gotos))

    def test_coords_invalid_after_navigation(self):
        root = _root()
        artifacts = root / "data" / "artifacts" / "demo"
        page = FakePage()
        page.nav_on_click["a"] = "https://example.com/next"
        provider = ScriptedProvider(
            [
                '{"schema_version":1,"action":"click","css":"a"}',
                '{"schema_version":1,"action":"click","x":10,"y":10,"screenshot_id":"obs-001"}',
                '{"schema_version":1,"action":"done","reason":"ok"}',
            ]
        )
        out = run_recover(
            page=page,
            goal="g",
            stall={"error": "x"},
            artifacts_dir=artifacts,
            skill_name="demo",
            task_origin="https://example.com",
            cfg=_cfg(),
            root=root,
            provider=provider,
        )
        self.assertEqual(out.status, "done")
        # second click coords should have been rejected (old screenshot / nav)
        self.assertEqual(page.coord_clicks, [])
        traj = json.loads(Path(out.trajectory_path).read_text(encoding="utf-8"))
        rejects = [e for e in traj["events"] if e.get("event") == "action_reject"]
        self.assertTrue(rejects)

    def test_timeout_writes_trajectory(self):
        root = _root()
        artifacts = root / "data" / "artifacts" / "demo"
        page = FakePage()
        out = run_recover(
            page=page,
            goal="g",
            stall={"error": "x"},
            artifacts_dir=artifacts,
            skill_name="demo",
            task_origin="https://example.com",
            cfg=_cfg(recover_timeout_sec=5),  # min clamp in file load; in-memory 5 still runs
            root=root,
            provider=ScriptedProvider(
                ['{"schema_version":1,"action":"wait","ms":1}'] * 3
            ),
        )
        # With 5s budget this should complete via wait+... actually waits are no-op so it may loop.
        # Force 0-like by using a tiny timeout via dataclass (5s min in parse, but dataclass allows smaller)
        page = FakePage()
        out = run_recover(
            page=page,
            goal="g",
            stall={"error": "x"},
            artifacts_dir=artifacts,
            skill_name="demo",
            task_origin="https://example.com",
            cfg=_cfg(recover_timeout_sec=0.0),
            root=root,
            provider=ScriptedProvider(['{"schema_version":1,"action":"done","reason":"x"}']),
        )
        self.assertEqual(out.status, "timeout")
        self.assertTrue(Path(out.trajectory_path).is_file())

    def test_ask_human_pauses(self):
        root = _root()
        artifacts = root / "data" / "artifacts" / "demo"
        (root / "config").mkdir(exist_ok=True)
        (root / "config" / "llm.json").write_text(
            json.dumps(
                {
                    "enabled": True,
                    "base_url": "https://api.example.com/v1",
                    "model": "v",
                    "api_key_env": "OPENAI_API_KEY",
                    "recover_timeout_sec": 300,
                }
            ),
            encoding="utf-8",
        )
        page = FakePage()
        skill = {
            "name": "demo",
            "on_stall": "recover",
            "steps": [
                {
                    "action": "click",
                    "selector": "#nope",
                    "goal": "need a human",
                    "timeout": 10,
                }
            ],
        }
        provider = ScriptedProvider(
            ['{"schema_version":1,"action":"ask_human","reason":"captcha"}']
        )
        with self.assertRaises(SkillRunError) as ctx:
            execute_skill(
                page=page,
                skill=skill,
                skill_name="demo",
                variables={},
                artifacts_dir=artifacts,
                project_root=root,
                provider=provider,
            )
        self.assertIn("ASK_HUMAN", str(ctx.exception))
        self.assertEqual(ctx.exception.status, "paused")
        self.assertIn("captcha", ctx.exception.data["recover"]["reason"])

    def test_on_stall_inherit(self):
        skill = {"on_stall": "recover"}
        self.assertEqual(resolve_on_stall({}, skill), "recover")
        self.assertEqual(resolve_on_stall({"on_stall": "fail"}, skill), "fail")
        self.assertEqual(resolve_on_stall({}, {}), "fail")
        self.assertIn("Click", step_goal({"goal": "Click the link"}, {}, "click"))

    def test_stall_payload_allows_password_redacts_api_key(self):
        from cloakcli_worker.runner import _stall_payload

        stall = _stall_payload(
            0,
            "fill",
            {"selector": "#password", "text": "hunter2-login"},
            TimeoutError("waiting for #password"),
        )
        dumped = json.dumps(stall)
        self.assertIn("hunter2-login", dumped)

        stall_key = _stall_payload(
            0,
            "fill",
            {"selector": "#api_key", "text": "sk-supersecret-abc12345"},
            TimeoutError("waiting for #api_key"),
        )
        dumped_key = json.dumps(stall_key)
        self.assertNotIn("sk-supersecret-abc12345", dumped_key)
        self.assertIn("(redacted)", dumped_key)

        stall_auth = _stall_payload(
            0,
            "type",
            {"selector": "input", "text": "Authorization: Bearer sk-abc12345zzzz"},
            TimeoutError("x"),
        )
        self.assertNotIn("sk-abc12345zzzz", json.dumps(stall_auth))

    def test_coord_click_rejected_without_screenshot_id(self):
        root = _root()
        artifacts = root / "data" / "artifacts" / "demo"
        page = FakePage()
        provider = ScriptedProvider(
            [
                '{"schema_version":1,"action":"click","x":10,"y":10}',
                '{"schema_version":1,"action":"done","reason":"ok"}',
            ]
        )
        out = run_recover(
            page=page,
            goal="g",
            stall={"error": "x"},
            artifacts_dir=artifacts,
            skill_name="demo",
            task_origin="https://example.com",
            cfg=_cfg(),
            root=root,
            provider=provider,
        )
        self.assertEqual(out.status, "done")
        self.assertEqual(page.coord_clicks, [])
        traj = json.loads(Path(out.trajectory_path).read_text(encoding="utf-8"))
        errors = []
        for e in traj["events"]:
            errors.extend(e.get("errors") or [])
        self.assertTrue(any("screenshot_id" in err for err in errors), errors)

    def test_coord_click_rejected_mismatched_screenshot_id(self):
        root = _root()
        artifacts = root / "data" / "artifacts" / "demo"
        page = FakePage()
        provider = ScriptedProvider(
            [
                '{"schema_version":1,"action":"click","x":10,"y":10,"screenshot_id":"obs-999"}',
                '{"schema_version":1,"action":"done","reason":"ok"}',
            ]
        )
        out = run_recover(
            page=page,
            goal="g",
            stall={"error": "x"},
            artifacts_dir=artifacts,
            skill_name="demo",
            task_origin="https://example.com",
            cfg=_cfg(),
            root=root,
            provider=provider,
        )
        self.assertEqual(out.status, "done")
        self.assertEqual(page.coord_clicks, [])
        traj = json.loads(Path(out.trajectory_path).read_text(encoding="utf-8"))
        rejects = [e for e in traj["events"] if e.get("event") == "action_reject"]
        self.assertTrue(any(e.get("reason") == "screenshot_id mismatch" for e in rejects), rejects)

    def test_coord_click_matching_screenshot_id_ok(self):
        root = _root()
        artifacts = root / "data" / "artifacts" / "demo"
        page = FakePage()
        provider = ScriptedProvider(
            [
                # text stage: coord is rejected (no image) and escalates to vision
                '{"schema_version":1,"action":"click","x":10,"y":10,"screenshot_id":"obs-001"}',
                '{"schema_version":1,"action":"click","x":10,"y":10,"screenshot_id":"obs-001"}',
                '{"schema_version":1,"action":"done","reason":"clicked"}',
            ]
        )
        out = run_recover(
            page=page,
            goal="g",
            stall={"error": "x"},
            artifacts_dir=artifacts,
            skill_name="demo",
            task_origin="https://example.com",
            cfg=_cfg(),
            root=root,
            provider=provider,
        )
        self.assertEqual(out.status, "done")
        self.assertEqual(page.coord_clicks, [(10, 10)])

    def test_coords_invalidated_same_turn_after_navigation(self):
        root = _root()
        artifacts = root / "data" / "artifacts" / "demo"
        page = FakePage()
        page.nav_on_click["a"] = "https://example.com/next"
        provider = ScriptedProvider(
            [
                json.dumps(
                    {
                        "schema_version": 1,
                        "actions": [
                            {"action": "click", "css": "a"},
                            {
                                "action": "click",
                                "x": 10,
                                "y": 10,
                                "screenshot_id": "obs-001",
                            },
                        ],
                    }
                ),
                '{"schema_version":1,"action":"done","reason":"ok"}',
            ]
        )
        out = run_recover(
            page=page,
            goal="g",
            stall={"error": "x"},
            artifacts_dir=artifacts,
            skill_name="demo",
            task_origin="https://example.com",
            cfg=_cfg(),
            root=root,
            provider=provider,
        )
        self.assertEqual(out.status, "done")
        self.assertEqual(page.coord_clicks, [])
        traj = json.loads(Path(out.trajectory_path).read_text(encoding="utf-8"))
        rejects = [e for e in traj["events"] if e.get("event") == "action_reject"]
        self.assertTrue(
            any(e.get("reason") == "coords invalidated" for e in rejects), rejects
        )

    def test_coords_invalidated_after_viewport_change(self):
        root = _root()
        artifacts = root / "data" / "artifacts" / "demo"
        page = FakePage()

        def shrink(_calls):
            page.viewport_size = {"width": 800, "height": 600}

        provider = ScriptedProvider(
            [
                '{"schema_version":1,"action":"click","x":10,"y":10,"screenshot_id":"obs-001"}',
                '{"schema_version":1,"action":"done","reason":"ok"}',
            ],
            before_complete=shrink,
        )
        out = run_recover(
            page=page,
            goal="g",
            stall={"error": "x"},
            artifacts_dir=artifacts,
            skill_name="demo",
            task_origin="https://example.com",
            cfg=_cfg(),
            root=root,
            provider=provider,
        )
        self.assertEqual(out.status, "done")
        self.assertEqual(page.coord_clicks, [])
        traj = json.loads(Path(out.trajectory_path).read_text(encoding="utf-8"))
        rejects = [e for e in traj["events"] if e.get("event") == "action_reject"]
        self.assertTrue(
            any(e.get("reason") == "coords invalidated" for e in rejects), rejects
        )

    def test_fill_password_field_allowed(self):
        root = _root()
        artifacts = root / "data" / "artifacts" / "demo"
        page = FakePage()
        page.elements["#password"] = {"text": "", "type": "password"}
        page.elements["#user"] = {"text": "", "type": "text"}
        provider = ScriptedProvider(
            [
                '{"schema_version":1,"action":"fill","css":"#user","text":"alice"}',
                '{"schema_version":1,"action":"fill","css":"#password","text":"hunter2-login"}',
                '{"schema_version":1,"action":"done","reason":"logged in"}',
            ]
        )
        out = run_recover(
            page=page,
            goal="log in",
            stall={"error": "x"},
            artifacts_dir=artifacts,
            skill_name="demo",
            task_origin="https://example.com",
            cfg=_cfg(),
            root=root,
            provider=provider,
        )
        self.assertEqual(out.status, "done")
        self.assertEqual(page.filled, [("#user", "alice"), ("#password", "hunter2-login")])
        self.assertIn("fill", SYSTEM_PROMPT)
        self.assertIn("screenshot_id", SYSTEM_PROMPT)

    def test_binding_invalid_on_url_or_viewport(self):
        page = FakePage()
        b = CoordBinding(
            screenshot_id="obs-001", width=1280, height=720, url=page.url
        )
        self.assertTrue(binding_still_valid(page, b))
        page.url = "https://example.com/next"
        self.assertFalse(binding_still_valid(page, b))
        page.url = "https://example.com/"
        page.viewport_size = {"width": 800, "height": 600}
        self.assertFalse(binding_still_valid(page, b))
        page.viewport_size = {"width": 1280, "height": 720}
        dead = CoordBinding(
            screenshot_id="obs-001", width=1280, height=720, url=page.url, valid=False
        )
        self.assertFalse(binding_still_valid(page, dead))

    def test_local_backup_selector_skips_llm(self):
        root = _root()
        artifacts = root / "data" / "artifacts" / "demo"
        page = FakePage()
        page.elements["#alt"] = {"text": "Go"}
        provider = ScriptedProvider(
            ['{"schema_version":1,"action":"done","reason":"should not be called"}']
        )
        out = run_recover(
            page=page,
            goal="click go",
            stall={"action": "click", "selector": "#missing", "selectors": ["#missing", "#alt"]},
            artifacts_dir=artifacts,
            skill_name="demo",
            task_origin="https://example.com",
            cfg=_cfg(),
            root=root,
            provider=provider,
        )
        self.assertEqual(out.status, "done")
        self.assertEqual(out.stage, "local")
        self.assertEqual(provider.calls, 0)
        self.assertIn("#alt", page.clicked)
        traj = json.loads(Path(out.trajectory_path).read_text(encoding="utf-8"))
        self.assertEqual(traj["telemetry"]["model_rounds"], 0)
        self.assertEqual(traj["telemetry"]["status"], "done")

    def test_text_stage_does_not_attach_image(self):
        root = _root()
        artifacts = root / "data" / "artifacts" / "demo"
        page = FakePage()
        provider = ScriptedProvider(
            ['{"schema_version":1,"action":"click","css":"a"}', '{"schema_version":1,"action":"done","reason":"ok"}']
        )
        out = run_recover(
            page=page,
            goal="g",
            stall={"error": "x", "action": "click", "selector": "#nope"},
            artifacts_dir=artifacts,
            skill_name="demo",
            task_origin="https://example.com",
            cfg=_cfg(),
            root=root,
            provider=provider,
        )
        self.assertEqual(out.status, "done")
        self.assertTrue(provider.images)
        self.assertFalse(provider.images[0], "text stage must not attach a screenshot")
        self.assertFalse(any(c.get("full_page") for c in page.screenshot_calls))

    def test_vision_attaches_one_compressed_not_full_page(self):
        root = _root()
        artifacts = root / "data" / "artifacts" / "demo"
        page = FakePage()
        provider = ScriptedProvider(
            [
                '{"schema_version":1,"action":"fail","reason":"need vision"}',
                '{"schema_version":1,"action":"click","css":"a"}',
                '{"schema_version":1,"action":"done","reason":"ok"}',
            ]
        )
        out = run_recover(
            page=page,
            goal="g",
            stall={"error": "x", "action": "click", "selector": "#nope"},
            artifacts_dir=artifacts,
            skill_name="demo",
            task_origin="https://example.com",
            cfg=_cfg(),
            root=root,
            provider=provider,
        )
        self.assertEqual(out.status, "done")
        self.assertGreaterEqual(len(provider.images), 2)
        self.assertFalse(provider.images[0])
        self.assertTrue(provider.images[1])
        self.assertTrue(any(not c.get("full_page") for c in page.screenshot_calls))
        self.assertFalse(any(c.get("full_page") for c in page.screenshot_calls))
        traj = json.loads(Path(out.trajectory_path).read_text(encoding="utf-8"))
        self.assertGreaterEqual(traj["telemetry"]["screenshot_bytes"], 0)
        self.assertGreaterEqual(traj["telemetry"]["model_rounds"], 2)

    def test_max_three_model_rounds(self):
        root = _root()
        artifacts = root / "data" / "artifacts" / "demo"
        page = FakePage()
        provider = ScriptedProvider(
            ['{"schema_version":1,"action":"wait","ms":1}'] * 6
        )
        out = run_recover(
            page=page,
            goal="g",
            stall={"error": "x"},
            artifacts_dir=artifacts,
            skill_name="demo",
            task_origin="https://example.com",
            cfg=_cfg(max_model_rounds=3, recover_timeout_sec=30),
            root=root,
            provider=provider,
        )
        self.assertEqual(out.status, "failed")
        self.assertEqual(out.model_rounds, 3)
        self.assertLessEqual(provider.calls, 3)

    def test_press_and_select_execute(self):
        root = _root()
        artifacts = root / "data" / "artifacts" / "demo"
        page = FakePage()
        page.elements["select#n"] = {"text": ""}
        provider = ScriptedProvider(
            [
                '{"schema_version":1,"action":"select","css":"select#n","value":"CA"}',
                '{"schema_version":1,"action":"press","key":"Enter"}',
                '{"schema_version":1,"action":"done","reason":"ok"}',
            ]
        )
        out = run_recover(
            page=page,
            goal="g",
            stall={"error": "x"},
            artifacts_dir=artifacts,
            skill_name="demo",
            task_origin="https://example.com",
            cfg=_cfg(),
            root=root,
            provider=provider,
        )
        self.assertEqual(out.status, "done")
        self.assertEqual(page.selected, [("select#n", "CA")])
        self.assertEqual(page.pressed, ["Enter"])

    def test_runner_tries_backup_selectors(self):
        root = _root()
        artifacts = root / "data" / "artifacts" / "demo"
        page = FakePage()
        page.elements["#alt"] = {"text": "ok"}
        skill = {
            "name": "demo",
            "steps": [
                {
                    "action": "click",
                    "selector": "#missing",
                    "selectors": ["#missing", "#alt"],
                    "timeout": 50,
                }
            ],
        }
        result = execute_skill(
            page=page,
            skill=skill,
            skill_name="demo",
            variables={},
            artifacts_dir=artifacts,
            project_root=root,
        )
        self.assertEqual(result["status"], "succeeded")
        self.assertIn("#alt", page.clicked)
        self.assertFalse(result.get("recover"))
