# pinterest-nurture-browse (0.2.5)

Logged-in **nurture browse** for a single Pinterest account. Behavior hardening
(Gemini JS strategy 2026-09-21): default **headed**, continuous **randomized
mouse trails**, **inertial scroll**, session **personas**, log-normal/gamma
pauses, quiet window after load. 0 likes is a valid success.

## Policy

- **One account ↔ one geo/proxy** (`profiles/<id>/profile.json` + `data/profiles/<id>-pinterest-run`).
- **Do not** wipe `user_data_dir` (keep cookies).
- **Default headed.** `--headless` is opt-in only (reCAPTCHA HeadlessChrome). Linux **Xvfb** is OK (still headed).
- Do **not** change CloakBrowser fingerprint / launch knobs.
- Use the account’s **bound proxy** from `profile.json`.


## 0.2.5 P0 (de-homology engineering)

Per-profile `behavior_profile` in `profile.json` (separate from `fingerprint_seed`), split RNG streams, session budgets (`max_session_elapsed` / `max_actions` / `max_state_visits`), same-profile session lock. Idle wander is **opt-in only**. This is engineering de-homology — **not** an anti-detect guarantee.

## Behavior hardening (0.2.5)

| Surface | What the runner does |
|---------|----------------------|
| Display | Headed by default; `--headless` opt-in; Xvfb OK |
| Mouse | Many small `mouse.move` steps: randomized multi-segment Bézier interpolation, variable speed (gamma dwell). Hover, then `mousedown`/`mouseup`. No teleport click to center. No physiological tremor / overshoot-then-correct biometrics. **Idle ambient wander (0.2.5): DEFAULT OFF** — enable only via `--idle-wander` / `CLOAKCLI_IDLE_WANDER=1`; then small hops (~15–80px) + occasional large loop/arc during dwell; skipped while click/type/scroll busy |
| Scroll | Inertial wheel: `v(t)=v0 e^{-kt}`, pause, slight reverse — not a fixed delta square wave |
| Session | Personas `browse_only` / `light_like` / `deep_browse` / `bounce_early`. Pins **0–12** (or `--pins`; ~15–25% feed-only). Like probability **15–40%** (browse_only / feed-only = 0). **0 likes allowed** |
| Timing | Log-normal / gamma pauses (not `randint`). Quiet window 1–2s after load before first pointer/key event |
| Typing | Focus + per-key delays; **no `fill`** for human fields (NUX name) |
| Duration | Respects `--min-sec` / `--max-sec` |

- **Hang before close (0.2.2+):** after browse ends, ambient idle hang ~60–180s (lognormal; `CLOAKCLI_HANG_BEFORE_CLOSE_MS=0` skips in tests), then storage_state flush, then `ctx.close`.
- **Idle ambient mouse wander (0.2.5): DEFAULT OFF (opt-in only).** Enable with `--idle-wander` or `CLOAKCLI_IDLE_WANDER=1`. When enabled, during feed/pin dwell (`pause(..., ambient=True)`), `IdleWanderState` schedules small Bézier hops (15–80px, every ~2–8s) and occasional large circle/half-lap (radius 120–280px, every ~25–60s). No click. No tremor/overshoot biometrics. Intentional click/type/scroll marks busy so idle ticks skip. Hang-before-close may use the same scheduler and is clipped to the session time budget.
- **Pin linger (0.2.3 P0):** open → independently sampled gaze 2.5–5.5s → optional ~50% peek scroll 300–600px → like only at 60–85% of planned dwell (never immediate) → pre-exit 1–3s → mixed close. Total ~4.5–22s (bounce ~2–4.5s).
- **Visibility keepalive (0.2.3 P0):** before key/mouse bursts and during long pauses, soft-check `visibilityState` / `hasFocus`; bring_to_front + focus if needed. Logs `visibility_keepalive` (no secrets).
- **Micro reverse scroll (0.2.3 P1):** after downward feed scrolls, ~12–18% (persona-weighted) reverse 20–45% of prior down magnitude; pause 1–3s; ≥1.5s between direction flips; net scroll still down.
- **Mixed close paths (0.2.3 P1):** weighted button 50% / Esc 30% / `goBack` 15% / backdrop 5% with fallbacks; logs which path worked.
- **Zero-pin bounce + browsed_ok (0.2.3 P1):** ~15–25% sessions open 0 pins (mostly bounce_early / browse_only); feed scroll 3–7 screens, dwell ~25–90s (lognormal ~45s). Success gate:
  `browsed_ok` iff `(pins_opened >= 1) OR (feed_dwell_sec >= 25 AND scroll_distance_px >= 1500)`.
  Zero-pin success is **not** `like_failed`.
- **Timing variation (must):** every intermediate pause re-samples lognormal/gamma independently — no identical fixed sleep chains across steps/runs/profiles.

Helpers: `scripts/pinterest_nurture_behavior.py` (unit-tested). Product path is this **python_runner**.

## Supervisor timeout

Hang 60–180s + longer pin linger → recommend wall clock **≥30–35 min** (bump from prior ~20 min).

## Pipeline (register → nurture same job)

**Register E2E chains nurture by default** after `status=ok` (keep-open same browser / same `user_data_dir` + proxy) **before `ctx.close()`**. See `skills/pinterest-register-outlook-verify/` and `skills/pinterest-register-visual/`.

- Register runner omits `--skip-nurture` — **never close before nurture**
- Register runs `session_keepalive_probe` + cookie flush before browse
- If probe sees login wall immediately → `session_lost_before_nurture` (not `browsed_ok`)
- **Never** run independent nurture minutes later as the **primary** post-register path
- Standalone nurture: **only** for already-warm alive accounts
- `--nurture-pins 0` (register default) lets the persona choose pin count

## Human flow

1. Open `https://www.pinterest.com/` — **quiet window**, then confirm logged-in (no Log in / Sign up CTA / `unauth-header`).
2. If NUX name / gender onboarding appears, complete the name (typed, not filled) and choose Male or Female (random 50/50).
3. If NUX **use-case picker** appears, pick ≥3 tiles → continue (button text becomes **continue to your feed**).
4. Browse the feed with inertial scrolls and ambient mouse; open **1–12** pins (persona).
5. View each pin; maybe like (`react-button`) with 15–40% probability (or never, for `browse_only`).
6. Close/back with a mouse trail; linger to `--min-sec` (soft cap `--max-sec`).

## Selectors (verified 2026-09-19 CST on geo46 / Krystal)

| Role | Verified / candidates |
|------|------------------------|
| Logged-out | `[data-test-id="unauth-header"]`, `[data-test-id="simple-login-button"]`, `[data-test-id="simple-signup-button"]` |
| Logged-in | `[data-test-id="header-accounts-options-button"]`, `[data-test-id="header-profile"]`, many `a[href*="/pin/"]` |
| Deactivated | body / dialog text contains `deactivated` |
| NUX use-case picker | `[data-test-id="desktop-use-case-picker"]` |
| NUX tiles | `[data-test-id^="use-case-tap-area-"]` |
| NUX continue | `[data-test-id="skip-or-continue-button"]` |
| Pin card | `a[href*="/pin/"]` (stable); also `pin`, `pinWrapper`, `pinrep-image`, `non-story-pin-image` |
| Close / back | **`[data-test-id="back-icon-button"]`** (aria Back); also Escape / history.back |
| Like / react | **`[data-test-id="react-button"]`** (aria React) |

`skill.json` steps are a coarse fallback — **use the python runner**.

## Statuses

| id | success | retryable | notes |
|----|---------|-----------|-------|
| `browsed_ok` | yes | no | ≥1 pin opened; likes optional |
| `not_logged_in` | no | yes | |
| `like_failed` | no | yes | empty feed / no pins — not “0 likes” |
| `account_deactivated` | no | no | |
| `session_lost_before_nurture` | no | yes | register-chain probe only |

## Run

```bash
# from CloakCLI root — profile must already be logged-in
python3 scripts/run_pinterest_nurture_browse.py --profile geo46

# persona / duration
python3 scripts/run_pinterest_nurture_browse.py --profile geo46 --persona light_like --min-sec 120 --max-sec 180

# headless fallback (not default):
python3 scripts/run_pinterest_nurture_browse.py --profile geo46 --headless
```

Artifacts: `artifacts/pinterest/nurture-browse/<profile>-run/`  
(e.g. `artifacts/pinterest/nurture-browse/geo46-run/`).

Last stdout line is the status JSON (`status` field) for fleet reporting.

## Tests

```bash
python3 -m unittest python/tests/test_pinterest_nurture_behavior.py -v
```

## Hardening notes

- Fresh accounts often show **desktop-use-case-picker**; without clearing it the feed has zero pin links → `like_failed` / empty feed.
- Final linger previously crashed when remaining time < 3s (`randrange` empty); fixed in 0.1.3.
- Runner does **not** log in or recover cookies — requires an already logged-in `*-pinterest-run` profile (or be chained from register keep-open).
- Do not nurture dead/deactivated accounts; only known-alive sessions.
- Exposes `run_nurture_session(page, …)` for in-process keep-open chaining and `run_nurture_reopen(…)` for persistent reopen.
- Source of truth for this pass: `artifacts/pinterest/js-strategy-20260921/gemini-analysis.md` + `SUMMARY.md`.
- 0.2.1: no `locator.click` / `force=True` teleport fallback — trail then mousedown/mouseup, or fail.
- 0.2.2: hang before close (~60–180s ambient drift) + storage flush; then `ctx.close`.
