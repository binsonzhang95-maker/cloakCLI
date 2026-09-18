"""Classify Pinterest verify/signup error copy without launching a browser."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from run_pinterest_register_outlook_verify import (  # noqa: E402
    classify_verify_status,
    detect_oops_modal,
    inline_error_kind,
    redact_raw_secrets,
    snip_error,
)

# geo07 Gabriel after-code (inline field; NOT the Oops! modal)
GABRIEL_VERIFY = """
Log in
Sign up
Enter the code
We sent a 6-digit code to you
123456
Sorry! Something went wrong on our end.
Open outlook.com
Send new code
Continue
"""

# geo06 Titus / geo10 Ethelyn signup Continue
TITUS_OOPS_MODAL = """
Log in
Sign up
Oops!
Sorry! Something went wrong on our end.
Okay
Welcome to Pinterest
Birthday
Continue
"""

PRUDENCE_ONBOARDING = """
Nice to meet you
What's your name?
"""

ONBOARDING_STILL_CODE = """
What's your name?
Enter the code
Sorry! Something went wrong on our end.
"""


class ClassifyTests(unittest.TestCase):
    def test_gabriel_inline_is_soft_oops_not_blocked(self):
        kind = inline_error_kind(GABRIEL_VERIFY)
        self.assertEqual(kind, "soft_oops")
        self.assertFalse(detect_oops_modal(GABRIEL_VERIFY, code_visible=True))
        status = classify_verify_status(
            still_code_ui=True,
            oops_modal=False,
            inline_kind=kind,
            onboarding=False,
            login_signup_cta=True,
        )
        self.assertEqual(status, "verify_soft_oops")

    def test_titus_modal_is_oops_blocked(self):
        self.assertTrue(detect_oops_modal(TITUS_OOPS_MODAL, code_visible=False))
        kind = inline_error_kind(TITUS_OOPS_MODAL)
        self.assertEqual(kind, "soft_oops")  # same sentence; modal flag wins
        status = classify_verify_status(
            still_code_ui=False,
            oops_modal=True,
            inline_kind=kind,
            onboarding=False,
            login_signup_cta=True,
        )
        self.assertEqual(status, "oops_blocked")

    def test_onboarding_without_error_is_ok(self):
        self.assertIsNone(inline_error_kind(PRUDENCE_ONBOARDING))
        status = classify_verify_status(
            still_code_ui=False,
            oops_modal=False,
            inline_kind=None,
            onboarding=True,
            login_signup_cta=False,
        )
        self.assertEqual(status, "ok")

    def test_onboarding_does_not_win_over_code_ui_error(self):
        kind = inline_error_kind(ONBOARDING_STILL_CODE)
        self.assertEqual(kind, "soft_oops")
        status = classify_verify_status(
            still_code_ui=True,
            oops_modal=False,
            inline_kind=kind,
            onboarding=True,
            login_signup_cta=False,
        )
        self.assertNotEqual(status, "ok")
        self.assertEqual(status, "verify_soft_oops")

    def test_invalid_and_expired_are_code_error(self):
        self.assertEqual(inline_error_kind("That code is incorrect"), "invalid")
        self.assertEqual(inline_error_kind("This code has expired"), "expired")
        self.assertEqual(
            classify_verify_status(
                still_code_ui=True,
                oops_modal=False,
                inline_kind="invalid",
                onboarding=False,
                login_signup_cta=True,
            ),
            "code_error",
        )
        self.assertEqual(
            classify_verify_status(
                still_code_ui=True,
                oops_modal=False,
                inline_kind="expired",
                onboarding=False,
                login_signup_cta=True,
            ),
            "code_error",
        )

    def test_snip_redacts_six_digit_code(self):
        body = "code 123456\nSorry! Something went wrong on our end."
        snip = snip_error(body)
        self.assertIsNotNone(snip)
        self.assertNotIn("123456", snip)
        self.assertIn("******", snip)
        self.assertIn("something went wrong", snip.lower())

    def test_redact_does_not_touch_non_codes(self):
        self.assertEqual(redact_raw_secrets("age 31"), "age 31")
        self.assertEqual(redact_raw_secrets("pin 12345"), "pin 12345")

    def test_still_code_no_error_is_check_ui(self):
        status = classify_verify_status(
            still_code_ui=True,
            oops_modal=False,
            inline_kind=None,
            onboarding=False,
            login_signup_cta=True,
        )
        self.assertEqual(status, "code_submitted_check_ui")


if __name__ == "__main__":
    unittest.main()
