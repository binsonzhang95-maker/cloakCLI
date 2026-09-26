# Implement: CloakCLI P0 fingerprint ICE + fonts gate + headed window

Implement per Astra next-steps verdict `artifacts/astra-fingerprint-next-steps-verdict.md`
(**有条件通过**). Base: `feat/fingerprint-persona-geoip` @ `3d3c8a8` (or newer on that line).
Branch: `feat/fingerprint-ice-fonts-window`. Do NOT restore intermittent register. Do NOT
message the user or notify intermittent bots. P1 Accept-Language / Sec-CH-UA / same-seed
restart are OUT OF SCOPE.

## Astra P0 (must land)

### 1. WebRTC full ICE assertion (CI first)
- Host ICE candidates must equal the echo-verified exit IP (`--fingerprint-webrtc-ip`).
- Private / loopback / link-local / host LAN IPs → fail.
- Helpers + unit tests for parsing/asserting candidate lines.
- Probe script covering **page + iframe + Worker** (restart optional if cheap); CI/probe
  must fail on leak.
- Failure **blocks strict register** (callable post-launch assert used by register/strict
  paths, or wired so require_geo/register cannot proceed past a failed assert helper).

### 2. Font availability gate
- Audited minimum Windows OS font set (match cloakbrowser `_WINDOWS_FONT_TELLS`):
  Segoe UI, Segoe UI Light, Calibri, Marlett, MS UI Gothic, Franklin Gothic,
  Consolas, Courier New.
- **Detect / check only** via `fc-list` (or equivalent). **No silent MS font install**
  in code. Document how ops installs a licensed pack under `docs/`.
- Missing fonts: **register / strict → fail-closed**; **nurture → warn + log missing set**.
- On Chromium **146**: **omit** `--fingerprint-windows-font-metrics` (no-op). Do not
  claim native Windows font metrics.

### 3. Headed window alignment
- Emit measured `--window-size=<screen_width>,<screen_height>` from persona when headed.
- Do **not** rely on `--start-maximized` alone.
- Headless still uses Playwright viewport from persona; headed still does **not** set
  Playwright viewport.
- Probe/assert helpers for outer/inner vs persona screen/taskbar/available when feasible
  in unit tests (flag math) + optional live probe.

## Implementation surface
- Primary: `python/cloakcli_worker/fingerprint.py`, `python/cloakcli_worker/browser.py`
- Tests: `python/tests/test_fingerprint.py`, `python/tests/test_browser_launch.py`
- Probes: extend `scripts/probe_fingerprint_persona.py` and/or add
  `scripts/probe_fingerprint_ice_fonts_window.py`
- Docs: `docs/windows-fonts-linux-ops.md` (licensed pack install; no auto-download)
- Report: `artifacts/fingerprint-ice-fonts-window-report.md`

## Hard constraints
- Secrets stay under `data/secrets/`; never echo proxy URLs / passwords.
- No per-profile fake font-list lottery; no casual cloakbrowser bump.
- No Playwright `user_agent` for persona.
- Keep existing geo fail-closed / persona seed binding / Chrome-only whitelist.

## Acceptance
1. Unit tests green: ICE assert pass/fail cases; font missing → fail-closed vs warn;
   headed args include `--window-size`; args never include `--fingerprint-windows-font-metrics`
   on this path; register detection still correct.
2. `PYTHONPATH=python python3 -m unittest discover -s python/tests` green.
3. Commit on feature branch; push if remotes work.
4. Write ask-astra code review file for `codex exec -m gpt-6-astra`.

## Report back
Branch, HEAD SHA, commits, test count, residual risks, one-line 中文 user report.
