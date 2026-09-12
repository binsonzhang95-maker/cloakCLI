结论先行：**通过，可同步到 Mac `~/Documents/CloakCLI`**。Netscape 二期未实现不影响本轮结论。

1) 上一轮必须项

- `ensure_dir_layout` 覆盖所有 cookie 操作及 open：**已修**
- worker 侧 `INVALID_COOKIE` schema 校验：**已修**
- origins 默认关闭，启用时限制 http/https 并阻断 localhost/私网等：**已修**
- `cookie_file_for_open` canonicalize、拒绝 symlink、限制在 profile 目录：**已修**
- export 拒绝 `data/`、`artifacts/` 和目录目标：**已修**
- `0600`、已有目标权限重置、symlink、损坏文件、脱敏测试：**已修**
- status 增加 valid/expired 统计：**已修**

代码中还确认了损坏 cookie 在 open/status/export 路径会失败，不再静默当作无 cookie。

2) 同步前最短清单

- **可空**。当前没有发现需要再打回的上一轮必须项。

验证限制：本环境文件系统只读，执行 `cargo test` 时因无法创建 `target/debug/.cargo-lock` 被环境阻断；静态代码与现有测试覆盖已完成复核。