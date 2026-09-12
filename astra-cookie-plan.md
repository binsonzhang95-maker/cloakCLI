**结论：有条件通过（仅方案）。**  
方向正确，但必须先冻结存储、注入和脱敏契约；实现后按“改完再审”执行。当前开发树已有 worker daemon、profile 锁和多 client 架构，cookie 方案应直接复用，不能另起一套 session 管理。

**1) 存储位置与格式**

- 绑定 profile，建议：`profiles/<name>/cookie.json`，权限 `0600`；profile 元数据仍放 `profile.json`。
- `cookie.json` 采用 Playwright `storage_state` 兼容格式：`{"cookies":[...],"origins":[...]}`。首期允许只含 `cookies`，保留 `origins` 以便后续 localStorage。
- Netscape 仅作为导入格式，导入后立即规范化为上述格式；不保留原始文件。
- 与 `user_data_dir` 分离：`user_data_dir` 是浏览器持久上下文运行目录，cookie 文件是可导入/导出的配置源。不要把 cookie 再复制到 `user_data_dir` 之外的隐式位置。
- `export` 默认输出 stdout；`--out` 写文件时同样设 `0600`，禁止默认写入仓库根目录或 `data/` 公共产物目录。

**2) 导入时机**

推荐“写入 persistent context + open 时校验”的组合：

- `profile cookie import` 先校验并规范化文件，写入 profile cookie 文件。
- `browser open` 创建 persistent context 后，将 profile cookie 注入 context（必要时 `add_cookies`），再执行 skill。
- 已运行 session 不热更新；导入后对新 session 生效。若要刷新，明确提供 `browser close` 后重新 `open`。
- 不要只在 open 请求里临时传 cookie，否则 CLI/TUI/远程 client 行为不一致；也不要直接篡改浏览器 SQLite/内部存储。

**3) 安全要求**

- 所有日志、错误、TUI 列表和 job 事件禁止输出 `value`、`name=value` 完整 cookie；最多显示域、路径、名称和过期时间，名称也建议可选脱敏。
- proxy 凭据继续按现有 `redact_proxy` 规则处理。
- `cookie.json`、导出文件、临时 Netscape 文件加入 `.gitignore`；文档明确“勿把 cookie 同步进 git/工件/截图”。
- 多 client 下发时只发送给目标 client、目标 profile 和目标 job；默认不在 master 持久化明文 cookie。优先“client 本地导入/引用”，协议支持加密传输或一次性 secret，禁止广播和写入普通 job 日志。
- 清理语义明确：`clear` 删除 profile cookie 文件，并可选关闭该 profile 的现有 session；不自动删除整个 `user_data_dir`。

**4) MVP 最短范围**

必须做：

- `profile cookie import <profile> <file> [--format auto|storage-state|cookies-json]`
- `profile cookie export <profile> [--out FILE]`
- `profile cookie clear <profile>`
- storage_state/cookies JSON 解析、字段校验、原子写入、`0600` 权限。
- open 时注入；profile 锁与现有 worker daemon 复用。
- 日志/TUI/错误脱敏；单元测试覆盖格式校验、原子写入、脱敏。

可二期：

- Netscape 导入（若首期实现，必须转换后不落原文）。
- origins/localStorage 编辑与合并策略。
- cookie 加密存储、OS keychain、远程 client 一次性密文下发。
- TUI 可视化逐条编辑、过期清理、跨 profile 复制。

**5) 给 Grok 的可执行接口清单**

CLI：

```text
cloakcli profile cookie import <profile> <file> --format auto
cloakcli profile cookie export <profile> [--out <file>]
cloakcli profile cookie clear <profile> [--close-sessions]
cloakcli browser open --profile <profile> ...
cloakcli browser list|close [--profile <profile>]
```

Worker JSONL（请求/响应均带 `version`、`request_id`）：

```json
{"version":1,"request_id":"...","op":"browser.open","profile":"p","user_data_dir":"...","cookie_file":"..."}
{"version":1,"request_id":"...","op":"cookie.validate","format":"storage_state","payload":{...}}
{"version":1,"request_id":"...","op":"cookie.apply","session_id":"...","cookies":[...]}
{"version":1,"request_id":"...","op":"browser.close","session_id":"..."}
```

约束：worker 只接受 daemon 启动时确定的 root/profile 路径；响应不得回显 cookie value；失败返回稳定 `code`（`INVALID_COOKIE`、`PROFILE_LOCKED`、`SESSION_NOT_FOUND` 等）。

TUI：

- Profile 页面：`Cookie status`（未配置/有效/已过期）。
- 操作：Import、Export、Clear、Open with profile。
- 导入前显示文件格式与 cookie 数量；完成后只显示域名/数量/过期统计。
- Sessions 页面显示真实 session、profile、client、是否已应用 cookie，不显示 cookie 内容。