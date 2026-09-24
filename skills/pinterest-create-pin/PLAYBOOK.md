# Pinterest Create Pin playbook (0.1.0)

Live runner: `skills/pinterest-create-pin/scripts/run_pinterest_create_pin.py`.

The flow below is the geo46 publish from **2026-09-23 ~12:12–12:15 ET**
(`data/profiles/geo46-pinterest-run`). Pin
https://www.pinterest.com/pin/1152217885972567993/ on board **Classic World**,
image `cw-10048-forest-friend-gift-sq.jpg`.

Evidence, kept as reference and not deleted:
`artifacts/pinterest/create-pin/RESULT.json`, `EXPLORE-NOTES.md`,
`run_create_pin_explore_r2.py`, `steps/*.png` (round 1 failure is under
`steps/round1/`).

Round 1 stayed on `/pin-creation-tool/` because a loose `div:has-text("Classic World")`
hit the **title**. The placeholder stayed, and a generic Publish button was a no-op.
Round 2 cleared the placeholder, created the board, and clicked
`storyboard-creation-nav-done`.

## 0. Inputs

| Param | Notes |
| --- | --- |
| `PROFILE` | CloakCLI id. Prefer `data/profiles/<id>-pinterest-run` |
| `IMAGE_PATH` | Local JPG/PNG |
| `TITLE` | `human_type_text` only |
| `DESCRIPTION` | `human_type_text` only. Scroll the description container first |
| `BOARD` | Exact board name. Create-if-missing defaults on |
| `HEADED` | Default true (Xvfb OK). `--headless` opts out |
| `PUBLISH` | Default true once the placeholder is gone |

## 1. Launch (CloakBrowser only)

```python
from cloakbrowser import launch_persistent_context
kwargs = {"user_data_dir": str(ud), "headless": not headed}
if proxy:
    kwargs["proxy"] = proxy
ctx = launch_persistent_context(**kwargs)
page.set_viewport_size({"width": 1440, "height": 960})
```

No system Chrome. Use persisted fingerprint_seed from profile.json; do not randomize per launch. Do not log the proxy.
Reuse `scripts/pinterest_nurture_behavior.py`: `human_click_locator`,
`human_type_text`, `sample_pause_ms`, `sample_quiet_window_ms`, ambient drift.
Default close is a short wrap-up plus `storage_state` flush. A nurture-length
hang runs only when `CLOAKCLI_HANG_BEFORE_CLOSE_MS` is set.

## 2. Home / login gate

1. `goto https://www.pinterest.com/`
2. Quiet window (`sample_quiet_window_ms`)
3. Logged-in means the account menu (`header-profile` / accounts button) or a
   pin feed with no unauth Log in + Sign up CTA
4. Park immediately: login wall → `login_required`; captcha → `captcha_parked`;
   Oops / unusual activity → `oops_park`; deactivated → `account_deactivated`;
   suspicious / automated-behavior copy → `parked_risk`

## 3. Enter create tool

1. Human-click `[data-test-id="create-tab"]` (fallbacks: header create button, Create aria)
2. Menu: `a[href*="pin-creation-tool"]`
3. If still off the tool, open `https://www.pinterest.com/pin-creation-tool/` directly
4. URL must contain `pin-creation-tool`, else `ui_unknown_park`

## 4. Upload image

1. Human-click the upload area (`[aria-label*="Upload" i]` / storyboard upload)
2. `input[type=file].set_input_files(IMAGE_PATH)`, or a file chooser
3. Failure → `upload_fail`

## 5. Title + description

1. Title: `#storyboard-selector-title` → `human_type_text`. No `fill`.
2. Scroll `[data-test-id="storyboard-description-field-container"]` into view
   (round 1 missed it below the fold)
3. Type into `[data-test-id="editor-with-mentions"]` / its contenteditable
   (placeholder *Tell everyone what your Pin is about*)
4. Focus failure or a short typed length → `ui_unknown_park`. Do not publish a blank pin.

## 6. Board select / create

1. Scroll `[data-test-id="board-dropdown-select-button"]` into view and open it
2. Match `BOARD` against the first line of a `[role="option"]` or
   `board-row` / `boardFromList` / `boardWithoutSection` node. Comparison is
   exact (case-insensitive). Skip options with `y < 120` (header chrome).
3. Banned: loose `div:has-text(BOARD)`, and banned again: picking some other
   board (All Pins, a first row, or any name the operator did not pass)
4. If the name is absent and create-if-missing is on:
   - `div[role="button"]:has-text("Create board")` (or the create-board test id)
   - `human_type_text` the board name
   - `[data-test-id="board-form-submit-button"]`
5. Gate: `[data-test-id="board-dropdown-placeholder"]` must be **gone**
   (recheck once after a short pause). If it remains → `board_missing`.
   Do not click Publish.

## 7. Publish

1. Locator: `[data-test-id="storyboard-creation-nav-done"]` only
2. It must be present and not disabled (`disabled` / `aria-disabled`)
3. Dwell, then `human_click_locator`
4. Banned: `button:has-text("Publish")` as a stand-in, `locator.click`, `force=True`
5. `--no-publish` stops here with `publish_fail` / `publish_skipped_by_flag`

The verified button sat in the header (about x=953, y=93) and read Publish.
That y is expected for this control. The y filter applies to board options, not to Publish.

## 8. Verify success

Poll about eight pauses (~0.7–1.4s each). If the drafts sidebar already says
**Publish Complete** (or `[data-test-id="success-publish-icon-container"]` is up)
and there is still no pin id, poll four more times.

Accept a **new** pin id from, in order:

1. Toast / `[aria-label="Navigate to created Pin"]` href
2. Navigation to `/pin/<id>/` (leaving the creation tool)
3. An href on the draft card that shows Publish Complete
4. Any new `/pin/<id>/` href on the page, only when Publish Complete is also showing

Ignore pin ids that were already on the page before the click.
`published_ok` stores `pin_url`, `pin_id`, and `pin_source`.
No pin id → `publish_fail`. Generic words like "saved" are not success.
The geo46 page URL stayed on `/pin-creation-tool/` while the pin id was captured
from the post-publish UI. That still counts.

## Failure modes

| Symptom | Status |
| --- | --- |
| Unauth CTA / login URL / no profile directory on a live run | `login_required` |
| Captcha iframe / verify human | `captcha_parked` |
| Oops / unusual activity | `oops_park` |
| Account deactivated copy | `account_deactivated` |
| Suspicious / automated-behavior copy | `parked_risk` |
| Image missing / upload error | `upload_fail` |
| Placeholder still visible, or the named board was not created | `board_missing` |
| Publish missing, disabled, skipped, or no pin URL | `publish_fail` |
| Create tool unknown, field missing, or an exception | `ui_unknown_park` |

## Anti-patterns

- System Chrome, or any fingerprint / launch knob
- Teleport click or `force=True`
- `fill` or a full paste for title, description, or board name
- Clicking the board via loose `div:has-text`
- Publishing while `board-dropdown-placeholder` is present
- Treating "left the creation tool" or the words "published" / "saved" as success
- Thrashing Publish, or retrying captcha / Oops / deactivated
- Logging secrets or proxy credentials
