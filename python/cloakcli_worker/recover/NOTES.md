# Recover notes (RECOVER PATH — not teach)

Teach recording/export is the Rust CLI + `extensions/teach/`. This directory
only runs after a skill step stalls.

Balanced LLM-assist MVP (`astra-teach-llm-assist.md`, not the ultra-frugal
token plan):

## Cascade (success stops)

1. **Local** (0 tokens): recorded selector → backup `selectors[]` → unique
   role/text/label match on the clickable DOM summary.
2. **Text + DOM**: URL, failed action, structured clickable list. **No screenshot.**
3. **Vision**: attach **one** compressed/crop screenshot (viewport JPEG, never a
   full-page original). Vision only if text cannot decide.

Default wall-clock **90s** (form range 60–120). **300s remains an advanced
override** (`recover_timeout_sec`). Max **3** model rounds (`max_model_rounds`).
Token counts, latency, rounds, screenshot bytes, and success/fail are telemetry
only — no hard min-token goal.

## Form whitelist

`click` / `fill` / `press` / `select` / small scroll (`|delta|<=800`).
`type` stays as in-browser typing. `wait` / `done` / `fail` / `ask_human` are
control. `goto` is same-origin unless `allow_hosts`.

## Coordinate clicks

`x`/`y` clicks **must** include `screenshot_id` equal to the current **vision**
observation. Text-stage coords are rejected (no image) and may escalate.

## `fill` is kept

Playwright `page.fill` on the existing page. Not host I/O.

## Form credentials

Recover **may** type into username/password form fields. Never log API keys,
Authorization headers, raw `llm.json` secrets, or cookie **values**.
