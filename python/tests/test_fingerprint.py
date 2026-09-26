"""Unit tests for persistent fingerprint_seed helper."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cloakcli_worker.fingerprint import (
    PERSONA_SCHEMA,
    SEED_MAX,
    SEED_MIN,
    WINDOWS_MINIMUM_FONTS,
    FontAvailabilityError,
    GeoResolutionError,
    PersonaWhitelistError,
    WebrtcIceLeakError,
    apply_to_launch_kwargs,
    assert_webrtc_host_equals_exit_ip,
    ensure_fingerprint_persona,
    ensure_fingerprint_seed,
    ensure_language_preference,
    enforce_windows_font_gate,
    fingerprint_chrome_args,
    geo_cache_valid,
    gpu_hw_memory_pool,
    headed_window_size_arg,
    host_candidate_ips,
    is_register_launch,
    is_valid_fingerprint_seed,
    is_whitelisted_persona,
    mint_fingerprint_persona,
    mint_fingerprint_seed,
    missing_windows_minimum_fonts,
    parse_ice_candidate_ip,
    persona_brand_tuple,
    persona_derived_from_seed,
    persona_gpu_tuple,
    persona_window_geometry,
    proxy_session_identity,
    resolve_fingerprint_seed,
    resolve_geo_for_launch,
    validate_persona,
    verified_brand_tuples,
    verified_gpu_tuples,
)

# Synthetic fc-list blob with the full Windows minimum set (unit tests only).
_FULL_FONT_LISTING = "\n".join(f"/usr/share/fonts/x/{f}.ttf: {f}:style=Regular" for f in WINDOWS_MINIMUM_FONTS).lower()


class FingerprintSeedTests(unittest.TestCase):
    def test_mint_in_range(self):
        for _ in range(50):
            s = mint_fingerprint_seed()
            self.assertTrue(SEED_MIN <= s <= SEED_MAX)

    def test_args_override_shape(self):
        args = fingerprint_chrome_args(12345)
        self.assertIn("--fingerprint=12345", args)
        self.assertTrue(any(a.startswith("--fingerprint-brand=") for a in args))
        self.assertTrue(any(a.startswith("--fingerprint-gpu-vendor=") for a in args))
        self.assertTrue(any(a.startswith("--fingerprint-gpu-renderer=") for a in args))
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
        self.assertTrue(merged["--fingerprint-gpu-vendor"].startswith("--fingerprint-gpu-vendor="))
        self.assertTrue(merged["--fingerprint-gpu-renderer"].startswith("--fingerprint-gpu-renderer="))


class PersonaMintTests(unittest.TestCase):
    def test_mint_determinism_from_seed(self):
        a = mint_fingerprint_persona(42424)
        b = mint_fingerprint_persona(42424)
        self.assertEqual(a, b)
        self.assertTrue(is_whitelisted_persona(a))
        self.assertEqual(a["brand"], "Chrome")
        self.assertEqual(a["platform"], "windows")
        self.assertEqual(a["schema"], PERSONA_SCHEMA)
        self.assertEqual(a["derived_from_seed"], 42424)
        self.assertTrue(persona_derived_from_seed(a, 42424))
        self.assertFalse(persona_derived_from_seed(a, 11111))
        self.assertEqual(a["device_scale_factor"], 1)
        self.assertEqual(
            a["viewport_height"],
            a["screen_height"] - a["taskbar_height"] - a["chrome_ui_height"],
        )
        self.assertEqual(a["available_height"], a["screen_height"] - a["taskbar_height"])
        self.assertEqual(a["viewport_width"], a["screen_width"])
        self.assertEqual(a["gpu_vendor"], b["gpu_vendor"])
        self.assertEqual(a["gpu_renderer"], b["gpu_renderer"])
        self.assertIn(persona_gpu_tuple(a), verified_gpu_tuples())
        pool = gpu_hw_memory_pool(a["gpu_vendor"], a["gpu_renderer"])
        self.assertIsNotNone(pool)
        self.assertIn((a["hardware_concurrency"], a["device_memory"]), pool)

    def test_different_seeds_vary_non_brand_fields(self):
        seen = set()
        vendors = set()
        for seed in (10000, 22222, 33333, 44444, 55555, 66666, 77777, 88888, 99999):
            p = mint_fingerprint_persona(seed)
            seen.add(
                (
                    p["platform_version"],
                    p["hardware_concurrency"],
                    p["device_memory"],
                    p["gpu_vendor"],
                    p["gpu_renderer"],
                    p["screen_width"],
                    p["screen_height"],
                )
            )
            vendors.add(p["gpu_vendor"])
        self.assertGreater(len(seen), 1)
        self.assertGreater(len(vendors), 1)
        self.assertTrue(any("Intel" in v or "AMD" in v for v in vendors))

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

    def test_gpu_vendor_diversity_across_seeds(self):
        vendors = set()
        renderers = set()
        for seed in range(10000, 10150):
            p = mint_fingerprint_persona(seed)
            vendors.add(p["gpu_vendor"])
            renderers.add(p["gpu_renderer"])
            pool = gpu_hw_memory_pool(p["gpu_vendor"], p["gpu_renderer"])
            self.assertIsNotNone(pool)
            self.assertIn((p["hardware_concurrency"], p["device_memory"]), pool)
        self.assertGreaterEqual(len(vendors), 2)
        self.assertTrue(any("Intel" in v for v in vendors) or any("AMD" in v for v in vendors))
        self.assertGreater(len(renderers), 1)
        families = {v.split("(")[-1].rstrip(")") for v in vendors}
        self.assertNotEqual(families, {"NVIDIA"})

    def test_gpu_whitelist_covers_intel_amd_nvidia(self):
        vendors = {v for v, _r in verified_gpu_tuples()}
        self.assertIn("Google Inc. (Intel)", vendors)
        self.assertIn("Google Inc. (AMD)", vendors)
        self.assertIn("Google Inc. (NVIDIA)", vendors)
        for vendor, renderer in verified_gpu_tuples():
            self.assertTrue(vendor.startswith("Google Inc. ("))
            self.assertTrue(renderer.startswith("ANGLE ("))
            self.assertIn("Direct3D11 vs_5_0 ps_5_0, D3D11)", renderer)
            self.assertGreaterEqual(len(gpu_hw_memory_pool(vendor, renderer) or ()), 1)

    def test_whitelist_rejection_garbage_gpu(self):
        bad = dict(mint_fingerprint_persona(12345))
        bad["gpu_vendor"] = "Acme GPU Corp"
        bad["gpu_renderer"] = "SwiftShader Device"
        self.assertFalse(is_whitelisted_persona(bad))
        with self.assertRaises(PersonaWhitelistError):
            validate_persona(bad)
        with self.assertRaises(PersonaWhitelistError):
            fingerprint_chrome_args(12345, bad)

    def test_whitelist_rejection_mismatched_gpu_tuple(self):
        bad = dict(mint_fingerprint_persona(12345))
        bad["gpu_vendor"] = "Google Inc. (Intel)"
        bad["gpu_renderer"] = (
            "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"
        )
        self.assertFalse(is_whitelisted_persona(bad))
        with self.assertRaises(PersonaWhitelistError):
            validate_persona(bad)

    def test_whitelist_rejection_incoherent_gpu_hw_pair(self):
        vendor = "Google Inc. (Intel)"
        renderer = "ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)"
        bad = dict(mint_fingerprint_persona(12345))
        bad["gpu_vendor"] = vendor
        bad["gpu_renderer"] = renderer
        bad["hardware_concurrency"] = 16
        bad["device_memory"] = 8
        self.assertNotIn((16, 8), gpu_hw_memory_pool(vendor, renderer) or ())
        self.assertFalse(is_whitelisted_persona(bad))
        with self.assertRaises(PersonaWhitelistError):
            validate_persona(bad)

    def test_schema_v1_without_gpu_remints(self):
        root = Path(tempfile.mkdtemp(prefix="fp_schema_"))
        meta = root / "profile.json"
        stale = dict(mint_fingerprint_persona(42424))
        stale["schema"] = 1
        stale.pop("gpu_vendor", None)
        stale.pop("gpu_renderer", None)
        meta.write_text(
            json.dumps(
                {
                    "name": "x",
                    "fingerprint_seed": 42424,
                    "fingerprint_persona": stale,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        reminted = ensure_fingerprint_persona(meta, 42424)
        self.assertEqual(reminted["schema"], PERSONA_SCHEMA)
        self.assertIn("gpu_vendor", reminted)
        self.assertIn("gpu_renderer", reminted)
        self.assertEqual(reminted, mint_fingerprint_persona(42424))

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

    def test_ensure_same_seed_twice_is_stable(self):
        root = Path(tempfile.mkdtemp(prefix="fp_seed_stable_"))
        meta = root / "profile.json"
        meta.write_text(json.dumps({"name": "x", "fingerprint_seed": 42424}) + "\n", encoding="utf-8")
        first = ensure_fingerprint_persona(meta, 42424)
        second = ensure_fingerprint_persona(meta, 42424)
        minted = mint_fingerprint_persona(42424)
        self.assertEqual(first, second)
        self.assertEqual(first, minted)
        self.assertEqual(first["gpu_vendor"], minted["gpu_vendor"])
        self.assertEqual(first["gpu_renderer"], minted["gpu_renderer"])
        self.assertEqual(first["hardware_concurrency"], minted["hardware_concurrency"])
        self.assertEqual(first["device_memory"], minted["device_memory"])
        self.assertEqual(first["derived_from_seed"], 42424)
        data = json.loads(meta.read_text(encoding="utf-8"))
        self.assertEqual(data["fingerprint_persona"], first)
        self.assertEqual(data["fingerprint_seed"], 42424)

    def test_ensure_seed_change_remints_to_new_seed(self):
        root = Path(tempfile.mkdtemp(prefix="fp_seed_change_"))
        meta = root / "profile.json"
        meta.write_text(json.dumps({"name": "x", "fingerprint_seed": 42424}) + "\n", encoding="utf-8")
        first = ensure_fingerprint_persona(meta, 42424)
        self.assertEqual(first, mint_fingerprint_persona(42424))
        self.assertTrue(is_whitelisted_persona(first))
        reminted = ensure_fingerprint_persona(meta, 11111)
        self.assertEqual(reminted, mint_fingerprint_persona(11111))
        self.assertEqual(reminted["derived_from_seed"], 11111)
        self.assertTrue(persona_derived_from_seed(reminted, 11111))
        self.assertFalse(persona_derived_from_seed(reminted, 42424))
        self.assertNotEqual(reminted, first)
        data = json.loads(meta.read_text(encoding="utf-8"))
        self.assertEqual(data["fingerprint_persona"], reminted)
        self.assertEqual(data["fingerprint_seed"], 11111)

    def test_whitelist_persona_from_other_seed_is_not_reused(self):
        root = Path(tempfile.mkdtemp(prefix="fp_seed_bind_"))
        meta = root / "profile.json"
        foreign = mint_fingerprint_persona(42424)
        self.assertTrue(is_whitelisted_persona(foreign))
        meta.write_text(
            json.dumps(
                {
                    "name": "x",
                    "fingerprint_seed": 11111,
                    "fingerprint_persona": foreign,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        bound = ensure_fingerprint_persona(meta, 11111)
        self.assertEqual(bound, mint_fingerprint_persona(11111))
        self.assertNotEqual(bound, foreign)
        self.assertEqual(bound["derived_from_seed"], 11111)

    def test_three_seeds_diverge_hw_or_gpu(self):
        seeds = (10000, 22222, 33333, 44444, 55555)
        personas = [mint_fingerprint_persona(s) for s in seeds]
        self.assertGreaterEqual(len(seeds), 3)
        hw_mem = {(p["hardware_concurrency"], p["device_memory"]) for p in personas}
        vendors = {p["gpu_vendor"] for p in personas}
        self.assertTrue(len(hw_mem) > 1 or len(vendors) > 1)
        for seed, persona in zip(seeds, personas):
            self.assertEqual(persona["derived_from_seed"], seed)
            self.assertTrue(persona_derived_from_seed(persona, seed))


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
            font_listing=_FULL_FONT_LISTING,
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
        self.assertTrue(any(a.startswith("--fingerprint-gpu-vendor=") for a in args))
        self.assertTrue(any(a.startswith("--fingerprint-gpu-renderer=") for a in args))
        self.assertTrue(any(a.startswith("--fingerprint-screen-width=") for a in args))
        persona = mint_fingerprint_persona(42424)
        self.assertIn(f"--fingerprint-gpu-vendor={persona['gpu_vendor']}", args)
        self.assertIn(f"--fingerprint-gpu-renderer={persona['gpu_renderer']}", args)
        self.assertIn("--fingerprint-timezone=America/New_York", args)
        self.assertIn("--lang=en-US", args)
        self.assertIn("--fingerprint-locale=en-US", args)
        self.assertIn("--fingerprint-webrtc-ip=8.8.8.8", args)
        self.assertTrue(kwargs.get("geoip"))
        self.assertEqual(kwargs.get("timezone"), "America/New_York")
        self.assertEqual(kwargs.get("locale"), "en-US")
        self.assertIn("viewport", kwargs)
        self.assertNotIn("user_agent", kwargs)
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
                font_listing=_FULL_FONT_LISTING,
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



class FontGateTests(unittest.TestCase):
    def test_missing_detects_absent_families(self):
        listing = "arial: Arial\nconsolas: Consolas\n"
        missing = missing_windows_minimum_fonts(listing=listing)
        self.assertIsNotNone(missing)
        self.assertIn("Segoe UI", missing)
        self.assertIn("Calibri", missing)
        self.assertNotIn("Consolas", missing)

    def test_full_listing_empty_missing(self):
        self.assertEqual(missing_windows_minimum_fonts(listing=_FULL_FONT_LISTING), [])

    def test_unknown_listing_is_none(self):
        with mock.patch(
            "cloakcli_worker.fingerprint._fc_list_blob",
            return_value=None,
        ):
            self.assertIsNone(missing_windows_minimum_fonts())

    def test_fail_closed_raises(self):
        with self.assertRaises(FontAvailabilityError) as ctx:
            enforce_windows_font_gate(fail_closed=True, listing="arial only\n")
        self.assertIn("FONT_GATE_MISSING", str(ctx.exception))

    def test_warn_only_returns_missing(self):
        missing = enforce_windows_font_gate(fail_closed=False, listing="arial only\n")
        self.assertTrue(missing)
        self.assertIn("Segoe UI", missing)

    def test_register_launch_fail_closed_on_fonts(self):
        kwargs: dict = {"user_data_dir": "/tmp/ud", "headless": True}
        with self.assertRaises(FontAvailabilityError):
            apply_to_launch_kwargs(
                kwargs,
                seed=42424,
                headed=False,
                require_geo=False,
                require_fonts=True,
                font_listing="nope\n",
            )

    def test_nurture_warns_and_continues(self):
        kwargs: dict = {"user_data_dir": "/tmp/ud", "headless": True}
        apply_to_launch_kwargs(
            kwargs,
            seed=42424,
            headed=False,
            require_geo=False,
            require_fonts=False,
            font_listing="nope\n",
        )
        self.assertIn("--fingerprint=42424", kwargs["args"])

    def test_chrome_args_omit_font_metrics(self):
        args = fingerprint_chrome_args(42424)
        self.assertFalse(any("fingerprint-windows-font-metrics" in a for a in args))


class WebrtcIceAssertTests(unittest.TestCase):
    def _cand(self, ip: str, typ: str = "host") -> str:
        return (
            f"candidate:1 1 UDP 2122260223 {ip} 54400 typ {typ} generation 0"
        )

    def test_parse_host_and_srflx(self):
        self.assertEqual(
            parse_ice_candidate_ip(self._cand("203.0.113.10")),
            ("203.0.113.10", "host"),
        )
        self.assertEqual(
            parse_ice_candidate_ip("a=" + self._cand("198.51.100.1", "srflx")),
            ("198.51.100.1", "srflx"),
        )

    def test_host_ips_ignore_srflx(self):
        cands = [
            self._cand("8.8.8.8"),
            self._cand("10.0.0.5", "srflx"),
            self._cand("8.8.8.8"),
        ]
        self.assertEqual(host_candidate_ips(cands), ["8.8.8.8"])

    def test_assert_pass_when_host_equals_exit(self):
        cands = [self._cand("8.8.8.8"), self._cand("8.8.4.4", "srflx")]
        self.assertEqual(
            assert_webrtc_host_equals_exit_ip(cands, "8.8.8.8"),
            ["8.8.8.8"],
        )

    def test_assert_fail_on_private_host_leak(self):
        cands = [self._cand("192.168.1.20"), self._cand("8.8.8.8")]
        with self.assertRaises(WebrtcIceLeakError) as ctx:
            assert_webrtc_host_equals_exit_ip(cands, "8.8.8.8")
        self.assertIn("WEBRTC_ICE_LEAK", str(ctx.exception))

    def test_assert_fail_on_wrong_public_host(self):
        cands = [self._cand("1.1.1.1")]
        with self.assertRaises(WebrtcIceLeakError):
            assert_webrtc_host_equals_exit_ip(cands, "8.8.8.8")

    def test_assert_fail_when_no_host_candidates(self):
        cands = [self._cand("8.8.8.8", "srflx")]
        with self.assertRaises(WebrtcIceLeakError) as ctx:
            assert_webrtc_host_equals_exit_ip(cands, "8.8.8.8")
        self.assertIn("no host candidates", str(ctx.exception))


class HeadedWindowTests(unittest.TestCase):
    def test_window_size_arg_matches_screen(self):
        persona = mint_fingerprint_persona(42424)
        self.assertEqual(
            headed_window_size_arg(persona),
            f"--window-size={persona['screen_width']},{persona['screen_height']}",
        )

    def test_headed_launch_emits_window_size_not_maximize(self):
        kwargs: dict = {"user_data_dir": "/tmp/ud", "headless": False}
        apply_to_launch_kwargs(
            kwargs,
            seed=42424,
            headed=True,
            require_geo=False,
            require_fonts=False,
            font_listing=_FULL_FONT_LISTING,
        )
        persona = mint_fingerprint_persona(42424)
        self.assertIn(
            f"--window-size={persona['screen_width']},{persona['screen_height']}",
            kwargs["args"],
        )
        self.assertFalse(any(a == "--start-maximized" or a.startswith("--start-maximized") for a in kwargs["args"]))
        self.assertNotIn("viewport", kwargs)
        self.assertFalse(any("fingerprint-windows-font-metrics" in a for a in kwargs["args"]))

    def test_geometry_relations(self):
        persona = mint_fingerprint_persona(11111)
        geo = persona_window_geometry(persona)
        self.assertEqual(
            geo["available_height"],
            geo["screen_height"] - geo["taskbar_height"],
        )
        self.assertEqual(
            geo["viewport_height"],
            geo["available_height"] - geo["chrome_ui_height"],
        )
        self.assertEqual(geo["window_width"], geo["screen_width"])
        self.assertEqual(geo["window_height"], geo["screen_height"])



if __name__ == "__main__":
    unittest.main()
