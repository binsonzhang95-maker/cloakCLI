# CloakCLI Teach (MV3)

Unpacked Chromium extension loaded automatically by `cloakcli teach start`.
Do not pass an arbitrary `--load-extension` path on the CLI.

## Record

1. `cloakcli teach start --profile NAME [--url URL]`
2. Use the toolbar popup: **Record** → browse (click / input / navigation) → **Mark goal** → **Stop** → **Export**
3. Skill lands at `skills/<name>/skill.json` (existing schema: `goal` + `goto` / `click` / `fill`)

## Safety

- No default `<all_urls>` content script. Host access is `http://127.0.0.1/*` (export) plus optional `http(s)` origins added while recording.
- Content script refuses non-allowlisted origins.
- Password / token / secret fields export as `{{vars.NAME}}` unless `--allow-secrets`.
- Master and fleet do not teach; they only run exported skills.
