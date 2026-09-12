import json
import tempfile
import unittest
from pathlib import Path

from cloakcli_worker.llm_config import LlmConfig
from cloakcli_worker.recover.loop import run_recover
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
                '{"schema_version":1,"action":"click","x":99999,"y":10}',
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
            cfg=_cfg(),
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

    def test_type_does_not_put_secret_in_stall_prompt(self):
        from cloakcli_worker.runner import _stall_payload

        stall = _stall_payload(
            0,
            "fill",
            {"selector": "#password", "text": "hunter2-secret"},
            TimeoutError("waiting for #password"),
        )
        dumped = json.dumps(stall)
        self.assertNotIn("hunter2-secret", dumped)
