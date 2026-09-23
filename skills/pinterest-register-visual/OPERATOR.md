# OPERATOR — pinterest-register-visual (product MM loop) 0.2.7

Short playbook. Execution kind: **`python_runner`**.

**Bot must call `scripts/run_pinterest_register_visual_mm.py`.**  
Free-form **computerUse** is **not** the product path. Do not click the UI yourself except when diagnosing a parked `oops_blocked` cell.

## 0. Display, identity, LLM

- Confirm `DISPLAY` or `WAYLAND_DISPLAY` (headed CloakBrowser). No display → do not fake headless unless operator accepts Oops risk; otherwise `visual_stuck`.
- Args: `--profile <id>` (e.g. `geo02`), `--secrets data/secrets/<file>.env`.
- Read proxy + label from `profiles/<id>/profile.json`. Persistent dir: `data/profiles/<id>-pinterest-run`.
- Load secrets from env file only. Never print token/password/API key values.
- LLM: product `config/llm.json` + `CLOAKCLI_LLM_API_KEY` (see README). Default model **`grok-4.6`** when unset. Vision override: `CLOAKCLI_LLM_VISION_MODEL` then `llm.json` `vision_model` then text model. Grok: `base_url=https://api.x.ai/v1`. Never `--api-key`.

```bash
python3 scripts/run_pinterest_register_visual_mm.py \
  --profile geo02 \
  --secrets data/secrets/pinterest-outlook-XX.env
# smoke (mock vision, no CloakBrowser):
python3 scripts/run_pinterest_register_visual_mm.py --dry-run --profile geo02
```

## 1. Headed launch (runner does this — do not spawn Chrome)

```python
# NEVER system Chrome — CloakBrowser only. No fingerprint knobs.
from cloakbrowser import launch_persistent_context
kwargs = {"user_data_dir": str(ud), "headless": False}
if meta.get("proxy"):
    kwargs["proxy"] = meta["proxy"]  # udeal/geo as bound in profile.json
ctx = launch_persistent_context(**kwargs)
```

The product runner already does this. Bot should **exec the runner**, not reimplement launch.

### Human pacing (mandatory — no rapid click storms)

Register now shares nurture 0.2.3+ human behavior (trail click + key stream). `click` / Continue / field focus use `human_click_locator` (never `locator.click` / force teleport; one trail retry then fail). `type` uses `human_type_text`. Quiet window after signup land.

Applied by the runner around model actions:

- Between form fields: **800–2500ms** log-normal
- Before Continue (signup + verify): **2–5s**
- After Continue before judging Oops / code UI / success: **3–8s** settle
- After code fill: human pause before next Continue
- Soft verify / IMAP miss → `verify_soft_fail`; hard **Oops**: **park**, no immediate re-Continue spam
- No click storms, no double-Continue loops

## 2. Loop (screenshot → vision → JSON → execute)

Artifacts: `artifacts/pinterest/visual/<profile>/`

| # | When | File hint |
|---|------|-----------|
| 1 | Signup form visible | `01-signup.png` |
| 2 | After Continue | `02-after-continue.png` |
| 3 | Code UI filled / settings confirm | `03-code-or-settings.png` |
| 4 | Logged-in feed / account menu | `04-logged-in.png` |
| 5 | After nurture (if chained) | `05-nurture.png` |

Allowed actions: `click|type|press|wait|scroll|imap_fetch_code|nurture|done|fail`.  
IMAP: `scripts/outlook_imap_pinterest_code.py --secrets <env>` (runner action `imap_fetch_code`).

**Signup type (0.2.5):** `field: email|password|birthday|name|code` auto-binds `#email` / `#password` / `#birthdate` / `#code` (birthday **YYYY-MM-DD**). Runner **trail-clicks** the real input then `human_type_text` (date uses fill after trail focus). Missing selector after bind, or a failed focus/click, is rejected (`ok=False`) — never silent `keyboard.type` or teleport click. Re-typing a filled field is skipped; after email+password+birthday the model must click `button:has-text('Continue')` (not Google). After 3 redundant types the runner may one-shot that Continue (trail click). Vision timeout / transient HTTP retries 2–3× before `model_error` (does not burn `max_steps` on timeouts alone).

On **independently confirmed** logged-in (account menu / pin feed / no unauth Log in+Sign up CTA) → **same-session nurture BEFORE `ctx.close()`** (mandatory unless `--skip-nurture`). Model `done: registered_ok` / `done: browsed_ok` / `nurture` are hints; they do **not** write `.cloak_session_ok` or count as success by themselves. A success claim on the register/login page is rejected (`not_logged_in` / `visual_stuck`). Flush cookies; `session_keepalive_probe` — login wall → `nurture_status=session_lost_before_nurture` (not a fake top-level `browsed_ok`). Nurture **0.2.2+**. After nurture: hang ~60–180s (ambient) + flush, then close. Nurture failure does not negate a confirmed `registered_ok`.

If **Oops** → stop, `oops_blocked`, park (cooldown 5–10 min on that exit).

Touch `data/profiles/<id>-pinterest-run/.cloak_session_ok` only after the login heuristic confirms success. Never `--fresh-profile` after success.

## 3. Session keep-alive (critical)

- **Default: same-context nurture BEFORE any `ctx.close()`**
- **Never** treat independent nurture minutes later as the primary path
- Independent nurture only for already-warm alive accounts
- Concurrency: **≤2–3**; shared udeal exit → serial or 1 per exit; 5–10 min cooldown after Oops

## 4. What to report (final JSON)

Last stdout / job result must include:

```json
{
  "skill_id": "pinterest-register-visual",
  "version": "0.2.7",
  "status": "registered_ok",
  "path": "code_ui|settings_confirm|already_logged_in|…",
  "nurture_status": "browsed_ok|skipped|session_lost_before_nurture|like_failed|error:…",
  "nurture_elapsed_s": 0,
  "elapsed_s": 0,
  "profile": "geoXX"
}
```

Allowed `status`: loaded from this package `manifest.json` (`registered_ok` | `browsed_ok` | `oops_blocked` | `verify_soft_fail` | `account_deactivated` | `not_logged_in` | `visual_stuck`). Success flags and process-exit codes come from that file; unknown model status → `visual_stuck`. Model `reason` / `path` (and other extra result strings) are run through `redact_text` before emit — email/password must not appear in the result JSON.

- Prefer primary register outcome `registered_ok`; set `nurture_status=browsed_ok` when nurture succeeds (optional success). Top-level `browsed_ok` still requires the login gate.
- `oops_blocked` → park (not retryable).
- Soft IMAP / transient UI → `verify_soft_fail` (retryable).
- Unrecognized screen after reasonable retries → `visual_stuck` (retryable).

## 5. Do not

- Use computerUse / headed clicking instead of the runner.
- Touch other cells’ running A/B jobs.
- Wipe `user_data_dir` unless explicit fresh signup **at attempt start** (refuse if `.cloak_session_ok` present).
- Force a proxy different from `profile.json`.
- Put secrets in screenshots filenames, chat, or skill files.
- Close the browser before same-session nurture completes.
- Change fingerprint / cloakbrowser launch fingerprint knobs.
- Pass `--api-key` on the command line.
