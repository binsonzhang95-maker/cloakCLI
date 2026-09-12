**结论：master/client 拆分合理，且应作为主架构。**  
主控 TUI 负责“配置、调度、观察”，远程 client 负责“执行、采集、回报”。不要把多浏览器并行作为核心抽象；浏览器实例只是 client 内部的执行资源。

### 1) MVP 最小协议

建议使用 **主控主动建立连接的出站长连接**，避免 NAT/端口暴露：

- **传输**：WebSocket over TLS（后续可换 QUIC）；消息采用 versioned JSON，截图/大文件用对象流或压缩二进制。
- **鉴权**：首次配对码/短期 token，换取每 client 的长期公钥身份；之后采用 mTLS 或签名消息。每台 client 有唯一 `client_id`。
- **心跳**：client 每 10–30 秒发送 heartbeat，携带版本、能力、资源状态；主控维护 `last_seen` 和连接状态。
- **下发**：
  - `skill_sync`：skill manifest、版本、哈希、依赖；client 回报已安装版本。
  - `job_submit`：job_id、目标 client、skill、参数、超时、取消策略。
  - `job_cancel` / `config_update`：均带版本号和幂等 ID。
- **回传**：
  - `job_state`：queued/running/succeeded/failed/cancelled。
  - `log_chunk`：带序号，支持断线续传。
  - `screenshot`：缩略图优先，原图按需拉取。
  - 所有消息带 `protocol_version`、`request_id`、时间戳和签名/校验信息。

### 2) Rust CLI 与 Python/CloakBrowser 分层

- **Rust CLI/TUI（master）**：节点管理、proxy/并发配置、skill/job 编排、协议客户端、状态存储、日志聚合和展示。
- **Rust client daemon**：连接主控、鉴权、心跳、任务队列、资源限制、skill 缓存、进程监管；不要把浏览器业务逻辑塞进 TUI。
- **薄 Python/CloakBrowser worker**：只负责浏览器自动化和页面级动作，通过 stdin/stdout 或本地 RPC 接受结构化 job，输出结构化事件、日志和截图。
- 建议定义稳定的 **Job/Event schema**，Rust 只调度，Python 只执行；worker 崩溃可由 daemon 重启。

### 3) 必须避免的坑

- **NAT**：不要要求 client 入站端口；统一出站连接，必要时提供 relay。
- **鉴权**：不要共享一个全局 token；撤销、轮换、最小权限和审计必须按 client 独立处理。
- **skill 同步**：以 manifest + 内容哈希为准，支持锁定版本、校验失败重传和回滚；禁止“目录直接覆盖”。
- **凭据**：密码、proxy 密钥、Cookie 不进普通 job JSON 或日志；使用系统密钥环/密文引用，按 job 临时注入。
- **配置漂移**：主控保存期望状态（desired state），client 回报实际状态（observed state）；配置带 revision，支持 diff、ack 和失败重试。
- 另需限制并发、单 job 超时、幂等重试，避免断线后重复执行。

### 4) MVP 边界

**一期足够做：**

1. 单主控、多 client 注册/配对和在线状态。
2. TLS WebSocket 长连接、心跳、断线重连。
3. 主控下发一个版本化 skill 和一个 job。
4. client 执行 Python worker，回传状态、日志和低频截图。
5. 基础 client 配置：proxy、并发上限、标签；带 revision 的同步。
6. 本地持久化 job/client 状态和基础审计日志。

**二期再做：**

- 多主控/高可用、relay/QUIC、复杂 DAG 工作流。
- 大规模 skill 仓库、增量分发、签名发布。
- RBAC、团队协作、精细 secret manager。
- Web 控制台、指标/告警、自动扩缩容、跨区域调度。
- 视频流、实时交互、复杂浏览器池管理。

**给 Grok 的执行指令：**  
> 请按以上 master/client 架构审阅 CloakCLI 现有代码，先输出当前模块映射与差距清单；然后设计并实现一期最小协议（versioned message schema、WebSocket TLS、client_id 配对鉴权、heartbeat、skill_sync、job_submit、job_state、log_chunk、screenshot），保持 Rust TUI/master、Rust client daemon、Python/CloakBrowser worker 三层边界。优先完成单主控多 client、出站长连接、版本化配置同步和可恢复 job；为二期能力预留接口，但不要提前实现。