**结论：有条件通过。** 当前仓库仍是单一 Rust CLI crate，尚未出现 Tauri/`desktop/` 开发树；可以开工，但必须按下列边界实现，不能把业务迁入桌面壳。

1. **目录结构**

推荐 monorepo 内新增独立 `desktop/`：

```text
/
├─ src/                 # 现有 cloakcli CLI/TUI/worker，保持不动
├─ Cargo.toml
├─ desktop/
│  ├─ src-tauri/        # Tauri 2 Rust 壳
│  ├─ package.json
│  └─ src/              # 前端、xterm.js、自定义标题栏
└─ ...
```

`desktop/src-tauri` 是独立 crate；根 crate 暂不改 workspace，避免 Tauri 依赖污染 CLI。后续若需要统一构建，再转 Cargo workspace。

2. **嵌入终端与子进程**

采用 **xterm.js + 官方/成熟 PTY 插件**（如 `xterm-addon-fit` 配合 Tauri PTY 插件或自建最小命令桥）。不要把 `ratatui` 重写成 Web UI，也不要直接用普通 stdout 管道代替 PTY，否则 TUI 的尺寸、颜色、键盘和 resize 会失真。

启动：

- Tauri 后端解析已安装的 `cloakcli` 可执行文件路径；开发期允许配置 `CLOAKCLI_BIN`。
- 用 PTY 启动 `cloakcli tui`，继承必要环境变量，并显式传 `CLOAKCLI_HOME`。
- `CLOAKCLI_HOME` 应指向用户选择/应用管理的项目根；不能依赖当前工作目录。仓库现有状态逻辑已明确优先读取该变量。
- 处理 PTY 输入、输出、退出码和窗口 resize；关闭窗口时发送正常终止，超时再 kill。

3. **MVP 最短范围**

必须包含：

- 无系统 File/Edit 菜单感的自定义标题栏；
- 最小窗口控制：拖拽、最小化、最大化/还原、关闭；
- 默认窗口尺寸（建议 1100×720）及最小尺寸；
- 一个终端视图，启动/停止 `cloakcli tui`；
- PTY resize 与退出状态提示；
- Linux 本机可运行，Mac mini 可构建验证。

**本期不要求正式打包发布。** 但应提交 Tauri 配置和 `tauri build` 可用的骨架；签名、公证、DMG/AppImage、Windows 安装包列为后续任务。

4. **安全注意**

- PTY 本质上可执行任意命令；桌面壳只启动固定的 `cloakcli tui`，不要把任意命令参数暴露给前端。
- Tauri capability 仅开放必要窗口、PTY 和事件权限，禁止宽泛 shell/API 权限。
- `CLOAKCLI_HOME`、二进制路径、profile 路径必须做规范化和存在性校验；拒绝危险的空路径、相对路径和越界路径。
- 不把 token、cookie、环境变量或完整命令行写入前端日志。
- 子进程继承环境需白名单化；PTY 退出、崩溃、重复启动必须可控。

5. **给 Grok Build 的任务清单**

- 新建 `desktop/` Tauri 2 应用，不改现有 CLI/TUI/worker 业务代码。
- 实现自定义标题栏和窗口事件。
- 集成 xterm.js、fit addon、PTY；完成 `cloakcli tui` 启停、输入输出、resize、退出处理。
- 实现 `CLOAKCLI_BIN`（开发期）和发布期二进制发现策略。
- 实现 `CLOAKCLI_HOME` 配置与启动时校验。
- 配置 capability 最小权限、Linux 开发运行和 Mac 构建说明。
- 添加 README：开发命令、依赖、已知限制、后续打包计划。

**验收：**

- `cargo test`（根项目）通过，现有 `cloakcli tui` 行为不变；
- Linux 执行 `cd desktop && npm run tauri dev` 可打开窗口；
- 终端内可正常操作 TUI，颜色、键盘、Unicode 和 resize 正常；
- 点击关闭可终止子进程，不遗留 worker/PTY；
- 设置不同 `CLOAKCLI_HOME` 后，配置和数据确实来自该目录；
- 前端无法调用任意 shell 命令；
- Mac mini 上能完成开发构建并启动同一 TUI。