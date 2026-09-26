"""Unit tests for launch_context persona + geoip wiring."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cloakcli_worker.browser import launch_context
from cloakcli_worker.fingerprint import GeoResolutionError, mint_fingerprint_persona


class LaunchContextTests(unittest.TestCase):
    def test_launch_args_include_persona_and_geo(self):
        root = Path(tempfile.mkdtemp(prefix="fp_ctx_"))
        meta = root / "profile.json"
        meta.write_text(
            json.dumps({"name": "demo", "fingerprint_seed": 42424}) + "\n",
            encoding="utf-8",
        )
        geo = {
            "schema": 1,
            "proxy_identity": "x",
            "exit_ip": "8.8.8.8",
            "timezone": "America/New_York",
            "locale_from_geo": "en-US",
            "country": "US",
            "looked_up_at": "2026-09-25T00:00:00+00:00",
            "geo_db_version": "geolite2-city:1",
        }
        with mock.patch("cloakbrowser.launch_persistent_context") as launch:
            with mock.patch(
                "cloakcli_worker.fingerprint.resolve_geo_for_launch",
                return_value=geo,
            ):
                with mock.patch(
                    "cloakcli_worker.fingerprint.enforce_windows_font_gate",
                    return_value=[],
                ):
                    with mock.patch(
                        "cloakcli_worker.fingerprint.verify_webrtc_ice_no_leak",
                        return_value={"page": []},
                    ):
                        with mock.patch(
                            "cloakcli_worker.browser.get_page",
                            return_value=mock.Mock(),
                        ):
                            launch_context(
                                user_data_dir=str(root / "ud"),
                                headed=False,
                                proxy="http://sess:pw@127.0.0.1:9",
                                fingerprint_seed=42424,
                                profile_meta_path=meta,
                                require_geo=True,
                            )
        self.assertTrue(launch.called)
        kwargs = launch.call_args.kwargs
        args = kwargs["args"]
        self.assertIn("--fingerprint=42424", args)
        self.assertIn("--fingerprint-brand=Chrome", args)
        persona = mint_fingerprint_persona(42424)
        self.assertIn(f"--fingerprint-gpu-vendor={persona['gpu_vendor']}", args)
        self.assertIn(f"--fingerprint-gpu-renderer={persona['gpu_renderer']}", args)
        self.assertIn("--fingerprint-timezone=America/New_York", args)
        self.assertIn("--fingerprint-webrtc-ip=8.8.8.8", args)
        self.assertIn("--lang=en-US", args)
        self.assertTrue(kwargs.get("geoip"))
        self.assertEqual(kwargs.get("timezone"), "America/New_York")
        self.assertNotIn("user_agent", kwargs)
        self.assertEqual(
            kwargs["viewport"],
            {"width": persona["viewport_width"], "height": persona["viewport_height"]},
        )

    def test_register_path_fail_closed(self):
        with mock.patch("cloakbrowser.launch_persistent_context") as launch:
            with mock.patch(
                "cloakcli_worker.fingerprint.enforce_windows_font_gate",
                return_value=[],
            ):
                with mock.patch(
                    "cloakcli_worker.fingerprint.resolve_geo_for_launch",
                    side_effect=GeoResolutionError("GEO_DB_MISSING: GeoLite2-City database unavailable"),
                ):
                    with self.assertRaises(GeoResolutionError):
                        launch_context(
                            user_data_dir="/tmp/ud",
                            headed=False,
                            proxy="http://sess:pw@127.0.0.1:9",
                            fingerprint_seed=42424,
                            skill_name="pinterest-register-visual",
                            skill_path="skills/pinterest-register-visual/skill.json",
                        )
        launch.assert_not_called()

    def test_headed_emits_window_size(self):
        persona = mint_fingerprint_persona(42424)
        with mock.patch("cloakbrowser.launch_persistent_context") as launch:
            with mock.patch(
                "cloakcli_worker.fingerprint.enforce_windows_font_gate",
                return_value=[],
            ):
                launch_context(
                    user_data_dir="/tmp/ud",
                    headed=True,
                    fingerprint_seed=42424,
                    require_geo=False,
                )
        kwargs = launch.call_args.kwargs
        self.assertIn(
            f"--window-size={persona['screen_width']},{persona['screen_height']}",
            kwargs["args"],
        )
        self.assertNotIn("viewport", kwargs)

    def test_no_playwright_user_agent_when_persona_present(self):
        with mock.patch("cloakbrowser.launch_persistent_context") as launch:
            with mock.patch(
                "cloakcli_worker.fingerprint.enforce_windows_font_gate",
                return_value=[],
            ):
                launch_context(
                    user_data_dir="/tmp/ud",
                    headed=True,
                    fingerprint_seed=42424,
                    user_agent="Mozilla/5.0 FakeUA",
                    require_geo=False,
                )
        kwargs = launch.call_args.kwargs
        self.assertNotIn("user_agent", kwargs)
        self.assertIn("--fingerprint-brand=Chrome", kwargs["args"])


    def test_register_path_runs_webrtc_ice_verify(self):
        geo = {
            "schema": 1,
            "proxy_identity": "x",
            "exit_ip": "8.8.8.8",
            "timezone": "America/New_York",
            "locale_from_geo": "en-US",
            "country": "US",
            "looked_up_at": "2026-09-25T00:00:00+00:00",
            "geo_db_version": "geolite2-city:1",
        }
        with mock.patch("cloakbrowser.launch_persistent_context") as launch:
            launch.return_value = mock.Mock()
            with mock.patch(
                "cloakcli_worker.fingerprint.resolve_geo_for_launch",
                return_value=geo,
            ):
                with mock.patch(
                    "cloakcli_worker.fingerprint.enforce_windows_font_gate",
                    return_value=[],
                ):
                    with mock.patch(
                        "cloakcli_worker.fingerprint.verify_webrtc_ice_no_leak",
                        return_value={"page": [], "iframe": [], "worker": []},
                    ) as ice:
                        with mock.patch(
                            "cloakcli_worker.browser.get_page",
                            return_value=mock.Mock(),
                        ):
                            launch_context(
                                user_data_dir="/tmp/ud",
                                headed=False,
                                proxy="http://sess:pw@127.0.0.1:9",
                                fingerprint_seed=42424,
                                skill_name="pinterest-register-visual",
                                skill_path="skills/pinterest-register-visual/skill.json",
                            )
        ice.assert_called_once()
        self.assertEqual(ice.call_args.args[1], "8.8.8.8")


if __name__ == "__main__":
    unittest.main()
