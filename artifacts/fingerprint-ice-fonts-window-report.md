# Fingerprint P0: ICE assert + font gate + headed window-size

**Branch:** `feat/fingerprint-ice-fonts-window`  
**Base:** `feat/fingerprint-persona-geoip` @ `3d3c8a8`  
**Astra next-steps:** `artifacts/astra-fingerprint-next-steps-verdict.md` — 有条件通过  
**Implement brief:** `artifacts/ask-grok-fingerprint-ice-fonts-window.md`

## Round 1 (Astra code 有条件通过 nits)

SHA after R1: *(see git)*. Cleared clearable nits from R1 有条件通过:

1. **Register ICE auto-gate** — `launch_context` now calls `verify_webrtc_ice_no_leak`
   when `require_geo` and `--fingerprint-webrtc-ip` are set (opt out:
   `CLOAKCLI_REQUIRE_WEBRTC_ICE=0`). Closes context on leak.
2. **IPv6-normalized ICE compare** — `assert_webrtc_host_equals_exit_ip` uses
   `ipaddress` canonical form.
3. Remaining residual (docs only): live headed outer/inner WM smoke still ops-run.

**294 tests, OK.**


## What landed (P0 only)

### 1. WebRTC full ICE assertion
- `parse_ice_candidate_ip` / `host_candidate_ips` / `assert_webrtc_host_equals_exit_ip`
- Host candidates must equal echo exit IP; private / wrong public → `WebrtcIceLeakError`
- No host candidates → fail (cannot verify spoof)
- `gather_webrtc_ice_candidates` + `verify_webrtc_ice_no_leak(page, exit_ip)` for
  **page / iframe / worker** (strict register post-launch + CI probe)
- Unit vectors + offline probe self-check

### 2. Font availability gate
- `WINDOWS_MINIMUM_FONTS` synced with cloakbrowser `_WINDOWS_FONT_TELLS`
- Detect via `fc-list` only — **never** downloads/installs Microsoft fonts
- Register/strict (`require_fonts` defaults from `require_geo` / register skill /
  `CLOAKCLI_REQUIRE_FONTS`) → **fail-closed** (`FontAvailabilityError`)
- Nurture → warn + log missing set
- **Omits** `--fingerprint-windows-font-metrics` on Chromium 146 (no-op)
- Ops doc: `docs/windows-fonts-linux-ops.md`

### 3. Headed window alignment
- Headed persona launches emit `--window-size=<screen_w>,<screen_h>`
- Suppresses cloakbrowser `--start-maximized` injection (build_args skips maximize
  when `--window-size` present)
- Headless still sets Playwright viewport; headed does not
- `persona_window_geometry()` documents screen/available/viewport/taskbar math

## Out of scope (P1+)
Accept-Language / Sec-CH-UA / iframe Worker CH / same-seed restart / Edge brand /
geo double-echo dedupe.

## Files
| Path | Change |
|---|---|
| `python/cloakcli_worker/fingerprint.py` | ICE assert, font gate, window-size args |
| `python/cloakcli_worker/browser.py` | Docstring: fonts + ICE post-launch |
| `python/tests/test_fingerprint.py` | Font / ICE / headed unit tests |
| `python/tests/test_browser_launch.py` | Headed window-size + font mocks |
| `scripts/probe_fingerprint_ice_fonts_window.py` | Offline + optional live probe |
| `docs/windows-fonts-linux-ops.md` | Licensed pack install (ops only) |
| `artifacts/ask-grok-fingerprint-ice-fonts-window.md` | Implement brief |

## Tests
```
PYTHONPATH=python python3 -m unittest discover -s python/tests
```
**294 tests, OK.**

Offline probe:
```
PYTHONPATH=python python3 scripts/probe_fingerprint_ice_fonts_window.py
```
Confirms `--window-size` present headed, no font-metrics flag, ICE vectors pass,
and this box currently **missing all 8** Windows minimum fonts (register would
fail-closed until ops installs licensed pack).

## Residual risks
- Live ICE against a real proxy exit still needs ops to run `--launch` with proxy
  geo; unit/offline vectors gate CI parsing/assert logic.
- Font pack is deploy-layer; this host cannot register-strict until fonts land.
- `--window-size` is the intended outer target; WM chrome may still nudge outer
  pixels — live headed measurement is the follow-up smoke, not a maximize hack.
- P1 HTTP CH / Accept-Language still open.

## Register ICE wiring note
`launch_context` auto-gathers ICE when `require_geo` and `--fingerprint-webrtc-ip`
are set; failure raises `WebrtcIceLeakError` and closes the context. Opt out with
`CLOAKCLI_REQUIRE_WEBRTC_ICE=0` (nurture / debug).
