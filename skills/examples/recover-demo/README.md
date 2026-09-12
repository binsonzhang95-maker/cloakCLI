# recover-demo

Example skill for AI stall-recovery MVP.

1. Opens `https://example.com`.
2. Clicks a **deliberately missing** selector (`#cloakcli-missing-more-info`).
3. `on_stall: recover` + `goal` start a vision recover loop on the **same** Playwright page.
4. The model should click the real “More information” link (css or coordinates), then `done`.
5. The skill continues: extract `h1`, screenshot.

Requires:

```bash
export CLOAKCLI_LLM_API_KEY=...    # or OPENAI_API_KEY; never stored in llm.json; never --api-key
cloakcli llm configure --base-url https://api.openai.com/v1 --model gpt-4o --enabled
cloakcli skill run recover-demo --profile demo --headless
```

Recover budget defaults to 300 seconds. Trajectory: `data/artifacts/recover-demo/recover/<run_id>/`.

Notes (Astra re-approval):

- Coordinate clicks must send `screenshot_id` matching the current observation; stale coords after navigation are rejected.
- `fill` is allowed (Playwright `page.fill` on the existing page) alongside `type`.
- Recover may type into username/password fields. API keys, Authorization headers, cookie values, and `llm.json` secrets are never logged.
