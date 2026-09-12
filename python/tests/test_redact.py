import json
import os
import tempfile
import unittest
from pathlib import Path

from cloakcli_worker.redact import redact_any, redact_text
from cloakcli_worker.recover.loop import run_recover
from cloakcli_worker.llm_config import LlmConfig

from fakes import FakePage, ScriptedProvider


class RedactTests(unittest.TestCase):
    def test_redact_authorization_cookie_proxy_key(self):
        s = redact_text(
            "Authorization: Bearer sk-secretTEST99abc "
            "cookie=abc123 "
            "http://user:hunter2@127.0.0.1:7890 "
            '{"api_key":"xyz"}',
            extra=["sk-secretTEST99abc"],
        )
        self.assertNotIn("sk-secretTEST99abc", s)
        self.assertNotIn("hunter2", s)
        self.assertNotIn("xyz", s)
        self.assertIn("***", s)

    def test_trajectory_strips_secrets(self):
        root = Path(tempfile.mkdtemp(prefix="cloakcli_redact_"))
        artifacts = root / "data" / "artifacts" / "s"
        artifacts.mkdir(parents=True)
        page = FakePage()
        page.elements["#ok"] = {"text": "ok"}
        os.environ["CLOAKCLI_FAKE_SECRET"] = "super-secret-value-xyz"
        try:
            cfg = LlmConfig(
                enabled=True,
                base_url="https://api.example.com/v1",
                model="m",
                api_key_env="CLOAKCLI_FAKE_SECRET",
                recover_timeout_sec=30,
            )
            provider = ScriptedProvider(
                [
                    json.dumps(
                        {
                            "schema_version": 1,
                            "action": "done",
                            "reason": "ok Authorization: Bearer super-secret-value-xyz",
                        }
                    )
                ]
            )
            result = run_recover(
                page=page,
                goal="g",
                stall={"error": "Authorization: Bearer super-secret-value-xyz cookie=abc"},
                artifacts_dir=artifacts,
                skill_name="s",
                task_origin="https://example.com",
                cfg=cfg,
                root=root,
                provider=provider,
            )
            text = Path(result.trajectory_path).read_text(encoding="utf-8")
            self.assertNotIn("super-secret-value-xyz", text)
            self.assertNotIn("cookie=abc", text.lower().replace("***", ""))
        finally:
            os.environ.pop("CLOAKCLI_FAKE_SECRET", None)
