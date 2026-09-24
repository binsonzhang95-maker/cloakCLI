# pinterest-register-visual (0.2.7)

**PRODUCT path:** multimodal loop on **CloakBrowser** —

`screenshot → OpenAI-compatible vision API → JSON action → CloakBrowser execute → same-session nurture before ctx.close`.

**Bot / operators must call this runner.** Free-form **computerUse** is **not** the product path (0.1.1 visual_operator playbook is retired).

```bash
python3 scripts/run_pinterest_register_visual_mm.py \
  --profile geo02 \
  --secrets data/secrets/pinterest-outlook-XX.env
```

Skill package entry is `python_runner` → `scripts/run_pinterest_register_visual_mm.py` (same file under this package).

## When to use vs `pinterest-register-outlook-verify`

| | `pinterest-register-visual` 0.2.7 | `pinterest-register-outlook-verify` |
|--|-----------------------------------|-------------------------------------|
| Execution | Product MM loop (`python_runner`) | Declarative `skill.json` steps + selector runner |
| Targeting | Vision JSON actions (`click` css or x/y + `screenshot_id`) | CSS / Playwright selectors |
| Best for | Geo/UI drift, Oops-prone selector arm, A/B visual arm | Stable automated fleet when selectors hold |
| IMAP | Same: `scripts/outlook_imap_pinterest_code.py` + secrets env | Same |
| Nurture | Same-session **before** `ctx.close` (nurture 0.2.3+) | Same (register runner chains by default) |

## Hard constraints

1. **NEVER** launch system Chrome / Chromium / Google Chrome. Only CloakBrowser persistent + profile proxy.
2. **One account ↔ one profile** (`profiles/<id>/profile.json` + `data/profiles/<id>-pinterest-run`).
3. **Proxy from `profile.json` only**. Do not force a different proxy onto a live geo session.
4. **Secrets never in skill** — names only in `manifest.json`. Values in `data/secrets/*.env` (gitignored).
5. Prefer **headed** + real `DISPLAY`. Headless often hits Pinterest **Oops**.
6. Do **not** wipe live `user_data_dir` unless `--fresh-profile` at **attempt start** (refused if `.cloak_session_ok` exists).
7. Do not commit/push secrets; do not embed tokens/passwords in README / OPERATOR / scripts.
8. **Never close before same-session nurture** (unless explicit `--skip-nurture`). Independent nurture minutes later is not the primary path.
9. Use persisted `fingerprint_seed` from profile.json; do not randomize per launch.
10. Do **not** pass `--api-key` on the CLI (shell history). Use env / `config/llm.json`.

## LLM env (product, OpenAI-compatible + Grok)

The runner discovers the same product LLM config as recover (`config/llm.json` + `CLOAKCLI_LLM_*`).

When **model is unset**, it defaults to **`grok-4.6`**. Missing model alone is **not** `llm_incomplete` if `base_url` and an API key are present.

Screenshot `chat/completions` uses the **vision** model, first match:

1. CLI `--model`
2. `CLOAKCLI_LLM_VISION_MODEL`
3. `config/llm.json` `vision_model`
4. text model (`CLOAKCLI_LLM_MODEL` / `llm.json` `model` / default `grok-4.6`)

| Source | Fields |
|--------|--------|
| `config/llm.json` (mode 0600) | `base_url`, `model`, optional `vision_model`, `api_key_env` (name only, default `CLOAKCLI_LLM_API_KEY`), `enabled` |
| Env | `CLOAKCLI_LLM_API_KEY` (or `OPENAI_API_KEY` fallback). Overlays: `CLOAKCLI_LLM_BASE_URL`, `CLOAKCLI_LLM_MODEL`, `CLOAKCLI_LLM_VISION_MODEL` |
| Grok / xAI extra fallback | `XAI_API_KEY` if the default env is unset |
| Secrets file | same key **names** if fleet `python_runner` stripped process env |
| CLI | `--base-url` `--model` only — **never** `--api-key` |

Vision call: `POST {base}/chat/completions` with `image_url` (`data:image/jpeg;base64,…`). Trailing slash stripped; `{base}/v1` is not duplicated.

**Grok (xAI) example:**

```bash
export CLOAKCLI_LLM_API_KEY=...          # xAI key; never --api-key
# optional if grok-4.6 is text-only on this gateway:
export CLOAKCLI_LLM_VISION_MODEL=grok-2-vision-1212
# config/llm.json:
#   "base_url": "https://api.x.ai/v1"
#   "model": "grok-4.6"            # default when unset
#   "vision_model": "grok-2-vision-1212"   # optional; else text model
#   "api_key_env": "CLOAKCLI_LLM_API_KEY"
#   "enabled": true
cloakcli llm configure --base-url https://api.x.ai/v1
cloakcli llm models
```

**OpenAI-compatible example:** `base_url=https://api.openai.com/v1`, vision model e.g. `gpt-4o`.

## Actions (JSON, one per turn)

`click` | `type` | `press` | `wait` | `scroll` | `imap_fetch_code` | `nurture` | `done` | `fail`

- `click`: CSS `selector`, **or** `x`/`y` **plus** `screenshot_id` equal to the current observation
- `type`: prefer `field: email|password|birthday|name|code` — runner **auto-binds** CSS (`#email`, `#password`, `#birthdate`, `#code`, onboarding name) then **trail-clicks** the real input and types with nurture `human_type_text` (date uses `fill` with **YYYY-MM-DD** after trail focus). `text` with `{{EMAIL}}` `{{PASSWORD}}` `{{BIRTHDAY}}` `{{DISPLAY_NAME}}` `{{CODE}}` also works. Values never logged. After email+password+birthday, click `button:has-text('Continue')` (not Google); the runner skips re-types and may one-shot Continue if the model loops. Vision timeouts / transient HTTP retry 2–3× before `model_error`.
- `imap_fetch_code`: existing Outlook IMAP helper
- `nurture` / `done` with any **success** status (`registered_ok`, `browsed_ok`, … from `manifest.json`): **hints only**. Runner confirms login (account menu / pin feed / no unauth Log in+Sign up CTA) before writing `.cloak_session_ok`, chaining nurture, or returning success. `done: browsed_ok` on a register/login page is rejected (`not_logged_in` / `visual_stuck`). `nurture` alone never sets registered. Unknown model status → `visual_stuck` (never invent success).

## Behavior pacing

Register now shares nurture 0.2.3+ human behavior (trail click + key stream). Clicks use `human_click_locator` (mouse trail then mousedown/mouseup — never `locator.click` / `force=True` teleport). Email/password/code/name use `human_type_text`. Quiet window after signup land; log-normal pauses (ambient drift on longer waits).

- Fields: 800–2500ms · before Continue: 2–5s · after Continue settle: 3–8s · no click storms
- Signup anti-loop: skip duplicate field types; after three fills bias Continue; one-shot Continue recovery after 3 redundant types
- Soft verify: IMAP miss → `verify_soft_fail` (retryable) · Oops: park + 5–10 min cooldown on that exit
- Concurrency ≤2–3; shared udeal → serial / 1 per exit

## Session keep-alive

- Default: same-context nurture **before** `ctx.close()`; probe with `session_keepalive_probe`; flush cookies (wait + home).
- After nurture: ambient hang ~60–180s (`hang_before_close`) + storage flush, then `ctx.close` (`CLOAKCLI_HANG_BEFORE_CLOSE_MS=0` skips hang in tests).
- `session_lost_before_nurture` if login wall before browse (not `browsed_ok`).
- Touch `.cloak_session_ok` only after **independent login confirmation** (account menu / pin feed / absence of unauth Log in+Sign up CTA). Model `done: registered_ok` or `done: browsed_ok` is a hint, never enough on its own.

## Statuses

| id | success | retryable | notes |
|----|---------|-----------|-------|
| `registered_ok` | yes | no | Logged-in after signup / verify (page heuristic, not model-only) |
| `browsed_ok` | yes (optional) | no | Nurture chained successfully (`nurture_status`). Still requires login gate; not a fake success on signup. |
| `oops_blocked` | no | no | Park — do not hammer |
| `verify_soft_fail` | no | yes | IMAP/UI soft miss |
| `account_deactivated` | no | no | Dead |
| `not_logged_in` | no | yes | Still unauth CTA |
| `visual_stuck` | no | yes | Unrecognized UI / invalid model loop |

Primary register outcome is `registered_ok`; set `nurture_status=browsed_ok` when nurture succeeds. Status ids, `success` flags, and process-exit mapping are loaded from this package `manifest.json` — the runner does not keep a parallel hardcoded enum.

## Dry-run smoke (mock vision, no browser)

```bash
python3 scripts/run_pinterest_register_visual_mm.py --dry-run --profile geo02
```

Mock vision + stub page. Does **not** launch CloakBrowser or call a live model. Last stdout line is the fleet JSON report.

## Secrets (names only)

- `PINTEREST_EMAIL` / `PINTEREST_PASSWORD`
- `OUTLOOK_EMAIL` / `OUTLOOK_CLIENT_ID` / `OUTLOOK_REFRESH_TOKEN` (IMAP XOAUTH2; not Graph)

## Related

- Product runner: `scripts/run_pinterest_register_visual_mm.py`
- Declarative register: `skills/pinterest-register-outlook-verify/` (runner 0.1.4, same human helpers + hang before close)
- Nurture: `skills/pinterest-nurture-browse/` (0.2.2+)
- IMAP helper: `scripts/outlook_imap_pinterest_code.py`
- Operator playbook: `OPERATOR.md`
- Preflight (no browser): `scripts/run_pinterest_register_visual_hint.py`
