"""Recover action schema — wraps shared cloakcli_worker.actions (compat).

Recover keeps press/select and a 4-action-per-turn cap. Teach Chat uses the
shared module directly with the unified 1–3 action whitelist.
"""

from __future__ import annotations

from typing import Any

from cloakcli_worker.actions import (
    ALLOWED_PRESS_KEYS,
    FORBIDDEN_ACTIONS,
    MAX_CSS_LEN,
    MAX_REASON_LEN,
    MAX_SCROLL_DELTA,
    MAX_TEXT_LEN,
    MAX_URL_LEN,
    MAX_WAIT_MS,
    RECOVER_EXTRA_ACTIONS,
    SCHEMA_VERSION,
    UNIFIED_ACTIONS,
    Action,
    ActionError,
    ParseResult,
    execute_action,
    execute_actions,
    parse_model_output as _parse_model_output,
    validate_action as _validate_action,
)

# fill is Playwright page.fill on the existing page (in-browser replace).
# Kept alongside type: see recover/NOTES.md. Not host-side I/O.
# Form whitelist (RECOVER PATH): click/fill/press/select/small scroll.
# type stays as fill-like in-browser typing. wait/done/fail/ask_human are control.
# goto is policy-limited (same origin / allow_hosts) — not a default form action.
ALLOWED_ACTIONS = set(UNIFIED_ACTIONS) | set(RECOVER_EXTRA_ACTIONS)
MAX_ACTIONS_PER_TURN = 4

RecoverAction = Action


def parse_model_output(text: str) -> ParseResult:
    return _parse_model_output(
        text,
        extra_allowed=RECOVER_EXTRA_ACTIONS,
        max_actions=MAX_ACTIONS_PER_TURN,
        reject_over_max=False,
    )


def validate_action(item: Any) -> RecoverAction:
    return _validate_action(item, extra_allowed=RECOVER_EXTRA_ACTIONS)


__all__ = [
    "ALLOWED_ACTIONS",
    "ALLOWED_PRESS_KEYS",
    "FORBIDDEN_ACTIONS",
    "MAX_ACTIONS_PER_TURN",
    "MAX_CSS_LEN",
    "MAX_REASON_LEN",
    "MAX_SCROLL_DELTA",
    "MAX_TEXT_LEN",
    "MAX_URL_LEN",
    "MAX_WAIT_MS",
    "SCHEMA_VERSION",
    "Action",
    "ActionError",
    "ParseResult",
    "RecoverAction",
    "execute_action",
    "execute_actions",
    "parse_model_output",
    "validate_action",
]
