# CloakCLI Teach (MV3)

Unpacked Chromium extension loaded automatically by `cloakcli teach start`.
Do not pass an arbitrary `--load-extension` path on the CLI.

## Record

1. `cloakcli teach start --profile NAME [--url URL]`
2. Use the toolbar popup: **Record** → browse (click / input / navigation) → **Allow this origin** if you leave the `--url` site → **Mark goal** → **Stop** → **Export**
3. Skill lands at `skills/<name>/skill.json` (existing schema: `goal` + `goto` / `click` / `fill`, plus backup `selectors` / `field_name` from local post-process)
4. **Smart optimize** is on by default (one LLM call after export; uncheck in the popup or pass `--no-smart-optimize`). Recording does not call the model per step.

## Safety

- No default `<all_urls>` content script. Host access is `http://127.0.0.1/*` (export) plus optional `http(s)` origins from CLI `--url` or **Allow this origin** in the popup. Visiting a site does not silent-add it.
- Content script refuses non-allowlisted origins; unapproved origins are not injected. Click/fill/page_state stay allowlist-gated. Any `http(s)` navigation is still recorded as goto (javascript:/file:/data: rejected).
- Password / token / secret fields export as `{{vars.NAME}}` unless `--allow-secrets`.
- Master and fleet do not teach; they only run exported skills.

## Teach Chat takeover (M3)

`cloakcli teach chat` sends `takeover_start` / `takeover_stop` over the hub. While recording, this extension emits `takeover_event` with selector candidates in plan-C order (`id` → `data-testid` → `name` → aria/role → text → CSS path → coords last). Password fields send type/length/`redacted` only. Any `http(s)` goto is recorded even off the initial allowlist; `javascript:` / `file:` / `data:` are dropped. The Python worker locally normalizes events to Playwright actions (`source=human`); raw DOM is never an exportable step. Ctrl-E in Teach Chat exports those merged steps as `skills/<name>/skill.json` (`source=human|agent`, secrets as `{{vars.NAME}}`).
