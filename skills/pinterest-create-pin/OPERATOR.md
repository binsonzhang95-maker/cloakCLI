# OPERATOR — pinterest-create-pin 0.1.0

Execution kind: **`python_runner`**.

Entry: `skills/pinterest-create-pin/scripts/run_pinterest_create_pin.py`
(package-relative `scripts/run_pinterest_create_pin.py`).
Repo shim: `scripts/run_pinterest_create_pin.py` forwards to that file.

Human helpers: `scripts/pinterest_nurture_behavior.py` (nurture **0.2.3**).
The skill does not vendor a second copy; the runner walks up to the CloakCLI
checkout and imports `scripts/pinterest_nurture_behavior.py`.

Explore evidence (not the product entry): `artifacts/pinterest/create-pin/`
(`RESULT.json`, `EXPLORE-NOTES.md`, `run_create_pin_explore_r2.py`, `steps/`).
Verified pin https://www.pinterest.com/pin/1152217885972567993/ on geo46,
board Classic World, 2026-09-23.

Branch base: `feat/pinterest-visual-mm-0.2.0` @ `142eeb7`, because `main` does
not contain the nurture behavior module.

## Hard rules

- One account, one CloakCLI profile. `cloakbrowser.launch_persistent_context` only.
- Prefer `data/profiles/<PROFILE>-pinterest-run` when that directory exists.
- Proxy is read from `profiles/<PROFILE>/profile.json` and never logged.
- Never system Chrome. Use persisted fingerprint_seed from profile.json; do not randomize per launch.
- No teleport click. No `fill` for title, description, or new board name.
- Publish only `[data-test-id="storyboard-creation-nav-done"]`, and only after
  `[data-test-id="board-dropdown-placeholder"]` is gone.
- Board choice is an exact first-line match on `role=option` / board-row.
  Do not click `div:has-text(<board name>)`. Do not fall back to another board.
- Captcha, Oops, deactivated, or an explicit risk/bot wall → park, no retries.
- Default close is the short explore wrap-up (about 2.5–5.5s) plus a cookie flush.
  Set `CLOAKCLI_HANG_BEFORE_CLOSE_MS` to opt into the nurture hang (`0` skips it).

## Params

| Param | Meaning |
| --- | --- |
| `PROFILE` | CloakCLI profile id (for example `geo46`) |
| `IMAGE_PATH` | Local image path |
| `TITLE` | Pin title |
| `DESCRIPTION` | Pin description |
| `BOARD` | Board name |
| `CREATE_BOARD_IF_MISSING` | Default true |
| `HEADED` | Default true. `--headless` forces headless |
| `PUBLISH` | Default true. `--no-publish` does not click Publish |
| `DRY_RUN` | Validate params, write `result.json`, no browser |

Fleet `python_runner` stdin (`skill_id`, `version`, `digest`, `profile`, `vars`)
fills any flag that was not passed on argv. CLI flags win.

## Dry-run (no browser)

```bash
python3 skills/pinterest-create-pin/scripts/run_pinterest_create_pin.py \
  --dry-run \
  --profile geo46 \
  --image artifacts/pinterest/create-pin/cw-10048-forest-friend-gift-sq.jpg \
  --title "Forest Friend Baby Gift Set | Classic World" \
  --description "A sweet wooden forest friend baby gift from Classic World." \
  --board "Classic World"
```

Expect process exit 0 and a last-line JSON `status=dry_run_ok`, `version=0.1.0`.
`result.json` is under `artifacts/pinterest/create-pin/<profile>-run/<timestamp>/`
unless `--out` is set. A missing image is `upload_fail` (exit 3) even on dry-run.
An empty title, description, or board is `ui_unknown_park` (exit 4).

## Live

Same command without `--dry-run`. Headed by default (Xvfb is fine). One pin.
Do not run this from CI against a real account.

`--no-publish` still opens the browser, fills the composer, and requires the
board placeholder to clear, then stops. Status is `publish_fail` with
`reason=publish_skipped_by_flag` so a rehearsal cannot be counted as published.

Success (`published_ok`) needs a pin id that was not already on the page:

- toast link `[aria-label="Navigate to created Pin"]`, or
- a redirect to `/pin/<id>/` off the creation tool, or
- a `/pin/<id>/` link on the drafts card that shows **Publish Complete**

Publish Complete text alone extends the wait (four extra polls) and then
`publish_fail` if no pin id appears. The creation-tool URL may stay put; that
matched the geo46 run, where the pin URL came from the toast/draft UI.

## Statuses (0.1.0)

| id | success | exit | notes |
| --- | --- | --- | --- |
| `published_ok` | yes | 0 | `pin_url` / `pin_id` captured |
| `dry_run_ok` | yes | 0 | params only |
| `login_required` | no | 8 | retryable; also missing user-data dir on a live run |
| `upload_fail` | no | 3 | retryable |
| `board_missing` | no | 5 | placeholder still visible, or named board not created |
| `publish_fail` | no | 6 | no pin URL, disabled Publish, or `--no-publish` |
| `captcha_parked` | no | 2 | park |
| `oops_park` | no | 2 | park |
| `account_deactivated` | no | 7 | park |
| `ui_unknown_park` | no | 4 | create tool stuck, missing field, exception |
| `parked_risk` | no | 2 | suspicious / automated-behavior copy |

The `exit` values are the CLI process code (same numbers as the 0.1.0-draft
manifest). Fleet `run_python_runner` only parses the stdout report when the
process exits 0, so a non-zero catalog exit is a protocol failure on the
current host even if the last line is valid JSON. Local dry-run stays exit 0.

## Verified probe

- Profile: `geo46` / `data/profiles/geo46-pinterest-run`
- Pin: https://www.pinterest.com/pin/1152217885972567993/
- Board: Classic World (created in the explore run)
- Publish control: `[data-test-id="storyboard-creation-nav-done"]`
