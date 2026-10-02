# hubstudio-pinterest-register

Pinterest **selector** register against an already-started **Hubstudio** env (Playwright `connect_over_cdp`). Not CloakBrowser. Not a vision / multimodal loop.

Product path: `scripts/run_hubstudio_pinterest_register.py` (package python_runner). Repo shim: `scripts/run_hubstudio_pinterest_register.py`.

Mirrors Cloak `run_pinterest_register_outlook_verify.py` **0.1.4** (human trail click + key stream).

## Browser control

- Hubstudio Local API: `http://127.0.0.1:6873` (override `HUBSTUDIO_API`)
  - env list, `/api/v1/env/proxy/update` with required `asDynamicType`, browser start/stop
- Playwright attaches with `chromium.connect_over_cdp` after API start
- **Never** launch CloakBrowser or system Chrome
- **Never** start/stop protected `containerCode`s: `1863073221`, `1863107506`, `1863108423`

Env name pattern: `hub-reg-YYYYMMDD-NN`.

## Secrets (never in git)

Either Cloak-style `data/secrets/*.env`:

- `PINTEREST_EMAIL` / `PINTEREST_PASSWORD`
- `OUTLOOK_EMAIL` / `OUTLOOK_CLIENT_ID` / `OUTLOOK_REFRESH_TOKEN`

or STATE `secrets/accounts-raw.txt` lines (1-based row index):

```
outlook----pwd----uuid----token----evenemail----evenpwd
```

Do not log values. Proxies: HTTP `host:port:user:pass` from STATE `secrets/proxies-fresh-*.txt`.

## CLI

Standalone (this process starts/stops the Hub env):

```bash
python3 scripts/run_hubstudio_pinterest_register.py \
  --row-id row-0041 \
  --state-dir /home/box/.local/state/hubstudio-registration/20260930
```

Attach after `run_api_row` already started the env (fleet hook):

```bash
python3 skills/hubstudio-pinterest-register/scripts/run_hubstudio_pinterest_register.py \
  --row-id row-0041 \
  --state-dir /home/box/.local/state/hubstudio-registration/20260930 \
  --cdp-endpoint http://127.0.0.1:PORT \
  --external-lifecycle
```

`--skip-nurture` or `HUB_SKIP_NURTURE=1` skips same-session nurture (warning; not the primary path).

## Fleet hook

`STATE/bin/run_api_row.py` already calls optional `bin/row_register_hook.py <row_id> <cdp_endpoint>` after API start.

Install the drop-in:

```bash
install -m 700 \
  skills/hubstudio-pinterest-register/scripts/row_register_hook.py \
  /home/box/.local/state/hubstudio-registration/<batch>/bin/row_register_hook.py
install -m 700 \
  skills/hubstudio-pinterest-register/scripts/row_nurture_hook.py \
  /home/box/.local/state/hubstudio-registration/<batch>/bin/row_nurture_hook.py
export HUB_CLOAKCLI_ROOT=/path/to/CloakCLI   # if the hook is not beside the runner
# or write the CloakCLI path (no secrets) to STATE/bin/hub_cloakcli_root
```

This skill **is** the missing registration adapter. Do not start a live fleet GO from this package; the coordinator owns that.

## Selector path (0.1.4)

- `#email` `#password` `#birthdate` (YYYY-MM-DD, age 25–35 random)
- `human_click_locator` / `human_type_text` (trail click; never `locator.click` teleport)
- Continue (not Google); 800–2500ms between fields; 2–5s before Continue; 3–8s settle
- Oops → `oops_blocked` park
- Path A: `#code` + IMAP; `#code` value mismatch → `verify_soft_fail` (no Continue)
- Soft verify: Send-new-code once; Email confirmed toast → success candidate
- Path B: `signup_ok_no_code_challenge` → Settings Confirm Email (best-effort)

## Success bar

`registered_ok` = logged-in: onboarding or homefeed / account menu / pin feed, **no** unauth Log in+Sign up CTA.

Email **Confirmed** is nurture-prefer-same-run. It is **not** required for `registered_ok` unless Confirm Email already succeeded this run.

Default chains same-session nurture (`run_nurture_session`) before Hub `browser_stop`. Nurture fail does not negate `registered_ok`.

Statuses are declared only in this package `manifest.json` (no global enum). Last stdout line is the JSON report (`skill_id` / `version` / `status`, plus `digest` when the host supplies one).

## Concurrency

Keep register jobs ≤2–3. Shared udeal / Oops → park with 5–10 min cooldown.
