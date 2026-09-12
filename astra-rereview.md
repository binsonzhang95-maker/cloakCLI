结论先行：**再打回，暂不同步到 Mac `~/Documents/CloakCLI`**。

最直接的阻断问题是当前 Rust 代码疑似无法编译：`src/master_hub.rs:179` 和 `:197` 的字符串被写成了实际跨行内容：

```rust
let line = serde_json::to_string(&resp)? + "
";
```

应为 `+ "\n"`。当前环境又是只读文件系统，无法替你修复后重新构建；`cargo test/check` 也因无法写入 `target/debug/.cargo-lock` 未能完成验证。

上一轮必须项：

| 项目 | 结论 | 依据 |
|---|---|---|
| 常驻 worker/session 生命周期 | 已修 | Python Unix socket daemon；`worker serve/status/stop`；CLI 间通过 daemon 共享 session |
| IPC 超时 | 已修 | Rust worker IPC 有连接/响应 timeout，并跳过错 ID |
| 优雅退出 | 已修 | shutdown、等待、再终止；worker daemon 会关闭浏览器 |
| profile 锁 | 已修 | `data/locks/<profile>.lock`，带等待超时 |
| batch 临时文件唯一 | 已修 | `batch_<uuid>.json` |
| 路径穿越 | 已修 | 名称校验、`ensure_under_root`、skill/artifact 路径约束 |
| worker root 信任 | 已修 | daemon 启动时固定 `CLOAKCLI_ROOT`，忽略请求传入 root |
| proxy 脱敏 | 已修 | profile list/show/TUI 使用 `redact_proxy` |
| skill 参数校验 | 已修 | required/default 参数及未定义 `{{var}}` 检查 |
| TUI Sessions 真实会话 | 已修 | Sessions pane 从 worker daemon 查询真实 sessions，不再显示已完成 skill |

这些修复从代码看基本已落地，但尚缺一次可执行构建和端到端验证，因此不能视为已验收。

fleet stub 方向是正确的：

- 主控 hub 接收 client 的出站 TCP 连接；
- client daemon 主动连接 master，并有重连、心跳；
- 本地多浏览器仍属于 client 内部 worker；
- TUI 的 Clients pane 和远程 job submit 已接入。

但它距离新架构要求的 MVP 仍有明显缺口：

1. 现在是**明文 TCP JSONL**，不是 TLS/WebSocket。
2. 鉴权是 master 级共享 token，不是每 client 独立配对身份、轮换和撤销。
3. `skill_sync` 只是打印 stub，没有 manifest、内容 hash、版本锁定、校验或回滚。
4. `config_update` 没有实际下发、ack、diff、失败重试。
5. job 状态没有持久化、断线恢复或幂等去重；client 断线后任务可能重复或丢失。
6. `job_cancel` 只是回报 cancelled，未真正停止正在执行的 worker/job。
7. 日志虽有序号字段，但没有断点续传机制；截图协议也未真正接通。
8. hub 的 desired config 只存在内存，重启即丢。
9. client heartbeat 使用 `hello_ok` 中的 revision 回报，未维护真实 observed revision；配置漂移状态不可信。
10. fleet 配置中的 token 明文存储，且仍是共享 token 模型。

若要“有条件通过”，同步前至少还要改：

- 修正 `master_hub.rs` 两处跨行字符串，并在可写环境完成 `cargo build/test`。
- 增加最小端到端验收：master、client connect、clients、submit、job_state、断线重连。
- 明确把当前 fleet 标为 stub/开发模式，禁止把明文共享 token 当生产安全方案。
- 实现至少一个真实的 `config_update` ack/diff 流程，并修正 heartbeat 的 observed revision。
- 为 job 增加持久化或明确的幂等恢复策略。
- `job_cancel` 必须能取消实际运行任务，不能只发送状态。
- 在 README/FIXES 中明确 TLS/WebSocket、独立 client 身份、skill sync 是同步前的未完成安全缺口。

因此本轮不是上一轮十项又全部失败，而是**基础修复大多已完成，但当前提交存在编译阻断，fleet 仍只是方向正确的 stub，尚不足以同步到 Mac**。