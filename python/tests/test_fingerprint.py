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
    ensure_fingerprint_seed,
    fingerprint_chrome_args,
    is_valid_fingerprint_seed,
    mint_fingerprint_seed,
    resolve_fingerprint_seed,
)


class FingerprintSeedTests(unittest.TestCase):
    def test_mint_in_range(self):
        for _ in range(50):
            s = mint_fingerprint_seed()
            self.assertTrue(SEED_MIN <= s <= SEED_MAX)

    def test_args_override_shape(self):
        self.assertEqual(fingerprint_chrome_args(12345), ["--fingerprint=12345"])
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


if __name__ == "__main__":
    unittest.main()
