"""RECOVER PATH (not teach): stall recovery on the existing Playwright page.

Cascade: local selectors → text+DOM → one compressed vision shot. Success stops.
See NOTES.md. Teach recording/export is the Rust CLI + extensions/teach/.
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
