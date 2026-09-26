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
