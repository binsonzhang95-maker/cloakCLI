结论先行：**打回，暂不同步到 Mac**。`cargo check` 与 Python 编译检查通过，但当前 `browser open` 的 session 生命周期是功能级阻断问题：Rust 的 `oneshot()` 每次命令启动 worker，收到响应后立即 `shutdown`；Python 中的 session 只存在于该 worker 进程。因此 `browser open` 返回的 session 随即被关闭，后续 `browser list`/`close` 也无法看到或操作它。见 [src/worker.rs](/workspace/CloakCLI/src/worker.rs)、[python/cloakcli_worker/__main__.py](/workspace/CloakCLI/python/cloakcli_worker/__main__.py)。

1. 必须修的问题

- **浏览器 session 生命周期错误**：需要常驻 worker（TUI/CLI 共享或明确的 daemon），或改为真正无状态的 `open` 行为。当前 `open/list/close` 三个命令语义无法成立。
- **IPC 请求匹配不完整**：Rust 会忽略 id 不匹配的响应，但没有超时、取消、积压响应管理；worker 卡死时请求永久等待。应加请求超时、子进程存活检查和明确错误。
- **worker 退出与资源回收不可靠**：`shutdown()` 先发请求再无条件 `child.kill()`，没有等待正常退出；异常路径、父进程崩溃、TUI 退出时的清理也不完整。
- **并发与 profile 冲突**：batch 为每个 job 启动独立 worker，但同一个 profile 可以并发执行，多个 Chromium persistent context 共用同一 `user_data_dir`，容易锁冲突、数据损坏或启动失败。需要按 profile 串行化，或为任务分配独立上下文目录。
- **batch 临时文件竞争**：`data/batch_tmp.json` 是固定路径，并发运行两个 batch 会互相覆盖。
- **技能导入存在路径穿越**：`skills::import()` 没有校验 `--name`；`../x`、绝对路径等可将 skill 写到 `skills/` 外。应复用严格名称校验并确认目标路径仍位于 skills 根目录。
- **技能执行的 artifact 路径也不安全**：`skill.name` 直接参与 `data/artifacts/<skill_name>`；导入的恶意名称可能造成目录逃逸。截图中 `artifacts/...` 分支同样需要 canonicalize 后做根目录约束。
- **IPC 输入信任过度**：Python worker 接受请求中的任意 `root`、`skill_path`、`user_data_dir`，若 worker 被单独暴露或误接入管道，可读写任意本地路径。至少限制在启动时确定的 root 下，并校验 skill 路径。
- **错误协议处理不稳健**：Python 收到坏 JSON 后返回错误但继续循环；空行被当作 `noop`。应明确丢弃空行、对协议错误计数或退出，避免上游永久重试。
- **proxy 凭据可能泄漏**：`profile list/show`、batch 日志和错误输出可能直接打印包含用户名密码的代理 URL。应脱敏展示，日志避免回显完整 URL。
- **skill 语义不完整**：`params` 被读取但完全没有校验、默认值和类型处理；变量只做字符串替换，没有未定义变量报错、类型保持或步骤级输入约束。当前文档宣称的 skill 参数能力尚未实现。

2. 应该尽快改的设计问题

- `browser open` 的默认 URL、导航失败后“保留 session”的行为需要明确；现在失败也返回成功响应加 warning，脚本难以判断任务是否成功。
- Python worker 单线程串行处理请求；如果未来改成常驻 worker，需要定义并发模型，避免一个长时间 skill 阻塞 `list/close/shutdown`。
- profile 删除只删除 JSON，不删除 user data；应提供显式清理选项，并在文档中说明磁盘残留语义。
- `skills::list()` 对解析失败的 skill 静默跳过，导致用户无法知道为什么 skill 消失；应在 CLI/TUI/doctor 中报告无效文件。
- `resolve_headed()` 的平台默认符合方案（macOS headed、Linux headless），但 batch 只有 `--headed`，缺少显式 `--headless`，命令行语义不对称。
- 项目根目录依赖 `Cargo.toml + skills/`；同步到 `~/Documents/CloakCLI` 后从任意目录运行仍需设置 `CLOAKCLI_HOME` 或位于仓库目录，应该提供安装后的稳定数据目录策略。
- “stealth” 文案仍容易被理解成匿名能力。README 虽未明确承诺绝对匿名，但 CLI about、包描述应加入“不能保证匿名/绕过检测”的准确表述。

3. 可后续再做的改进

- 结构化日志、任务 ID、持久化运行记录和失败重试。
- batch 的结果 JSON 输出、实时进度和取消机制。
- 更丰富的 skill 动作（select、键盘、断言、条件、循环、下载等）。
- profile 加密存储代理凭据或改用环境变量/凭据引用。
- worker 健康检查、版本协商和协议 schema。
- TUI 中的 session 操作、任务队列、并发控制和错误详情。
- 集成测试：真实 worker 生命周期、IPC 超时、路径穿越、同 profile 并发、浏览器启动失败等。

4. TUI/脚本双入口是否清晰

**命令分层本身清晰**：`profile` 管配置，`skill` 管技能，`batch` 管批量，`doctor` 管诊断；脚本入口适合 Linux 回放，TUI 适合 Mac 教学。

但 TUI 当前承诺过头：

- TUI 的 `Sessions` 页面只是把“完成的 skill”追加到内存列表，并没有调用 `list_sessions`，不是浏览器 session 监控。
- TUI 只有 Enter 执行 skill，没有 browser open/close、headed/headless 切换或 batch 控制。
- TUI 退出后所有 worker 都已是 oneshot，无法维持浏览器会话。
- CLI 的 `browser open/list/close` 在现实现下互相不连贯，脚本用户会直接遇到失效流程。

因此双入口的定位可以保留，但必须先统一 session/worker 生命周期，再把 TUI 文案和功能范围改到与实际行为一致。