import unittest

from cloakcli_worker.recover.origin import origin_of, url_allowed


class OriginTests(unittest.TestCase):
    def test_origin_http(self):
        self.assertEqual(origin_of("https://Example.COM/foo?q=1"), "https://example.com")

    def test_reject_file_javascript_data(self):
        for u in (
            "file:///etc/passwd",
            "javascript:alert(1)",
            "data:text/html,hi",
            "about:blank",
        ):
            ok, reason = url_allowed(u, task_origin="https://example.com", allow_hosts=[])
            self.assertFalse(ok, u)
            self.assertIn("scheme", reason)

    def test_same_origin_ok(self):
        ok, _ = url_allowed(
            "https://example.com/more",
            task_origin="https://example.com",
            allow_hosts=[],
        )
        self.assertTrue(ok)

    def test_cross_origin_requires_allow_hosts(self):
        ok, reason = url_allowed(
            "https://evil.example/x",
            task_origin="https://example.com",
            allow_hosts=[],
        )
        self.assertFalse(ok)
        self.assertIn("allow_hosts", reason)

        ok, _ = url_allowed(
            "https://evil.example/x",
            task_origin="https://example.com",
            allow_hosts=["evil.example"],
        )
        self.assertTrue(ok)

    def test_wildcard_allow_host(self):
        ok, _ = url_allowed(
            "https://sub.foo.com/a",
            task_origin="https://example.com",
            allow_hosts=["*.foo.com"],
        )
        self.assertTrue(ok)
