import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cloakcli_worker.llm_config import (
    DEFAULT_API_KEY_ENV,
    MAX_MODELS,
    MAX_MODELS_BODY,
    accept_model_id,
    join_openai_path,
    load_llm_config,
    normalize_base_url,
    parse_llm_config,
)
from cloakcli_worker.paths import set_root
from cloakcli_worker.recover.provider import (
    OpenAICompatProvider,
    ProviderError,
    list_models,
    test_llm,
)


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

    def test_default_api_key_env(self):
        cfg = parse_llm_config(
            {"base_url": "https://api.openai.com/v1", "model": "gpt-4o"}
        )
        self.assertEqual(cfg.api_key_env, DEFAULT_API_KEY_ENV)

    def test_join_does_not_duplicate_v1(self):
        base = "https://api.openai.com/v1/"
        self.assertEqual(
            join_openai_path(base, "models"), "https://api.openai.com/v1/models"
        )
        self.assertEqual(
            join_openai_path(base, "v1/chat/completions"),
            "https://api.openai.com/v1/chat/completions",
        )
        self.assertEqual(
            normalize_base_url("https://api.openai.com/v1/v1/models"),
            "https://api.openai.com/v1",
        )

    def test_rejects_javascript_and_data(self):
        self.assertEqual(normalize_base_url("javascript:alert(1)"), "")
        self.assertEqual(normalize_base_url("data:text/plain,hi"), "")
        self.assertEqual(normalize_base_url("file:///etc/passwd"), "")

    def test_normalize_rejects_query_fragment_credentials_keeps_v10(self):
        self.assertEqual(normalize_base_url("https://api.openai.com/v1?x=1"), "")
        self.assertEqual(normalize_base_url("https://api.openai.com/v1#frag"), "")
        self.assertEqual(normalize_base_url("https://user:pass@api.openai.com/v1"), "")
        self.assertEqual(normalize_base_url("https://user@api.openai.com/v1"), "")
        self.assertEqual(normalize_base_url("https:///v1"), "")
        self.assertEqual(
            normalize_base_url("https://api.openai.com/v1/v10"),
            "https://api.openai.com/v1/v10",
        )
        self.assertEqual(
            join_openai_path("https://api.openai.com/v1/v10", "models"),
            "https://api.openai.com/v1/v10/models",
        )
        self.assertEqual(
            join_openai_path("https://api.openai.com/v1", "models"),
            "https://api.openai.com/v1/models",
        )

    def test_accept_model_id_rejects_key_and_control_chars(self):
        key = "sk-live-secret-abc"
        self.assertIsNone(accept_model_id(key, key))
        self.assertIsNone(accept_model_id(f"pre-{key}", key))
        self.assertIsNone(accept_model_id("gpt-4o\x07bell", key))
        self.assertEqual(accept_model_id("gpt-4o", key), "gpt-4o")


class ModelsFetchTests(unittest.TestCase):
    def _serve(self, handler):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading

        auths: list[str] = []

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                auths.append(self.headers.get("Authorization") or "")
                code, body = handler(self.path)
                data = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, fmt, *args):
                return

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        host, port = httpd.server_address[:2]
        cfg = parse_llm_config(
            {
                "enabled": True,
                "base_url": f"http://{host}:{port}/v1",
                "model": "m",
                "api_key_env": "CLOAKCLI_TMP_KEY",
            }
        )
        self.addCleanup(httpd.shutdown)
        self.addCleanup(httpd.server_close)
        return httpd, cfg, auths

    def test_list_models_bearer_and_ids(self):
        os.environ["CLOAKCLI_TMP_KEY"] = "sk-list-secret-xyz"
        httpd, cfg, auths = self._serve(
            lambda path: (
                200,
                json.dumps(
                    {
                        "object": "list",
                        "data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}],
                    }
                ),
            )
        )
        try:
            out = list_models(cfg)
            self.assertTrue(out["ok"], out)
            self.assertEqual(out["ids"], ["gpt-4o", "gpt-4o-mini"])
            self.assertTrue(any(a == "Bearer sk-list-secret-xyz" for a in auths))
            blob = json.dumps(out)
            self.assertNotIn("sk-list-secret-xyz", blob)
        finally:
            httpd.shutdown()
            os.environ.pop("CLOAKCLI_TMP_KEY", None)

    def test_list_models_truncates_count(self):
        os.environ["CLOAKCLI_TMP_KEY"] = "k"
        data = [{"id": f"m{i:04}"} for i in range(MAX_MODELS + 20)]
        httpd, cfg, _ = self._serve(lambda path: (200, json.dumps({"data": data})))
        try:
            out = list_models(cfg)
            self.assertTrue(out["ok"], out)
            self.assertEqual(len(out["ids"]), MAX_MODELS)
            self.assertTrue(out["truncated"])
        finally:
            httpd.shutdown()
            os.environ.pop("CLOAKCLI_TMP_KEY", None)

    def test_list_models_bad_json_no_leak(self):
        os.environ["CLOAKCLI_TMP_KEY"] = "sk-badjson-secret"
        httpd, cfg, _ = self._serve(lambda path: (200, "NOT JSON {"))
        try:
            out = list_models(cfg)
            self.assertFalse(out["ok"])
            blob = json.dumps(out)
            self.assertNotIn("sk-badjson-secret", blob)
            self.assertNotIn("NOT JSON", blob)
            self.assertIn("non-JSON", out["error"])
        finally:
            httpd.shutdown()
            os.environ.pop("CLOAKCLI_TMP_KEY", None)

    def test_list_models_rejects_file_url(self):
        cfg = parse_llm_config(
            {
                "base_url": "file:///etc/passwd",
                "model": "x",
                "api_key_env": "CLOAKCLI_TMP_KEY",
            }
        )
        os.environ["CLOAKCLI_TMP_KEY"] = "sk-file-secret"
        try:
            out = list_models(cfg)
            self.assertFalse(out["ok"])
            blob = json.dumps(out)
            self.assertNotIn("sk-file-secret", blob)
            self.assertEqual(cfg.base_url, "")
        finally:
            os.environ.pop("CLOAKCLI_TMP_KEY", None)

    def test_list_models_http_error_redacts_body(self):
        os.environ["CLOAKCLI_TMP_KEY"] = "sk-http-secret"
        httpd, cfg, _ = self._serve(
            lambda path: (
                401,
                json.dumps({"error": "invalid_api_key sk-http-secret"}),
            )
        )
        try:
            out = list_models(cfg)
            self.assertFalse(out["ok"])
            blob = json.dumps(out)
            self.assertNotIn("sk-http-secret", blob)
            self.assertNotIn("invalid_api_key", blob)
            self.assertIn("401", out["error"])
        finally:
            httpd.shutdown()
            os.environ.pop("CLOAKCLI_TMP_KEY", None)

    def test_list_models_http_error_body_size_capped(self):
        huge = b"E" * (MAX_MODELS_BODY * 2)
        read_sizes: list[int] = []

        class SpyFP:
            def read(self, n=-1):
                read_sizes.append(n)
                if n is None or n < 0:
                    return huge
                return huge[:n]

            def close(self):
                return None

            def readline(self, *a, **k):
                return b""

        import urllib.error

        spy = SpyFP()

        def boom(*a, **k):
            raise urllib.error.HTTPError(
                url="http://127.0.0.1/v1/models",
                code=500,
                msg="ERR",
                hdrs=None,
                fp=spy,
            )

        cfg = parse_llm_config(
            {
                "enabled": True,
                "base_url": "http://127.0.0.1/v1",
                "model": "m",
                "api_key_env": "CLOAKCLI_TMP_KEY",
            }
        )
        os.environ["CLOAKCLI_TMP_KEY"] = "k"
        try:
            with mock.patch("urllib.request.urlopen", boom):
                out = list_models(cfg)
            self.assertFalse(out["ok"])
            blob = json.dumps(out)
            self.assertNotIn("E" * 50, blob)
            self.assertLess(len(blob), 10_000)
            for n in read_sizes:
                self.assertIsInstance(n, int)
                self.assertGreaterEqual(n, 0)
            self.assertTrue(
                not read_sizes or all(n >= 0 for n in read_sizes),
                f"unbounded read: {read_sizes}",
            )
        finally:
            os.environ.pop("CLOAKCLI_TMP_KEY", None)

    def test_list_models_http_error_huge_body_not_kept(self):
        os.environ["CLOAKCLI_TMP_KEY"] = "k"
        huge = "x" * (MAX_MODELS_BODY + 2048)
        httpd, cfg, _ = self._serve(lambda path: (500, huge))
        try:
            out = list_models(cfg)
            self.assertFalse(out["ok"])
            blob = json.dumps(out)
            self.assertNotIn("x" * 50, blob)
            self.assertLess(len(out.get("error") or ""), 500)
        finally:
            httpd.shutdown()
            os.environ.pop("CLOAKCLI_TMP_KEY", None)

    def test_list_models_rejects_key_and_control_char_ids(self):
        os.environ["CLOAKCLI_TMP_KEY"] = "sk-id-secret-xyz"
        httpd, cfg, _ = self._serve(
            lambda path: (
                200,
                json.dumps(
                    {
                        "data": [
                            {"id": "sk-id-secret-xyz"},
                            {"id": "pre-sk-id-secret-xyz"},
                            {"id": "ok-model"},
                            {"id": "bad\x07id"},
                        ]
                    }
                ),
            )
        )
        try:
            out = list_models(cfg)
            self.assertTrue(out["ok"], out)
            self.assertEqual(out["ids"], ["ok-model"])
            blob = json.dumps(out)
            self.assertNotIn("sk-id-secret-xyz", blob)
            self.assertNotIn("\x07", blob)
        finally:
            httpd.shutdown()
            os.environ.pop("CLOAKCLI_TMP_KEY", None)
