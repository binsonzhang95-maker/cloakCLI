# Fingerprint persona + geoip alignment

**Branch:** `feat/fingerprint-persona-geoip`  
**SHA:** 6b3868914c396179dc07405a874be6192b5697c7  
**Astra plan verdict:** `astra-ua-geoip-plan-verdict.md` — 有条件通过; this is the implementation, not a restore of registration.

## Round 1 (this landing): WebGL / GPU vendor diversity

Binary auto-GPU from `--fingerprint=<seed>` was homogenized: 12/12 probes landed in `Google Inc. (NVIDIA)` (different RTX SKUs, same vendor cluster). Explicit `--fingerprint-gpu-vendor` / `--fingerprint-gpu-renderer` apply exactly on Chromium **146.0.7680.177.5**.

- `PERSONA_SCHEMA` bumped **1 → 2**. Old personas without GPU fields remint from the same seed.
- `mint_fingerprint_persona(seed)` picks a verified Windows ANGLE `(gpu_vendor, gpu_renderer)` from `Random(seed)`, then a **compatible** `(hardwareConcurrency, deviceMemory)` pair (UHD/HD 620 ↔ 4–8 cores / 4–8 GB; discrete RTX/Radeon ↔ mid/high). Same seed relaunch = same GPU.
- Launch emits binary flags `--fingerprint-gpu-vendor=...` and `--fingerprint-gpu-renderer=...`. No Playwright-only GPU override. Opera/Vivaldi stay off. Geo fail-closed unchanged.

Whitelist (11 SKUs, Intel + AMD + NVIDIA, multiple each). Proven on this binary plus modest real D3D11 ANGLE siblings:

| vendor | renderer (abbrev.) | hw/mem pool |
|---|---|---|
| `Google Inc. (Intel)` | UHD 620 / HD 620 / UHD 630 / Iris Xe | 4/4–12/8 depending on SKU |
| `Google Inc. (AMD)` | Radeon(TM) Graphics / RX 580 Series / RX 6600 | 4/8–16/8 depending on SKU |
| `Google Inc. (NVIDIA)` | GTX 1650 / GTX 1660 SUPER / RTX 3060 / RTX 3070 | 4/8–16/8 depending on SKU |

Garbage GPU strings, vendor/renderer mismatches, and iGPU+16-core pairs are rejected and reminted like brand whitelist.

### Live WebGL probe (headless 146.0.7680.177.5)

`PYTHONPATH=python python3 scripts/probe_fingerprint_persona.py --launch`

Observed `WEBGL_debug_renderer_info` matched the persona flags exactly. Intel and AMD are present; not 100% NVIDIA.

| seed | hw / mem | unmasked vendor | unmasked renderer |
|---:|---|---|---|
| 11111 | 4 / 8 | `Google Inc. (Intel)` | `ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)` |
| 42424 | 4 / 4 | `Google Inc. (Intel)` | `ANGLE (Intel, Intel(R) HD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)` |
| 99887 | 6 / 8 | `Google Inc. (AMD)` | `ANGLE (AMD, AMD Radeon(TM) Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)` |
| 10004 | 8 / 8 | `Google Inc. (NVIDIA)` | `ANGLE (NVIDIA, NVIDIA GeForce RTX 3070 Direct3D11 vs_5_0 ps_5_0, D3D11)` |

Mint histogram over seeds 10000–10149: Intel 52 / AMD 38 / NVIDIA 60.

## What landed (persona + geo, prior)

Consistency-first Chrome identity on the deployed **146.0.7680.177.5** binary, plus fail-closed proxy geo alignment.

- Persona is minted **deterministically from `fingerprint_seed`** against a verified whitelist:
  `(Chrome, 146.0.7680.177, 146.0.7680.177, windows, 10.0.0|15.0.0)`.
- Opera / Vivaldi / Edge are **not** enabled (unverified CH/UA on this 146 binary).
- Diversity is **platform_version / hardwareConcurrency+deviceMemory / Windows ANGLE GPU / screen / geo**, not a brand lottery.
- Launch uses **CloakBrowser binary flags**, not Playwright `user_agent`.
- With a proxy: echo-verify exit IP (no proxy-hostname fallback), City DB **IANA timezone**, persist `geo_cache` bound to proxy session identity + lookup time + GeoLite build epoch. Exit IP change or DB version change invalidates.
- Register paths **fail closed** on missing DB / timeout / unknown timezone. No host timezone fallback.
- Language is a **profile preference** (`language`). Geo locale is used only when unset.
- WebRTC: `--fingerprint-webrtc-ip=<echo-verified exit IP>` when proxy geo resolves; `geoip=True` is also passed so cloakbrowser’s documented path stays on.

## Files

| Path | Change |
|---|---|
| `python/cloakcli_worker/fingerprint.py` | Persona whitelist, mint/persist, geo cache, launch kwargs |
| `python/cloakcli_worker/browser.py` | `launch_context` wires persona + geoip; drops Playwright UA when seed/persona present |
| `python/cloakcli_worker/runner.py` | Pass skill name/path so register skills fail-close on geo |
| `python/tests/test_fingerprint.py` | Persona / geo cache / launch-kwargs tests |
| `python/tests/test_browser_launch.py` | `launch_context` kwargs + fail-closed |
| `scripts/probe_fingerprint_persona.py` | Flag dump; `--launch` for live UA/CH |
| `scripts/run_pinterest_register_*.py` | Register launch fail-closes geo |
| `skills/pinterest-register-visual/scripts/run_pinterest_register_visual_mm.py` | Same |
| `skills/pinterest-nurture-browse/scripts/run_pinterest_nurture_browse.py` | One-line `apply_to_launch_kwargs` |
| `scripts/run_pinterest_nurture_browse.py` | Same |
| `skills/pinterest-create-pin/scripts/run_pinterest_create_pin.py` | Persona/geo kwargs (`require_geo=False`) |
| `scripts/run_pinterest_settings_email_verify.py` | Same |

No GeoLite DB, proxy URLs, or secrets committed.

## Tests

```
PYTHONPATH=python python3 -m unittest discover -s python/tests
```

- Full suite: **270 tests, OK** (includes GPU mint/whitelist/launch tests).
- Cargo: not run (no Rust changes).

Covered: persona mint determinism from seed (including gpu_vendor/renderer); ≥3 seeds not all the same GPU vendor; hw/mem paired to GPU SKU; whitelist rejection (Opera/Vivaldi and garbage GPU strings); schema v1 without GPU remints from the same seed; geo cache hit / invalidate on exit_ip and DB version; launch args include brand/hw/gpu/screen/tz/lang/webrtc; fail-closed when geo required; Playwright `user_agent` not forwarded when persona is set.

## Live probe (headless, Chromium 146.0.7680.177.5)

`https://example.com`, three seeds. UA stayed **UA-reduced** `Chrome/146.0.0.0`. High-entropy CH `uaFullVersion` / `fullVersionList` = `146.0.7680.177`. Brands = Google Chrome + Chromium 146. No Playwright UA desync.

| seed | platformVersion | hw / mem | GPU vendor | screen (avail) | tz (no proxy) | lang |
|---:|---|---|---|---|---|---|
| 11111 | 15.0.0 | 4 / 8 | Intel UHD 620 | 1536×864 (1536×824) | UTC | en-US |
| 42424 | 15.0.0 | 4 / 4 | Intel HD 620 | 1536×864 (1536×824) | UTC | en-US |
| 99887 | 15.0.0 | 6 / 8 | AMD Radeon(TM) Graphics | 2560×1440 (2560×1392) | UTC | en-US |
| 10004 | 15.0.0 | 8 / 8 | NVIDIA RTX 3070 | 1920×1200 (1920×1152) | UTC | en-US |

Win10 CH (`platformVersion=10.0.0`) is on the whitelist; e.g. seed `10003`. Probe seeds happened to draw 15.0.0.

`--fingerprint-brand-version=146.0.7680.177` was verified on this binary: UA stays `Chrome/146.0.0.0`, high-entropy full version fills in.

Flag-only dump: `PYTHONPATH=python python3 scripts/probe_fingerprint_persona.py`  
Live: add `--launch` (no proxy; tz/lang not from GeoIP).

## How to verify geo + WebRTC

1. Profile with proxy; `pip install 'cloakbrowser[geoip]'` so GeoLite2-City can download to `~/.cloakbrowser/geoip/` (not in git).
2. Launch a **register** skill / `launch_context(..., require_geo=True)`:
   - stderr: `[fingerprint] brand=Chrome/146.0.7680.177 ...` and `[geo] cache=hit|miss tz=...`
   - `profile.json` gains `fingerprint_persona`, `language`, `geo_cache` (no proxy password).
3. Change sticky session (username) or wait until echo IP differs → `geo_cache` re-resolves timezone.
4. Hide/remove the City DB or force echo timeout → register launch raises `GeoResolutionError` (`GEO_DB_MISSING` / `GEO_TIMEOUT` / `GEO_TIMEZONE`). Nurture (`require_geo=False`) launches without applying host TZ.
5. **WebRTC ICE** (follow-up test, not asserted in CI):

```javascript
const pc = new RTCPeerConnection({iceServers:[{urls:'stun:stun.l.google.com:19302'}]});
pc.createDataChannel('x');
pc.onicecandidate = e => console.log(e.candidate && e.candidate.candidate);
pc.createOffer().then(o => pc.setLocalDescription(o));
```

Host candidates should carry `--fingerprint-webrtc-ip` (echo-verified exit IP). A host candidate with the machine IP is a leak.

## Residual risks mapped to Astra A–F

| Astra | Status | Residual |
|---|---|---|
| **A** IP/TZ mismatch vs UA-sameness | TZ/locale now aligned to exit IP; UA `Chrome/146.0.0.0` is treated as normal reduction. Duplicate-rate is **not** a gate. | Does not prove registration failures were caused by fingerprint. |
| **B** Binary flags on **this** 146 binary | Verified UA / `userAgentData.brands` / high-entropy `fullVersionList` + `platformVersion` / hw / screen / Intl TZ on headless 146. Playwright `user_agent` is not used when a persona is applied. | iframe / Worker CH not probed. HTTP `Sec-CH-UA*` request headers not captured (JS CH only). |
| **C** No brand lottery | Chrome-only whitelist; Opera/Vivaldi/Edge rejected and reminted. | Edge still omitted until CH/UA is proven on this binary. |
| **D** Geo cache invalidation + fail-closed | Cache keyed by proxy identity, exit IP, lookup time, GeoLite `build_epoch`. Echo-only exit IP (hostname fallback refused). Register fail-closed. Language sticky. | First launch with `geoip=True` may echo twice (our verify + cloakbrowser WebRTC). City DB download is still online. |
| **E** Screen / hw / WebRTC / fonts | Screen flags coherent with viewport/available/taskbar (DPR=1). hw/mem paired combos from seed. WebGL vendor/renderer now explicit Windows ANGLE tuples paired with hw/mem (Intel+AMD+NVIDIA). WebRTC flag set from echo IP. | Headed OS window vs spoofed screen can still disagree. Linux Windows-font pack **not** installed (font metrics residual). Full ICE assert is a follow-up test. `create-pin` still calls `set_viewport_size(1440×960)` after launch. Canvas/audio restart stability inherited from seed, not newly tested. WebGPU / `WEBGL_debug_renderer_info` in iframes not probed. |
| **F** Gate rewrite | Gate is: flags applied, UA/CH coherent on 146, cache invalidation, fail-closed register geo, no host TZ fallback. | Do not treat this landing as “registration is fixed.” Do not restore intermittent register bots until Astra **code** review. |

## Out of scope (as requested)

- Restoring intermittent register bots
- Upgrading cloakbrowser past the installed 146 binary
- Font pack install on Linux
