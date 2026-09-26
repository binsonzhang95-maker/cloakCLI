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
