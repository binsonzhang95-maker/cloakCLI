# Ask Astra — CloakCLI fingerprint P0 ICE+fonts+window code review R2

**To:** Pi's Astra (`codex exec -m gpt-6-astra`)  
**Repo:** `/workspace/CloakCLI`  
**Branch:** `feat/fingerprint-ice-fonts-window`  
**R1:** `artifacts/astra-fingerprint-ice-fonts-window-code-verdict.md` — **有条件通过**, blockers 无; nits listed below.  
**Do NOT implement. Code review only. 中文。半页内。**

你是监工 Astra。只审代码正确性。结论三选一：通过 / 有条件通过 / 打回。

## R1 nits → R2 fixes

1. ~~ICE 只是函数、launch 不调用~~ → `browser.launch_context` 在 `require_geo` + `--fingerprint-webrtc-ip` 时自动 `verify_webrtc_ice_no_leak`（可 `CLOAKCLI_REQUIRE_WEBRTC_ICE=0` 退出）；leak 关 context。单测 `test_register_path_runs_webrtc_ice_verify`。
2. ~~IP 字符串比较~~ → `assert_webrtc_host_equals_exit_ip` 用 `ipaddress` 规范化；单测 `test_assert_normalizes_ipv6`。
3. headed 实测 outer/inner — 仍为 ops smoke residual（文档 nit，非代码 blocker）。
4. Astra sandbox 无法跑满 unittest — 本机已跑 **294 OK**（非代码问题）。

## Please answer
若无新的代码必改，给 **通过**。仅剩 headed WM smoke / ops 字体包 不算打回。  
最终一行：`VERDICT: 通过|有条件通过|打回`
