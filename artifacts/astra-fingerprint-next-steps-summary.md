# Astra fingerprint NEXT-STEPS — summary

**Ask:** `artifacts/ask-astra-fingerprint-next-steps.md`  
**Full verdict:** `artifacts/astra-fingerprint-next-steps-verdict.md`  
**Model:** `gpt-6-astra` via `codex exec` (session `01a0db1f-f204-7133-b3d7-e9f323515dd5`)  
**Branch/SHA reviewed against:** `feat/fingerprint-persona-geoip` @ `3d3c8a8`

## Verdict

**有条件通过** (`VERDICT: 有条件通过`)

Roadmap direction OK; next cut must turn leak/inconsistency into measurable gates. Font install must stay license/deploy bounded. ICE assertion should enter the CI block chain **before** fonts.

## Astra reordered priority

1. **P0 — WebRTC full ICE assertion** — host candidates must only expose echo exit IP; private/host IP → fail
2. **P0 — Font availability gate** — audited/licensed Linux image/package; verify Windows minimum set
3. **P0 — Headed window alignment** — measured outer/inner vs persona screen/taskbar/viewport
4. **P1 — HTTP consistency probes** — Accept-Language + `Sec-CH-UA*` + iframe/Worker
5. **P1 — Same-seed restart stability** — canvas / audio / GPU across restarts
6. **P2 — GeoIP double-echo dedupe**
7. **P2 — Edge brand** — only after proven on this 146 binary
8. **P3 — Operational safeguards** — no mass-scale register; no UA collision ship gate

**Suggested next gate:** fonts gate + ICE probe, with **ICE first** in CI.

## What NOT to do (Astra)

- No silent runtime download/install of Microsoft proprietary fonts; fixed audited licensed image/package only
- Do **not** fail-closed missing fonts for all flows — register/strict: fail-closed (configurable); nurture: warn + log missing set
- Do **not** treat `--fingerprint-windows-font-metrics` as effective on Chromium **146** (no-op); omit or mark explicitly
- Do **not** rely on `--start-maximized` alone; prefer measured `--window-size`
- No per-profile fake font-list lottery; no casual cloakbrowser bump

## Next-gate acceptance (Astra)

- **Fonts:** each minimum-set font enumerable/usable in browser; version/source/license/image reproducible; missing → register fail-closed / nurture warn
- **ICE:** page + iframe + Worker + restart; no host/private IPs; host candidates == echo exit IP; failure blocks strict register
- **Headed:** measured window vs persona screen/DPR/work area stable; no unproven maximize
- Artifacts must record probe results + binary `146.0.7680.177.5` + seed + proxy identity + exit IP

## Risks (Astra)

- Windows fonts on Linux: license, image distribution, supply-chain — deploy-layer choice, not silent CLI
- Register fail-closed on fonts may hurt availability → strict/register only
- Fonts alone (no 148+ metrics flag) still valuable for enumeration/fallback/layout — do **not** claim native Windows font metrics
- Headed alignment needs real measurement; account for taskbar/viewport math

## Blocked

Nothing blocked — Astra authenticated and returned a full verdict. No code implemented; other bots not notified.
