# pinterest-create-pin 0.1.0

Publish **one** Pinterest Pin from a logged-in CloakCLI profile. Product path is
`python_runner`: `skills/pinterest-create-pin/scripts/run_pinterest_create_pin.py`.
A thin forwarder lives at `scripts/run_pinterest_create_pin.py`.

Verified on geo46, 2026-09-23 ~12:12–12:15 ET. Pin
https://www.pinterest.com/pin/1152217885972567993/ on board **Classic World**,
image `cw-10048-forest-friend-gift-sq.jpg`. The explore tree
`artifacts/pinterest/create-pin/` (`RESULT.json`, `EXPLORE-NOTES.md`,
`run_create_pin_explore_r2.py`, `steps/`) stays the evidence reference. It is
not the product entry.

Branch base: `feat/pinterest-visual-mm-0.2.0` @ `142eeb7` (not `main`). `main`
does not carry `scripts/pinterest_nurture_behavior.py`, which this runner imports.

## Hard rules

1. One account, one profile. CloakBrowser `launch_persistent_context` only.
   Prefer `data/profiles/<id>-pinterest-run`. Never system Chrome. Use persisted `fingerprint_seed` from profile.json; do not randomize per launch.
2. Secrets stay in `data/secrets/`. This skill does not read passwords. Do not log proxy URLs.
3. Clicks go through `human_click_locator` (trail, hover, mouse down/up).
4. Title, description, and a new board name go through `human_type_text`. No `fill`.
5. Board: exact option label inside `role=option` / board-row. The placeholder
   `[data-test-id=board-dropdown-placeholder]` must be gone before Publish.
   Never click a loose `div:has-text(board name)` — round 1 hit the pin title.
6. Publish only `[data-test-id=storyboard-creation-nav-done]`.
7. `published_ok` requires `pin_url` / `pin_id` (toast "Navigate to created Pin",
   a `/pin/<id>/` redirect, or a link on the drafts card that says Publish Complete).
8. Artifacts: `artifacts/pinterest/create-pin/<profile>-run/<timestamp>/`.
9. Classic World assets are the verified sample. Do not batch-spam pins.

## When

A logged-in Pinterest profile should publish one image with title, description, and board.

## Params

| Param | Meaning |
| --- | --- |
| `PROFILE` | CloakCLI profile id |
| `IMAGE_PATH` | Local JPG/PNG |
| `TITLE` / `DESCRIPTION` | Typed with `human_type_text` |
| `BOARD` | Board name. Create-if-missing defaults to true |
| `HEADED` | Default true. `--headless` opts out |
| `PUBLISH` | Default true. `--no-publish` stops after the board gate |
| `DRY_RUN` | Param check only. No browser |

## Runner

```bash
python3 skills/pinterest-create-pin/scripts/run_pinterest_create_pin.py \
  --dry-run \
  --profile geo46 \
  --image artifacts/pinterest/create-pin/cw-10048-forest-friend-gift-sq.jpg \
  --title "Forest Friend Baby Gift Set | Classic World" \
  --description "A sweet wooden forest friend baby gift from Classic World." \
  --board "Classic World"
```

Live (headed, publishes one pin — do not point this at a throwaway account from CI):

```bash
python3 skills/pinterest-create-pin/scripts/run_pinterest_create_pin.py \
  --profile geo46 \
  --image artifacts/pinterest/create-pin/cw-10048-forest-friend-gift-sq.jpg \
  --title "Forest Friend Baby Gift Set | Classic World" \
  --description "A sweet wooden forest friend baby gift from Classic World." \
  --board "Classic World"
```

Last stdout line is JSON: `skill_id`, `version`, `status`, and `digest` when the fleet passes one.
Human helpers: `scripts/pinterest_nurture_behavior.py` (nurture 0.2.3).

## Docs

- `PLAYBOOK.md` — verified flow and selectors
- `OPERATOR.md` — statuses, dry-run, parks
- `manifest.json` — entry + terminal statuses

## Statuses

`published_ok` · `dry_run_ok` · `login_required` · `upload_fail` · `board_missing` ·
`publish_fail` · `captcha_parked` · `oops_park` · `account_deactivated` ·
`ui_unknown_park` · `parked_risk`
