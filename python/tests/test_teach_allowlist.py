"""Runtime origin allowlist: inject/page_state gated; http(s) goto still recorded."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cloakbrowser

from cloakcli_worker.teach import _preflight_binary

ROOT = Path(__file__).resolve().parents[2]
BG = ROOT / "extensions" / "teach" / "background.js"
POPUP = ROOT / "extensions" / "teach" / "popup.js"
POPUP_HTML = ROOT / "extensions" / "teach" / "popup.html"
RUNTIME = Path(__file__).resolve().parent / "teach_allowlist_runtime.js"
MANIFEST = ROOT / "extensions" / "teach" / "manifest.json"


class TeachAllowlistTests(unittest.TestCase):
    def test_unapproved_second_origin_not_injected_or_recorded(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "node is required for the teach allowlist runtime test")
        self.assertTrue(RUNTIME.is_file(), RUNTIME)
        proc = subprocess.run(
            [node, str(RUNTIME)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        self.assertEqual(proc.returncode, 0, out)
        self.assertIn("unapproved second origin not injected", out)
        self.assertIn("unapproved second origin http(s) navigation recorded", out)
        self.assertIn("unapproved origin click not recorded", out)
        self.assertIn("blocked schemes not recorded", out)
        self.assertIn("takeover records off-allowlist http(s) goto", out)

    def test_background_does_not_auto_add_on_navigation(self):
        text = BG.read_text(encoding="utf-8")
        # Navigation listeners must not grow the allowlist; approveOrigin is the only add path
        # besides session.json loadConfig.
        committed = text.split("webNavigation.onCommitted", 1)[1].split(
            "tabs.onUpdated", 1
        )[0]
        updated = text.split("tabs.onUpdated", 1)[1].split(
            "runtime.onMessage", 1
        )[0]
        self.assertNotIn("requestOrigin", committed)
        self.assertNotIn("addOrigin", committed)
        self.assertNotIn("requestOrigin", updated)
        self.assertNotIn("addOrigin", updated)
        start = text.split('type === "start"', 1)[1].split('type === "stop"', 1)[0]
        self.assertNotIn("requestOrigin", start)
        self.assertIn("approveOrigin", text)
        self.assertIn("approveOrigin", POPUP.read_text(encoding="utf-8"))
        self.assertIn("Allow this origin", POPUP_HTML.read_text(encoding="utf-8"))

    def test_manifest_still_no_all_urls(self):
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertNotIn("content_scripts", data)
        text = MANIFEST.read_text(encoding="utf-8")
        self.assertNotIn("<all_urls>", text)
        hosts = data.get("host_permissions") or []
        self.assertIn("ws://127.0.0.1/*", hosts)
        self.assertTrue(all(h != "<all_urls>" for h in hosts))

    def test_preflight_missing_binary(self):
        with patch.object(
            cloakbrowser,
            "binary_info",
            return_value={"binary_path": "/nonexistent/cloak-chrome", "installed": False},
        ):
            with self.assertRaises(SystemExit) as ctx:
                _preflight_binary()
        self.assertIn("binary not found", str(ctx.exception))

    def test_preflight_ok_when_installed_file_exists(self):
        with tempfile.NamedTemporaryFile(prefix="cloak-chrome-", delete=False) as fh:
            fake = Path(fh.name)
        try:
            with patch.object(
                cloakbrowser,
                "binary_info",
                return_value={"binary_path": str(fake), "installed": True},
            ):
                self.assertEqual(_preflight_binary(), fake)
        finally:
            os.unlink(fake)


if __name__ == "__main__":
    unittest.main()
