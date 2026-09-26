Reading prompt from stdin...
OpenAI Codex v0.154.0
--------
workdir: /workspace/CloakCLI
model: gpt-6-astra
provider: openai
approval: never
sandbox: read-only
reasoning effort: none
reasoning summaries: none
session id: 01a0db29-d824-72f3-85e7-fee8df09dfb1
--------
user
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

codex
我会按给定范围核对实现、测试与已知残留，只做代码正确性审查，不改文件、不联系其他代理。
exec
/bin/bash -lc "git status --short && sed -n '1,240p' artifacts/fingerprint-ice-fonts-window-report.md && sed -n '1,220p' artifacts/astra-fingerprint-next-steps-verdict.md" in /workspace/CloakCLI
 succeeded in 0ms:
?? ask-astra-account-ledger-ui.md
?? ask-astra-ai-recover.md
?? ask-astra-geek-desktop.md
?? ask-astra-geek-m1-rereview.md
?? ask-astra-geek-m1-review.md
?? ask-astra-geek-m2-rereview.md
?? ask-astra-geek-m2-rereview2.md
?? ask-astra-geek-m2-review.md
?? ask-astra-geek-m3-review.md
?? ask-astra-human-pw.md
?? ask-astra-impl-plan.md
?? ask-astra-linkedin-inbox-comments-hook-0.1.0.md
?? ask-astra-llm-models-ux.md
?? ask-astra-m1-rereview.md
?? ask-astra-m1-rereview2.md
?? ask-astra-m1-review.md
?? ask-astra-m2-rereview.md
?? ask-astra-m2-review.md
?? ask-astra-m3-rereview.md
?? ask-astra-m3-review.md
?? ask-astra-m4-rereview.md
?? ask-astra-m4-review.md
?? ask-astra-nurture-behavior-0.2.1.md
?? ask-astra-nurture-behavior-code-only.md
?? ask-astra-ops-ux-code.md
?? ask-astra-ops-ux.md
?? ask-astra-skill-package-master.md
?? ask-astra-skill-statuses.md
?? ask-astra-teach-chat.md
?? ask-astra-teach-llm-assist.md
?? ask-astra-teach-plugin.md
?? ask-astra-teach-tokens.md
?? ask-astra-thinking-ui-code.md
?? ask-astra-thinking-ui.md
?? ask-astra-tui-browser-bridge.md
?? ask-astra-ua-geoip-code.md
?? ask-astra-visual-mm-0.2.0-rereview.md
?? ask-astra-visual-mm-0.2.0.md
?? ask-astra-visual-mm-0.2.1-rereview.md
?? ask-astra-visual-mm-0.2.2-rereview.md
?? ask-astra-visual-mm-0.2.3-rereview.md
?? astra-account-ledger-ui.md
?? astra-ai-recover-code.md
?? astra-ai-recover-plan.md
?? astra-ai-recover-rereview.md
?? astra-geek-desktop.md
?? astra-geek-m1-rereview.md
?? astra-geek-m1-review.md
?? astra-geek-m2-rereview.md
?? astra-geek-m2-rereview2.md
?? astra-geek-m2-review.md
?? astra-geek-m3-review.md
?? astra-human-playwright.md
?? astra-llm-models-code.md
?? astra-llm-models-rereview.md
?? astra-llm-models-rereview2.md
?? astra-llm-models-ux.md
?? astra-m1-rereview.md
?? astra-m1-rereview2.md
?? astra-m1-review.md
?? astra-m2-rereview.md
?? astra-m2-review.md
?? astra-m3-rereview.md
?? astra-m3-review.md
?? astra-m4-rereview.md
?? astra-m4-review.md
?? astra-nurture-behavior-0.2.0-verdict.md
?? astra-nurture-behavior-0.2.0.md
?? astra-nurture-behavior-0.2.1-verdict.md
?? astra-nurture-behavior-0.2.1.md
?? astra-ops-ux-code.md
?? astra-ops-ux-verdict.md
?? astra-ops-ux.md
?? astra-register-behavior-38aa003-verdict.md
?? astra-register-behavior-38aa003.md
?? astra-register-behavior-cd41b44-verdict.md
?? astra-register-behavior-cd41b44.md
?? astra-skill-package-master-verdict.md
?? astra-skill-package-master.md
?? astra-skill-statuses-verdict.md
?? astra-skill-statuses.md
?? astra-tauri-shell-code.md
?? astra-tauri-shell-rereview.err
?? astra-tauri-shell-rereview.md
?? astra-teach-chat-impl-plan.md
?? astra-teach-chat.md
?? astra-teach-code.md
?? astra-teach-llm-assist.md
?? astra-teach-plugin-plan.md
?? astra-teach-rereview.md
?? astra-teach-tokens.md
?? astra-thinking-ui-code.md
?? astra-thinking-ui.md
?? astra-tui-anim-code.md
?? astra-tui-anim-plan.md
?? astra-tui-browser-bridge.md
?? astra-ua-geoip-code-verdict-r1-summary.md
?? astra-ua-geoip-code-verdict-r1.md
?? astra-ua-geoip-code-verdict-r2.md
?? astra-ua-geoip-code-verdict-r3-summary.md
?? astra-ua-geoip-code-verdict.md
?? astra-ua-geoip-plan-verdict.md
?? astra-visual-mm-0.2.0-rereview-verdict.md
?? astra-visual-mm-0.2.0-rereview.md
?? astra-visual-mm-0.2.0-verdict.md
?? astra-visual-mm-0.2.0.md
?? astra-visual-mm-0.2.1-rereview-verdict.md
?? astra-visual-mm-0.2.1-rereview.md
?? astra-visual-mm-0.2.2-rereview-verdict.md
?? astra-visual-mm-0.2.2-rereview.md
?? astra-visual-mm-0.2.3-rereview-verdict.md
?? astra-visual-mm-0.2.3-rereview.md
?? grok-m2-fix-run.log.pre-policy-restart
?? m1-geek-desktop-monitor-report.md
?? m1-geek-fix-monitor-report.md
?? m1-logscan-monitor-report.md
?? m1-monitor-report.md
?? m1-smoke-monitor-report.md
?? m2-fix-monitor-report.md
?? m2-geek-desktop-monitor-report.md
?? m2-geek-fix-monitor-report.md
?? m2-monitor-report.md
?? m2-schema-fix-monitor-report.md
?? m3-fix-monitor-report.md
?? m3-geek-desktop-monitor-report.md
?? m3-monitor-report.md
?? m4-fix-monitor-report.md
?? m4-monitor-report.md
?? mac-package-monitor-report.md
?? macmini-tui-dark-build.log
?? profiles/batch10-01/
?? profiles/batch10-02/
?? profiles/batch10-03/
?? profiles/batch10-04/
?? profiles/batch10-05/
?? profiles/batch10-06/
?? profiles/batch10-07/
?? profiles/batch10-08/
?? profiles/batch10-09/
?? profiles/batch10-10/
?? profiles/cell2-01/
?? profiles/cell2-07/
?? profiles/cell2-08/
?? profiles/cell3-02/
?? profiles/cell3-04/
?? profiles/cell3-07/
?? profiles/cell4r-05/
?? profiles/cell4r-06/
?? profiles/cell4r-15/
?? profiles/cell4r-27/
?? profiles/cell4r-28/
?? profiles/cell4r-29/
?? profiles/cell4r-42/
?? profiles/cell5-01/
?? profiles/cell5-02/
?? profiles/cell5-03/
?? profiles/cell5-04/
?? profiles/cell5-05/
?? profiles/cell5-06/
?? profiles/cell5-07/
?? profiles/cell5-08/
?? profiles/cell5-09/
?? profiles/cell5-10/
?? profiles/cell5r-03/
?? profiles/cell5r-04/
?? profiles/cell5r-05/
?? profiles/cell5r-08/
?? profiles/cell5r-09/
?? profiles/cell5r-10/
?? profiles/cell6-04/
?? profiles/cell6-05/
?? profiles/cell6-06/
?? profiles/cell6-07/
?? profiles/cell6-09/
?? profiles/cell6-10/
?? profiles/cell6r-05/
?? profiles/cell6r-09/
?? profiles/cell6r-10/
?? profiles/cell7-01/
?? profiles/cell7-02/
?? profiles/cell7-03/
?? profiles/cell7-04/
?? profiles/cell7-05/
?? profiles/cell7-06/
?? profiles/cell7-07/
?? profiles/cell7-08/
?? profiles/cell7-09/
?? profiles/cell7-10/
?? profiles/cell7r-03/
?? profiles/cell7r-05/
?? profiles/cell7r-06/
?? profiles/cell7r-08/
?? profiles/cell7r-09/
?? profiles/cell8-03/
?? profiles/cell8-05/
?? profiles/cell8-06/
?? profiles/cell8-07/
?? profiles/cell8-09/
?? profiles/cell8-10/
?? profiles/cell8-100/
?? profiles/cell8-12/
?? profiles/cell8-14/
?? profiles/cell8-15/
?? profiles/cell8-17/
?? profiles/cell8-18/
?? profiles/cell8-19/
?? profiles/cell8-25/
?? profiles/cell8-27/
?? profiles/cell8-28/
?? profiles/cell8-30/
?? profiles/cell8-33/
?? profiles/cell8-35/
?? profiles/cell8-37/
?? profiles/cell8-41/
?? profiles/cell8-42/
?? profiles/cell8-44/
?? profiles/cell8-45/
?? profiles/cell8-46/
?? profiles/cell8-50/
?? profiles/cell8-53/
?? profiles/cell8-56/
?? profiles/cell8-58/
?? profiles/cell8-59/
?? profiles/cell8-61/
?? profiles/cell8-62/
?? profiles/cell8-63/
?? profiles/cell8-65/
?? profiles/cell8-66/
?? profiles/cell8-68/
?? profiles/cell8-69/
?? profiles/cell8-72/
?? profiles/cell8-74/
?? profiles/cell8-76/
?? profiles/cell8-78/
?? profiles/cell8-79/
?? profiles/cell8-81/
?? profiles/cell8-86/
?? profiles/cell8-87/
?? profiles/cell8-90/
?? profiles/cell8-91/
?? profiles/cell8-92/
?? profiles/cell8-94/
?? profiles/cell8-95/
?? profiles/cell8-97/
?? profiles/cell8-98/
?? profiles/ctrl10-01/
?? profiles/ctrl10-02/
?? profiles/ctrl10-03/
?? profiles/ctrl10-04/
?? profiles/ctrl10-05/
?? profiles/ctrl10-06/
?? profiles/ctrl10-07/
?? profiles/ctrl10-08/
?? profiles/ctrl10-09/
?? profiles/ctrl10-10/
?? profiles/geo46/
?? profiles/i10-001/
?? profiles/i10-002/
?? profiles/i10-003/
?? profiles/i10-004/
?? profiles/i10-005/
?? profiles/i10-006/
?? profiles/i10-007/
?? profiles/i10-008/
?? profiles/i10-009/
?? profiles/i10-010/
?? profiles/i10-011/
?? profiles/i10-012/
?? profiles/i10-013/
?? profiles/i10-014/
?? profiles/i10-015/
?? profiles/i10-016/
?? profiles/i10-017/
?? profiles/i10-018/
?? profiles/i10-019/
?? profiles/i10-020/
?? profiles/i10-021/
?? profiles/i10-022/
?? profiles/i10-023/
?? profiles/i10-024/
?? profiles/i10-025/
?? profiles/i10-026/
?? profiles/i10-027/
?? profiles/i10-028/
?? profiles/i10-029/
?? profiles/i10-030/
?? profiles/i10-031/
?? profiles/i10-032/
?? profiles/i10-033/
?? profiles/i10-034/
?? profiles/i10-035/
?? profiles/i10-036/
?? profiles/i10-037/
?? profiles/i10-038/
?? profiles/i10-039/
?? profiles/i10-040/
?? profiles/ins-reg-explore-01/
?? profiles/ins-reg-explore-02/
?? profiles/ins-reg-explore-03/
?? profiles/linkedin-classicworld-company/
?? profiles/linkedin-yiren-personal/
?? profiles/reddit-reg-01/
?? profiles/reddit-reg-explore-01/
?? profiles/reddit-warm-02/
?? scripts/outlook_imap_pinterest_code.py
?? thinking-ui-monitor-report.md
?? tui-dark-monitor-report.md
# Fingerprint P0: ICE assert + font gate + headed window-size

**Branch:** `feat/fingerprint-ice-fonts-window`  
**Base:** `feat/fingerprint-persona-geoip` @ `3d3c8a8`  
**Astra next-steps:** `artifacts/astra-fingerprint-next-steps-verdict.md` — 有条件通过  
**Implement brief:** `artifacts/ask-grok-fingerprint-ice-fonts-window.md`

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
**292 tests, OK.**

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
`launch_context` does not auto-gather ICE (latency). Strict register should call
`verify_webrtc_ice_no_leak(page, exit_ip)` after launch when `geo_cache.exit_ip`
/ `--fingerprint-webrtc-ip` is known. Failure raises `WebrtcIceLeakError`.
Reading prompt from stdin...
OpenAI Codex v0.154.0
--------
workdir: /workspace/CloakCLI
model: gpt-6-astra
provider: openai
approval: never
sandbox: read-only
reasoning effort: none
reasoning summaries: none
session id: 01a0db1f-f204-7133-b3d7-e9f323515dd5
--------
user
# Ask Astra — CloakCLI / CloakBrowser fingerprint NEXT-STEPS roadmap

**To:** Pi's Astra (`codex exec -m gpt-6-astra`)  
**From:** 匿名多开浏览器 (via coordinator; user said 问问astra看看)  
**Repo:** `/workspace/CloakCLI`  
**Branch / SHA:** `feat/fingerprint-persona-geoip` @ `3d3c8a8`  
**Do NOT implement code. Do NOT notify other bots. Verdict + priority only.**

---

## 已落地 / What already shipped

Astra code review 通过后刚落地（详见 `artifacts/fingerprint-persona-geoip-report.md`）：

- Persistent `fingerprint_seed` + `fingerprint_persona`（schema 3，`derived_from_seed` 绑定）
- Platform/brand whitelist：**Chrome-only** on Windows（10.0.0 | 15.0.0）；Opera/Vivaldi/Edge 未开
- `hardwareConcurrency` / `deviceMemory` 与 Windows ANGLE GPU whitelist 配对（Intel / AMD / NVIDIA，11 SKUs，已在 **Chromium 146.0.7680.177.5** 实测）
- Screen presets（DPR=1）+ headless Playwright viewport；**headed 不设 Playwright viewport**
- GeoIP：echo 验证 exit IP（拒绝 hostname fallback）；City DB IANA TZ；`geo_cache` 绑定 proxy identity + exit IP + DB epoch；**register fail-closed**（无 host TZ fallback）
- WebRTC：`--fingerprint-webrtc-ip=<echo exit IP>` + `geoip=True`
- **不传** Playwright `user_agent`（persona 生效时）
- create-pin 已移除 `set_viewport_size(1440×960)` stomping persona viewport

Launch flags emitted by `python/cloakcli_worker/fingerprint.py` → `fingerprint_chrome_args()`:

`--fingerprint` / `--fingerprint-platform` / `--fingerprint-platform-version` / `--fingerprint-brand` / `--fingerprint-brand-version` / `--fingerprint-hardware-concurrency` / `--fingerprint-device-memory` / `--fingerprint-gpu-vendor` / `--fingerprint-gpu-renderer` / `--fingerprint-screen-width|height` / `--fingerprint-taskbar-height` / `--lang` + `--fingerprint-locale` / `--fingerprint-timezone` / `--fingerprint-webrtc-ip`

**用户目标：** 往 AdsPower / 紫鸟 风格的 **环境一致性** 靠（不是假唯一性抽奖）。

**明确不做（landing residual + 产品线）：** 不把本轮落地当成「注册已修好」；UA 碰撞率不做 ship gate；不 casual bump cloakbrowser；不做 per-profile fake font-list lottery。

---

## Residual（来自 landing report）

| Item | Notes |
|---|---|
| Accept-Language header | 与 geo locale 同源未保证（目前 `--lang` / `--fingerprint-locale`；HTTP Accept-Language 同源未断言） |
| iframe / Worker Client Hints | 仅 JS CH 探过；HTTP `Sec-CH-UA*` 未抓 |
| Full ICE leak assert | 文档有探针，**CI/probe 未断言** host candidates == echo exit IP |
| Linux Windows font pack | **未安装**；cloakbrowser 仅 warn（`_WINDOWS_FONT_TELLS`）；见 `cloakbrowser/browser.py` ~1480+ |
| `--fingerprint-windows-font-metrics` | README：**Chromium 148+ only**，在当前 **146** binary 上为 **no-op** |
| Headed OS window vs spoofed screen | 可不一致（window ≠ persona screen） |
| Same-seed canvas/audio/GPU restart | 继承于 seed，**未新测** |
| Causality | **不证明** 注册失败是 fingerprint 导致 |

`docs/chrome40-fpjs-font-minimum-set-investigation.md` 在本机 cloakbrowser 树中 **引用存在、文件缺失**（browser.py / fonts.ts 注释指向它）。Minimum Windows OS tells：Segoe UI, Segoe UI Light, Calibri, Marlett, MS UI Gothic, Franklin Gothic, Consolas, Courier New。Office pack 为 informational only。

---

## Proposed roadmap（协调者 → 用户 → 请 Astra 审）

### Proposed P0
1. Install Linux Windows font **minimum set** + optionally `--fingerprint-windows-font-metrics`；missing fonts → warn **or** register fail-closed.
2. Align **headed window size** with spoofed screen，避免 OS window ≠ persona screen。

### Proposed P1
3. WebRTC ICE assert in CI/probe：host candidates **must** be echo exit IP.
4. HTTP `Sec-CH-UA*` + iframe/Worker Client Hints probes（当前仅 JS CH）。
5. Same-seed restart stability probe（canvas / audio / GPU）。

### Proposed P2
6. `Accept-Language` header 与 geo locale 同源。
7. Deduplicate **double echo** on first `geoip=True` launch。
8. Edge brand only after proven on this **146** binary.

### Proposed P3 / don't
9. Don't bump cloakbrowser casually；don't per-profile fake font-list lottery；don't use UA collision rate as ship gate.
10. Don't mass-scale register until intermittent two-batch results land.

**Suggested next build if Astra agrees:** P0 fonts gate + ICE probe.

---

## 请 Astra 给出 / Please answer

1. **Verdict：** `通过` / `有条件通过` / `打回`（对上述 roadmap，不是对已落地代码再审）
2. **Reordered priority**（P0→Pn 列表；可合并/拆分/降级项）
3. **What NOT to do**（尤其 fonts install 副作用、fail-closed 对 nurture vs register 是否过激、146 上 `--fingerprint-windows-font-metrics` no-op）
4. **Acceptance criteria** for the **next implementation gate**（建议下一刀：fonts gate + ICE probe？）
5. **Risks：**
   - 在 Linux 装 Microsoft-proprietary Windows fonts 的许可/部署副作用
   - miss fonts → register fail-closed 是否过激；nurture 是否只 warn
   - headed window 对齐是否该用 `--window-size` / `--start-maximized` / 其它（146 上 `start_maximized` 已有 binary gate）
   - 在不 bump 到 148+ 的前提下，仅装字体（无 font-metrics flag）是否仍值得做 P0

请用简洁中英双语回复；最终一行明确写：`VERDICT: 通过|有条件通过|打回`。

codex
**Verdict / 结论：有条件通过。** Roadmap方向正确，但下一刀应先把“泄漏/环境不一致”变成可测量的 gate；字体安装必须受许可与部署边界约束。

**Priority / 重排：**

1. **P0 — WebRTC full ICE assertion**：host candidates 必须只暴露 echo exit IP；发现私网/宿主机地址即 fail。
2. **P0 — Font availability gate**：提供可审计、已授权的 Linux image/package；至少验证 Windows minimum set。
3. **P0 — Headed window alignment**：实际 outer/inner window、persona screen、taskbar/content viewport 必须满足明确关系。
4. **P1 — HTTP consistency probes**：Accept-Language、`Sec-CH-UA*`，并覆盖 iframe/Worker。
5. **P1 — Same-seed restart stability**：canvas、audio、GPU 输出跨重启保持稳定。
6. **P2 — GeoIP double-echo 去重**。
7. **P2 — Edge brand**：只有在 Chromium 146 当前 binary 上完成行为证明后再开放。
8. **P3 — Operational safeguards**：保留“不批量放大注册”“不以 UA collision rate 作为 ship gate”等限制。

建议下一 gate 仍为 **fonts gate + ICE probe**，但 ICE assertion 应先于字体进入 CI 阻断链。

**What not to do / 不要做：**

- 不要运行时无提示地下载或安装 Microsoft proprietary fonts；使用固定、可审计、具授权依据的镜像/安装包。
- 不要把缺字体对所有流程一律 fail-closed。  
  - **register/strict persona**：可配置为 fail-closed。  
  - **nurture/探索流程**：warn，并记录缺失集合。
- 不要在 Chromium 146 上把 `--fingerprint-windows-font-metrics` 当作有效能力或 acceptance 条件；当前为 no-op，应省略或明确标记。
- 不要用 `--start-maximized` 作为唯一对齐手段；窗口管理器行为不够确定。优先使用经过实测的 `--window-size`，并验证实际尺寸。
- 不要为了字体一致性引入 per-profile 随机字体列表，也不要 casual bump cloakbrowser。

**Next-gate acceptance criteria / 下一 gate 验收：**

- Font gate：minimum set 每个字体可被浏览器实际枚举/使用；版本、来源、许可证和镜像内容可复现；缺失行为按 register/nurture 策略执行。
- ICE probe：至少覆盖普通页面、iframe、Worker、重启；host candidates 不得出现宿主机/私网地址，且必须等于 echo exit IP；失败阻断 strict register。
- Headed probe：实测窗口与 persona screen 的宽高、DPR、可用工作区关系稳定；不得依赖未验证的 maximize 行为。
- 输出 probe 结果、binary 版本（146.0.7680.177.5）、seed、proxy identity 和 exit IP，便于复现。

**Risks / 风险：**

- Linux 安装 Windows 字体有许可、镜像分发和供应链审计风险；应由部署层显式选择，不应由 CLI 静默完成。
- 缺字体对 register fail-closed 可能降低可用性，因此应限于 strict/register，nurture 只告警。
- 仅安装字体（不启用 148+ metrics flag）仍有价值：可改善字体枚举、fallback 和布局一致性；但不要宣称获得 Windows 原生字体 metrics。
- headed 对齐应以实际测量结果为准；`--window-size` 通常比 `--start-maximized` 可控，但必须考虑 taskbar 与 viewport 的换算。

VERDICT: 有条件通过
tokens used
5,018
**Verdict / 结论：有条件通过。** Roadmap方向正确，但下一刀应先把“泄漏/环境不一致”变成可测量的 gate；字体安装必须受许可与部署边界约束。

**Priority / 重排：**

1. **P0 — WebRTC full ICE assertion**：host candidates 必须只暴露 echo exit IP；发现私网/宿主机地址即 fail。
2. **P0 — Font availability gate**：提供可审计、已授权的 Linux image/package；至少验证 Windows minimum set。
3. **P0 — Headed window alignment**：实际 outer/inner window、persona screen、taskbar/content viewport 必须满足明确关系。
4. **P1 — HTTP consistency probes**：Accept-Language、`Sec-CH-UA*`，并覆盖 iframe/Worker。
5. **P1 — Same-seed restart stability**：canvas、audio、GPU 输出跨重启保持稳定。
6. **P2 — GeoIP double-echo 去重**。
7. **P2 — Edge brand**：只有在 Chromium 146 当前 binary 上完成行为证明后再开放。
8. **P3 — Operational safeguards**：保留“不批量放大注册”“不以 UA collision rate 作为 ship gate”等限制。

建议下一 gate 仍为 **fonts gate + ICE probe**，但 ICE assertion 应先于字体进入 CI 阻断链。

**What not to do / 不要做：**

- 不要运行时无提示地下载或安装 Microsoft proprietary fonts；使用固定、可审计、具授权依据的镜像/安装包。
- 不要把缺字体对所有流程一律 fail-closed。  
  - **register/strict persona**：可配置为 fail-closed。  
  - **nurture/探索流程**：warn，并记录缺失集合。
- 不要在 Chromium 146 上把 `--fingerprint-windows-font-metrics` 当作有效能力或 acceptance 条件；当前为 no-op，应省略或明确标记。
- 不要用 `--start-maximized` 作为唯一对齐手段；窗口管理器行为不够确定。优先使用经过实测的 `--window-size`，并验证实际尺寸。
- 不要为了字体一致性引入 per-profile 随机字体列表，也不要 casual bump cloakbrowser。

**Next-gate acceptance criteria / 下一 gate 验收：**

- Font gate：minimum set 每个字体可被浏览器实际枚举/使用；版本、来源、许可证和镜像内容可复现；缺失行为按 register/nurture 策略执行。
- ICE probe：至少覆盖普通页面、iframe、Worker、重启；host candidates 不得出现宿主机/私网地址，且必须等于 echo exit IP；失败阻断 strict register。
- Headed probe：实测窗口与 persona screen 的宽高、DPR、可用工作区关系稳定；不得依赖未验证的 maximize 行为。
- 输出 probe 结果、binary 版本（146.0.7680.177.5）、seed、proxy identity 和 exit IP，便于复现。

**Risks / 风险：**

- Linux 安装 Windows 字体有许可、镜像分发和供应链审计风险；应由部署层显式选择，不应由 CLI 静默完成。
- 缺字体对 register fail-closed 可能降低可用性，因此应限于 strict/register，nurture 只告警。
- 仅安装字体（不启用 148+ metrics flag）仍有价值：可改善字体枚举、fallback 和布局一致性；但不要宣称获得 Windows 原生字体 metrics。
- headed 对齐应以实际测量结果为准；`--window-size` 通常比 `--start-maximized` 可控，但必须考虑 taskbar 与 viewport 的换算。

VERDICT: 有条件通过

exec
/bin/bash -lc 'git diff 1f2e8c9a8b86cdc643f280d0c2cd6407a62ac904''^ 1f2e8c9a8b86cdc643f280d0c2cd6407a62ac904 -- python/cloakcli_worker/fingerprint.py python/cloakcli_worker/browser.py python/tests/test_fingerprint.py python/tests/test_browser_launch.py scripts/probe_fingerprint_ice_fonts_window.py docs/windows-fonts-linux-ops.md' in /workspace/CloakCLI
 succeeded in 0ms:
diff --git a/docs/windows-fonts-linux-ops.md b/docs/windows-fonts-linux-ops.md
new file mode 100644
index 0000000..c3f24ec
--- /dev/null
+++ b/docs/windows-fonts-linux-ops.md
@@ -0,0 +1,79 @@
+# Windows fonts on Linux (ops) — CloakCLI font gate
+
+CloakCLI spoofs **Windows** Chrome on Linux. Anti-bot font probes expect a
+minimum set of **Windows OS** fonts. CloakBrowser warns when they are missing;
+CloakCLI turns that into a **gate**:
+
+| Path | Missing fonts |
+|---|---|
+| **register / strict** (`require_geo` / register skill / `CLOAKCLI_REQUIRE_FONTS=1`) | **fail-closed** — launch aborted |
+| **nurture / explore** | **warn only** — logs the missing set, continues |
+
+## Minimum set (detect-only)
+
+Must match `cloakbrowser.browser._WINDOWS_FONT_TELLS` / CloakCLI
+`WINDOWS_MINIMUM_FONTS`:
+
+- Segoe UI
+- Segoe UI Light
+- Calibri
+- Marlett
+- MS UI Gothic
+- Franklin Gothic
+- Consolas
+- Courier New
+
+Office supplemental fonts (Century Gothic, Wingdings 2, …) are **informational
+only** and are **not** part of this gate.
+
+## What CloakCLI will NOT do
+
+- **No silent download or install** of Microsoft-proprietary fonts at runtime.
+- **No** `--fingerprint-windows-font-metrics` on Chromium **146** (documented
+  effective on **148+** only; emitting it on 146 is a no-op and must not be an
+  acceptance claim).
+- **No** per-profile fake font-list lottery.
+
+Font presence is checked with `fc-list` only.
+
+## How ops installs a licensed pack
+
+1. Obtain fonts under a **valid Microsoft / OEM / enterprise license** that
+   permits install on your Linux fleet image (do **not** scrape Windows ISOs
+   or download random “font packs” from the internet without license review).
+2. Install into a **fixed, audited** image or package (example layout — adjust
+   to your distro and license terms):
+
+   ```bash
+   # Example only — paths/package names are site-specific.
+   sudo mkdir -p /usr/local/share/fonts/ms-windows-minimum
+   # Copy licensed .ttf/.ttc files for the eight families above into that dir.
+   sudo fc-cache -f -v
+   fc-list | grep -E 'Segoe UI|Calibri|Consolas|Courier New|Marlett|Franklin Gothic|MS UI Gothic'
+   ```
+
+3. Bake the pack into the **golden AMI / container / Nix closure** so every
+   register host is identical. Record license evidence + package version in
+   your change ticket.
+4. Verify before opening register:
+
+   ```bash
+   PYTHONPATH=python python3 -c "
+   from cloakcli_worker.fingerprint import missing_windows_minimum_fonts
+   m = missing_windows_minimum_fonts()
+   print('missing', m)
+   raise SystemExit(0 if m == [] else 1)
+   "
+   ```
+
+5. Optional env overrides:
+
+   - `CLOAKCLI_REQUIRE_FONTS=1` — force fail-closed even off register paths
+   - `CLOAKCLI_REQUIRE_FONTS=0` — force warn-only (not recommended for register)
+
+## Chromium note
+
+Binary under test: **146.0.7680.177.5**. Installing fonts improves
+enumeration / fallback / layout consistency with a Windows persona. It does
+**not** grant Windows-native font **metrics**; do not claim that until a
+148+ binary with `--fingerprint-windows-font-metrics` is proven.
diff --git a/python/cloakcli_worker/browser.py b/python/cloakcli_worker/browser.py
index c7e4466..11716db 100644
--- a/python/cloakcli_worker/browser.py
+++ b/python/cloakcli_worker/browser.py
@@ -42,8 +42,13 @@ def launch_context(
     (Chrome brand + platform_version + hw/screen/GPU) is minted from the seed.
     With a proxy, geoip is resolved against the echo-verified exit IP and
     timezone/locale/WebRTC flags are set; register paths fail closed on geo
-    failure (no host timezone fallback). Playwright user_agent is not used for
-    persona (it desyncs HTTP UA / Client Hints / JS userAgentData).
+    failure (no host timezone fallback). Register/strict also fail-closed when
+    Windows minimum fonts are missing (nurture warns only; no silent MS font
+    install). Headed launches emit --window-size from persona screen (not
+    maximize-only). Post-launch ICE: call
+    cloakcli_worker.fingerprint.verify_webrtc_ice_no_leak(page, exit_ip) so
+    host candidates must equal the echo exit IP. Playwright user_agent is not
+    used for persona (it desyncs HTTP UA / Client Hints / JS userAgentData).
     """
     from cloakbrowser import launch_persistent_context
 
diff --git a/python/cloakcli_worker/fingerprint.py b/python/cloakcli_worker/fingerprint.py
index c70d1ae..6be6756 100644
--- a/python/cloakcli_worker/fingerprint.py
+++ b/python/cloakcli_worker/fingerprint.py
@@ -16,14 +16,18 @@ Never log proxy URLs, passwords, emails, or cookie values.
 Never set Playwright user_agent — that desyncs HTTP UA / Client Hints /
 JS userAgentData. Brand identity is binary flags only.
 
-WebRTC ICE verification (follow-up test; not asserted here):
-  Launch with proxy, then in the page:
-    const pc = new RTCPeerConnection({iceServers:[{urls:'stun:stun.l.google.com:19302'}]});
-    pc.createDataChannel('x');
-    pc.onicecandidate = e => console.log(e.candidate && e.candidate.candidate);
-    pc.createOffer().then(o => pc.setLocalDescription(o));
-  Host ICE candidates should carry --fingerprint-webrtc-ip (the echo-verified
-  exit IP). A host candidate with the machine's real IP is a leak.
+WebRTC ICE assertion (P0):
+  Host candidates from RTCPeerConnection must equal the echo-verified exit IP
+  passed as --fingerprint-webrtc-ip. Use assert_webrtc_host_equals_exit_ip /
+  gather_webrtc_ice_candidates (page probe). A host candidate with a private,
+  loopback, or non-exit public IP is a leak and fails strict register / CI.
+
+Font gate (P0): detect Windows minimum font set via fc-list only — never
+silently install Microsoft fonts. Register/strict fail-closed; nurture warns.
+Omit --fingerprint-windows-font-metrics on Chromium 146 (no-op).
+
+Headed window (P0): emit --window-size=<screen_w>,<screen_h> from persona;
+do not rely on --start-maximized alone.
 """
 
 from __future__ import annotations
@@ -155,6 +159,23 @@ SCREEN_PRESETS: tuple[tuple[int, int, int, int], ...] = (
     (1920, 1200, 48, 85),
 )
 
+# Keep in sync with cloakbrowser.browser._WINDOWS_FONT_TELLS (issue #395).
+# Detect-only; ops installs a licensed pack — see docs/windows-fonts-linux-ops.md.
+WINDOWS_MINIMUM_FONTS: tuple[str, ...] = (
+    "Segoe UI",
+    "Segoe UI Light",
+    "Calibri",
+    "Marlett",
+    "MS UI Gothic",
+    "Franklin Gothic",
+    "Consolas",
+    "Courier New",
+)
+
+# Chromium major where --fingerprint-windows-font-metrics is documented effective.
+# On 146 it is a no-op; never emit it from CloakCLI on this binary.
+WINDOWS_FONT_METRICS_MIN_MAJOR = 148
+
 class PersonaWhitelistError(ValueError):
     """Persona is not on the verified (brand, version, chromium, platform) list."""
 
@@ -163,6 +184,14 @@ class GeoResolutionError(RuntimeError):
     """Fail-closed geo lookup — do not launch with the host timezone."""
 
 
+class FontAvailabilityError(RuntimeError):
+    """Fail-closed Windows font gate — register/strict only; nurture warns."""
+
+
+class WebrtcIceLeakError(RuntimeError):
+    """Host ICE candidates leaked a non-exit IP (or none matched exit IP)."""
+
+
 def mint_fingerprint_seed() -> int:
     """Return a new seed in [SEED_MIN, SEED_MAX] inclusive."""
     return random.randint(SEED_MIN, SEED_MAX)
@@ -410,17 +439,316 @@ def validate_persona(persona: Any) -> dict[str, Any]:
     return persona
 
 
+
+def env_require_fonts() -> bool | None:
+    raw = os.environ.get("CLOAKCLI_REQUIRE_FONTS", "").strip().lower()
+    if raw in ("1", "true", "yes", "on"):
+        return True
+    if raw in ("0", "false", "no", "off"):
+        return False
+    return None
+
+
+def _fc_list_blob() -> str | None:
+    """Return lowercased fc-list stdout, or None if unavailable."""
+    import subprocess
+
+    try:
+        result = subprocess.run(
+            ["fc-list"],
+            capture_output=True,
+            text=True,
+            timeout=5,
+            check=False,
+        )
+    except (OSError, subprocess.SubprocessError):
+        return None
+    if result.returncode != 0:
+        return None
+    return (result.stdout or "").lower()
+
+
+def missing_windows_minimum_fonts(
+    *,
+    listing: str | None = None,
+    fonts: tuple[str, ...] = WINDOWS_MINIMUM_FONTS,
+) -> list[str] | None:
+    """Return missing font family names, or None when presence cannot be determined.
+
+    None means unknown (fc-list missing/errored) — callers must not treat that
+    as an empty missing list. An empty list means the full minimum set is present.
+    """
+    blob = listing if listing is not None else _fc_list_blob()
+    if blob is None:
+        return None
+    missing: list[str] = []
+    for font in fonts:
+        if font.lower() not in blob:
+            missing.append(font)
+    return missing
+
+
+def enforce_windows_font_gate(
+    *,
+    fail_closed: bool,
+    listing: str | None = None,
+) -> list[str]:
+    """Check Windows minimum fonts. Fail-closed for register/strict; warn otherwise.
+
+    Never downloads or installs fonts. Returns the missing list (possibly empty).
+    When fc-list is unavailable: fail-closed raises; warn path logs and returns [].
+    `listing` injects an fc-list blob for tests (None = run fc-list).
+    """
+    missing = missing_windows_minimum_fonts(listing=listing)
+    if missing is None:
+        msg = (
+            "FONT_GATE_UNKNOWN: fc-list unavailable; cannot verify Windows minimum fonts. "
+            "See docs/windows-fonts-linux-ops.md"
+        )
+        if fail_closed:
+            raise FontAvailabilityError(msg)
+        sys.stderr.write(f"[fonts] warn: {msg}\n")
+        sys.stderr.flush()
+        return []
+    if not missing:
+        return []
+    detail = ", ".join(missing)
+    msg = (
+        f"FONT_GATE_MISSING: Windows minimum fonts not installed: {detail}. "
+        "Ops must install a licensed pack — docs/windows-fonts-linux-ops.md "
+        "(CLI never silent-installs Microsoft fonts)."
+    )
+    if fail_closed:
+        raise FontAvailabilityError(msg)
+    sys.stderr.write(f"[fonts] warn missing={detail}\n")
+    sys.stderr.flush()
+    return missing
+
+
+def parse_ice_candidate_ip(candidate_line: str) -> tuple[str, str] | None:
+    """Parse (ip, typ) from an ICE candidate line. typ is host/srflx/relay/prflx.
+
+    Accepts raw `candidate:...` or `a=candidate:...` SDP forms.
+    """
+    if not isinstance(candidate_line, str) or not candidate_line.strip():
+        return None
+    line = candidate_line.strip()
+    if line.startswith("a="):
+        line = line[2:].strip()
+    if line.lower().startswith("candidate:"):
+        body = line.split(":", 1)[1]
+    else:
+        body = line
+    parts = body.split()
+    # foundation component protocol priority ip port typ <type>
+    if len(parts) < 8:
+        return None
+    try:
+        typ_idx = parts.index("typ")
+    except ValueError:
+        return None
+    if typ_idx < 5 or typ_idx + 1 >= len(parts):
+        return None
+    ip = parts[4]
+    cand_type = parts[typ_idx + 1].lower()
+    return ip, cand_type
+
+
+def host_candidate_ips(candidates: list[str] | tuple[str, ...]) -> list[str]:
+    """Ordered unique host-type candidate IPs (IPv4/IPv6 as written)."""
+    seen: set[str] = set()
+    out: list[str] = []
+    for raw in candidates:
+        parsed = parse_ice_candidate_ip(raw)
+        if parsed is None:
+            continue
+        ip, typ = parsed
+        if typ != "host":
+            continue
+        if ip in seen:
+            continue
+        seen.add(ip)
+        out.append(ip)
+    return out
+
+
+def assert_webrtc_host_equals_exit_ip(
+    candidates: list[str] | tuple[str, ...],
+    exit_ip: str,
+) -> list[str]:
+    """Fail if any host ICE IP != echo exit IP, or if no host candidates appear.
+
+    Also fails when a host candidate is private/loopback/link-local even if it
+    somehow matched (defensive). Returns the host IP list on success.
+    """
+    if not isinstance(exit_ip, str) or not exit_ip.strip():
+        raise WebrtcIceLeakError("WEBRTC_ICE: missing echo exit IP for assertion")
+    expected = exit_ip.strip()
+    if not _is_public_ip(expected):
+        raise WebrtcIceLeakError(
+            f"WEBRTC_ICE: exit IP is not public; refusing assert ({expected!r})"
+        )
+    hosts = host_candidate_ips(candidates)
+    if not hosts:
+        raise WebrtcIceLeakError(
+            "WEBRTC_ICE: no host candidates gathered; cannot verify exit IP spoof"
+        )
+    bad: list[str] = []
+    for ip in hosts:
+        if ip != expected:
+            bad.append(ip)
+        elif not _is_public_ip(ip):
+            bad.append(ip)
+    if bad:
+        # Never claim "machine IP" in logs beyond the leaked address itself.
+        raise WebrtcIceLeakError(
+            "WEBRTC_ICE_LEAK: host candidates must equal echo exit IP "
+            f"{expected}; got host={hosts}"
+        )
+    return hosts
+
+
+# Playwright page.evaluate snippet: gather ICE candidates (page context).
+WEBRTC_ICE_GATHER_JS = """
+async ({ stunUrls, timeoutMs }) => {
+  const urls = (stunUrls && stunUrls.length)
+    ? stunUrls
+    : ['stun:stun.l.google.com:19302'];
+  const candidates = [];
+  const pc = new RTCPeerConnection({
+    iceServers: urls.map((u) => ({ urls: u })),
+  });
+  try {
+    pc.createDataChannel('cloakcli-ice');
+    await new Promise((resolve, reject) => {
+      const timer = setTimeout(() => resolve(null), timeoutMs || 8000);
+      pc.onicecandidate = (ev) => {
+        if (!ev.candidate) {
+          clearTimeout(timer);
+          resolve(null);
+          return;
+        }
+        if (ev.candidate.candidate) {
+          candidates.push(ev.candidate.candidate);
+        }
+      };
+      pc.onicecandidateerror = () => {};
+      pc.createOffer()
+        .then((offer) => pc.setLocalDescription(offer))
+        .catch((err) => {
+          clearTimeout(timer);
+          reject(err);
+        });
+    });
+  } finally {
+    try { pc.close(); } catch (e) {}
+  }
+  return candidates;
+}
+"""
+
+
+WEBRTC_ICE_GATHER_WORKER_JS = """
+async ({ stunUrls, timeoutMs }) => {
+  const urls = (stunUrls && stunUrls.length)
+    ? stunUrls
+    : ['stun:stun.l.google.com:19302'];
+  const src = `
+    self.onmessage = async (ev) => {
+      const { urls, timeoutMs } = ev.data;
+      const candidates = [];
+      const pc = new RTCPeerConnection({
+        iceServers: urls.map((u) => ({ urls: u })),
+      });
+      try {
+        pc.createDataChannel('cloakcli-ice-worker');
+        await new Promise((resolve) => {
+          const timer = setTimeout(() => resolve(null), timeoutMs || 8000);
+          pc.onicecandidate = (e) => {
+            if (!e.candidate) { clearTimeout(timer); resolve(null); return; }
+            if (e.candidate.candidate) candidates.push(e.candidate.candidate);
+          };
+          pc.createOffer().then((o) => pc.setLocalDescription(o)).catch(() => resolve(null));
+        });
+      } finally {
+        try { pc.close(); } catch (e) {}
+      }
+      self.postMessage(candidates);
+    };
+  `;
+  const blob = new Blob([src], { type: 'application/javascript' });
+  const url = URL.createObjectURL(blob);
+  try {
+    return await new Promise((resolve, reject) => {
+      const worker = new Worker(url);
+      const timer = setTimeout(() => {
+        try { worker.terminate(); } catch (e) {}
+        reject(new Error('worker ICE gather timeout'));
+      }, (timeoutMs || 8000) + 2000);
+      worker.onmessage = (ev) => {
+        clearTimeout(timer);
+        try { worker.terminate(); } catch (e) {}
+        resolve(ev.data || []);
+      };
+      worker.onerror = (err) => {
+        clearTimeout(timer);
+        try { worker.terminate(); } catch (e) {}
+        reject(err);
+      };
+      worker.postMessage({ urls, timeoutMs: timeoutMs || 8000 });
+    });
+  } finally {
+    URL.revokeObjectURL(url);
+  }
+}
+"""
+
+
+def headed_window_size_arg(persona: dict[str, Any]) -> str:
+    """Return --window-size=W,H for persona screen (outer target)."""
+    return (
+        f"--window-size={int(persona['screen_width'])},"
+        f"{int(persona['screen_height'])}"
+    )
+
+
+def persona_window_geometry(persona: dict[str, Any]) -> dict[str, int]:
+    """Explicit outer/inner/available relationships for headed probes."""
+    sw = int(persona["screen_width"])
+    sh = int(persona["screen_height"])
+    taskbar = int(persona["taskbar_height"])
+    chrome_ui = int(persona["chrome_ui_height"])
+    return {
+        "screen_width": sw,
+        "screen_height": sh,
+        "window_width": sw,
+        "window_height": sh,
+        "available_width": int(persona["available_width"]),
+        "available_height": int(persona["available_height"]),
+        "viewport_width": int(persona["viewport_width"]),
+        "viewport_height": int(persona["viewport_height"]),
+        "taskbar_height": taskbar,
+        "chrome_ui_height": chrome_ui,
+        # expected: available_height == screen_height - taskbar
+        # expected: viewport_height == available_height - chrome_ui
+    }
+
+
 def fingerprint_chrome_args(
     seed: int,
     persona: dict[str, Any] | None = None,
     *,
     geo: dict[str, Any] | None = None,
     language: str | None = None,
+    headed: bool = False,
 ) -> list[str]:
     """Chrome args that override cloakbrowser's random --fingerprint default.
 
     Always includes the verified persona flags so HTTP UA / CH / JS stay on
     the same Chrome identity. Playwright user_agent is never emitted.
+
+    Headed launches add --window-size matching persona screen (not maximize-only).
+    Never emits --fingerprint-windows-font-metrics on Chromium < 148 (146 no-op).
     """
     if not is_valid_fingerprint_seed(seed):
         raise ValueError(f"fingerprint_seed out of range [{SEED_MIN}, {SEED_MAX}]: {seed!r}")
@@ -443,6 +771,13 @@ def fingerprint_chrome_args(
         f"--fingerprint-screen-height={persona['screen_height']}",
         f"--fingerprint-taskbar-height={persona['taskbar_height']}",
     ]
+    if headed:
+        # Measured geometry from persona screen. Suppresses cloakbrowser's
+        # --start-maximized injection (build_args skips maximize when
+        # --window-size is already present).
+        args.append(
+            f"--window-size={int(persona['screen_width'])},{int(persona['screen_height'])}"
+        )
     lang = language or (geo or {}).get("locale") or (geo or {}).get("locale_from_geo")
     if lang and is_valid_locale(lang):
         lang = normalize_locale(lang)
@@ -454,6 +789,12 @@ def fingerprint_chrome_args(
     exit_ip = (geo or {}).get("exit_ip")
     if exit_ip:
         args.append(f"--fingerprint-webrtc-ip={exit_ip}")
+    # Explicitly never emit font-metrics on 146 (no-op / unproven).
+    major = deployed_chromium_major()
+    if major.isdigit() and int(major) >= WINDOWS_FONT_METRICS_MIN_MAJOR:
+        # Reserved: only enable when ops + binary gate say so. Still omitted
+        # by default so CloakCLI does not claim Windows native metrics.
+        pass
     return args
 
 
@@ -956,6 +1297,8 @@ def apply_to_launch_kwargs(
     headed: bool = False,
     profile_meta_path: str | Path | None = None,
     require_geo: bool | None = None,
+    require_fonts: bool | None = None,
+    font_listing: str | None = None,
     resolve_exit_ip: Callable[[str], str] | None = None,
     lookup_city: Callable[[str], dict[str, Any]] | None = None,
     db_version: str | None = None,
@@ -965,6 +1308,11 @@ def apply_to_launch_kwargs(
 
     Sets binary flags only. Passes geoip=True when a proxy is present and geo
     resolved (cloakbrowser still injects WebRTC if we did not already set it).
+
+    Font gate runs before launch args are finalized: register/strict fail-closed
+    on missing Windows minimum fonts; nurture warns. Headed emits --window-size
+    from persona screen. ICE leak assert is post-launch (see
+    assert_webrtc_host_equals_exit_ip) — call after gather on register paths.
     """
     meta = Path(profile_meta_path) if profile_meta_path is not None else None
     persona: dict[str, Any] | None = None
@@ -979,6 +1327,16 @@ def apply_to_launch_kwargs(
     if require_geo is None:
         require_geo = bool(env_geo) if env_geo is not None else False
 
+    env_fonts = env_require_fonts()
+    if require_fonts is None:
+        if env_fonts is not None:
+            require_fonts = bool(env_fonts)
+        else:
+            # Same default as geo: strict when caller marked require_geo
+            # (browser.py maps register skills → require_geo=True).
+            require_fonts = bool(require_geo)
+    enforce_windows_font_gate(fail_closed=bool(require_fonts), listing=font_listing)
+
     geo: dict[str, Any] = {}
     language: str | None = None
     if meta is not None:
@@ -1012,7 +1370,7 @@ def apply_to_launch_kwargs(
 
     if seed is not None:
         kwargs["args"] = fingerprint_chrome_args(
-            seed, persona, geo=geo or None, language=language
+            seed, persona, geo=geo or None, language=language, headed=headed
         )
     elif geo or language:
         extra: list[str] = []
@@ -1025,8 +1383,12 @@ def apply_to_launch_kwargs(
         exit_ip = geo.get("exit_ip")
         if exit_ip:
             extra.append(f"--fingerprint-webrtc-ip={exit_ip}")
+        if headed and persona is not None:
+            extra.append(headed_window_size_arg(persona))
         if extra:
             kwargs["args"] = extra
+    elif headed and persona is not None:
+        kwargs["args"] = [headed_window_size_arg(persona)]
 
     if persona is not None and not headed:
         kwargs["viewport"] = {
@@ -1035,3 +1397,78 @@ def apply_to_launch_kwargs(
         }
 
     return kwargs
+
+def gather_webrtc_ice_candidates(
+    page: Any,
+    *,
+    via: str = "page",
+    stun_urls: list[str] | None = None,
+    timeout_ms: int = 8000,
+) -> list[str]:
+    """Gather ICE candidate lines from a Playwright page (page / iframe / worker).
+
+    `via`:
+      - "page": RTCPeerConnection in the main frame
+      - "iframe": same gather inside a sandboxed about:blank iframe
+      - "worker": DedicatedWorker gather
+    """
+    payload = {"stunUrls": stun_urls or ["stun:stun.l.google.com:19302"], "timeoutMs": timeout_ms}
+    if via == "page":
+        raw = page.evaluate(WEBRTC_ICE_GATHER_JS, payload)
+    elif via == "worker":
+        raw = page.evaluate(WEBRTC_ICE_GATHER_WORKER_JS, payload)
+    elif via == "iframe":
+        frame_handle = page.evaluate_handle(
+            """async () => {
+              const iframe = document.createElement('iframe');
+              iframe.src = 'about:blank';
+              document.body.appendChild(iframe);
+              await new Promise((r) => { iframe.onload = r; });
+              return iframe;
+            }"""
+        )
+        try:
+            content_frame = frame_handle.as_element().content_frame()
+            if content_frame is None:
+                raise WebrtcIceLeakError("WEBRTC_ICE: iframe content_frame unavailable")
+            raw = content_frame.evaluate(WEBRTC_ICE_GATHER_JS, payload)
+        finally:
+            try:
+                page.evaluate(
+                    """() => {
+                      for (const iframe of document.querySelectorAll('iframe')) {
+                        iframe.remove();
+                      }
+                    }"""
+                )
+            except Exception:
+                pass
+    else:
+        raise ValueError(f"unknown ICE gather via={via!r}")
+    if not isinstance(raw, list):
+        return []
+    return [str(c) for c in raw if isinstance(c, str) and c.strip()]
+
+
+def verify_webrtc_ice_no_leak(
+    page: Any,
+    exit_ip: str,
+    *,
+    contexts: tuple[str, ...] = ("page", "iframe", "worker"),
+    stun_urls: list[str] | None = None,
+    timeout_ms: int = 8000,
+) -> dict[str, list[str]]:
+    """Gather ICE in each context and assert host candidates == exit_ip.
+
+    Raises WebrtcIceLeakError on leak/mismatch. Intended for strict register
+    post-launch and CI probes. Returns {context: candidate_lines}.
+    """
+    results: dict[str, list[str]] = {}
+    for via in contexts:
+        cands = gather_webrtc_ice_candidates(
+            page, via=via, stun_urls=stun_urls, timeout_ms=timeout_ms
+        )
+        assert_webrtc_host_equals_exit_ip(cands, exit_ip)
+        results[via] = cands
+    return results
+
diff --git a/python/tests/test_browser_launch.py b/python/tests/test_browser_launch.py
index e95cfe5..903bab0 100644
--- a/python/tests/test_browser_launch.py
+++ b/python/tests/test_browser_launch.py
@@ -35,14 +35,18 @@ class LaunchContextTests(unittest.TestCase):
                 "cloakcli_worker.fingerprint.resolve_geo_for_launch",
                 return_value=geo,
             ):
-                launch_context(
-                    user_data_dir=str(root / "ud"),
-                    headed=False,
-                    proxy="http://sess:pw@127.0.0.1:9",
-                    fingerprint_seed=42424,
-                    profile_meta_path=meta,
-                    require_geo=True,
-                )
+                with mock.patch(
+                    "cloakcli_worker.fingerprint.enforce_windows_font_gate",
+                    return_value=[],
+                ):
+                    launch_context(
+                        user_data_dir=str(root / "ud"),
+                        headed=False,
+                        proxy="http://sess:pw@127.0.0.1:9",
+                        fingerprint_seed=42424,
+                        profile_meta_path=meta,
+                        require_geo=True,
+                    )
         self.assertTrue(launch.called)
         kwargs = launch.call_args.kwargs
         args = kwargs["args"]
@@ -65,29 +69,57 @@ class LaunchContextTests(unittest.TestCase):
     def test_register_path_fail_closed(self):
         with mock.patch("cloakbrowser.launch_persistent_context") as launch:
             with mock.patch(
-                "cloakcli_worker.fingerprint.resolve_geo_for_launch",
-                side_effect=GeoResolutionError("GEO_DB_MISSING: GeoLite2-City database unavailable"),
+                "cloakcli_worker.fingerprint.enforce_windows_font_gate",
+                return_value=[],
             ):
-                with self.assertRaises(GeoResolutionError):
-                    launch_context(
-                        user_data_dir="/tmp/ud",
-                        headed=False,
-                        proxy="http://sess:pw@127.0.0.1:9",
-                        fingerprint_seed=42424,
-                        skill_name="pinterest-register-visual",
-                        skill_path="skills/pinterest-register-visual/skill.json",
-                    )
+                with mock.patch(
+                    "cloakcli_worker.fingerprint.resolve_geo_for_launch",
+                    side_effect=GeoResolutionError("GEO_DB_MISSING: GeoLite2-City database unavailable"),
+                ):
+                    with self.assertRaises(GeoResolutionError):
+                        launch_context(
+                            user_data_dir="/tmp/ud",
+                            headed=False,
+                            proxy="http://sess:pw@127.0.0.1:9",
+                            fingerprint_seed=42424,
+                            skill_name="pinterest-register-visual",
+                            skill_path="skills/pinterest-register-visual/skill.json",
+                        )
         launch.assert_not_called()
 
+    def test_headed_emits_window_size(self):
+        persona = mint_fingerprint_persona(42424)
+        with mock.patch("cloakbrowser.launch_persistent_context") as launch:
+            with mock.patch(
+                "cloakcli_worker.fingerprint.enforce_windows_font_gate",
+                return_value=[],
+            ):
+                launch_context(
+                    user_data_dir="/tmp/ud",
+                    headed=True,
+                    fingerprint_seed=42424,
+                    require_geo=False,
+                )
+        kwargs = launch.call_args.kwargs
+        self.assertIn(
+            f"--window-size={persona['screen_width']},{persona['screen_height']}",
+            kwargs["args"],
+        )
+        self.assertNotIn("viewport", kwargs)
+
     def test_no_playwright_user_agent_when_persona_present(self):
         with mock.patch("cloakbrowser.launch_persistent_context") as launch:
-            launch_context(
-                user_data_dir="/tmp/ud",
-                headed=True,
-                fingerprint_seed=42424,
-                user_agent="Mozilla/5.0 FakeUA",
-                require_geo=False,
-            )
+            with mock.patch(
+                "cloakcli_worker.fingerprint.enforce_windows_font_gate",
+                return_value=[],
+            ):
+                launch_context(
+                    user_data_dir="/tmp/ud",
+                    headed=True,
+                    fingerprint_seed=42424,
+                    user_agent="Mozilla/5.0 FakeUA",
+                    require_geo=False,
+                )
         kwargs = launch.call_args.kwargs
         self.assertNotIn("user_agent", kwargs)
         self.assertIn("--fingerprint-brand=Chrome", kwargs["args"])
diff --git a/python/tests/test_fingerprint.py b/python/tests/test_fingerprint.py
index b0b8225..aa54476 100644
--- a/python/tests/test_fingerprint.py
+++ b/python/tests/test_fingerprint.py
@@ -12,23 +12,33 @@ from cloakcli_worker.fingerprint import (
     PERSONA_SCHEMA,
     SEED_MAX,
     SEED_MIN,
+    WINDOWS_MINIMUM_FONTS,
+    FontAvailabilityError,
     GeoResolutionError,
     PersonaWhitelistError,
+    WebrtcIceLeakError,
     apply_to_launch_kwargs,
+    assert_webrtc_host_equals_exit_ip,
     ensure_fingerprint_persona,
     ensure_fingerprint_seed,
     ensure_language_preference,
+    enforce_windows_font_gate,
     fingerprint_chrome_args,
     geo_cache_valid,
     gpu_hw_memory_pool,
+    headed_window_size_arg,
+    host_candidate_ips,
     is_register_launch,
     is_valid_fingerprint_seed,
     is_whitelisted_persona,
     mint_fingerprint_persona,
     mint_fingerprint_seed,
+    missing_windows_minimum_fonts,
+    parse_ice_candidate_ip,
     persona_brand_tuple,
     persona_derived_from_seed,
     persona_gpu_tuple,
+    persona_window_geometry,
     proxy_session_identity,
     resolve_fingerprint_seed,
     resolve_geo_for_launch,
@@ -37,6 +47,9 @@ from cloakcli_worker.fingerprint import (
     verified_gpu_tuples,
 )
 
+# Synthetic fc-list blob with the full Windows minimum set (unit tests only).
+_FULL_FONT_LISTING = "\n".join(f"/usr/share/fonts/x/{f}.ttf: {f}:style=Regular" for f in WINDOWS_MINIMUM_FONTS).lower()
+
 
 class FingerprintSeedTests(unittest.TestCase):
     def test_mint_in_range(self):
@@ -590,6 +603,7 @@ class LaunchKwargsTests(unittest.TestCase):
             headed=False,
             profile_meta_path=meta,
             require_geo=True,
+            font_listing=_FULL_FONT_LISTING,
             resolve_exit_ip=lambda p: "8.8.8.8",
             lookup_city=lambda ip: {
                 "timezone": "America/New_York",
@@ -633,6 +647,7 @@ class LaunchKwargsTests(unittest.TestCase):
                 proxy="http://sess:pw@127.0.0.1:9",
                 headed=False,
                 require_geo=True,
+                font_listing=_FULL_FONT_LISTING,
                 resolve_exit_ip=lambda p: (_ for _ in ()).throw(
                     GeoResolutionError("GEO_DB_MISSING: GeoLite2-City database unavailable")
                 ),
@@ -649,5 +664,156 @@ class LaunchKwargsTests(unittest.TestCase):
         self.assertFalse(is_register_launch("pinterest-nurture-browse", "skills/pinterest-nurture-browse/skill.json"))
 
 
+
+class FontGateTests(unittest.TestCase):
+    def test_missing_detects_absent_families(self):
+        listing = "arial: Arial\nconsolas: Consolas\n"
+        missing = missing_windows_minimum_fonts(listing=listing)
+        self.assertIsNotNone(missing)
+        self.assertIn("Segoe UI", missing)
+        self.assertIn("Calibri", missing)
+        self.assertNotIn("Consolas", missing)
+
+    def test_full_listing_empty_missing(self):
+        self.assertEqual(missing_windows_minimum_fonts(listing=_FULL_FONT_LISTING), [])
+
+    def test_unknown_listing_is_none(self):
+        with mock.patch(
+            "cloakcli_worker.fingerprint._fc_list_blob",
+            return_value=None,
+        ):
+            self.assertIsNone(missing_windows_minimum_fonts())
+
+    def test_fail_closed_raises(self):
+        with self.assertRaises(FontAvailabilityError) as ctx:
+            enforce_windows_font_gate(fail_closed=True, listing="arial only\n")
+        self.assertIn("FONT_GATE_MISSING", str(ctx.exception))
+
+    def test_warn_only_returns_missing(self):
+        missing = enforce_windows_font_gate(fail_closed=False, listing="arial only\n")
+        self.assertTrue(missing)
+        self.assertIn("Segoe UI", missing)
+
+    def test_register_launch_fail_closed_on_fonts(self):
+        kwargs: dict = {"user_data_dir": "/tmp/ud", "headless": True}
+        with self.assertRaises(FontAvailabilityError):
+            apply_to_launch_kwargs(
+                kwargs,
+                seed=42424,
+                headed=False,
+                require_geo=False,
+                require_fonts=True,
+                font_listing="nope\n",
+            )
+
+    def test_nurture_warns_and_continues(self):
+        kwargs: dict = {"user_data_dir": "/tmp/ud", "headless": True}
+        apply_to_launch_kwargs(
+            kwargs,
+            seed=42424,
+            headed=False,
+            require_geo=False,
+            require_fonts=False,
+            font_listing="nope\n",
+        )
+        self.assertIn("--fingerprint=42424", kwargs["args"])
+
+    def test_chrome_args_omit_font_metrics(self):
+        args = fingerprint_chrome_args(42424)
+        self.assertFalse(any("fingerprint-windows-font-metrics" in a for a in args))
+
+
+class WebrtcIceAssertTests(unittest.TestCase):
+    def _cand(self, ip: str, typ: str = "host") -> str:
+        return (
+            f"candidate:1 1 UDP 2122260223 {ip} 54400 typ {typ} generation 0"
+        )
+
+    def test_parse_host_and_srflx(self):
+        self.assertEqual(
+            parse_ice_candidate_ip(self._cand("203.0.113.10")),
+            ("203.0.113.10", "host"),
+        )
+        self.assertEqual(
+            parse_ice_candidate_ip("a=" + self._cand("198.51.100.1", "srflx")),
+            ("198.51.100.1", "srflx"),
+        )
+
+    def test_host_ips_ignore_srflx(self):
+        cands = [
+            self._cand("8.8.8.8"),
+            self._cand("10.0.0.5", "srflx"),
+            self._cand("8.8.8.8"),
+        ]
+        self.assertEqual(host_candidate_ips(cands), ["8.8.8.8"])
+
+    def test_assert_pass_when_host_equals_exit(self):
+        cands = [self._cand("8.8.8.8"), self._cand("8.8.4.4", "srflx")]
+        self.assertEqual(
+            assert_webrtc_host_equals_exit_ip(cands, "8.8.8.8"),
+            ["8.8.8.8"],
+        )
+
+    def test_assert_fail_on_private_host_leak(self):
+        cands = [self._cand("192.168.1.20"), self._cand("8.8.8.8")]
+        with self.assertRaises(WebrtcIceLeakError) as ctx:
+            assert_webrtc_host_equals_exit_ip(cands, "8.8.8.8")
+        self.assertIn("WEBRTC_ICE_LEAK", str(ctx.exception))
+
+    def test_assert_fail_on_wrong_public_host(self):
+        cands = [self._cand("1.1.1.1")]
+        with self.assertRaises(WebrtcIceLeakError):
+            assert_webrtc_host_equals_exit_ip(cands, "8.8.8.8")
+
+    def test_assert_fail_when_no_host_candidates(self):
+        cands = [self._cand("8.8.8.8", "srflx")]
+        with self.assertRaises(WebrtcIceLeakError) as ctx:
+            assert_webrtc_host_equals_exit_ip(cands, "8.8.8.8")
+        self.assertIn("no host candidates", str(ctx.exception))
+
+
+class HeadedWindowTests(unittest.TestCase):
+    def test_window_size_arg_matches_screen(self):
+        persona = mint_fingerprint_persona(42424)
+        self.assertEqual(
+            headed_window_size_arg(persona),
+            f"--window-size={persona['screen_width']},{persona['screen_height']}",
+        )
+
+    def test_headed_launch_emits_window_size_not_maximize(self):
+        kwargs: dict = {"user_data_dir": "/tmp/ud", "headless": False}
+        apply_to_launch_kwargs(
+            kwargs,
+            seed=42424,
+            headed=True,
+            require_geo=False,
+            require_fonts=False,
+            font_listing=_FULL_FONT_LISTING,
+        )
+        persona = mint_fingerprint_persona(42424)
+        self.assertIn(
+            f"--window-size={persona['screen_width']},{persona['screen_height']}",
+            kwargs["args"],
+        )
+        self.assertFalse(any(a == "--start-maximized" or a.startswith("--start-maximized") for a in kwargs["args"]))
+        self.assertNotIn("viewport", kwargs)
+        self.assertFalse(any("fingerprint-windows-font-metrics" in a for a in kwargs["args"]))
+
+    def test_geometry_relations(self):
+        persona = mint_fingerprint_persona(11111)
+        geo = persona_window_geometry(persona)
+        self.assertEqual(
+            geo["available_height"],
+            geo["screen_height"] - geo["taskbar_height"],
+        )
+        self.assertEqual(
+            geo["viewport_height"],
+            geo["available_height"] - geo["chrome_ui_height"],
+        )
+        self.assertEqual(geo["window_width"], geo["screen_width"])
+        self.assertEqual(geo["window_height"], geo["screen_height"])
+
+
+
 if __name__ == "__main__":
     unittest.main()
diff --git a/scripts/probe_fingerprint_ice_fonts_window.py b/scripts/probe_fingerprint_ice_fonts_window.py
new file mode 100755
index 0000000..25e2906
--- /dev/null
+++ b/scripts/probe_fingerprint_ice_fonts_window.py
@@ -0,0 +1,190 @@
+#!/usr/bin/env python3
+"""P0 probe: ICE host==exit IP, font gate status, headed window-size flags.
+
+Default mode is offline (no browser): prints font missing set, sample chrome
+args (incl. --window-size when headed), and ICE assert self-check vectors.
+
+With --launch: headless CloakBrowser gathers ICE on page/iframe/worker against
+a synthetic --fingerprint-webrtc-ip (no real proxy). Use --exit-ip to override.
+With --headed: also emit window-size and measure screen/outer/inner when a
+display is available.
+
+Never prints proxy URLs or secrets.
+"""
+
+from __future__ import annotations
+
+import argparse
+import json
+import sys
+from pathlib import Path
+
+ROOT = Path(__file__).resolve().parents[1]
+sys.path.insert(0, str(ROOT / "python"))
+
+from cloakcli_worker.fingerprint import (  # noqa: E402
+    WINDOWS_MINIMUM_FONTS,
+    WebrtcIceLeakError,
+    assert_webrtc_host_equals_exit_ip,
+    fingerprint_chrome_args,
+    mint_fingerprint_persona,
+    missing_windows_minimum_fonts,
+    persona_window_geometry,
+    verify_webrtc_ice_no_leak,
+)
+
+
+SEED = 42424
+
+
+def offline_report(exit_ip: str) -> dict:
+    persona = mint_fingerprint_persona(SEED)
+    missing = missing_windows_minimum_fonts()
+    headed_args = fingerprint_chrome_args(
+        SEED,
+        persona,
+        geo={"timezone": "America/New_York", "exit_ip": exit_ip, "locale_from_geo": "en-US"},
+        language="en-US",
+        headed=True,
+    )
+    headless_args = fingerprint_chrome_args(
+        SEED,
+        persona,
+        geo={"timezone": "America/New_York", "exit_ip": exit_ip, "locale_from_geo": "en-US"},
+        language="en-US",
+        headed=False,
+    )
+    # Synthetic ICE vectors (CI unit-style)
+    good = [f"candidate:1 1 UDP 2122260223 {exit_ip} 54400 typ host generation 0"]
+    leak = [
+        "candidate:1 1 UDP 2122260223 192.168.0.10 54400 typ host generation 0",
+        f"candidate:2 1 UDP 2122260223 {exit_ip} 54401 typ host generation 0",
+    ]
+    ice_ok = True
+    ice_err = None
+    try:
+        assert_webrtc_host_equals_exit_ip(good, exit_ip)
+        try:
+            assert_webrtc_host_equals_exit_ip(leak, exit_ip)
+            ice_ok = False
+            ice_err = "leak vector did not fail"
+        except WebrtcIceLeakError:
+            pass
+    except WebrtcIceLeakError as e:
+        ice_ok = False
+        ice_err = str(e)
+
+    return {
+        "mode": "offline",
+        "seed": SEED,
+        "binary_hint": "146.0.7680.177.5",
+        "exit_ip": exit_ip,
+        "fonts": {
+            "minimum": list(WINDOWS_MINIMUM_FONTS),
+            "missing": missing,
+            "gate_would_fail_closed": bool(missing),
+        },
+        "persona_geometry": persona_window_geometry(persona),
+        "headed_args": headed_args,
+        "headless_args": headless_args,
+        "has_window_size": any(a.startswith("--window-size=") for a in headed_args),
+        "has_font_metrics_flag": any(
+            "fingerprint-windows-font-metrics" in a for a in headed_args + headless_args
+        ),
+        "ice_vector_selfcheck_ok": ice_ok,
+        "ice_vector_error": ice_err,
+    }
+
+
+def live_ice(exit_ip: str, headed: bool) -> dict:
+    from cloakbrowser import launch_persistent_context
+    from cloakcli_worker.fingerprint import apply_to_launch_kwargs
+
+    persona = mint_fingerprint_persona(SEED)
+    ud = ROOT / "data" / "artifacts" / f"fp-ice-probe-{SEED}"
+    ud.mkdir(parents=True, exist_ok=True)
+    kwargs: dict = {
+        "user_data_dir": str(ud),
+        "headless": not headed,
+    }
+    # Full font listing inject so probe can run on boxes without MS fonts.
+    listing = "\n".join(f"/x/{f}.ttf: {f}" for f in WINDOWS_MINIMUM_FONTS).lower()
+    apply_to_launch_kwargs(
+        kwargs,
+        seed=SEED,
+        headed=headed,
+        require_geo=False,
+        require_fonts=False,
+        font_listing=listing,
+    )
+    # Force webrtc IP even without proxy geo.
+    args = list(kwargs.get("args") or [])
+    args = [a for a in args if not a.startswith("--fingerprint-webrtc-ip=")]
+    args.append(f"--fingerprint-webrtc-ip={exit_ip}")
+    kwargs["args"] = args
+
+    ctx = launch_persistent_context(**kwargs)
+    try:
+        page = ctx.pages[0] if ctx.pages else ctx.new_page()
+        page.goto("about:blank")
+        ice = {}
+        ice_error = None
+        try:
+            ice = verify_webrtc_ice_no_leak(page, exit_ip)
+        except WebrtcIceLeakError as e:
+            ice_error = str(e)
+        measured = page.evaluate(
+            """() => ({
+              screen: {width: screen.width, height: screen.height,
+                       availWidth: screen.availWidth, availHeight: screen.availHeight},
+              outer: {width: window.outerWidth, height: window.outerHeight},
+              inner: {width: window.innerWidth, height: window.innerHeight},
+              dpr: window.devicePixelRatio
+            })"""
+        )
+        return {
+            "mode": "launch",
+            "headed": headed,
+            "seed": SEED,
+            "exit_ip": exit_ip,
+            "persona": {
+                "screen": f"{persona['screen_width']}x{persona['screen_height']}",
+                "viewport": f"{persona['viewport_width']}x{persona['viewport_height']}",
+            },
+            "args": kwargs.get("args"),
+            "measured": measured,
+            "ice_contexts_ok": ice_error is None,
+            "ice_error": ice_error,
+            "ice_candidate_counts": {k: len(v) for k, v in ice.items()},
+        }
+    finally:
+        try:
+            ctx.close()
+        except Exception:
+            pass
+
+
+def main() -> int:
+    ap = argparse.ArgumentParser(description=__doc__)
+    ap.add_argument("--launch", action="store_true")
+    ap.add_argument("--headed", action="store_true")
+    ap.add_argument("--exit-ip", default="8.8.8.8")
+    args = ap.parse_args()
+    if args.launch:
+        report = live_ice(args.exit_ip, headed=args.headed)
+    else:
+        report = offline_report(args.exit_ip)
+    print(json.dumps(report, indent=2, ensure_ascii=False))
+    if not report.get("ice_vector_selfcheck_ok", True):
+        return 2
+    if report.get("has_font_metrics_flag"):
+        return 3
+    if args.launch and not report.get("ice_contexts_ok", True):
+        # Live ICE against synthetic IP often fails without real webrtc spoof path
+        # matching network; still exit non-zero so CI can gate when configured.
+        return 4
+    return 0
+
+
+if __name__ == "__main__":
+    raise SystemExit(main())

exec
/bin/bash -lc "sed -n '700,920p' python/cloakcli_worker/fingerprint.py; sed -n '920,1120p' python/cloakcli_worker/fingerprint.py; sed -n '1120,1350p' python/cloakcli_worker/fingerprint.py" in /workspace/CloakCLI
 succeeded in 0ms:
  } finally {
    URL.revokeObjectURL(url);
  }
}
"""


def headed_window_size_arg(persona: dict[str, Any]) -> str:
    """Return --window-size=W,H for persona screen (outer target)."""
    return (
        f"--window-size={int(persona['screen_width'])},"
        f"{int(persona['screen_height'])}"
    )


def persona_window_geometry(persona: dict[str, Any]) -> dict[str, int]:
    """Explicit outer/inner/available relationships for headed probes."""
    sw = int(persona["screen_width"])
    sh = int(persona["screen_height"])
    taskbar = int(persona["taskbar_height"])
    chrome_ui = int(persona["chrome_ui_height"])
    return {
        "screen_width": sw,
        "screen_height": sh,
        "window_width": sw,
        "window_height": sh,
        "available_width": int(persona["available_width"]),
        "available_height": int(persona["available_height"]),
        "viewport_width": int(persona["viewport_width"]),
        "viewport_height": int(persona["viewport_height"]),
        "taskbar_height": taskbar,
        "chrome_ui_height": chrome_ui,
        # expected: available_height == screen_height - taskbar
        # expected: viewport_height == available_height - chrome_ui
    }


def fingerprint_chrome_args(
    seed: int,
    persona: dict[str, Any] | None = None,
    *,
    geo: dict[str, Any] | None = None,
    language: str | None = None,
    headed: bool = False,
) -> list[str]:
    """Chrome args that override cloakbrowser's random --fingerprint default.

    Always includes the verified persona flags so HTTP UA / CH / JS stay on
    the same Chrome identity. Playwright user_agent is never emitted.

    Headed launches add --window-size matching persona screen (not maximize-only).
    Never emits --fingerprint-windows-font-metrics on Chromium < 148 (146 no-op).
    """
    if not is_valid_fingerprint_seed(seed):
        raise ValueError(f"fingerprint_seed out of range [{SEED_MIN}, {SEED_MAX}]: {seed!r}")
    if persona is None:
        persona = mint_fingerprint_persona(int(seed))
    else:
        persona = validate_persona(persona)

    args = [
        f"--fingerprint={int(seed)}",
        f"--fingerprint-platform={persona['platform']}",
        f"--fingerprint-platform-version={persona['platform_version']}",
        f"--fingerprint-brand={persona['brand']}",
        f"--fingerprint-brand-version={persona['brand_version']}",
        f"--fingerprint-hardware-concurrency={persona['hardware_concurrency']}",
        f"--fingerprint-device-memory={persona['device_memory']}",
        f"--fingerprint-gpu-vendor={persona['gpu_vendor']}",
        f"--fingerprint-gpu-renderer={persona['gpu_renderer']}",
        f"--fingerprint-screen-width={persona['screen_width']}",
        f"--fingerprint-screen-height={persona['screen_height']}",
        f"--fingerprint-taskbar-height={persona['taskbar_height']}",
    ]
    if headed:
        # Measured geometry from persona screen. Suppresses cloakbrowser's
        # --start-maximized injection (build_args skips maximize when
        # --window-size is already present).
        args.append(
            f"--window-size={int(persona['screen_width'])},{int(persona['screen_height'])}"
        )
    lang = language or (geo or {}).get("locale") or (geo or {}).get("locale_from_geo")
    if lang and is_valid_locale(lang):
        lang = normalize_locale(lang)
        args.append(f"--lang={lang}")
        args.append(f"--fingerprint-locale={lang}")
    tz = (geo or {}).get("timezone")
    if tz and is_valid_iana_timezone(tz):
        args.append(f"--fingerprint-timezone={tz}")
    exit_ip = (geo or {}).get("exit_ip")
    if exit_ip:
        args.append(f"--fingerprint-webrtc-ip={exit_ip}")
    # Explicitly never emit font-metrics on 146 (no-op / unproven).
    major = deployed_chromium_major()
    if major.isdigit() and int(major) >= WINDOWS_FONT_METRICS_MIN_MAJOR:
        # Reserved: only enable when ops + binary gate say so. Still omitted
        # by default so CloakCLI does not claim Windows native metrics.
        pass
    return args


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _read_profile_meta(meta_path: Path) -> dict[str, Any]:
    if not meta_path.is_file():
        return {}
    try:
        loaded = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def ensure_fingerprint_seed(meta_path: Path, *, regenerate: bool = False) -> int:
    """Load profile.json; mint/persist fingerprint_seed if missing/invalid or regenerate.

    Preserves all other fields (proxy, notes, etc.). Returns the seed to use.
    A reminted seed also remints fingerprint_persona from that seed.
    """
    meta_path = Path(meta_path)
    data = _read_profile_meta(meta_path)

    existing = coerce_fingerprint_seed(data.get(_FIELD))
    if existing is not None and not regenerate:
        return existing

    seed = mint_fingerprint_seed()
    data[_FIELD] = seed
    data[_PERSONA_FIELD] = mint_fingerprint_persona(seed)
    _atomic_write_json(meta_path, data)
    return seed


def ensure_fingerprint_persona(
    meta_path: Path,
    seed: int,
    *,
    regenerate: bool = False,
) -> dict[str, Any]:
    """Load or mint a whitelist persona bound to this seed; persist on profile.json.

    A whitelist-valid persona minted for a *different* fingerprint_seed is
    reminted (same path as a schema bump). Same profile + same seed is stable.
    """
    meta_path = Path(meta_path)
    data = _read_profile_meta(meta_path)
    existing = data.get(_PERSONA_FIELD)
    if (
        not regenerate
        and is_whitelisted_persona(existing)
        and persona_derived_from_seed(existing, seed)
    ):
        assert isinstance(existing, dict)
        return existing
    if existing is not None and not is_whitelisted_persona(existing):
        sys.stderr.write(
            "[fingerprint] rejected non-whitelist persona; reminting from seed\n"
        )
        sys.stderr.flush()
    elif existing is not None:
        sys.stderr.write(
            "[fingerprint] persona not bound to current fingerprint_seed; reminting\n"
        )
        sys.stderr.flush()
    persona = mint_fingerprint_persona(seed)
    data[_FIELD] = int(seed)
    data[_PERSONA_FIELD] = persona
    _atomic_write_json(meta_path, data)
    return persona


def ensure_language_preference(
    meta_path: Path,
    *,
    geo_locale: str | None = None,
) -> str | None:
    """Persist language as a profile preference. Never overwrite from country."""
    meta_path = Path(meta_path)
    data = _read_profile_meta(meta_path)
    existing = data.get(_LANGUAGE_FIELD)
    if is_valid_locale(existing):
        return normalize_locale(str(existing))
    if geo_locale and is_valid_locale(geo_locale):
        lang = normalize_locale(geo_locale)
        data[_LANGUAGE_FIELD] = lang
        _atomic_write_json(meta_path, data)
        return lang
    return None


def find_meta_path_for_user_data_dir(
    profiles_root: Path,
    user_data_dir: str | Path,
) -> Path | None:
    """Best-effort match profiles/*/profile.json by user_data_dir field or path heuristics.

    Matching is by resolved path when possible, else by string equality / basename
    suffix (e.g. data/profiles/<name>-pinterest-run ↔ profiles/<name>/profile.json).
    Does not create files.
    """
    profiles_root = Path(profiles_root)
    profiles_root = Path(profiles_root)
    if not profiles_root.is_dir():
        return None

    target = Path(user_data_dir)
    try:
        target_resolved = target.resolve()
    except OSError:
        target_resolved = target

    target_str = str(user_data_dir).replace("\\", "/").rstrip("/")
    target_name = target.name

    candidates: list[Path] = []
    try:
        entries = sorted(profiles_root.iterdir(), key=lambda p: p.name)
    except OSError:
        return None

    for entry in entries:
        if not entry.is_dir():
            continue
        meta = entry / "profile.json"
        if not meta.is_file():
            continue
        try:
            raw = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        ud = raw.get("user_data_dir")
        if isinstance(ud, str) and ud.strip():
            ud_path = Path(ud)
            if not ud_path.is_absolute():
                # Relative to CloakCLI root (parent of profiles/)
                ud_path = profiles_root.parent / ud
            try:
                if ud_path.resolve() == target_resolved:
                    return meta
            except OSError:
                pass
            if ud.replace("\\", "/").rstrip("/") == target_str:
                return meta
            if Path(ud).name == target_name:
                candidates.append(meta)
        # Heuristic: data/profiles/<name>-pinterest-run or <name>
        name = entry.name
        if target_name in (name, f"{name}-pinterest-run", f"{name}-run"):
            candidates.append(meta)

    if len(candidates) == 1:
        return candidates[0]
    return None


def resolve_fingerprint_seed(
    *,
    fingerprint_seed: int | None = None,
    profile_meta_path: str | Path | None = None,
    profiles_root: str | Path | None = None,
    user_data_dir: str | Path | None = None,
    regenerate: bool = False,
) -> int | None:
    """Resolve a seed from explicit value, meta path, or user_data_dir lookup.

    Returns None when no profile.json can be found (caller may launch without
    override; cloakbrowser will still pick a random seed for that launch).
    When fingerprint_seed is passed explicitly it wins (must be in range);
    regenerate only applies when loading/ensuring via profile.json.
    """
    if fingerprint_seed is not None:
        coerced = coerce_fingerprint_seed(fingerprint_seed)
        if coerced is None:
            raise ValueError(
                f"fingerprint_seed out of range [{SEED_MIN}, {SEED_MAX}]: {fingerprint_seed!r}"
            )
        return coerced

    if profile_meta_path is not None:
        return ensure_fingerprint_seed(Path(profile_meta_path), regenerate=regenerate)

    if profiles_root is not None and user_data_dir is not None:
        meta = find_meta_path_for_user_data_dir(Path(profiles_root), user_data_dir)
        if meta is not None:
            return ensure_fingerprint_seed(meta, regenerate=regenerate)

    return None


def resolve_profile_meta_path(
    *,
    profile_meta_path: str | Path | None = None,
    profiles_root: str | Path | None = None,
    user_data_dir: str | Path | None = None,
) -> Path | None:
    if profile_meta_path is not None:
        return Path(profile_meta_path)
    if profiles_root is not None and user_data_dir is not None:
        return find_meta_path_for_user_data_dir(Path(profiles_root), user_data_dir)
    return None


def log_fingerprint_seed(seed: int) -> None:
    """Safe stderr line — seed only, never proxy/email/cookies."""
    sys.stderr.write(f"[fingerprint] seed={int(seed)}\n")
    sys.stderr.flush()


def log_fingerprint_persona(persona: dict[str, Any]) -> None:
    sys.stderr.write(
        "[fingerprint] "
        f"brand={persona.get('brand')}/{persona.get('brand_version')} "
        f"platform={persona.get('platform')}/{persona.get('platform_version')} "
        f"hw={persona.get('hardware_concurrency')}/{persona.get('device_memory')} "
        f"gpu={persona.get('gpu_vendor')} "
        f"screen={persona.get('screen_width')}x{persona.get('screen_height')}\n"
    )
    sys.stderr.flush()


def is_register_launch(skill_name: str | None = None, skill_path: str | None = None) -> bool:
    blob = f"{skill_name or ''} {skill_path or ''}".replace("\\", "/").lower()
    return "register" in blob


def env_require_geo() -> bool | None:
    raw = os.environ.get("CLOAKCLI_REQUIRE_GEO", "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return None


def proxy_session_identity(proxy: str) -> str:
    """Hash of scheme+username+host+port. Password is excluded. Never log `proxy`."""
    parsed = urlparse(proxy)
    material = "|".join(
        [
            parsed.scheme or "",
            parsed.username or "",
            (parsed.hostname or "").lower(),
            str(parsed.port or ""),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _is_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def geo_db_path() -> Path | None:
    try:
        from cloakbrowser.geoip import _ensure_geoip_db

        path = _ensure_geoip_db()
    except ImportError as e:
        raise GeoResolutionError(
            "GEO_DB_MISSING: geoip2 is required (pip install 'cloakbrowser[geoip]')"
        ) from e
    except GeoResolutionError:
        raise
    except Exception as e:
        raise GeoResolutionError(f"GEO_DB_MISSING: {type(e).__name__}") from e
    return Path(path) if path is not None else None


def read_geo_db_version(db_path: Path | None = None) -> str:
    path = db_path if db_path is not None else geo_db_path()
    if path is None:
        raise GeoResolutionError("GEO_DB_MISSING: GeoLite2-City database unavailable")
    try:
        import geoip2.database

        with geoip2.database.Reader(str(path)) as reader:
            epoch = int(reader.metadata().build_epoch)
            return f"geolite2-city:{epoch}"
    except GeoResolutionError:
        raise
    except Exception:
        try:
            return f"mtime:{int(path.stat().st_mtime)}"
        except OSError as e:
            raise GeoResolutionError("GEO_DB_MISSING: GeoLite2-City database unreadable") from e


def default_resolve_exit_ip(proxy: str) -> str:
    """Echo-verified egress IP. Refuses proxy-hostname fallback (not a verified exit)."""
    """Echo-verified egress IP. Refuses proxy-hostname fallback (not a verified exit)."""
    try:
        from cloakbrowser.geoip import resolve_proxy_exit_ip
    except ImportError as e:
        raise GeoResolutionError(
            "GEO_DB_MISSING: geoip2/httpx required for exit-IP echo"
        ) from e
    try:
        ip = resolve_proxy_exit_ip(proxy)
    except Exception as e:
        raise GeoResolutionError(f"GEO_TIMEOUT: exit-IP echo failed ({type(e).__name__})") from e
    if not ip:
        raise GeoResolutionError("GEO_TIMEOUT: could not discover proxy exit IP via echo")
    if not _is_public_ip(ip):
        raise GeoResolutionError(
            "GEO_EXIT_IP: echo returned a non-public address; refusing proxy-host fallback"
        )
    return ip


def default_lookup_city(ip: str) -> dict[str, Any]:
    """City-DB lookup for an already-verified public exit IP. IANA timezone required."""
    path = geo_db_path()
    if path is None:
        raise GeoResolutionError("GEO_DB_MISSING: GeoLite2-City database unavailable")
    try:
        import geoip2.database
        from cloakbrowser.geoip import COUNTRY_LOCALE_MAP
    except ImportError as e:
        raise GeoResolutionError(
            "GEO_DB_MISSING: geoip2 is required (pip install 'cloakbrowser[geoip]')"
        ) from e
    try:
        with geoip2.database.Reader(str(path)) as reader:
            resp = reader.city(ip)
    except GeoResolutionError:
        raise
    except Exception as e:
        raise GeoResolutionError(f"GEO_LOOKUP: City DB failed for exit IP ({type(e).__name__})") from e
    tz = getattr(getattr(resp, "location", None), "time_zone", None)
    country = getattr(getattr(resp, "country", None), "iso_code", None)
    if not is_valid_iana_timezone(tz):
        raise GeoResolutionError("GEO_TIMEZONE: City DB returned missing/unknown IANA timezone")
    locale = None
    if isinstance(country, str) and country:
        mapped = COUNTRY_LOCALE_MAP.get(country)
        if mapped and is_valid_locale(mapped):
            locale = normalize_locale(mapped)
    return {
        "timezone": str(tz).strip(),
        "country": country if isinstance(country, str) else None,
        "locale_from_geo": locale,
    }


def geo_cache_valid(
    cache: Any,
    *,
    proxy_identity: str,
    exit_ip: str,
    geo_db_version: str,
    now: datetime | None = None,
) -> bool:
    if not isinstance(cache, dict):
        return False
    if cache.get("schema") not in (GEO_CACHE_SCHEMA, None):
        return False
    if cache.get("proxy_identity") != proxy_identity:
        return False
    if cache.get("exit_ip") != exit_ip:
        return False
    if cache.get("geo_db_version") != geo_db_version:
        return False
    if not is_valid_iana_timezone(cache.get("timezone")):
        return False
    if not _is_public_ip(str(cache.get("exit_ip") or "")):
        return False
    raw_ts = cache.get("looked_up_at")
    if not isinstance(raw_ts, str) or not raw_ts.strip():
        return False
    try:
        looked = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
    except ValueError:
        return False
    if looked.tzinfo is None:
        looked = looked.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    if now - looked > GEO_CACHE_TTL:
        return False
    return True


def resolve_geo_for_launch(
    *,
    proxy: str,
    meta_path: Path | None = None,
    require_geo: bool = False,
    now: datetime | None = None,
    resolve_exit_ip: Callable[[str], str] | None = None,
    lookup_city: Callable[[str], dict[str, Any]] | None = None,
    db_version: str | None = None,
) -> dict[str, Any]:
    """Verify exit IP, reuse geo_cache when valid, otherwise City-DB re-resolve.

    Fail-closed when require_geo is True. Never falls back to the host timezone.
    """
    resolve_ip = resolve_exit_ip or default_resolve_exit_ip
    lookup = lookup_city or default_lookup_city
    now = now or datetime.now(timezone.utc)
    identity = proxy_session_identity(proxy)

    def _fail(err: GeoResolutionError) -> dict[str, Any]:
        if require_geo:
            raise err
        sys.stderr.write(f"[geo] skip: {err}\n")
        sys.stderr.flush()
        return {}

    try:
        exit_ip = resolve_ip(proxy)
        version = db_version if db_version is not None else read_geo_db_version()
    except GeoResolutionError as e:
        return _fail(e)

    data: dict[str, Any] = _read_profile_meta(meta_path) if meta_path is not None else {}
    cached = data.get(_GEO_FIELD)
    if geo_cache_valid(
        cached,
        proxy_identity=identity,
        exit_ip=exit_ip,
        geo_db_version=version,
        now=now,
    ):
        assert isinstance(cached, dict)
        sys.stderr.write("[geo] cache=hit\n")
        sys.stderr.flush()
        return dict(cached)

    try:
        city = lookup(exit_ip)
    except GeoResolutionError as e:
        return _fail(e)

    tz = city.get("timezone")
    if not is_valid_iana_timezone(tz):
        return _fail(GeoResolutionError("GEO_TIMEZONE: City DB returned missing/unknown IANA timezone"))

    record = {
        "schema": GEO_CACHE_SCHEMA,
        "proxy_identity": identity,
        "exit_ip": exit_ip,
        "timezone": str(tz).strip(),
        "locale_from_geo": city.get("locale_from_geo"),
        "country": city.get("country"),
        "looked_up_at": now.astimezone(timezone.utc).isoformat(),
        "geo_db_version": version,
    }
    if meta_path is not None:
        data = _read_profile_meta(meta_path)
        data[_GEO_FIELD] = record
        try:
            _atomic_write_json(meta_path, data)
        except OSError as e:
            if require_geo:
                raise GeoResolutionError(f"GEO_PERSIST: {type(e).__name__}") from e
    sys.stderr.write(
        f"[geo] cache=miss tz={record['timezone']} lang_geo={record.get('locale_from_geo') or '-'}\n"
    )
    sys.stderr.flush()
    return record


def apply_to_launch_kwargs(
    kwargs: dict[str, Any],
    *,
    seed: int | None,
    proxy: str | None = None,
    headed: bool = False,
    profile_meta_path: str | Path | None = None,
    require_geo: bool | None = None,
    require_fonts: bool | None = None,
    font_listing: str | None = None,
    resolve_exit_ip: Callable[[str], str] | None = None,
    lookup_city: Callable[[str], dict[str, Any]] | None = None,
    db_version: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fill launch_persistent_context kwargs with persona flags + geoip alignment.

    Sets binary flags only. Passes geoip=True when a proxy is present and geo
    resolved (cloakbrowser still injects WebRTC if we did not already set it).

    Font gate runs before launch args are finalized: register/strict fail-closed
    on missing Windows minimum fonts; nurture warns. Headed emits --window-size
    from persona screen. ICE leak assert is post-launch (see
    assert_webrtc_host_equals_exit_ip) — call after gather on register paths.
    """
    meta = Path(profile_meta_path) if profile_meta_path is not None else None
    persona: dict[str, Any] | None = None
    if seed is not None:
        if meta is not None:
            persona = ensure_fingerprint_persona(meta, seed)
        else:
            persona = mint_fingerprint_persona(seed)
        log_fingerprint_persona(persona)

    env_geo = env_require_geo()
    if require_geo is None:
        require_geo = bool(env_geo) if env_geo is not None else False

    env_fonts = env_require_fonts()
    if require_fonts is None:
        if env_fonts is not None:
            require_fonts = bool(env_fonts)
        else:
            # Same default as geo: strict when caller marked require_geo
            # (browser.py maps register skills → require_geo=True).
            require_fonts = bool(require_geo)
    enforce_windows_font_gate(fail_closed=bool(require_fonts), listing=font_listing)

    geo: dict[str, Any] = {}
    language: str | None = None
    if meta is not None:
        language = ensure_language_preference(meta)

    proxy_url = proxy.strip() if isinstance(proxy, str) and proxy.strip() else None
    if proxy_url:
        geo = resolve_geo_for_launch(
            proxy=proxy_url,
            meta_path=meta,
            require_geo=bool(require_geo),

exec
/bin/bash -lc "sed -n '1350,1445p' python/cloakcli_worker/fingerprint.py; rg -n \"def verify_webrtc|def gather_webrtc|deployed_chromium|build_args|start-maximized|apply_to_launch\" python/cloakcli_worker python/tests" in /workspace/CloakCLI
 succeeded in 0ms:
            require_geo=bool(require_geo),
            now=now,
            resolve_exit_ip=resolve_exit_ip,
            lookup_city=lookup_city,
            db_version=db_version,
        )
        if meta is not None and geo:
            language = ensure_language_preference(
                meta, geo_locale=geo.get("locale_from_geo")
            )
        if geo.get("timezone"):
            # Explicit equivalent of geoip=True: timezone/locale flags + WebRTC IP.
            # Also set geoip=True so cloakbrowser's launch path stays on the
            # documented proxy+geoip alignment (it will not clobber explicit tz/locale).
            kwargs["geoip"] = True
            kwargs["timezone"] = geo["timezone"]
            if language:
                kwargs["locale"] = language
            elif geo.get("locale_from_geo"):
                kwargs["locale"] = geo["locale_from_geo"]

    if seed is not None:
        kwargs["args"] = fingerprint_chrome_args(
            seed, persona, geo=geo or None, language=language, headed=headed
        )
    elif geo or language:
        extra: list[str] = []
        if language and is_valid_locale(language):
            lang = normalize_locale(language)
            extra.extend([f"--lang={lang}", f"--fingerprint-locale={lang}"])
        tz = geo.get("timezone")
        if tz and is_valid_iana_timezone(tz):
            extra.append(f"--fingerprint-timezone={tz}")
        exit_ip = geo.get("exit_ip")
        if exit_ip:
            extra.append(f"--fingerprint-webrtc-ip={exit_ip}")
        if headed and persona is not None:
            extra.append(headed_window_size_arg(persona))
        if extra:
            kwargs["args"] = extra
    elif headed and persona is not None:
        kwargs["args"] = [headed_window_size_arg(persona)]

    if persona is not None and not headed:
        kwargs["viewport"] = {
            "width": int(persona["viewport_width"]),
            "height": int(persona["viewport_height"]),
        }

    return kwargs

def gather_webrtc_ice_candidates(
    page: Any,
    *,
    via: str = "page",
    stun_urls: list[str] | None = None,
    timeout_ms: int = 8000,
) -> list[str]:
    """Gather ICE candidate lines from a Playwright page (page / iframe / worker).

    `via`:
      - "page": RTCPeerConnection in the main frame
      - "iframe": same gather inside a sandboxed about:blank iframe
      - "worker": DedicatedWorker gather
    """
    payload = {"stunUrls": stun_urls or ["stun:stun.l.google.com:19302"], "timeoutMs": timeout_ms}
    if via == "page":
        raw = page.evaluate(WEBRTC_ICE_GATHER_JS, payload)
    elif via == "worker":
        raw = page.evaluate(WEBRTC_ICE_GATHER_WORKER_JS, payload)
    elif via == "iframe":
        frame_handle = page.evaluate_handle(
            """async () => {
              const iframe = document.createElement('iframe');
              iframe.src = 'about:blank';
              document.body.appendChild(iframe);
              await new Promise((r) => { iframe.onload = r; });
              return iframe;
            }"""
        )
        try:
            content_frame = frame_handle.as_element().content_frame()
            if content_frame is None:
                raise WebrtcIceLeakError("WEBRTC_ICE: iframe content_frame unavailable")
            raw = content_frame.evaluate(WEBRTC_ICE_GATHER_JS, payload)
        finally:
            try:
                page.evaluate(
                    """() => {
                      for (const iframe of document.querySelectorAll('iframe')) {
                        iframe.remove();
                      }
                    }"""
                )
            except Exception:
                pass
python/tests/test_fingerprint.py:20:    apply_to_launch_kwargs,
python/tests/test_fingerprint.py:172:        """cloakbrowser build_args dedupes by flag key; our args win over random default."""
python/tests/test_fingerprint.py:599:        apply_to_launch_kwargs(
python/tests/test_fingerprint.py:644:            apply_to_launch_kwargs(
python/tests/test_fingerprint.py:658:        apply_to_launch_kwargs(kwargs, seed=42424, headed=True, require_geo=False)
python/tests/test_fingerprint.py:700:            apply_to_launch_kwargs(
python/tests/test_fingerprint.py:711:        apply_to_launch_kwargs(
python/tests/test_fingerprint.py:785:        apply_to_launch_kwargs(
python/tests/test_fingerprint.py:798:        self.assertFalse(any(a == "--start-maximized" or a.startswith("--start-maximized") for a in kwargs["args"]))
python/cloakcli_worker/browser.py:41:    so cloakbrowser build_args overrides the per-launch random default. Persona
python/cloakcli_worker/browser.py:56:        apply_to_launch_kwargs,
python/cloakcli_worker/browser.py:95:        apply_to_launch_kwargs(
python/cloakcli_worker/fingerprint.py:7:args=["--fingerprint=<seed>"] so cloakbrowser build_args dedupes by flag key
python/cloakcli_worker/fingerprint.py:30:do not rely on --start-maximized alone.
python/cloakcli_worker/fingerprint.py:221:def deployed_chromium_version() -> str:
python/cloakcli_worker/fingerprint.py:240:def deployed_chromium_major() -> str:
python/cloakcli_worker/fingerprint.py:241:    return deployed_chromium_version().split(".", 1)[0]
python/cloakcli_worker/fingerprint.py:252:    compatible = deployed_chromium_version()
python/cloakcli_worker/fingerprint.py:776:        # --start-maximized injection (build_args skips maximize when
python/cloakcli_worker/fingerprint.py:793:    major = deployed_chromium_major()
python/cloakcli_worker/fingerprint.py:1292:def apply_to_launch_kwargs(
python/cloakcli_worker/fingerprint.py:1401:def gather_webrtc_ice_candidates(
python/cloakcli_worker/fingerprint.py:1453:def verify_webrtc_ice_no_leak(

exec
/bin/bash -lc "sed -n '1400,1505p' python/cloakcli_worker/fingerprint.py; sed -n '1,140p' python/cloakcli_worker/browser.py; sed -n '1,220p' python/cloakbrowser/browser.py 2>/dev/null || true" in /workspace/CloakCLI
 succeeded in 0ms:

def gather_webrtc_ice_candidates(
    page: Any,
    *,
    via: str = "page",
    stun_urls: list[str] | None = None,
    timeout_ms: int = 8000,
) -> list[str]:
    """Gather ICE candidate lines from a Playwright page (page / iframe / worker).

    `via`:
      - "page": RTCPeerConnection in the main frame
      - "iframe": same gather inside a sandboxed about:blank iframe
      - "worker": DedicatedWorker gather
    """
    payload = {"stunUrls": stun_urls or ["stun:stun.l.google.com:19302"], "timeoutMs": timeout_ms}
    if via == "page":
        raw = page.evaluate(WEBRTC_ICE_GATHER_JS, payload)
    elif via == "worker":
        raw = page.evaluate(WEBRTC_ICE_GATHER_WORKER_JS, payload)
    elif via == "iframe":
        frame_handle = page.evaluate_handle(
            """async () => {
              const iframe = document.createElement('iframe');
              iframe.src = 'about:blank';
              document.body.appendChild(iframe);
              await new Promise((r) => { iframe.onload = r; });
              return iframe;
            }"""
        )
        try:
            content_frame = frame_handle.as_element().content_frame()
            if content_frame is None:
                raise WebrtcIceLeakError("WEBRTC_ICE: iframe content_frame unavailable")
            raw = content_frame.evaluate(WEBRTC_ICE_GATHER_JS, payload)
        finally:
            try:
                page.evaluate(
                    """() => {
                      for (const iframe of document.querySelectorAll('iframe')) {
                        iframe.remove();
                      }
                    }"""
                )
            except Exception:
                pass
    else:
        raise ValueError(f"unknown ICE gather via={via!r}")
    if not isinstance(raw, list):
        return []
    return [str(c) for c in raw if isinstance(c, str) and c.strip()]


def verify_webrtc_ice_no_leak(
    page: Any,
    exit_ip: str,
    *,
    contexts: tuple[str, ...] = ("page", "iframe", "worker"),
    stun_urls: list[str] | None = None,
    timeout_ms: int = 8000,
) -> dict[str, list[str]]:
    """Gather ICE in each context and assert host candidates == exit_ip.

    Raises WebrtcIceLeakError on leak/mismatch. Intended for strict register
    post-launch and CI probes. Returns {context: candidate_lines}.
    """
    results: dict[str, list[str]] = {}
    for via in contexts:
        cands = gather_webrtc_ice_candidates(
            page, via=via, stun_urls=stun_urls, timeout_ms=timeout_ms
        )
        assert_webrtc_host_equals_exit_ip(cands, exit_ip)
        results[via] = cands
    return results

"""CloakBrowser launch helpers + cookie injection (no value logging)."""

from __future__ import annotations

import ipaddress
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class InvalidCookieError(ValueError):
    """Stable INVALID_COOKIE errors for worker → CLI."""

    def __init__(self, message: str) -> None:
        if not message.startswith("INVALID_COOKIE"):
            message = f"INVALID_COOKIE: {message}"
        super().__init__(message)


def launch_context(
    *,
    user_data_dir: str,
    headed: bool = False,
    proxy: str | None = None,
    user_agent: str | None = None,
    extension_paths: list[str] | None = None,
    fingerprint_seed: int | None = None,
    profile_meta_path: str | Path | None = None,
    profiles_root: str | Path | None = None,
    require_geo: bool | None = None,
    skill_name: str | None = None,
    skill_path: str | None = None,
) -> Any:
    """Launch persistent stealth Chromium via cloakbrowser.

    When a fingerprint_seed is known (explicit, profile_meta_path, or best-effort
    match of user_data_dir under profiles_root), pass binary fingerprint flags
    so cloakbrowser build_args overrides the per-launch random default. Persona
    (Chrome brand + platform_version + hw/screen/GPU) is minted from the seed.
    With a proxy, geoip is resolved against the echo-verified exit IP and
    timezone/locale/WebRTC flags are set; register paths fail closed on geo
    failure (no host timezone fallback). Register/strict also fail-closed when
    Windows minimum fonts are missing (nurture warns only; no silent MS font
    install). Headed launches emit --window-size from persona screen (not
    maximize-only). Post-launch ICE: call
    cloakcli_worker.fingerprint.verify_webrtc_ice_no_leak(page, exit_ip) so
    host candidates must equal the echo exit IP. Playwright user_agent is not
    used for persona (it desyncs HTTP UA / Client Hints / JS userAgentData).
    """
    from cloakbrowser import launch_persistent_context

    from .fingerprint import (
        apply_to_launch_kwargs,
        env_require_geo,
        is_register_launch,
        log_fingerprint_seed,
        resolve_fingerprint_seed,
        resolve_profile_meta_path,
    )

    kwargs: dict[str, Any] = {
        "user_data_dir": user_data_dir,
        "headless": not headed,
    }
    if proxy:
        kwargs["proxy"] = proxy
    # Do not apply Playwright user_agent for fingerprint identity. Forwarding
    # a caller override still desyncs CH, so it is only kept when no seed/persona
    # will be attached (handled below after seed resolve).
    if extension_paths:
        kwargs["extension_paths"] = list(extension_paths)

    seed = resolve_fingerprint_seed(
        fingerprint_seed=fingerprint_seed,
        profile_meta_path=profile_meta_path,
        profiles_root=profiles_root,
        user_data_dir=user_data_dir,
    )
    meta = resolve_profile_meta_path(
        profile_meta_path=profile_meta_path,
        profiles_root=profiles_root,
        user_data_dir=user_data_dir,
    )
    if require_geo is None:
        env_geo = env_require_geo()
        if env_geo is not None:
            require_geo = env_geo
        else:
            require_geo = is_register_launch(skill_name, skill_path)

    if seed is not None or proxy:
        apply_to_launch_kwargs(
            kwargs,
            seed=seed,
            proxy=proxy,
            headed=headed,
            profile_meta_path=meta,
            require_geo=require_geo,
        )
    if seed is not None:
        log_fingerprint_seed(seed)
    if user_agent and seed is None:
        kwargs["user_agent"] = user_agent

    return launch_persistent_context(**kwargs)


def get_page(ctx: Any) -> Any:
    return ctx.pages[0] if ctx.pages else ctx.new_page()


def _validate_cookie_entry(c: Any, index: int) -> dict[str, Any]:
    if not isinstance(c, dict):
        raise InvalidCookieError(f"cookie[{index}] must be an object")
    name = c.get("name")
    if not isinstance(name, str) or not name:
        raise InvalidCookieError(f"cookie[{index}] missing name")
    if "value" not in c:
        raise InvalidCookieError(f"cookie[{index}] missing value")
    domain = c.get("domain")
    url = c.get("url")
    has_domain = isinstance(domain, str) and bool(domain)
    has_url = isinstance(url, str) and bool(url)
    if not has_domain and not has_url:
        raise InvalidCookieError(f"cookie[{index}] needs domain or url")
    return c


def validate_storage_state(data: Any) -> tuple[list[dict[str, Any]], list[Any]]:
    """Full schema validation. Never trust file blindly. Raises InvalidCookieError."""
    if not isinstance(data, dict):
        raise InvalidCookieError("cookie file must be a storage_state object")

    cookies_raw = data.get("cookies")
    if cookies_raw is None:
        cookies_raw = []
    origins = data.get("origins")

exec
/bin/bash -lc 'PYTHONPATH=python python3 -m unittest discover -s python/tests' in /workspace/CloakCLI
 exited 1 in 725ms:
...............[fingerprint] brand=Chrome/146.0.7680.177 platform=windows/15.0.0 hw=4/4 gpu=Google Inc. (Intel) screen=1536x864
[fingerprint] seed=42424
.E[fingerprint] brand=Chrome/146.0.7680.177 platform=windows/15.0.0 hw=4/4 gpu=Google Inc. (Intel) screen=1536x864
[fingerprint] seed=42424
.[fingerprint] brand=Chrome/146.0.7680.177 platform=windows/15.0.0 hw=4/4 gpu=Google Inc. (Intel) screen=1536x864
...E.E.EE....[fingerprint] brand=Chrome/146.0.7680.177 platform=windows/15.0.0 hw=4/4 gpu=Google Inc. (Intel) screen=1536x864
[fonts] warn missing=Segoe UI, Segoe UI Light, Calibri, Marlett, MS UI Gothic, Franklin Gothic, Consolas, Courier New
.[fingerprint] brand=Chrome/146.0.7680.177 platform=windows/15.0.0 hw=4/4 gpu=Google Inc. (Intel) screen=1536x864
..[fonts] warn missing=Segoe UI, Segoe UI Light, Calibri, Marlett, MS UI Gothic, Franklin Gothic, Consolas, Courier New
.E.E..[geo] skip: GEO_DB_MISSING: GeoLite2-City database unavailable
.EE..[fingerprint] brand=Chrome/146.0.7680.177 platform=windows/15.0.0 hw=4/4 gpu=Google Inc. (Intel) screen=1536x864
..E[fingerprint] brand=Chrome/146.0.7680.177 platform=windows/15.0.0 hw=4/4 gpu=Google Inc. (Intel) screen=1536x864
.[fingerprint] brand=Chrome/146.0.7680.177 platform=windows/15.0.0 hw=4/4 gpu=Google Inc. (Intel) screen=1536x864
[fonts] warn missing=Segoe UI, Segoe UI Light, Calibri, Marlett, MS UI Gothic, Franklin Gothic, Consolas, Courier New
...EE....EE..E.............E......EE.EE.EE.................................EEEEE.....EE............................................{"pause_ms": 450, "label": "hover_before_click"}
.{"human_click": {"ok": false, "method": "no_box"}}
{"pause_ms": 1289, "label": "between_email_password"}
{"pause_ms": 1281, "label": "quiet_window"}
....{"status": "code_filled", "len": 6, "value_len": 6, "value_match": false}
{"status": "code_value_mismatch", "len": 6, "value_len": 6}
..F..EEEEEE..EEEEEEE.......E...E....{"status": "model_retry", "attempt": 1, "max_attempts": 3, "error": "TimeoutError: model request timed out"}
{"status": "model_retry", "attempt": 2, "max_attempts": 3, "error": "TimeoutError: model request timed out"}
{"status": "model_retry", "attempt": 1, "max_attempts": 3, "error": "TimeoutError: model request timed out"}
{"status": "model_retry", "attempt": 2, "max_attempts": 3, "error": "TimeoutError: model request timed out"}
EE.EEEEEEEEEE.EEE.EEE.E...E.............................EEEE......E.E..
======================================================================
ERROR: test_launch_args_include_persona_and_geo (test_browser_launch.LaunchContextTests.test_launch_args_include_persona_and_geo)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_browser_launch.py", line 17, in test_launch_args_include_persona_and_geo
    root = Path(tempfile.mkdtemp(prefix="fp_ctx_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_invalid_remint (test_fingerprint.FingerprintSeedTests.test_invalid_remint)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_fingerprint.py", line 122, in test_invalid_remint
    root = Path(tempfile.mkdtemp(prefix="fp_bad_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_mint_and_persist (test_fingerprint.FingerprintSeedTests.test_mint_and_persist)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_fingerprint.py", line 82, in test_mint_and_persist
    root = Path(tempfile.mkdtemp(prefix="fp_seed_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_regenerate (test_fingerprint.FingerprintSeedTests.test_regenerate)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_fingerprint.py", line 108, in test_regenerate
    root = Path(tempfile.mkdtemp(prefix="fp_regen_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_resolve_from_user_data_dir (test_fingerprint.FingerprintSeedTests.test_resolve_from_user_data_dir)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_fingerprint.py", line 139, in test_resolve_from_user_data_dir
    root = Path(tempfile.mkdtemp(prefix="fp_resolve_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_cache_hit_same_exit_ip (test_fingerprint.GeoCacheTests.test_cache_hit_same_exit_ip)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_fingerprint.py", line 438, in test_cache_hit_same_exit_ip
    root = Path(tempfile.mkdtemp(prefix="fp_geo_hit_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_cache_invalidate_on_exit_ip_change (test_fingerprint.GeoCacheTests.test_cache_invalidate_on_exit_ip_change)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_fingerprint.py", line 469, in test_cache_invalidate_on_exit_ip_change
    root = Path(tempfile.mkdtemp(prefix="fp_geo_ip_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_language_minted_once_from_geo (test_fingerprint.GeoCacheTests.test_language_minted_once_from_geo)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_fingerprint.py", line 586, in test_language_minted_once_from_geo
    root = Path(tempfile.mkdtemp(prefix="fp_lang2_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_language_not_overwritten_from_country (test_fingerprint.GeoCacheTests.test_language_not_overwritten_from_country)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_fingerprint.py", line 575, in test_language_not_overwritten_from_country
    root = Path(tempfile.mkdtemp(prefix="fp_lang_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_args_include_persona_and_geo_flags (test_fingerprint.LaunchKwargsTests.test_args_include_persona_and_geo_flags)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_fingerprint.py", line 595, in test_args_include_persona_and_geo_flags
    root = Path(tempfile.mkdtemp(prefix="fp_launch_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_ensure_same_seed_twice_is_stable (test_fingerprint.PersonaMintTests.test_ensure_same_seed_twice_is_stable)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_fingerprint.py", line 359, in test_ensure_same_seed_twice_is_stable
    root = Path(tempfile.mkdtemp(prefix="fp_seed_stable_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_ensure_seed_change_remints_to_new_seed (test_fingerprint.PersonaMintTests.test_ensure_seed_change_remints_to_new_seed)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_fingerprint.py", line 377, in test_ensure_seed_change_remints_to_new_seed
    root = Path(tempfile.mkdtemp(prefix="fp_seed_change_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_persist_and_reject_then_remint (test_fingerprint.PersonaMintTests.test_persist_and_reject_then_remint)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_fingerprint.py", line 346, in test_persist_and_reject_then_remint
    root = Path(tempfile.mkdtemp(prefix="fp_persona_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_schema_v1_without_gpu_remints (test_fingerprint.PersonaMintTests.test_schema_v1_without_gpu_remints)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_fingerprint.py", line 322, in test_schema_v1_without_gpu_remints
    root = Path(tempfile.mkdtemp(prefix="fp_schema_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_whitelist_persona_from_other_seed_is_not_reused (test_fingerprint.PersonaMintTests.test_whitelist_persona_from_other_seed_is_not_reused)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_fingerprint.py", line 394, in test_whitelist_persona_from_other_seed_is_not_reused
    root = Path(tempfile.mkdtemp(prefix="fp_seed_bind_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_load_from_root (test_llm_config.LlmConfigTests.test_load_from_root)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_llm_config.py", line 54, in test_load_from_root
    root = Path(tempfile.mkdtemp(prefix="cloakcli_llm_py_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_list_models_bad_json_no_leak (test_llm_config.ModelsFetchTests.test_list_models_bad_json_no_leak)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_llm_config.py", line 261, in test_list_models_bad_json_no_leak
    httpd, cfg, _ = self._serve(lambda path: (200, "NOT JSON {"))
                    ~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/workspace/CloakCLI/python/tests/test_llm_config.py", line 206, in _serve
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
  File "/usr/lib/python3.13/socketserver.py", line 453, in __init__
    self.socket = socket.socket(self.address_family,
                  ~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
                                self.socket_type)
                                ^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/socket.py", line 233, in __init__
    _socket.socket.__init__(self, family, type, proto, fileno)
    ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
PermissionError: [Errno 1] Operation not permitted

======================================================================
ERROR: test_list_models_bearer_and_ids (test_llm_config.ModelsFetchTests.test_list_models_bearer_and_ids)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_llm_config.py", line 224, in test_list_models_bearer_and_ids
    httpd, cfg, auths = self._serve(
                        ~~~~~~~~~~~^
        lambda path: (
        ^^^^^^^^^^^^^^
    ...<7 lines>...
        )
        ^
    )
    ^
  File "/workspace/CloakCLI/python/tests/test_llm_config.py", line 206, in _serve
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
  File "/usr/lib/python3.13/socketserver.py", line 453, in __init__
    self.socket = socket.socket(self.address_family,
                  ~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
                                self.socket_type)
                                ^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/socket.py", line 233, in __init__
    _socket.socket.__init__(self, family, type, proto, fileno)
    ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
PermissionError: [Errno 1] Operation not permitted

======================================================================
ERROR: test_list_models_http_error_huge_body_not_kept (test_llm_config.ModelsFetchTests.test_list_models_http_error_huge_body_not_kept)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_llm_config.py", line 369, in test_list_models_http_error_huge_body_not_kept
    httpd, cfg, _ = self._serve(lambda path: (500, huge))
                    ~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/workspace/CloakCLI/python/tests/test_llm_config.py", line 206, in _serve
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
  File "/usr/lib/python3.13/socketserver.py", line 453, in __init__
    self.socket = socket.socket(self.address_family,
                  ~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
                                self.socket_type)
                                ^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/socket.py", line 233, in __init__
    _socket.socket.__init__(self, family, type, proto, fileno)
    ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
PermissionError: [Errno 1] Operation not permitted

======================================================================
ERROR: test_list_models_http_error_redacts_body (test_llm_config.ModelsFetchTests.test_list_models_http_error_redacts_body)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_llm_config.py", line 293, in test_list_models_http_error_redacts_body
    httpd, cfg, _ = self._serve(
                    ~~~~~~~~~~~^
        lambda path: (
        ^^^^^^^^^^^^^^
    ...<2 lines>...
        )
        ^
    )
    ^
  File "/workspace/CloakCLI/python/tests/test_llm_config.py", line 206, in _serve
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
  File "/usr/lib/python3.13/socketserver.py", line 453, in __init__
    self.socket = socket.socket(self.address_family,
                  ~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
                                self.socket_type)
                                ^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/socket.py", line 233, in __init__
    _socket.socket.__init__(self, family, type, proto, fileno)
    ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
PermissionError: [Errno 1] Operation not permitted

======================================================================
ERROR: test_list_models_rejects_key_and_control_char_ids (test_llm_config.ModelsFetchTests.test_list_models_rejects_key_and_control_char_ids)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_llm_config.py", line 382, in test_list_models_rejects_key_and_control_char_ids
    httpd, cfg, _ = self._serve(
                    ~~~~~~~~~~~^
        lambda path: (
        ^^^^^^^^^^^^^^
    ...<11 lines>...
        )
        ^
    )
    ^
  File "/workspace/CloakCLI/python/tests/test_llm_config.py", line 206, in _serve
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
  File "/usr/lib/python3.13/socketserver.py", line 453, in __init__
    self.socket = socket.socket(self.address_family,
                  ~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
                                self.socket_type)
                                ^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/socket.py", line 233, in __init__
    _socket.socket.__init__(self, family, type, proto, fileno)
    ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
PermissionError: [Errno 1] Operation not permitted

======================================================================
ERROR: test_list_models_truncates_count (test_llm_config.ModelsFetchTests.test_list_models_truncates_count)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_llm_config.py", line 249, in test_list_models_truncates_count
    httpd, cfg, _ = self._serve(lambda path: (200, json.dumps({"data": data})))
                    ~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/workspace/CloakCLI/python/tests/test_llm_config.py", line 206, in _serve
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
  File "/usr/lib/python3.13/socketserver.py", line 453, in __init__
    self.socket = socket.socket(self.address_family,
                  ~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
                                self.socket_type)
                                ^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/socket.py", line 233, in __init__
    _socket.socket.__init__(self, family, type, proto, fileno)
    ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
PermissionError: [Errno 1] Operation not permitted

======================================================================
ERROR: test_dry_run_exit_0 (test_pinterest_create_pin.DryRunTests.test_dry_run_exit_0)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_pinterest_create_pin.py", line 314, in test_dry_run_exit_0
    proc = self._run([])
  File "/workspace/CloakCLI/python/tests/test_pinterest_create_pin.py", line 277, in _run
    with tempfile.TemporaryDirectory() as tmp:
         ~~~~~~~~~~~~~~~~~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 1077, in __init__
    self.name = mkdtemp(suffix, prefix, dir)
                ~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_empty_title_parks_without_browser (test_pinterest_create_pin.DryRunTests.test_empty_title_parks_without_browser)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_pinterest_create_pin.py", line 376, in test_empty_title_parks_without_browser
    with tempfile.TemporaryDirectory() as tmp:
         ~~~~~~~~~~~~~~~~~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 1077, in __init__
    self.name = mkdtemp(suffix, prefix, dir)
                ~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_missing_image_is_upload_fail (test_pinterest_create_pin.DryRunTests.test_missing_image_is_upload_fail)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_pinterest_create_pin.py", line 367, in test_missing_image_is_upload_fail
    proc = self._run([], image=Path("/tmp/does-not-exist-create-pin.jpg"))
  File "/workspace/CloakCLI/python/tests/test_pinterest_create_pin.py", line 277, in _run
    with tempfile.TemporaryDirectory() as tmp:
         ~~~~~~~~~~~~~~~~~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 1077, in __init__
    self.name = mkdtemp(suffix, prefix, dir)
                ~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_shim_dry_run (test_pinterest_create_pin.DryRunTests.test_shim_dry_run)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_pinterest_create_pin.py", line 336, in test_shim_dry_run
    with tempfile.TemporaryDirectory() as tmp:
         ~~~~~~~~~~~~~~~~~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 1077, in __init__
    self.name = mkdtemp(suffix, prefix, dir)
                ~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_stdin_digest_and_overrides (test_pinterest_create_pin.DryRunTests.test_stdin_digest_and_overrides)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_pinterest_create_pin.py", line 408, in test_stdin_digest_and_overrides
    with tempfile.TemporaryDirectory() as tmp:
         ~~~~~~~~~~~~~~~~~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 1077, in __init__
    self.name = mkdtemp(suffix, prefix, dir)
                ~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_falls_back_to_profile_json (test_pinterest_create_pin.ProfileResolutionTests.test_falls_back_to_profile_json)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_pinterest_create_pin.py", line 228, in test_falls_back_to_profile_json
    with tempfile.TemporaryDirectory() as tmp:
         ~~~~~~~~~~~~~~~~~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 1077, in __init__
    self.name = mkdtemp(suffix, prefix, dir)
                ~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_prefers_pinterest_run_dir (test_pinterest_create_pin.ProfileResolutionTests.test_prefers_pinterest_run_dir)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_pinterest_create_pin.py", line 212, in test_prefers_pinterest_run_dir
    with tempfile.TemporaryDirectory() as tmp:
         ~~~~~~~~~~~~~~~~~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 1077, in __init__
    self.name = mkdtemp(suffix, prefix, dir)
                ~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_default_model_grok_46_when_unset (test_pinterest_visual_mm.LlmDiscoverTests.test_default_model_grok_46_when_unset)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_pinterest_visual_mm.py", line 258, in test_default_model_grok_46_when_unset
    root = Path(tempfile.mkdtemp(prefix="cloakcli_visual_llm_default_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_discover_from_config_no_key_leak (test_pinterest_visual_mm.LlmDiscoverTests.test_discover_from_config_no_key_leak)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_pinterest_visual_mm.py", line 200, in test_discover_from_config_no_key_leak
    root = Path(tempfile.mkdtemp(prefix="cloakcli_visual_llm_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_env_overlay_and_xai_fallback (test_pinterest_visual_mm.LlmDiscoverTests.test_env_overlay_and_xai_fallback)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_pinterest_visual_mm.py", line 232, in test_env_overlay_and_xai_fallback
    root = Path(tempfile.mkdtemp(prefix="cloakcli_visual_llm2_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_vision_model_prefers_env_then_file_then_text (test_pinterest_visual_mm.LlmDiscoverTests.test_vision_model_prefers_env_then_file_then_text)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_pinterest_visual_mm.py", line 287, in test_vision_model_prefers_env_then_file_then_text
    root = Path(tempfile.mkdtemp(prefix="cloakcli_visual_llm_vision_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_chain_nurture_and_imap_stderr_redacted (test_pinterest_visual_mm.LoginGateAndActionTests.test_chain_nurture_and_imap_stderr_redacted)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_pinterest_visual_mm.py", line 951, in test_chain_nurture_and_imap_stderr_redacted
    ud = Path(tempfile.mkdtemp(prefix="cloakcli_visual_ud_redact_"))
              ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_confirmed_login_writes_session_ok (test_pinterest_visual_mm.LoginGateAndActionTests.test_confirmed_login_writes_session_ok)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_pinterest_visual_mm.py", line 867, in test_confirmed_login_writes_session_ok
    ud = Path(tempfile.mkdtemp(prefix="cloakcli_visual_ud_ok_"))
              ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_finish_result_json_redacts_model_reason_secrets (test_pinterest_visual_mm.LoginGateAndActionTests.test_finish_result_json_redacts_model_reason_secrets)
Model done/fail reason with email/password must not appear in result JSON.
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_pinterest_visual_mm.py", line 764, in test_finish_result_json_redacts_model_reason_secrets
    ud_fail = Path(tempfile.mkdtemp(prefix="cloakcli_visual_ud_reason_fail_"))
                   ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_keep_alive_probe_ok_and_lost (test_pinterest_visual_mm.LoginGateAndActionTests.test_keep_alive_probe_ok_and_lost)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_pinterest_visual_mm.py", line 914, in test_keep_alive_probe_ok_and_lost
    ud = Path(tempfile.mkdtemp(prefix="cloakcli_visual_ud_ka_"))
              ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_model_browsed_ok_on_register_page_is_rejected (test_pinterest_visual_mm.LoginGateAndActionTests.test_model_browsed_ok_on_register_page_is_rejected)
done:browsed_ok on signup/login must not fake success or write .cloak_session_ok.
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_pinterest_visual_mm.py", line 815, in test_model_browsed_ok_on_register_page_is_rejected
    ud = Path(tempfile.mkdtemp(prefix="cloakcli_visual_ud_browse_"))
              ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_model_browsed_ok_with_login_is_success (test_pinterest_visual_mm.LoginGateAndActionTests.test_model_browsed_ok_with_login_is_success)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_pinterest_visual_mm.py", line 837, in test_model_browsed_ok_with_login_is_success
    ud = Path(tempfile.mkdtemp(prefix="cloakcli_visual_ud_browse_ok_"))
              ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_model_registered_ok_without_login_does_not_write_session_ok (test_pinterest_visual_mm.LoginGateAndActionTests.test_model_registered_ok_without_login_does_not_write_session_ok)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_pinterest_visual_mm.py", line 739, in test_model_registered_ok_without_login_does_not_write_session_ok
    ud = Path(tempfile.mkdtemp(prefix="cloakcli_visual_ud_gate_"))
              ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_nurture_action_does_not_set_registered_without_login (test_pinterest_visual_mm.LoginGateAndActionTests.test_nurture_action_does_not_set_registered_without_login)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_pinterest_visual_mm.py", line 856, in test_nurture_action_does_not_set_registered_without_login
    ud = Path(tempfile.mkdtemp(prefix="cloakcli_visual_ud_nurture_"))
              ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_nurture_fail_does_not_negate_register (test_pinterest_visual_mm.LoginGateAndActionTests.test_nurture_fail_does_not_negate_register)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_pinterest_visual_mm.py", line 885, in test_nurture_fail_does_not_negate_register
    ud = Path(tempfile.mkdtemp(prefix="cloakcli_visual_ud_nf_"))
              ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_status_catalog_loaded_from_manifest_file (test_pinterest_visual_mm.ParseActionTests.test_status_catalog_loaded_from_manifest_file)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_pinterest_visual_mm.py", line 156, in test_status_catalog_loaded_from_manifest_file
    tmp = Path(tempfile.mkdtemp(prefix="cloakcli_visual_man_")) / "manifest.json"
               ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_after_three_fills_continue_nudge_and_recovery (test_pinterest_visual_mm.SignupDeadLoopTests.test_after_three_fills_continue_nudge_and_recovery)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_pinterest_visual_mm.py", line 1163, in test_after_three_fills_continue_nudge_and_recovery
    ud = Path(tempfile.mkdtemp(prefix="cloakcli_visual_ud_loop_"))
              ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_vision_timeout_retries_then_succeeds (test_pinterest_visual_mm.SignupDeadLoopTests.test_vision_timeout_retries_then_succeeds)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_pinterest_visual_mm.py", line 1298, in test_vision_timeout_retries_then_succeeds
    ud = Path(tempfile.mkdtemp(prefix="cloakcli_visual_ud_to_"))
              ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_ask_human_pauses (test_recover_loop.RecoverLoopTests.test_ask_human_pauses)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 186, in test_ask_human_pauses
    root = _root()
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 30, in _root
    root = Path(tempfile.mkdtemp(prefix="cloakcli_rec_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_coord_click_matching_screenshot_id_ok (test_recover_loop.RecoverLoopTests.test_coord_click_matching_screenshot_id_ok)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 325, in test_coord_click_matching_screenshot_id_ok
    root = _root()
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 30, in _root
    root = Path(tempfile.mkdtemp(prefix="cloakcli_rec_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_coord_click_rejected_mismatched_screenshot_id (test_recover_loop.RecoverLoopTests.test_coord_click_rejected_mismatched_screenshot_id)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 298, in test_coord_click_rejected_mismatched_screenshot_id
    root = _root()
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 30, in _root
    root = Path(tempfile.mkdtemp(prefix="cloakcli_rec_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_coord_click_rejected_without_screenshot_id (test_recover_loop.RecoverLoopTests.test_coord_click_rejected_without_screenshot_id)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 269, in test_coord_click_rejected_without_screenshot_id
    root = _root()
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 30, in _root
    root = Path(tempfile.mkdtemp(prefix="cloakcli_rec_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_coords_invalid_after_navigation (test_recover_loop.RecoverLoopTests.test_coords_invalid_after_navigation)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 122, in test_coords_invalid_after_navigation
    root = _root()
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 30, in _root
    root = Path(tempfile.mkdtemp(prefix="cloakcli_rec_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_coords_invalidated_after_viewport_change (test_recover_loop.RecoverLoopTests.test_coords_invalidated_after_viewport_change)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 394, in test_coords_invalidated_after_viewport_change
    root = _root()
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 30, in _root
    root = Path(tempfile.mkdtemp(prefix="cloakcli_rec_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_coords_invalidated_same_turn_after_navigation (test_recover_loop.RecoverLoopTests.test_coords_invalidated_same_turn_after_navigation)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 351, in test_coords_invalidated_same_turn_after_navigation
    root = _root()
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 30, in _root
    root = Path(tempfile.mkdtemp(prefix="cloakcli_rec_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_fill_password_field_allowed (test_recover_loop.RecoverLoopTests.test_fill_password_field_allowed)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 428, in test_fill_password_field_allowed
    root = _root()
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 30, in _root
    root = Path(tempfile.mkdtemp(prefix="cloakcli_rec_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_illegal_actions_rejected_then_done (test_recover_loop.RecoverLoopTests.test_illegal_actions_rejected_then_done)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 91, in test_illegal_actions_rejected_then_done
    root = _root()
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 30, in _root
    root = Path(tempfile.mkdtemp(prefix="cloakcli_rec_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_local_backup_selector_skips_llm (test_recover_loop.RecoverLoopTests.test_local_backup_selector_skips_llm)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 474, in test_local_backup_selector_skips_llm
    root = _root()
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 30, in _root
    root = Path(tempfile.mkdtemp(prefix="cloakcli_rec_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_max_three_model_rounds (test_recover_loop.RecoverLoopTests.test_max_three_model_rounds)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 556, in test_max_three_model_rounds
    root = _root()
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 30, in _root
    root = Path(tempfile.mkdtemp(prefix="cloakcli_rec_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_press_and_select_execute (test_recover_loop.RecoverLoopTests.test_press_and_select_execute)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 578, in test_press_and_select_execute
    root = _root()
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 30, in _root
    root = Path(tempfile.mkdtemp(prefix="cloakcli_rec_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_runner_tries_backup_selectors (test_recover_loop.RecoverLoopTests.test_runner_tries_backup_selectors)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 605, in test_runner_tries_backup_selectors
    root = _root()
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 30, in _root
    root = Path(tempfile.mkdtemp(prefix="cloakcli_rec_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_selector_fail_recover_click_continue (test_recover_loop.RecoverLoopTests.test_selector_fail_recover_click_continue)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 38, in test_selector_fail_recover_click_continue
    root = _root()
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 30, in _root
    root = Path(tempfile.mkdtemp(prefix="cloakcli_rec_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_text_stage_does_not_attach_image (test_recover_loop.RecoverLoopTests.test_text_stage_does_not_attach_image)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 501, in test_text_stage_does_not_attach_image
    root = _root()
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 30, in _root
    root = Path(tempfile.mkdtemp(prefix="cloakcli_rec_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_timeout_writes_trajectory (test_recover_loop.RecoverLoopTests.test_timeout_writes_trajectory)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 152, in test_timeout_writes_trajectory
    root = _root()
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 30, in _root
    root = Path(tempfile.mkdtemp(prefix="cloakcli_rec_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_vision_attaches_one_compressed_not_full_page (test_recover_loop.RecoverLoopTests.test_vision_attaches_one_compressed_not_full_page)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 524, in test_vision_attaches_one_compressed_not_full_page
    root = _root()
  File "/workspace/CloakCLI/python/tests/test_recover_loop.py", line 30, in _root
    root = Path(tempfile.mkdtemp(prefix="cloakcli_rec_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_trajectory_strips_secrets (test_redact.RedactTests.test_trajectory_strips_secrets)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_redact.py", line 29, in test_trajectory_strips_secrets
    root = Path(tempfile.mkdtemp(prefix="cloakcli_redact_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_preflight_ok_when_installed_file_exists (test_teach_allowlist.TeachAllowlistTests.test_preflight_ok_when_installed_file_exists)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_teach_allowlist.py", line 87, in test_preflight_ok_when_installed_file_exists
    with tempfile.NamedTemporaryFile(prefix="cloak-chrome-", delete=False) as fh:
         ~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 755, in NamedTemporaryFile
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_duplicate_pairing_rejected (test_teach_chat_protocol.HubClientTests.test_duplicate_pairing_rejected)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_teach_chat_protocol.py", line 270, in test_duplicate_pairing_rejected
    hub = _FakeHub()
  File "/workspace/CloakCLI/python/tests/test_teach_chat_protocol.py", line 65, in __init__
    self.sock = socket.socket()
                ~~~~~~~~~~~~~^^
  File "/usr/lib/python3.13/socket.py", line 233, in __init__
    _socket.socket.__init__(self, family, type, proto, fileno)
    ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
PermissionError: [Errno 1] Operation not permitted

======================================================================
ERROR: test_pairing_bad_code_fails (test_teach_chat_protocol.HubClientTests.test_pairing_bad_code_fails)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_teach_chat_protocol.py", line 289, in test_pairing_bad_code_fails
    hub = _FakeHub()
  File "/workspace/CloakCLI/python/tests/test_teach_chat_protocol.py", line 65, in __init__
    self.sock = socket.socket()
                ~~~~~~~~~~~~~^^
  File "/usr/lib/python3.13/socket.py", line 233, in __init__
    _socket.socket.__init__(self, family, type, proto, fileno)
    ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
PermissionError: [Errno 1] Operation not permitted

======================================================================
ERROR: test_worker_drop_connection_reuses_session (test_teach_chat_protocol.HubClientTests.test_worker_drop_connection_reuses_session)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_teach_chat_protocol.py", line 252, in test_worker_drop_connection_reuses_session
    hub = _FakeHub()
  File "/workspace/CloakCLI/python/tests/test_teach_chat_protocol.py", line 65, in __init__
    self.sock = socket.socket()
                ~~~~~~~~~~~~~^^
  File "/usr/lib/python3.13/socket.py", line 233, in __init__
    _socket.socket.__init__(self, family, type, proto, fileno)
    ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
PermissionError: [Errno 1] Operation not permitted

======================================================================
ERROR: test_worker_pairs_and_reconnects_same_session (test_teach_chat_protocol.HubClientTests.test_worker_pairs_and_reconnects_same_session)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_teach_chat_protocol.py", line 228, in test_worker_pairs_and_reconnects_same_session
    hub = _FakeHub()
  File "/workspace/CloakCLI/python/tests/test_teach_chat_protocol.py", line 65, in __init__
    self.sock = socket.socket()
                ~~~~~~~~~~~~~^^
  File "/usr/lib/python3.13/socket.py", line 233, in __init__
    _socket.socket.__init__(self, family, type, proto, fileno)
    ~~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
PermissionError: [Errno 1] Operation not permitted

======================================================================
ERROR: test_merged_timeline_then_export_runs (test_teach_export.TeachExportTests.test_merged_timeline_then_export_runs)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_teach_export.py", line 113, in test_merged_timeline_then_export_runs
    root = Path(tempfile.mkdtemp(prefix="cloakcli_m4_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
ERROR: test_expected_skill_runs_click_input_nav (test_teach_roundtrip.TeachRoundTripTests.test_expected_skill_runs_click_input_nav)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_teach_roundtrip.py", line 44, in test_expected_skill_runs_click_input_nav
    root = Path(tempfile.mkdtemp(prefix="cloakcli_teach_rt_"))
                ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 564, in mkdtemp
    prefix, suffix, dir, output_type = _sanitize_params(prefix, suffix, dir)
                                       ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^
  File "/usr/lib/python3.13/tempfile.py", line 317, in _sanitize_params
    dir = gettempdir()
  File "/usr/lib/python3.13/tempfile.py", line 506, in gettempdir
    return _os.fsdecode(_gettempdir())
                        ~~~~~~~~~~~^^
  File "/usr/lib/python3.13/tempfile.py", line 499, in _gettempdir
    tempdir = _get_default_tempdir()
  File "/usr/lib/python3.13/tempfile.py", line 414, in _get_default_tempdir
    raise FileNotFoundError(_errno.ENOENT,
                            "No usable temporary directory found in %s" %
                            dirlist)
FileNotFoundError: [Errno 2] No usable temporary directory found in ['/tmp', '/var/tmp', '/usr/tmp', '/workspace/CloakCLI']

======================================================================
FAIL: test_dry_run_subprocess_report (test_pinterest_visual_mm.DryRunSmokeTests.test_dry_run_subprocess_report)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/workspace/CloakCLI/python/tests/test_pinterest_visual_mm.py", line 353, in test_dry_run_subprocess_report
    self.assertEqual(proc.returncode, 0, msg=proc.stderr[-2000:] + proc.stdout[-2000:])
    ~~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AssertionError: 1 != 0 : register_visual_mm.py", line 1236, in screenshot
    Path(path).write_bytes(TINY_PNG)
    ~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^
  File "/usr/lib/python3.13/pathlib/_abc.py", line 641, in write_bytes
    with self.open(mode='wb') as f:
         ~~~~~~~~~^^^^^^^^^^^
  File "/usr/lib/python3.13/pathlib/_local.py", line 539, in open
    return io.open(self, mode, buffering, encoding, errors, newline)
           ~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
OSError: [Errno 30] Read-only file system: '/workspace/CloakCLI/artifacts/pinterest/visual/geo02/obs-000.jpg'

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "/workspace/CloakCLI/scripts/run_pinterest_register_visual_mm.py", line 2987, in <module>
    raise SystemExit(main())
                     ~~~~^^
  File "/workspace/CloakCLI/scripts/run_pinterest_register_visual_mm.py", line 2937, in main
    report = run_loop(
        page=page,
    ...<10 lines>...
        root=ROOT,
    )
  File "/workspace/CloakCLI/scripts/run_pinterest_register_visual_mm.py", line 2312, in run_loop
    shot = capture_viewport(page, art / f"{sid}.jpg")
  File "/workspace/CloakCLI/scripts/run_pinterest_register_visual_mm.py", line 1630, in capture_viewport
    page.screenshot(path=str(dest), full_page=False)
    ~~~~~~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/workspace/CloakCLI/scripts/run_pinterest_register_visual_mm.py", line 1236, in screenshot
    Path(path).write_bytes(TINY_PNG)
    ~~~~~~~~~~~~~~~~~~~~~~^^^^^^^^^^
  File "/usr/lib/python3.13/pathlib/_abc.py", line 641, in write_bytes
    with self.open(mode='wb') as f:
         ~~~~~~~~~^^^^^^^^^^^
  File "/usr/lib/python3.13/pathlib/_local.py", line 539, in open
    return io.open(self, mode, buffering, encoding, errors, newline)
           ~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
OSError: [Errno 30] Read-only file system: '/workspace/CloakCLI/artifacts/pinterest/visual/geo02/obs-000.png'
{"skill_id": "pinterest-register-visual", "version": "0.2.7", "profile": "geo02", "email_domain": "outlook.com", "birthday": "1998-06-11", "age": 28, "headed": true, "dry_run": true, "skip_nurture": false, "llm": {"base_url": "https://us-1.yggraai.com/v1", "model": "grok-4.6", "text_model": "grok-4.6", "vision_model": "grok-4.6", "vision_source": "env:CLOAKCLI_LLM_VISION_MODEL", "api_key_env": "CLOAKCLI_LLM_API_KEY", "key_present": true, "key_source": "env:CLOAKCLI_LLM_API_KEY", "source": "config/llm.json+env+env+vision_env", "compat": "openai_chat_completions_image_url", "grok_compat": false, "grok_docs": {"base_url": "https://api.x.ai/v1", "api_key_env": "CLOAKCLI_LLM_API_KEY", "fallbacks": ["OPENAI_API_KEY", "XAI_API_KEY"], "default_model": "grok-4.6", "vision_model_hint": "CLOAKCLI_LLM_VISION_MODEL or llm.json vision_model (e.g. grok-2-vision-1212)"}, "chat_completions": "https://us-1.yggraai.com/v1/chat/completions", "default_model": "grok-4.6"}, "product_path": "scripts/run_pinterest_register_visual_mm.py", "not_product_path": "computerUse"}


----------------------------------------------------------------------
Ran 292 tests in 0.663s

FAILED (failures=1, errors=70)

exec
/bin/bash -lc 'rg -n "verify_webrtc_ice_no_leak|gather_webrtc_ice|exit_ip" python scripts | head -100; git show --stat --oneline 1f2e8c9a8b86cdc643f280d0c2cd6407a62ac904' in /workspace/CloakCLI
 succeeded in 0ms:
python/tests/test_fingerprint.py:21:    assert_webrtc_host_equals_exit_ip,
python/tests/test_fingerprint.py:437:    def test_cache_hit_same_exit_ip(self):
python/tests/test_fingerprint.py:451:            resolve_exit_ip=lambda p: "8.8.8.8",
python/tests/test_fingerprint.py:461:            resolve_exit_ip=lambda p: "8.8.8.8",
python/tests/test_fingerprint.py:465:        self.assertEqual(second["exit_ip"], "8.8.8.8")
python/tests/test_fingerprint.py:468:    def test_cache_invalidate_on_exit_ip_change(self):
python/tests/test_fingerprint.py:488:            resolve_exit_ip=lambda p: "8.8.8.8",
python/tests/test_fingerprint.py:496:            resolve_exit_ip=lambda p: "9.9.9.9",
python/tests/test_fingerprint.py:501:        self.assertEqual(updated["exit_ip"], "9.9.9.9")
python/tests/test_fingerprint.py:503:        self.assertEqual(persisted["geo_cache"]["exit_ip"], "9.9.9.9")
python/tests/test_fingerprint.py:509:            "exit_ip": "8.8.8.8",
python/tests/test_fingerprint.py:518:                exit_ip="8.8.8.8",
python/tests/test_fingerprint.py:526:                exit_ip="8.8.8.8",
python/tests/test_fingerprint.py:536:                resolve_exit_ip=lambda p: "8.8.8.8",
python/tests/test_fingerprint.py:554:                resolve_exit_ip=boom,
python/tests/test_fingerprint.py:567:            resolve_exit_ip=boom,
python/tests/test_fingerprint.py:607:            resolve_exit_ip=lambda p: "8.8.8.8",
python/tests/test_fingerprint.py:651:                resolve_exit_ip=lambda p: (_ for _ in ()).throw(
python/tests/test_fingerprint.py:753:            assert_webrtc_host_equals_exit_ip(cands, "8.8.8.8"),
python/tests/test_fingerprint.py:760:            assert_webrtc_host_equals_exit_ip(cands, "8.8.8.8")
python/tests/test_fingerprint.py:766:            assert_webrtc_host_equals_exit_ip(cands, "8.8.8.8")
python/tests/test_fingerprint.py:771:            assert_webrtc_host_equals_exit_ip(cands, "8.8.8.8")
python/tests/test_browser_launch.py:26:            "exit_ip": "8.8.8.8",
scripts/probe_fingerprint_ice_fonts_window.py:28:    assert_webrtc_host_equals_exit_ip,
scripts/probe_fingerprint_ice_fonts_window.py:33:    verify_webrtc_ice_no_leak,
scripts/probe_fingerprint_ice_fonts_window.py:40:def offline_report(exit_ip: str) -> dict:
scripts/probe_fingerprint_ice_fonts_window.py:46:        geo={"timezone": "America/New_York", "exit_ip": exit_ip, "locale_from_geo": "en-US"},
scripts/probe_fingerprint_ice_fonts_window.py:53:        geo={"timezone": "America/New_York", "exit_ip": exit_ip, "locale_from_geo": "en-US"},
scripts/probe_fingerprint_ice_fonts_window.py:58:    good = [f"candidate:1 1 UDP 2122260223 {exit_ip} 54400 typ host generation 0"]
scripts/probe_fingerprint_ice_fonts_window.py:61:        f"candidate:2 1 UDP 2122260223 {exit_ip} 54401 typ host generation 0",
scripts/probe_fingerprint_ice_fonts_window.py:66:        assert_webrtc_host_equals_exit_ip(good, exit_ip)
scripts/probe_fingerprint_ice_fonts_window.py:68:            assert_webrtc_host_equals_exit_ip(leak, exit_ip)
scripts/probe_fingerprint_ice_fonts_window.py:81:        "exit_ip": exit_ip,
scripts/probe_fingerprint_ice_fonts_window.py:99:def live_ice(exit_ip: str, headed: bool) -> dict:
scripts/probe_fingerprint_ice_fonts_window.py:123:    args.append(f"--fingerprint-webrtc-ip={exit_ip}")
scripts/probe_fingerprint_ice_fonts_window.py:133:            ice = verify_webrtc_ice_no_leak(page, exit_ip)
scripts/probe_fingerprint_ice_fonts_window.py:149:            "exit_ip": exit_ip,
scripts/probe_fingerprint_ice_fonts_window.py:174:        report = live_ice(args.exit_ip, headed=args.headed)
scripts/probe_fingerprint_ice_fonts_window.py:176:        report = offline_report(args.exit_ip)
scripts/probe_fingerprint_persona.py:43:                "exit_ip": "8.8.8.8",
python/cloakcli_worker/fingerprint.py:21:  passed as --fingerprint-webrtc-ip. Use assert_webrtc_host_equals_exit_ip /
python/cloakcli_worker/fingerprint.py:22:  gather_webrtc_ice_candidates (page probe). A host candidate with a private,
python/cloakcli_worker/fingerprint.py:575:def assert_webrtc_host_equals_exit_ip(
python/cloakcli_worker/fingerprint.py:577:    exit_ip: str,
python/cloakcli_worker/fingerprint.py:584:    if not isinstance(exit_ip, str) or not exit_ip.strip():
python/cloakcli_worker/fingerprint.py:586:    expected = exit_ip.strip()
python/cloakcli_worker/fingerprint.py:789:    exit_ip = (geo or {}).get("exit_ip")
python/cloakcli_worker/fingerprint.py:790:    if exit_ip:
python/cloakcli_worker/fingerprint.py:791:        args.append(f"--fingerprint-webrtc-ip={exit_ip}")
python/cloakcli_worker/fingerprint.py:1119:def default_resolve_exit_ip(proxy: str) -> str:
python/cloakcli_worker/fingerprint.py:1122:        from cloakbrowser.geoip import resolve_proxy_exit_ip
python/cloakcli_worker/fingerprint.py:1128:        ip = resolve_proxy_exit_ip(proxy)
python/cloakcli_worker/fingerprint.py:1179:    exit_ip: str,
python/cloakcli_worker/fingerprint.py:1189:    if cache.get("exit_ip") != exit_ip:
python/cloakcli_worker/fingerprint.py:1195:    if not _is_public_ip(str(cache.get("exit_ip") or "")):
python/cloakcli_worker/fingerprint.py:1218:    resolve_exit_ip: Callable[[str], str] | None = None,
python/cloakcli_worker/fingerprint.py:1226:    resolve_ip = resolve_exit_ip or default_resolve_exit_ip
python/cloakcli_worker/fingerprint.py:1239:        exit_ip = resolve_ip(proxy)
python/cloakcli_worker/fingerprint.py:1249:        exit_ip=exit_ip,
python/cloakcli_worker/fingerprint.py:1259:        city = lookup(exit_ip)
python/cloakcli_worker/fingerprint.py:1270:        "exit_ip": exit_ip,
python/cloakcli_worker/fingerprint.py:1302:    resolve_exit_ip: Callable[[str], str] | None = None,
python/cloakcli_worker/fingerprint.py:1315:    assert_webrtc_host_equals_exit_ip) — call after gather on register paths.
python/cloakcli_worker/fingerprint.py:1352:            resolve_exit_ip=resolve_exit_ip,
python/cloakcli_worker/fingerprint.py:1383:        exit_ip = geo.get("exit_ip")
python/cloakcli_worker/fingerprint.py:1384:        if exit_ip:
python/cloakcli_worker/fingerprint.py:1385:            extra.append(f"--fingerprint-webrtc-ip={exit_ip}")
python/cloakcli_worker/fingerprint.py:1401:def gather_webrtc_ice_candidates(
python/cloakcli_worker/fingerprint.py:1453:def verify_webrtc_ice_no_leak(
python/cloakcli_worker/fingerprint.py:1455:    exit_ip: str,
python/cloakcli_worker/fingerprint.py:1461:    """Gather ICE in each context and assert host candidates == exit_ip.
python/cloakcli_worker/fingerprint.py:1468:        cands = gather_webrtc_ice_candidates(
python/cloakcli_worker/fingerprint.py:1471:        assert_webrtc_host_equals_exit_ip(cands, exit_ip)
python/cloakcli_worker/browser.py:49:    cloakcli_worker.fingerprint.verify_webrtc_ice_no_leak(page, exit_ip) so
1f2e8c9 P0 fingerprint: ICE host assert, font gate, headed window-size.
 artifacts/ask-astra-fingerprint-next-steps.md      |  87 ++++
 artifacts/ask-grok-fingerprint-ice-fonts-window.md |  61 +++
 artifacts/astra-fingerprint-next-steps-summary.md  |  51 +++
 artifacts/astra-fingerprint-next-steps-verdict.md  | 184 +++++++++
 artifacts/fingerprint-ice-fonts-window-report.md   |  74 ++++
 docs/windows-fonts-linux-ops.md                    |  79 ++++
 python/cloakcli_worker/browser.py                  |   9 +-
 python/cloakcli_worker/fingerprint.py              | 455 ++++++++++++++++++++-
 python/tests/test_browser_launch.py                |  84 ++--
 python/tests/test_fingerprint.py                   | 166 ++++++++
 scripts/probe_fingerprint_ice_fonts_window.py      | 190 +++++++++
 11 files changed, 1403 insertions(+), 37 deletions(-)

exec
/bin/bash -lc "python3 - <<'PY'
import inspect, cloakbrowser.browser
print(inspect.getsourcefile(cloakbrowser.browser))
s=inspect.getsource(cloakbrowser.browser)
for i,l in enumerate(s.splitlines(),1):
 if 'start-maximized' in l or 'window-size' in l: print(i,l)
PY" in /workspace/CloakCLI
 succeeded in 0ms:
/workspace/cloakbrowser/cloakbrowser/browser.py
1472         k in seen for k in ("--start-maximized", "--window-size", "--window-position")
1474         seen["--start-maximized"] = "--start-maximized"

exec
/bin/bash -lc "sed -n '1440,1490p' /workspace/cloakbrowser/cloakbrowser/browser.py; rg -n \"font|fingerprint-windows-font\" /workspace/cloakbrowser/cloakbrowser/browser.py | head -20" in /workspace/CloakCLI
 succeeded in 0ms:
            features.append("MediaRouter")
            seen[key] = f"{key}={','.join(features)}"

    # Timezone/locale flags are independent of stealth_args — always inject when set
    if timezone:
        key = "--fingerprint-timezone"
        flag = f"{key}={timezone}"
        if key in seen:
            logger.debug("Arg override: %s -> %s", seen[key], flag)
        seen[key] = flag
    if locale:
        for key in ("--lang", "--fingerprint-locale"):
            flag = f"{key}={locale}"
            if key in seen:
                logger.debug("Arg override: %s -> %s", seen[key], flag)
            seen[key] = flag

    if extension_paths:
        abs_paths = [os.path.abspath(p) for p in extension_paths]
        ext_val = ",".join(abs_paths)

        seen["--load-extension"] = f"--load-extension={ext_val}"
        seen["--disable-extensions-except"] = (
            f"--disable-extensions-except={ext_val}"
        )

    # Open maximized (real Windows Chrome overwhelmingly runs maximized) so the
    # window fills the spoofed screen. Skipped if the caller already chose a
    # window geometry. Gated to binaries where this stays coherent (see
    # binary_supports_maximized_window) — below the gate it would create
    # outerWidth < innerWidth.
    if start_maximized and not any(
        k in seen for k in ("--start-maximized", "--window-size", "--window-position")
    ):
        seen["--start-maximized"] = "--start-maximized"

    return list(seen.values())


# ---------------------------------------------------------------------------
# Windows-font mismatch warning (Linux only)
#
# On Linux the binary spoofs the Windows platform by default, but fonts come
# from the host OS. A font-less Linux box contradicts the Windows claim and
# font-fingerprinting anti-bot systems flag the mismatch. Warn once per
# environment. See docs/chrome40-fpjs-font-minimum-set-investigation.md.
# ---------------------------------------------------------------------------

# Microsoft-proprietary fonts that signal a real Windows install (absent from
# ttf-mscorefonts-installer). Keep in sync with issue #395 and
# docs/chrome40-fpjs-font-minimum-set-investigation.md.
382:    _maybe_warn_windows_fonts(chrome_args)
509:    _maybe_warn_windows_fonts(chrome_args)
646:    _maybe_warn_windows_fonts(chrome_args)
808:    _maybe_warn_windows_fonts(chrome_args)
1432:    # ships enabled. Re-enable it alongside the Windows font-metrics profile so the
1435:    if "--fingerprint-windows-font-metrics" in seen:
1480:# Windows-font mismatch warning (Linux only)
1482:# On Linux the binary spoofs the Windows platform by default, but fonts come
1483:# from the host OS. A font-less Linux box contradicts the Windows claim and
1484:# font-fingerprinting anti-bot systems flag the mismatch. Warn once per
1485:# environment. See docs/chrome40-fpjs-font-minimum-set-investigation.md.
1488:# Microsoft-proprietary fonts that signal a real Windows install (absent from
1489:# ttf-mscorefonts-installer). Keep in sync with issue #395 and
1490:# docs/chrome40-fpjs-font-minimum-set-investigation.md.
1491:# Windows OS fonts — ship with Windows itself, so their absence on a
1492:# Windows-spoofing Linux host degrades results. The two monospace fonts
1494:# `monospace` family resolves to a Windows font. See issue #395.
1506:# MS Office supplemental fonts, installed as one atomic block by every Office
1523:_font_warning_checked = False
1526:def _count_fonts_present(tells: tuple[str, ...]) -> int | None:

exec
/bin/bash -lc 'PYTHONPATH=python python3 scripts/probe_fingerprint_ice_fonts_window.py >/dev/null; echo $?' in /workspace/CloakCLI
 succeeded in 43ms:
0

codex
结论：**有条件通过**。本次 P0 实现与报告范围一致，未发现必须打回的代码错误。

**Blockers（必改）：** 无。

**Nits：**

- `python/cloakcli_worker/fingerprint.py:verify_webrtc_ice_no_leak` 目前只提供校验函数，`launch_context` 不自动调用；register 调用方必须在启动后显式执行，否则 ICE gate 不会实际阻断。
- `assert_webrtc_host_equals_exit_ip` 采用字符串比较 IP；若支持 IPv6，建议先用 `ipaddress` 规范化后比较，避免等价地址格式误报。
- headed `--window-size` 的实际 outer/inner 尺寸仍需真实窗口 smoke 验证；当前单元/离线 probe 只能证明参数生成。
- 本地完整 unittest 受只读工作区与临时目录环境影响无法复现报告中的 292 OK；离线 P0 probe 返回 0，相关新增测试逻辑未见明显错误。

`VERDICT: 有条件通过`
tokens used
46,965
结论：**有条件通过**。本次 P0 实现与报告范围一致，未发现必须打回的代码错误。

**Blockers（必改）：** 无。

**Nits：**

- `python/cloakcli_worker/fingerprint.py:verify_webrtc_ice_no_leak` 目前只提供校验函数，`launch_context` 不自动调用；register 调用方必须在启动后显式执行，否则 ICE gate 不会实际阻断。
- `assert_webrtc_host_equals_exit_ip` 采用字符串比较 IP；若支持 IPv6，建议先用 `ipaddress` 规范化后比较，避免等价地址格式误报。
- headed `--window-size` 的实际 outer/inner 尺寸仍需真实窗口 smoke 验证；当前单元/离线 probe 只能证明参数生成。
- 本地完整 unittest 受只读工作区与临时目录环境影响无法复现报告中的 292 OK；离线 P0 probe 返回 0，相关新增测试逻辑未见明显错误。

`VERDICT: 有条件通过`
