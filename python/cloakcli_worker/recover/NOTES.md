# Recover notes

Astra code review of `50ff373` was a **conditional pass** (`astra-ai-recover-code.md`).
This records the user decisions for re-approval. Default `recover_timeout_sec` remains **300**.

## Coordinate clicks (Astra #1 — accepted)

`x`/`y` clicks **must** include `screenshot_id` equal to the current observation.
Omitting it is illegal. A mismatched id is rejected. Coordinate bindings invalidate
after navigation, viewport change, or any non-`wait` recover action.

CSS-selector clicks do not need `screenshot_id`.

## `fill` is kept (Astra #2 — request re-approval)

The incremental plan listed `click/type/scroll/wait/goto/done/fail/ask_human`.
CloakCLI also allows **`fill`**: Playwright `page.fill` on the **existing** page
(replace an input's value). This is in-browser control, not host filesystem/shell.

`type` clicks the field then `keyboard.type`. Both stay on the whitelist.

## Form credentials (Astra #3 — rejected as a hard block)

Recover **may** type into username/password form fields when the skill needs login.
Do **not** hard-block `type`/`fill` on `input[type=password]`.

Still **never** log or persist:

- API keys
- Authorization headers
- raw `llm.json` secrets (config stores `api_key_env` only)
- cookie **values**

## Tests (Astra #4)

Deterministic FakePage + scripted LLM cover control-flow (no live model / heavy browser):

- selector-fail → recover path, then skill continues
- coordinate click rejected without `screenshot_id`
- coordinate click rejected with mismatched `screenshot_id`
- coords invalidated after navigation (and viewport change)
- `ask_human` pause status
- illegal model action (`shell` / `read_file` / `file:` / cross-origin) rejected
