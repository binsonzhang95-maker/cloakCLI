"""Classify Pinterest verify/signup error copy without launching a browser."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from run_pinterest_register_outlook_verify import (  # noqa: E402
    _settle_result,
    classify_verify_status,
    detect_email_confirmed_toast,
    detect_oops_modal,
    email_badge_confirmed,
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

# geo07/08 after Send-new-code retry: leftover #code error + success toast
GABRIEL_TOAST = GABRIEL_VERIFY + "\nEmail confirmed\n"


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

    def test_email_confirmed_toast_is_candidate_even_with_code_modal(self):
        self.assertTrue(detect_email_confirmed_toast("Email confirmed"))
        self.assertTrue(detect_email_confirmed_toast("EMAIL CONFIRMED"))
        self.assertTrue(detect_email_confirmed_toast(GABRIEL_TOAST))
        self.assertFalse(detect_email_confirmed_toast(GABRIEL_VERIFY))
        kind = inline_error_kind(GABRIEL_TOAST)
        self.assertEqual(kind, "soft_oops")
        status = classify_verify_status(
            still_code_ui=True,
            oops_modal=False,
            inline_kind=kind,
            onboarding=False,
            login_signup_cta=True,
        )
        # Classify stays soft_oops until Settings badge is asserted.
        self.assertEqual(status, "verify_soft_oops")

    def test_settings_email_badge_confirmed(self):
        self.assertTrue(email_badge_confirmed("Email\nConfirmed\nPassword"))
        self.assertFalse(email_badge_confirmed("Email\nUnconfirmed\nConfirm Email"))
        self.assertFalse(email_badge_confirmed("Email confirmed"))  # toast, not badge
        self.assertFalse(email_badge_confirmed(""))
        self.assertFalse(email_badge_confirmed(None))

    def test_settle_overlay_toast_ok_and_unconfirmed_note(self):
        class _Page:
            url = "https://www.pinterest.com/settings/account-settings/"

        snap = {
            "still_code_ui": True,
            "login_signup_cta": True,
            "oops_modal": False,
            "error_snip": "Sorry! Something went wrong on our end.",
            "inline_kind": "soft_oops",
            "onboarding": False,
            "email_confirmed_toast": True,
            "url": "https://www.pinterest.com/",
        }
        ok = _settle_result(
            status="verify_soft_oops",
            snap=snap,
            page=_Page(),
            continue_enabled_before=True,
            val="xxxxxx",
            cont_sel="form:has(#code) button:has-text(\"Continue\")",
            retried=True,
            extra={
                "status": "ok",
                "path": "toast_email_confirmed",
                "still_code_ui": False,
            },
        )
        self.assertEqual(ok["status"], "ok")
        self.assertEqual(ok["path"], "toast_email_confirmed")
        self.assertFalse(ok["still_code_ui"])
        self.assertTrue(ok["email_confirmed_toast"])
        self.assertNotIn("xxxxxx", json.dumps(ok))

        nope = _settle_result(
            status="verify_soft_oops",
            snap=snap,
            page=_Page(),
            continue_enabled_before=True,
            val="xxxxxx",
            cont_sel="x",
            retried=True,
            extra={
                "status": "verify_soft_oops",
                "note": "toast_email_confirmed_but_settings_unconfirmed",
            },
        )
        self.assertEqual(nope["status"], "verify_soft_oops")
        self.assertEqual(
            nope["note"], "toast_email_confirmed_but_settings_unconfirmed"
        )
        self.assertTrue(nope["still_code_ui"])

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
