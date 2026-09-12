"""Stall-recovery: vision model drives the existing Playwright page.

See NOTES.md: coordinate clicks require a matching screenshot_id; fill is kept
as Playwright in-browser control; recover may type into login form fields.
"""

from .actions import ActionError, parse_model_output, validate_action
from .loop import AskHumanError, RecoverFailed, RecoverResult, run_recover
from .origin import origin_of, url_allowed

__all__ = [
    "ActionError",
    "AskHumanError",
    "RecoverFailed",
    "RecoverResult",
    "parse_model_output",
    "run_recover",
    "origin_of",
    "url_allowed",
    "validate_action",
]
