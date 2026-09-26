"""Dual free-bin (145 + 146) profile binding tests."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cloakcli_worker.browser import launch_context
from cloakcli_worker.fingerprint import (
    ALLOWED_BROWSER_VERSIONS,
    LEGACY_BROWSER_VERSION,
    BrowserVersionError,
    assign_browser_version_for_new_seed,
    ensure_browser_version,
    ensure_fingerprint_persona,
    ensure_fingerprint_seed,
    mint_fingerprint_persona,
    public_chromium_version,
    resolve_browser_version_for_launch,
)


class DualBinPinTests(unittest.TestCase):
    def test_allowed_pins_are_exact_free_builds(self):
        self.assertEqual(
            ALLOWED_BROWSER_VERSIONS,
            ("145.0.7632.109.2", "146.0.7680.177.5"),
        )
        self.assertEqual(LEGACY_BROWSER_VERSION, "146.0.7680.177.5")

    def test_assign_new_seed_is_deterministic_modulo(self):
        a = assign_browser_version_for_new_seed(42424)
        b = assign_browser_version_for_new_seed(42424)
        self.assertEqual(a, b)
        self.assertEqual(a, ALLOWED_BROWSER_VERSIONS[42424 % 2])
        other = assign_browser_version_for_new_seed(42425)
        self.assertEqual(other, ALLOWED_BROWSER_VERSIONS[42425 % 2])
        self.assertNotEqual(a, other)

    def test_public_version_strips_cloak_suffix(self):
        self.assertEqual(
            public_chromium_version("145.0.7632.109.2"),
            "145.0.7632.109",
        )
        self.assertEqual(
            public_chromium_version("146.0.7680.177.5"),
            "146.0.7680.177",
        )

    def test_persona_follows_bound_binary_not_wrapper_default(self):
        p145 = mint_fingerprint_persona(42424, browser_version="145.0.7632.109.2")
        p146 = mint_fingerprint_persona(42424, browser_version="146.0.7680.177.5")
        self.assertEqual(p145["brand_version"], "145.0.7632.109")
        self.assertEqual(p145["chromium_compatible"], "145.0.7632.109")
        self.assertEqual(p146["brand_version"], "146.0.7680.177")
        self.assertEqual(p146["chromium_compatible"], "146.0.7680.177")
        # Same seed → same non-version geometry/hw/gpu (RNG aligned after brand pick).
        self.assertEqual(p145["screen_width"], p146["screen_width"])
        self.assertEqual(p145["gpu_vendor"], p146["gpu_vendor"])


class DualBinProfileBindingTests(unittest.TestCase):
    def test_legacy_profile_without_field_binds_146_no_remint(self):
        root = Path(tempfile.mkdtemp(prefix="dual_legacy_"))
        meta = root / "profile.json"
        persona = mint_fingerprint_persona(42424, browser_version=LEGACY_BROWSER_VERSION)
        meta.write_text(
            json.dumps(
                {
                    "name": "legacy",
                    "fingerprint_seed": 42424,
                    "fingerprint_persona": persona,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        pin = ensure_browser_version(meta, seed=42424)
        self.assertEqual(pin, LEGACY_BROWSER_VERSION)
        data = json.loads(meta.read_text(encoding="utf-8"))
        self.assertEqual(data["browser_version"], LEGACY_BROWSER_VERSION)
        # Persona unchanged (no silent remint across majors).
        bound = ensure_fingerprint_persona(meta, 42424)
        self.assertEqual(bound, persona)

    def test_new_profile_assigns_from_seed_modulo(self):
        root = Path(tempfile.mkdtemp(prefix="dual_new_"))
        meta = root / "profile.json"
        # Name-only shell — not legacy (no seed/persona yet).
        meta.write_text(json.dumps({"name": "fresh"}) + "\n", encoding="utf-8")
        seed = ensure_fingerprint_seed(meta)
        data = json.loads(meta.read_text(encoding="utf-8"))
        expected = assign_browser_version_for_new_seed(seed)
        self.assertEqual(data["browser_version"], expected)
        self.assertEqual(
            data["fingerprint_persona"]["brand_version"],
            public_chromium_version(expected),
        )

    def test_hot_switch_forbidden(self):
        root = Path(tempfile.mkdtemp(prefix="dual_hot_"))
        meta = root / "profile.json"
        meta.write_text(
            json.dumps(
                {
                    "name": "x",
                    "fingerprint_seed": 42424,
                    "browser_version": "146.0.7680.177.5",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        with self.assertRaises(BrowserVersionError) as ctx:
            ensure_browser_version(meta, seed=42424, explicit="145.0.7632.109.2")
        self.assertIn("hot-switch", str(ctx.exception))

    def test_unpinned_launch_forbidden_in_dual_bin(self):
        env = {"CLOAKCLI_DUAL_BIN": "1"}
        # Clear any pin env.
        env_clear = {k: v for k, v in os.environ.items() if k != "CLOAKCLI_BROWSER_VERSION"}
        env_clear.update(env)
        with mock.patch.dict(os.environ, env_clear, clear=True):
            with self.assertRaises(BrowserVersionError) as ctx:
                resolve_browser_version_for_launch()
            self.assertIn("unpinned", str(ctx.exception).lower())

    def test_launch_passes_pinned_browser_version(self):
        root = Path(tempfile.mkdtemp(prefix="dual_launch_"))
        meta = root / "profile.json"
        meta.write_text(
            json.dumps(
                {
                    "name": "demo",
                    "fingerprint_seed": 42424,
                    "browser_version": "145.0.7632.109.2",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        with mock.patch("cloakbrowser.launch_persistent_context") as launch:
            with mock.patch(
                "cloakcli_worker.fingerprint.enforce_windows_font_gate",
                return_value=[],
            ):
                launch_context(
                    user_data_dir=str(root / "ud"),
                    headed=False,
                    fingerprint_seed=42424,
                    profile_meta_path=meta,
                    require_geo=False,
                )
        kwargs = launch.call_args.kwargs
        self.assertEqual(kwargs.get("browser_version"), "145.0.7632.109.2")
        self.assertIn("--fingerprint-brand=Chrome", kwargs["args"])
        self.assertIn("--fingerprint-brand-version=145.0.7632.109", kwargs["args"])

    def test_launch_146_persona_matches_bound_binary(self):
        root = Path(tempfile.mkdtemp(prefix="dual_launch146_"))
        meta = root / "profile.json"
        meta.write_text(
            json.dumps(
                {
                    "name": "demo",
                    "fingerprint_seed": 42424,
                    "browser_version": "146.0.7680.177.5",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        with mock.patch("cloakbrowser.launch_persistent_context") as launch:
            with mock.patch(
                "cloakcli_worker.fingerprint.enforce_windows_font_gate",
                return_value=[],
            ):
                launch_context(
                    user_data_dir=str(root / "ud"),
                    headed=False,
                    fingerprint_seed=42424,
                    profile_meta_path=meta,
                    require_geo=False,
                )
        kwargs = launch.call_args.kwargs
        self.assertEqual(kwargs.get("browser_version"), "146.0.7680.177.5")
        self.assertIn("--fingerprint-brand-version=146.0.7680.177", kwargs["args"])


    def test_seedless_user_agent_forbidden_under_dual_bin(self):
        with mock.patch.dict(
            os.environ,
            {"CLOAKCLI_DUAL_BIN": "1", "CLOAKCLI_BROWSER_VERSION": "145.0.7632.109.2"},
        ):
            with mock.patch("cloakbrowser.launch_persistent_context") as launch:
                with self.assertRaises(BrowserVersionError) as ctx:
                    launch_context(
                        user_data_dir="/tmp/ud",
                        headed=False,
                        user_agent="Mozilla/5.0 FakeUA-Chrome/999",
                        require_geo=False,
                        browser_version="145.0.7632.109.2",
                    )
                self.assertIn("user_agent", str(ctx.exception).lower())
                launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
