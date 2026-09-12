# recover-demo

Example skill for AI stall-recovery MVP.

1. Opens `https://example.com`.
2. Clicks a **deliberately missing** selector (`#cloakcli-missing-more-info`).
3. `on_stall: recover` + `goal` start a vision recover loop on the **same** Playwright page.
4. The model should click the real “More information” link (css or coordinates), then `done`.
5. The skill continues: extract `h1`, screenshot.

Requires:

```bash
export OPENAI_API_KEY=...          # never stored in llm.json
cloakcli llm set --base-url https://api.openai.com/v1 --model gpt-4o --api-key-env OPENAI_API_KEY --enabled
cloakcli skill run recover-demo --profile demo --headless
```

Recover budget defaults to 300 seconds. Trajectory: `data/artifacts/recover-demo/recover/<run_id>/`.
