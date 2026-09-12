结论先行：**有条件通过，暂不同步到 Mac**。当前实现主流程已基本覆盖 MVP，但存在一个明确的迁移契约缺口，以及若干安全/注入边界问题；修完“必须修”项后再同步。Netscape 未实现符合二期范围，**不因此打回**。

**1) 必须修（安全 / 注入 / 权限 / 路径）**

- **扁平 profile 迁移不完整。** `profiles::ensure_dir_layout()` 只在 `import` 调用；`export`、`clear`、`status` 没有触发迁移。现有文档却声明 cookie 操作会迁移。结果是 `profiles/<name>.json` 在执行 status/export/clear 后仍保持扁平布局，`open` 也不会把它迁移到目录布局。应统一在所有 cookie 操作和 open 前调用 `ensure_dir_layout`，或修正文档并明确迁移边界。
- **打开时未重新做 cookie schema 校验。** Rust import 会校验，但 `python/cloakcli_worker/browser.py::apply_cookie_file()` 直接读取并调用 `ctx.add_cookies(cookies)`；文件可能被手工篡改、被旧版本写入或通过 symlink 替换。应在 worker 侧复用完整字段校验，至少拒绝非对象、缺少 `name`/`value`、缺少 `domain`/`url` 的 cookie，并返回稳定的 `INVALID_COOKIE`。
- **`origins` 注入存在任意导航风险。** `_apply_origins_best_effort()` 对导入文件中的 `origin` 直接 `page.goto()`，可导航到任意公网或内网地址，且会把 localStorage 写入该站点。即使这是 storage_state 格式兼容，也属于主动网络访问/注入边界。MVP 建议禁用 origins 注入，或严格限制为 `http/https`、禁止 localhost/内网/非预期 origin，并在文档明确。
- **cookie 文件路径信任应再收紧。** worker 通过 `ensure_under_root` 校验路径，这是正确方向；但 Rust 的 `cookie_file_for_open()` 只拼接路径并返回，不检查 canonical path、是否真的是目标 profile 目录下的 regular file，也没有拒绝 symlink。应在生成 IPC 参数时 canonicalize/校验，避免 profile 目录内 symlink 指向其他敏感文件。
- **导出目标路径策略不一致。** `export --out` 允许任意绝对路径。虽然写入 `0600`，但这允许把明文 cookie 写到系统任意位置；计划只要求 0600，但同时强调不要写入仓库根/data 公共产物。建议默认限制在项目根外的显式路径，至少拒绝已知公共目录和目录型目标，并对已有文件强制重设 `0600`（当前 rename 后已处理，需补测试）。
- **文件权限测试只覆盖新文件。** `atomic_write_0600` 做得对，但应补充已有目标、父目录权限和 symlink 目标测试；否则权限回归容易漏掉。

**2) 应尽快改**

- `status` 只有 `present/cookie_count/origin_count/domains`，没有计划要求的有效/已过期状态，也没有过期统计。至少增加 `expired_count` 与 `valid/expired` 汇总。
- `load_storage_state()` 仅反序列化，不验证根对象中未知/异常字段，也不调用统一校验；status/export 对损坏文件只能在部分路径报错，行为不一致。
- CLI/TUI 的脱敏总体合格：状态、列表、worker 元数据没有输出 value；但应增加回归测试，覆盖错误文本、worker stderr、TUI 日志中不出现 cookie value 或 `name=value`。
- `clear --close-sessions` 的关闭逻辑需要确认只匹配 profile；当前 CLI 构造请求看起来按 profile 过滤，但应加测试防止误关其他 profile。
- `profiles/<name>/profile.json` 当前是 `0644`，这符合元数据定位；应在文档明确只有 `cookie.json` 和导出文件属于 secret，避免后续误把 profile 元数据整体改成 secret。
- `cookie_file_for_open()` 在 cookie 文件不存在时静默跳过；若文件存在但权限错误/格式损坏，应让 open 明确失败，而不是把 session 当成“无 cookie”启动。

**3) 可后续**

- Netscape 导入及转换。
- 独立 JSONL `cookie.validate` / `cookie.apply` 操作。
- 加密存储、OS keychain、远程一次性密文下发。
- TUI 逐条编辑、过期清理、跨 profile 复制。
- origins/localStorage 的完整合并策略；在此之前建议默认不应用 origins。

现有实现中，`cookie.json` 原子写入与 `0600`、`.gitignore`、CLI import/export/clear/status、open/skill 注入、TUI `i/E/C`、value 脱敏、目录布局和基础 flat→directory 迁移都已落地；但上述第一组问题触及路径隔离和注入安全，修复前不建议同步。