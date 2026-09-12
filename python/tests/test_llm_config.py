import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cloakcli_worker.llm_config import load_llm_config, parse_llm_config
from cloakcli_worker.paths import set_root
from cloakcli_worker.recover.provider import OpenAICompatProvider, ProviderError, test_llm


class LlmConfigTests(unittest.TestCase):
    def test_parse_defaults_timeout_300(self):
        cfg = parse_llm_config(
            {
                "enabled": True,
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-4o",
                "api_key_env": "OPENAI_API_KEY",
            }
        )
        self.assertEqual(cfg.recover_timeout_sec, 300)
        self.assertEqual(cfg.max_actions, 120)
        self.assertEqual(cfg.max_loops, 60)

    def test_rejects_file_base_url(self):
        cfg = parse_llm_config(
            {
                "base_url": "file:///etc/passwd",
                "model": "x",
                "api_key_env": "OPENAI_API_KEY",
            }
        )
        self.assertEqual(cfg.base_url, "")

    def test_load_from_root(self):
        root = Path(tempfile.mkdtemp(prefix="cloakcli_llm_py_"))
        (root / "config").mkdir()
        (root / "config" / "llm.json").write_text(
            json.dumps(
                {
                    "enabled": True,
                    "base_url": "https://api.example.com/v1",
                    "model": "vision",
                    "api_key_env": "MY_KEY",
                    "recover_timeout_sec": 300,
                    "allow_hosts": ["iana.org"],
                }
            ),
            encoding="utf-8",
        )
        set_root(root)
        cfg = load_llm_config()
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.model, "vision")
        self.assertEqual(cfg.allow_hosts, ["iana.org"])
        self.assertNotIn("sk-", json.dumps(cfg.public_dict()))

    def test_test_llm_missing_env_no_leak(self):
        cfg = parse_llm_config(
            {
                "enabled": True,
                "base_url": "https://api.example.com/v1",
                "model": "m",
                "api_key_env": "CLOAKCLI_MISSING_KEY_XYZ",
            }
        )
        out = test_llm(cfg)
        blob = json.dumps(out)
        self.assertFalse(out["ok"])
        self.assertFalse(out["key_present"])
        self.assertNotIn("sk-", blob)

    def test_provider_redacts_http_error_body(self):
        cfg = parse_llm_config(
            {
                "enabled": True,
                "base_url": "https://api.example.com/v1",
                "model": "m",
                "api_key_env": "CLOAKCLI_TMP_KEY",
            }
        )
        os.environ["CLOAKCLI_TMP_KEY"] = "sk-supersecret-abc"

        class FakeHTTPError(Exception):
            code = 401
            reason = "Unauthorized"

            def read(self):
                return b'{"error":"invalid_api_key sk-supersecret-abc"}'

        def boom(*a, **k):
            err = type("HTTPError", (FakeHTTPError, Exception), {})()
            # Use real HTTPError path by raising urllib.error.HTTPError-like
            import urllib.error

            raise urllib.error.HTTPError(
                url="https://api.example.com/v1/chat/completions",
                code=401,
                msg="Unauthorized",
                hdrs=None,
                fp=__import__("io").BytesIO(b'{"error":"invalid_api_key sk-supersecret-abc"}'),
            )

        try:
            with mock.patch("urllib.request.urlopen", boom):
                with self.assertRaises(ProviderError) as ctx:
                    OpenAICompatProvider().complete(
                        cfg, [{"role": "user", "content": "hi"}], timeout_sec=5
                    )
            self.assertNotIn("sk-supersecret-abc", str(ctx.exception))
        finally:
            os.environ.pop("CLOAKCLI_TMP_KEY", None)
