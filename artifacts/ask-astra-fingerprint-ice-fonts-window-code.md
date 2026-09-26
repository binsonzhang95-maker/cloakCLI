# Ask Astra — CloakCLI fingerprint P0 ICE + fonts + window code review

**To:** Pi's Astra (`codex exec -m gpt-6-astra`)  
**From:** coordinator (user said 行吧 先改吧按照astra)  
**Repo:** `/workspace/CloakCLI`  
**Branch / SHA:** `feat/fingerprint-ice-fonts-window` @ `1f2e8c9a8b86cdc643f280d0c2cd6407a62ac904`  
**Prior roadmap verdict:** `artifacts/astra-fingerprint-next-steps-verdict.md` — 有条件通过  
**Report:** `artifacts/fingerprint-ice-fonts-window-report.md`  
**Do NOT implement. Code correctness review only. Do NOT notify other bots.**

你是监工 Astra。只审代码正确性。中文。结论必须三选一：通过 / 有条件通过 / 打回。半页内。

## Scope landed (P0 only)

1. **WebRTC ICE assert** — `assert_webrtc_host_equals_exit_ip` / `verify_webrtc_ice_no_leak` (page+iframe+worker); host must equal echo exit IP; leak → `WebrtcIceLeakError`.
2. **Font gate** — detect via fc-list; register/strict fail-closed; nurture warn; no silent MS install; ops doc `docs/windows-fonts-linux-ops.md`; **omit** `--fingerprint-windows-font-metrics` on 146.
3. **Headed `--window-size=screen_w,screen_h`** from persona; not maximize-only; headless still Playwright viewport.

## Key files
- `python/cloakcli_worker/fingerprint.py`
- `python/cloakcli_worker/browser.py` (doc only)
- `python/tests/test_fingerprint.py` / `test_browser_launch.py`
- `scripts/probe_fingerprint_ice_fonts_window.py`

## Tests
`PYTHONPATH=python python3 -m unittest discover -s python/tests` → **292 OK**.

## Known intentional residuals (NOT 打回 reasons if documented)
- Live proxy ICE smoke still ops-run (`--launch`); CI gates assert logic + offline vectors.
- This host missing all 8 Windows fonts until ops licensed pack.
- P1 Accept-Language / Sec-CH-UA / same-seed restart OUT OF SCOPE.
- `launch_context` does not auto ICE-gather (latency); register must call `verify_webrtc_ice_no_leak` post-launch.

## Please answer
1. Verdict: 通过 / 有条件通过 / 打回  
2. Blockers (必改) if any — file:line or symbol  
3. Nits only if 有条件通过  
4. Final line: `VERDICT: 通过|有条件通过|打回`
