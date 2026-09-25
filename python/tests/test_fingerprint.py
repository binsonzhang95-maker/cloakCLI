"""Unit tests for persistent fingerprint_seed helper."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cloakcli_worker.fingerprint import (
    SEED_MAX,
    SEED_MIN,
    GeoResolutionError,
    PersonaWhitelistError,
    apply_to_launch_kwargs,
    ensure_fingerprint_persona,
    ensure_fingerprint_seed,
    ensure_language_preference,
    fingerprint_chrome_args,
    geo_cache_valid,
    is_register_launch,
    is_valid_fingerprint_seed,
    is_whitelisted_persona,
    mint_fingerprint_persona,
    mint_fingerprint_seed,
    persona_brand_tuple,
    proxy_session_identity,
    resolve_fingerprint_seed,
    resolve_geo_for_launch,
    validate_persona,
    verified_brand_tuples,
)


class FingerprintSeedTests(unittest.TestCase):
    def test_mint_in_range(self):
        for _ in range(50):
            s = mint_fingerprint_seed()
            self.assertTrue(SEED_MIN <= s <= SEED_MAX)

    def test_args_override_shape(self):
        args = fingerprint_chrome_args(12345)
        self.assertIn("--fingerprint=12345", args)
        self.assertTrue(any(a.startswith("--fingerprint-brand=") for a in args))
        with self.assertRaises(ValueError):
            fingerprint_chrome_args(999)
        with self.assertRaises(ValueError):
            fingerprint_chrome_args(100000)

    def test_is_valid(self):
        self.assertTrue(is_valid_fingerprint_seed(10000))
        self.assertTrue(is_valid_fingerprint_seed(99999))
        self.assertTrue(is_valid_fingerprint_seed("42424"))
        self.assertFalse(is_valid_fingerprint_seed(9999))
        self.assertFalse(is_valid_fingerprint_seed(100000))
        self.assertFalse(is_valid_fingerprint_seed(True))
        self.assertFalse(is_valid_fingerprint_seed(None))
        self.assertFalse(is_valid_fingerprint_seed("abc"))

    def test_mint_and_persist(self):
        root = Path(tempfile.mkdtemp(prefix="fp_seed_"))
        meta = root / "profiles" / "demo" / "profile.json"
        meta.parent.mkdir(parents=True)
        meta.write_text(
            json.dumps(
                {
                    "name": "demo",
                    "proxy": "http://user:hunter2@127.0.0.1:7890",
                    "notes": "keep-me",
                    "user_data_dir": "data/profiles/demo",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        seed = ensure_fingerprint_seed(meta)
        self.assertTrue(SEED_MIN <= seed <= SEED_MAX)
        data = json.loads(meta.read_text(encoding="utf-8"))
        self.assertEqual(data["fingerprint_seed"], seed)
        self.assertEqual(data["proxy"], "http://user:hunter2@127.0.0.1:7890")
        self.assertEqual(data["notes"], "keep-me")
        # Second call returns same seed (persist)
        self.assertEqual(ensure_fingerprint_seed(meta), seed)
        self.assertEqual(ensure_fingerprint_seed(meta), seed)

    def test_regenerate(self):
        root = Path(tempfile.mkdtemp(prefix="fp_regen_"))
        meta = root / "profile.json"
        with mock.patch(
            "cloakcli_worker.fingerprint.mint_fingerprint_seed",
            side_effect=[11111, 22222],
        ):
            first = ensure_fingerprint_seed(meta)
            self.assertEqual(first, 11111)
            second = ensure_fingerprint_seed(meta, regenerate=True)
            self.assertEqual(second, 22222)
        data = json.loads(meta.read_text(encoding="utf-8"))
        self.assertEqual(data["fingerprint_seed"], 22222)

    def test_invalid_remint(self):
        root = Path(tempfile.mkdtemp(prefix="fp_bad_"))
        meta = root / "profile.json"
        meta.write_text(
            json.dumps({"name": "x", "fingerprint_seed": 42, "notes": "n"}) + "\n",
            encoding="utf-8",
        )
        with mock.patch(
            "cloakcli_worker.fingerprint.mint_fingerprint_seed",
            return_value=55555,
        ):
            seed = ensure_fingerprint_seed(meta)
        self.assertEqual(seed, 55555)
        data = json.loads(meta.read_text(encoding="utf-8"))
        self.assertEqual(data["fingerprint_seed"], 55555)
        self.assertEqual(data["notes"], "n")

    def test_resolve_from_user_data_dir(self):
        root = Path(tempfile.mkdtemp(prefix="fp_resolve_"))
        profiles = root / "profiles"
        meta_dir = profiles / "cell8-01"
        meta_dir.mkdir(parents=True)
        ud = root / "data" / "profiles" / "cell8-01-pinterest-run"
        ud.mkdir(parents=True)
        meta = meta_dir / "profile.json"
        meta.write_text(
            json.dumps(
                {
                    "name": "cell8-01",
                    "user_data_dir": "data/profiles/cell8-01-pinterest-run",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        with mock.patch(
            "cloakcli_worker.fingerprint.mint_fingerprint_seed",
            return_value=77777,
        ):
            seed = resolve_fingerprint_seed(
                profiles_root=profiles,
                user_data_dir=ud,
            )
        self.assertEqual(seed, 77777)
        # Same again without remint
        self.assertEqual(
            resolve_fingerprint_seed(profiles_root=profiles, user_data_dir=str(ud)),
            77777,
        )

    def test_args_override_conceptually_dedupe_key(self):
        """cloakbrowser build_args dedupes by flag key; our args win over random default."""
        seed = 99887
        extra = fingerprint_chrome_args(seed)
        # Simulate merge: default random then user override by key prefix
        default = ["--fingerprint=11111", "--fingerprint-platform=windows"]
        merged: dict[str, str] = {}
        for a in default + extra:
            key = a.split("=", 1)[0]
            merged[key] = a
        self.assertEqual(merged["--fingerprint"], "--fingerprint=99887")
        self.assertEqual(merged["--fingerprint-platform"], "--fingerprint-platform=windows")
        self.assertEqual(merged["--fingerprint-brand"], "--fingerprint-brand=Chrome")


class PersonaMintTests(unittest.TestCase):
    def test_mint_determinism_from_seed(self):
        a = mint_fingerprint_persona(42424)
        b = mint_fingerprint_persona(42424)
        self.assertEqual(a, b)
        self.assertTrue(is_whitelisted_persona(a))
        self.assertEqual(a["brand"], "Chrome")
        self.assertEqual(a["platform"], "windows")
        self.assertEqual(a["device_scale_factor"], 1)
        self.assertEqual(
            a["viewport_height"],
            a["screen_height"] - a["taskbar_height"] - a["chrome_ui_height"],
        )
        self.assertEqual(a["available_height"], a["screen_height"] - a["taskbar_height"])
        self.assertEqual(a["viewport_width"], a["screen_width"])

    def test_different_seeds_vary_non_brand_fields(self):
        seen = set()
        for seed in (10000, 22222, 33333, 44444, 55555, 66666, 77777, 88888, 99999):
            p = mint_fingerprint_persona(seed)
            seen.add(
                (
                    p["platform_version"],
                    p["hardware_concurrency"],
                    p["device_memory"],
                    p["screen_width"],
                    p["screen_height"],
                )
            )
        self.assertGreater(len(seen), 1)

    def test_whitelist_only_chrome_windows(self):
        for tup in verified_brand_tuples():
            self.assertEqual(tup[0], "Chrome")
            self.assertEqual(tup[3], "windows")
            self.assertNotEqual(tup[0], "Opera")
            self.assertNotEqual(tup[0], "Vivaldi")
            self.assertNotEqual(tup[0], "Edge")

    def test_whitelist_rejection_opera(self):
        bad = mint_fingerprint_persona(12345)
        bad = dict(bad)
        bad["brand"] = "Opera"
        self.assertFalse(is_whitelisted_persona(bad))
        with self.assertRaises(PersonaWhitelistError):
            validate_persona(bad)
        with self.assertRaises(PersonaWhitelistError):
            fingerprint_chrome_args(12345, bad)

    def test_hw_not_stuck_at_8_8_across_seeds(self):
        combos = {
            (mint_fingerprint_persona(s)["hardware_concurrency"], mint_fingerprint_persona(s)["device_memory"])
            for s in range(10000, 10150)
        }
        self.assertGreater(len(combos), 1)
        self.assertTrue(any(c != (8, 8) for c in combos))

    def test_persist_and_reject_then_remint(self):
        root = Path(tempfile.mkdtemp(prefix="fp_persona_"))
        meta = root / "profile.json"
        meta.write_text(json.dumps({"name": "x", "fingerprint_seed": 42424}) + "\n", encoding="utf-8")
        first = ensure_fingerprint_persona(meta, 42424)
        self.assertEqual(first, mint_fingerprint_persona(42424))
        data = json.loads(meta.read_text(encoding="utf-8"))
        data["fingerprint_persona"]["brand"] = "Vivaldi"
        meta.write_text(json.dumps(data) + "\n", encoding="utf-8")
        reminted = ensure_fingerprint_persona(meta, 42424)
        self.assertEqual(reminted["brand"], "Chrome")
        self.assertEqual(reminted, mint_fingerprint_persona(42424))


class GeoCacheTests(unittest.TestCase):
    def _city(self, ip: str) -> dict:
        return {"timezone": "America/New_York", "country": "US", "locale_from_geo": "en-US"}

    def test_proxy_identity_ignores_password(self):
        a = proxy_session_identity("http://user-session-abc:secret@proxy.example:6969")
        b = proxy_session_identity("http://user-session-abc:other@proxy.example:6969")
        c = proxy_session_identity("http://user-session-xyz:secret@proxy.example:6969")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_cache_hit_same_exit_ip(self):
        root = Path(tempfile.mkdtemp(prefix="fp_geo_hit_"))
        meta = root / "profile.json"
        meta.write_text(json.dumps({"name": "g"}) + "\n", encoding="utf-8")
        calls = {"city": 0}

        def lookup(ip: str) -> dict:
            calls["city"] += 1
            return self._city(ip)

        first = resolve_geo_for_launch(
            proxy="http://sess:pw@127.0.0.1:9",
            meta_path=meta,
            require_geo=True,
            resolve_exit_ip=lambda p: "8.8.8.8",
            lookup_city=lookup,
            db_version="geolite2-city:1",
        )
        self.assertEqual(first["timezone"], "America/New_York")
        self.assertEqual(calls["city"], 1)
        second = resolve_geo_for_launch(
            proxy="http://sess:pw@127.0.0.1:9",
            meta_path=meta,
            require_geo=True,
            resolve_exit_ip=lambda p: "8.8.8.8",
            lookup_city=lookup,
            db_version="geolite2-city:1",
        )
        self.assertEqual(second["exit_ip"], "8.8.8.8")
        self.assertEqual(calls["city"], 1)

    def test_cache_invalidate_on_exit_ip_change(self):
        root = Path(tempfile.mkdtemp(prefix="fp_geo_ip_"))
        meta = root / "profile.json"
        meta.write_text(json.dumps({"name": "g"}) + "\n", encoding="utf-8")
        cities = {
            "8.8.8.8": {
                "timezone": "America/New_York",
                "country": "US",
                "locale_from_geo": "en-US",
            },
            "9.9.9.9": {
                "timezone": "America/Chicago",
                "country": "US",
                "locale_from_geo": "en-US",
            },
        }
        resolve_geo_for_launch(
            proxy="http://sess:pw@127.0.0.1:9",
            meta_path=meta,
            require_geo=True,
            resolve_exit_ip=lambda p: "8.8.8.8",
            lookup_city=lambda ip: cities[ip],
            db_version="geolite2-city:1",
        )
        updated = resolve_geo_for_launch(
            proxy="http://sess:pw@127.0.0.1:9",
            meta_path=meta,
            require_geo=True,
            resolve_exit_ip=lambda p: "9.9.9.9",
            lookup_city=lambda ip: cities[ip],
            db_version="geolite2-city:1",
        )
        self.assertEqual(updated["timezone"], "America/Chicago")
        self.assertEqual(updated["exit_ip"], "9.9.9.9")
        persisted = json.loads(meta.read_text(encoding="utf-8"))
        self.assertEqual(persisted["geo_cache"]["exit_ip"], "9.9.9.9")

    def test_cache_invalidate_on_db_version_change(self):
        cache = {
            "schema": 1,
            "proxy_identity": "abc",
            "exit_ip": "8.8.8.8",
            "timezone": "America/New_York",
            "looked_up_at": "2026-09-25T00:00:00+00:00",
            "geo_db_version": "geolite2-city:1",
        }
        self.assertTrue(
            geo_cache_valid(
                cache,
                proxy_identity="abc",
                exit_ip="8.8.8.8",
                geo_db_version="geolite2-city:1",
            )
        )
        self.assertFalse(
            geo_cache_valid(
                cache,
                proxy_identity="abc",
                exit_ip="8.8.8.8",
                geo_db_version="geolite2-city:2",
            )
        )

    def test_fail_closed_unknown_timezone(self):
        with self.assertRaises(GeoResolutionError) as ctx:
            resolve_geo_for_launch(
                proxy="http://sess:pw@127.0.0.1:9",
                require_geo=True,
                resolve_exit_ip=lambda p: "8.8.8.8",
                lookup_city=lambda ip: {
                    "timezone": "Not/AZone",
                    "country": "US",
                    "locale_from_geo": "en-US",
                },
                db_version="geolite2-city:1",
            )
        self.assertIn("GEO_TIMEZONE", str(ctx.exception))

    def test_fail_closed_timeout(self):
        def boom(_proxy: str) -> str:
            raise GeoResolutionError("GEO_TIMEOUT: could not discover proxy exit IP via echo")

        with self.assertRaises(GeoResolutionError) as ctx:
            resolve_geo_for_launch(
                proxy="http://sess:pw@127.0.0.1:9",
                require_geo=True,
                resolve_exit_ip=boom,
                lookup_city=self._city,
                db_version="geolite2-city:1",
            )
        self.assertIn("GEO_TIMEOUT", str(ctx.exception))

    def test_fail_open_without_require_geo_does_not_host_fallback(self):
        def boom(_proxy: str) -> str:
            raise GeoResolutionError("GEO_DB_MISSING: GeoLite2-City database unavailable")

        out = resolve_geo_for_launch(
            proxy="http://sess:pw@127.0.0.1:9",
            require_geo=False,
            resolve_exit_ip=boom,
            lookup_city=self._city,
            db_version="geolite2-city:1",
        )
        self.assertEqual(out, {})
        self.assertNotIn("timezone", out)

    def test_language_not_overwritten_from_country(self):
        root = Path(tempfile.mkdtemp(prefix="fp_lang_"))
        meta = root / "profile.json"
        meta.write_text(
            json.dumps({"name": "g", "language": "en-GB"}) + "\n",
            encoding="utf-8",
        )
        self.assertEqual(ensure_language_preference(meta, geo_locale="ja-JP"), "en-GB")
        data = json.loads(meta.read_text(encoding="utf-8"))
        self.assertEqual(data["language"], "en-GB")

    def test_language_minted_once_from_geo(self):
        root = Path(tempfile.mkdtemp(prefix="fp_lang2_"))
        meta = root / "profile.json"
        meta.write_text(json.dumps({"name": "g"}) + "\n", encoding="utf-8")
        self.assertEqual(ensure_language_preference(meta, geo_locale="de-DE"), "de-DE")
        self.assertEqual(ensure_language_preference(meta, geo_locale="ja-JP"), "de-DE")


class LaunchKwargsTests(unittest.TestCase):
    def test_args_include_persona_and_geo_flags(self):
        root = Path(tempfile.mkdtemp(prefix="fp_launch_"))
        meta = root / "profile.json"
        meta.write_text(json.dumps({"name": "g", "fingerprint_seed": 42424}) + "\n", encoding="utf-8")
        kwargs: dict = {"user_data_dir": "/tmp/ud", "headless": True}
        apply_to_launch_kwargs(
            kwargs,
            seed=42424,
            proxy="http://sess:pw@127.0.0.1:9",
            headed=False,
            profile_meta_path=meta,
            require_geo=True,
            resolve_exit_ip=lambda p: "8.8.8.8",
            lookup_city=lambda ip: {
                "timezone": "America/New_York",
                "country": "US",
                "locale_from_geo": "en-US",
            },
            db_version="geolite2-city:1",
        )
        args = kwargs["args"]
        self.assertIn("--fingerprint=42424", args)
        self.assertIn("--fingerprint-brand=Chrome", args)
        self.assertTrue(any(a.startswith("--fingerprint-platform-version=") for a in args))
        self.assertTrue(any(a.startswith("--fingerprint-hardware-concurrency=") for a in args))
        self.assertTrue(any(a.startswith("--fingerprint-device-memory=") for a in args))
        self.assertTrue(any(a.startswith("--fingerprint-screen-width=") for a in args))
        self.assertIn("--fingerprint-timezone=America/New_York", args)
        self.assertIn("--lang=en-US", args)
        self.assertIn("--fingerprint-locale=en-US", args)
        self.assertIn("--fingerprint-webrtc-ip=8.8.8.8", args)
        self.assertTrue(kwargs.get("geoip"))
        self.assertEqual(kwargs.get("timezone"), "America/New_York")
        self.assertEqual(kwargs.get("locale"), "en-US")
        self.assertIn("viewport", kwargs)
        self.assertNotIn("user_agent", kwargs)
        persona = mint_fingerprint_persona(42424)
        self.assertEqual(
            kwargs["viewport"],
            {"width": persona["viewport_width"], "height": persona["viewport_height"]},
        )

    def test_fail_closed_on_geo_when_required(self):
        kwargs: dict = {"user_data_dir": "/tmp/ud", "headless": True}
        with self.assertRaises(GeoResolutionError):
            apply_to_launch_kwargs(
                kwargs,
                seed=42424,
                proxy="http://sess:pw@127.0.0.1:9",
                headed=False,
                require_geo=True,
                resolve_exit_ip=lambda p: (_ for _ in ()).throw(
                    GeoResolutionError("GEO_DB_MISSING: GeoLite2-City database unavailable")
                ),
            )

    def test_headed_does_not_set_playwright_viewport(self):
        kwargs: dict = {"user_data_dir": "/tmp/ud", "headless": False}
        apply_to_launch_kwargs(kwargs, seed=42424, headed=True, require_geo=False)
        self.assertNotIn("viewport", kwargs)

    def test_register_path_detection(self):
        self.assertTrue(is_register_launch("pinterest-register-visual", None))
        self.assertTrue(is_register_launch(None, "skills/pinterest-register-visual/skill.json"))
        self.assertFalse(is_register_launch("pinterest-nurture-browse", "skills/pinterest-nurture-browse/skill.json"))


if __name__ == "__main__":
    unittest.main()
