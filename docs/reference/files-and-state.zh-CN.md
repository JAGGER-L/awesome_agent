# 文件与状态参考

Awesome 将用户拥有的配置、可替换的 runtime 状态、Workspace 拥有的输入和已安装程序文件
彼此分开。这种分离决定了哪些内容可以独立备份、重置、信任或升级。

## 根位置

| 平台 | 默认 `AWESOME_HOME` | 默认安装目录 |
| --- | --- | --- |
| Windows | `%LOCALAPPDATA%\Awesome` | `%LOCALAPPDATA%\Programs\Awesome` |
| macOS/Linux | `~/.awesome` | `~/.local/share/awesome` |

`AWESOME_HOME` 覆盖 User data root。官方安装器及其生成的 launcher 当前使用表中固定的安装
根目录；`AWESOME_INSTALL_DIR` 只读入一个除此之外没有使用的底层
`AwesomePaths.install_dir` 字段，不能迁移或发现 release 安装。如果 Windows 没有
`LOCALAPPDATA`，Awesome 的 path resolver 会回退到 `~/AppData/Local`。下文路径用
`<HOME>` 表示解析后的 Awesome home，而不是操作系统 home。

```text
<HOME>/
├── .env
├── .env.lock
├── .provider-credential-transaction.json
├── .provider-credential-transaction.env
├── .state.lock
├── .config.yaml.lock
├── config.yaml
├── ui.json
├── logs/
│   ├── .application.jsonl.lock
│   ├── application.jsonl
│   ├── application.jsonl.1
│   ├── application.jsonl.2
│   ├── application.jsonl.3
│   └── application.jsonl.4
├── skills/
├── memory/
│   ├── .USER.md.lock
│   └── USER.md
├── workspaces/
│   └── <workspace_key>/
│       ├── .MEMORY.md.lock
│       └── MEMORY.md
├── state/
│   ├── application.db
│   ├── application.db.pre-migration.bak
│   ├── checkpoints.db
│   ├── provider-model-transaction.json
│   └── change-journal/
│       └── blobs/
├── .workspace-leases/
│   └── <workspace_key>/.state.lock
└── .workspace-entity-leases/
    └── <entity_key>/.state.lock
```

目录和文件按需创建。它们不存在通常是正常默认状态，并非损坏。

## Application invocation 日志

`<HOME>/logs/application.jsonl` 是当前的进程/会话级结构化诊断日志。它位于
`WorkspaceRuntime`、Application database state 和 Thread history 之外。Awesome 最多保留
当前文件以及 `application.jsonl.1` 至 `.4`；每个文件上限为 5 MiB。
`<HOME>/logs/.application.jsonl.lock` 用于协调 writer，不属于这 5 个数据文件。

每个 JSON line 使用相同的封闭 schema：`version`、`timestamp`、`session_id`、
`correlation_id`、`operation`、`outcome`、`duration_ms`，以及可选的 `error_code` 与有界
`usage`。Prompt、模型或 Tool 正文、query、URL、path、secret 和任意 request/result payload
绝不会写入日志。写入是非阻塞、fail-open 的，因此记录缺失可能表示 queue 已满或本地日志
失败，并且不会改变 Application 结果。Invocation outcome 只描述 facade request，不是异步
准入的 Agent 工作之后到达的终态。

## 用户拥有的文件

### `<HOME>/config.yaml`

严格的 User 配置 schema 版本 `2`：Provider 默认值、凭据来源选择、预算、Web 设置、
Memory 开关、禁用的 Skills 和 User MCP 声明。版本 `1` 仍可读取，并由第一次受支持的写操作
原子升级。该文档不包含秘密值。见[配置](configuration.zh-CN.md)。

### `<HOME>/.env`

由 Awesome 管理的 `DEEPSEEK_API_KEY`、`MOONSHOT_API_KEY`、`MEM0_API_KEY`，以及可选的
`AWESOME_WEB_PROXY_URL` 凭据存储。`/auth` 只管理前三项；受支持的 writer 通过同目录
temporary file 写入、flush，然后以原子方式替换目标。在 POSIX 上，Awesome 创建目录时
只允许 owner 访问，创建文件时只允许 owner 读写。

这不是通用 dotenv 契约。任意条目不会被当作配置，值也不会自动转发给 MCP 服务器。绝不要
提交该文件或把它复制进 Workspace。

### `<HOME>/ui.json`

Ink 拥有的 UI preferences。当前 schema：

```json
{
  "schema_version": 1,
  "theme": "system"
}
```

`theme` 取值为 `system`、`dark` 或 `light`。缺失状态会静默默认为 `system`；不可读或无效
状态会报告 warning，同样回退到 `system`。`/theme` 通过临时 sibling file 原子写入。Core
不拥有此文档。

### `<HOME>/skills/`

User Skill 包，通常为 `<HOME>/skills/<name>/SKILL.md` 加可选资源。User Skills 是本地受信任
输入，并保留现有链接行为；更严格的禁止 reparse 包规则应用于 Workspace Skills。见
[Skills](../extensions/skills.zh-CN.md)。

### Local Memory 文件

`<HOME>/memory/USER.md` 存储 User 作用域事实。Workspace 作用域文档位于
`<HOME>/workspaces/<workspace_key>/MEMORY.md`；它刻意放在仓库之外，避免记忆事实意外变成
一次 commit。两者都是带稳定条目 ID 和 content hash 的有界托管 Markdown 文档。见
[Memory](../extensions/memory.zh-CN.md)。

### User 状态 mutation 锁

Core 会在线程和进程之间串行化 `config.yaml`、`.env`、`USER.md` 以及每个
Workspace `MEMORY.md` 的 read-modify-write 事务。它使用一个持久的单字节 sibling
`.<resource>.lock`；已经隐藏的 `.env` 使用 `.env.lock`，不会再增加一个前导点。
等待这些锁有明确上限，且不在 event-loop 线程中运行，因此另一进程不会冻结
foreground cancellation 或状态渲染。已取消的 mutation 会先在有界清理窗口内完成
已经开始的文件系统事务，再报告取消；错过该窗口的 worker 不能之后再提交内存状态。
在之后的进程重新加载持久文件前，必须把该 worker 的文件系统 outcome 视为不确定。

等待锁达到 deadline 时，Command 和 credential RPC 会返回可重试的
`operation_busy`，Memory tool 调用会返回可重试的 `timeout`。不安全或不可用的
sidecar/平台锁有两种类型化 envelope：Application command 或 RPC 返回可重试的
`state_unavailable`，并携带有界的 `state_directory` metadata；Memory tool 则返回
不可重试的 `state_unavailable` `ToolOutput`。这些错误是固定且脱敏的，不会暴露
sidecar 路径或操作系统异常。

这些 sidecar 是协调 artifact，不是配置或 Memory 内容。只要 Awesome 进程可能仍在运行，
就不要编辑或删除它们。第一次 mutation 前缺失它们是正常的；Core 会按需创建，
并拒绝 link、reparse point、非常规文件，以及打开后 identity 与路径不匹配的 sidecar。

## Workspace 拥有的文件

活动 working directory 会解析为 canonical directory，并绑定其 filesystem identity。它的
不透明 key 为 `ws_` 加 32 个从规范化 canonical path 派生的十六进制字符。该 key 避免在
次级存储名称中放入原始路径；单独捕获的 root identity 则能检测会话期间被替换的路径。

Awesome 识别以下由仓库控制的输入：

```text
<workspace>/
├── AGENTS.md
└── .awesome/
    ├── config.yaml
    └── skills/
        └── <name>/
            ├── SKILL.md
            └── ... resources
```

Workspace 获得信任前，不会打开其中任何文件。`AGENTS.md` 是有界、不可变的会话快照，
Workspace Skills 会接受逐组成部分 anti-link 与身份检查。运行会话期间对磁盘作出的更改不属于
受支持的 hot-reload 机制。这不意味着每个 Skill resource 都是 discovery-time snapshot：
在 lazy read 前安全完成的资源替换可以被观察到，而固定的 package/`SKILL.md` 替换与不安全
资源遍历会安全失败（fail closed）。

`.awesome/config.yaml` 会在信任后经过 schema 验证，但当前文件读取没有绑定 identity 或限制
大小，并且可能跟随 link/reparse point。这是已知安全加固缺口，不具备与 `AGENTS.md` 或
Workspace Skill 加载相同的保证。请把受信任 Workspace 配置当作特权输入，并参阅
[配置参考](configuration.zh-CN.md#workspace-配置)。

## Application database

`<HOME>/state/application.db` 是权威的嵌入式 Application SQLite database。当前
`PRAGMA user_version` 是 **7**。一个进程级有界 FIFO worker 持有其长期 connection；该
connection 会启用 foreign key、五秒 busy timeout、WAL journal mode 和 normal synchronous
mode。面向 Application 的 repository 暴露 async method：read 使用 deferred transaction，
write 使用 `BEGIN IMMEDIATE`。取消的 read 可以停止等待；已准入的 durable write 与 lifecycle
operation 会等到明确的 COMMIT、ROLLBACK 或 close 结果，再重新抛出第一次 cancellation。
SQLite connection、cursor 和 row 不会跨越 worker 边界。

其逻辑所有权为：

| 记录 | 用途 |
| --- | --- |
| `trusted_workspaces` | 已接受的 workspace key、canonical path 和信任时间 |
| `threads` | Workspace 关联、title/source、已选 model、Thinking 和 Skill mode |
| `thread_entries` | 按顺序排列的持久 User 消息、Assistant 消息和直接命令 |
| `turns` | Turn 生命周期、不可变执行选择/预算、usage、context manifest 和 checkpoint key |
| `thread_summaries` | 有界对话摘要及其覆盖的 sequence/count |
| `tool_activities` | 每个 operation/call 一行终态审计，不含原始参数/结果正文 |
| `change_sets` | Change 生命周期、可逆性、摘要和所有权 |
| `pending_mutations` | 用于 reconciliation 的 write-ahead mutation intent |
| `mcp_enablements` | 与配置 hash 绑定的 Workspace server 审批 |

Slash Command 是控制输入，不是模型对话条目。直接命令是持久 transcript 条目，但没有 Turn
ID。Tool activity 有唯一的 `(operation_id, call_id)` 边界，因此 completion 无法被静默复制。

不要手动编辑此 database。Row invariant、foreign key、Checkpoint store 和 Change Journal
blob 虽使用不同文件，却共同组成一份恢复契约。

## Provider model transaction journal

`<HOME>/state/provider-model-transaction.json` 用于闭合 `config.yaml` 中 default model 与
`application.db` 中 Thread selected model 之间的原子性缺口。这两类资源无法加入同一个
database transaction。因此 model mutation 会先写入包含旧值与目标 model identity 的持久
`prepared` 记录；该记录还带有唯一 transaction identity。随后系统替换并 reload 配置、
更新 Thread、验证两侧状态，将记录改为 `committed`，最后才移除 journal。若 callback
失败，系统会保留 `prepared` 证据，直到 SQLite 明确完成 rollback，且新的 transaction
重新验证两侧旧值。

启动时，`prepared` 记录会回滚到旧值，`committed` 记录会向前完成到目标值。
Reconciliation 是幂等的，只有两侧均验证通过后才清除 journal。journal 格式错误或无法恢复
时，activation 会以 `recovery_required` 失败；若 runtime 检测到同一状态，则会阻止新的
operation 与状态 mutation，但 snapshot 读取、取消和 shutdown 仍可使用。

journal 使用严格、有界的 UTF-8 JSON，且绝不包含 credential。Core 会拒绝 linked/reparse
parent、symlink/reparse file、hard link 或非常规文件、打开期间 identity 变化、重复 key、
非有限 JSON 值以及超过 4 KiB 的内容。不要编辑或删除此文件：它的存在表示恢复 intent，
而不是可随意丢弃的 cache 状态。

## Provider credential 事务文件

`/auth` 可能需要同时修改完整的 `<HOME>/.env` 文档，以及 `<HOME>/config.yaml` 中选中的
credential source。两个文件不能共享一次文件系统 commit，因此 Core 在 `<HOME>` 根目录用
两个隐藏文件协调：

- `.provider-credential-transaction.json` 是严格且不含 secret 的 journal，只记录 service、
  action、phase、source 选择和整文件 hash；
- `.provider-credential-transaction.env` 是之前 `.env` 的逐字节完整备份，包括注释和无关
  条目。

系统先暂存 backup，再发布 `PREPARED`。启动时，`PREPARED` 和 `SECRET_COMMITTED` 都会通过
恢复完整的旧 `.env` 与旧 source 来回滚；只有目标 `.env` hash 匹配时，`COMMITTED` 才会
向前收敛到目标 source。只有两个持久事实都验证通过后才删除这两个文件。Reconciliation
发生在首次真实配置加载、state preflight/reset 和 Workspace trust 处理之前，因此半写入的
secret 无法影响启动。

JSON 文件上限为 4 KiB，且绝不存储 credential。Backup 上限为 1 MiB，其中包含 secret，
在 POSIX 上仅 owner 可读写。两者都会拒绝 symlink、reparse point、hard link、非常规文件和
打开期间的 identity drift。`.env` 同样是有界的严格 UTF-8 输入；NUL 字节和不安全文件
identity 会 fail closed。不要手动删除任何一个事务文件。记录无效或不一致时，系统会产生
`recovery_required`，而不会猜测哪次写入成功。

## LangGraph checkpoint

`<HOME>/state/checkpoints.db` 由 LangGraph SQLite saver 拥有。Turn ID 同时也是其 checkpoint
key。恢复时，Awesome 把最新 checkpoint 投影到一组封闭的 `AgentState` channel，只允许
LangGraph 的内部 `branch:to:` channel 额外出现，然后验证 Thread、Turn、Workspace、
Provider、model、预算、continuation、工具进度、usage 和 termination 字段。

Checkpoint 与 Application table 分离，可以让第三方 saver layout 与产品 schema 隔离，
Turn 记录则提供 join。Checkpoint 缺失或损坏时，Awesome 绝不会杜撰 continuation 状态；
它会产生恢复错误/决定。

## Change Journal blob

`<HOME>/state/change-journal/blobs/<first-two-hex>/<sha256>` 存储 diff、undo、redo 和 crash
reconciliation 所需、按内容寻址的 before/after byte。写入使用 temporary-file-plus-replace，
读取会在返回内容前重新计算 digest。Metadata 和 pending intent 位于 `application.db`；两部分
单独存在时，都不足以提供完整 undo 历史。

每个 ChangeSet 限制为 1,000 个 node 和 50 MiB。Shell 执行会记录为不可逆 observation，
而不是虚构的 filesystem snapshot。见[变更指南](../user-guide/changes.zh-CN.md)。

## Lease 与多个会话

Awesome 对一字节 `.state.lock` 文件使用 non-blocking filesystem lock：

- `<HOME>/.state.lock` 由普通会话共享，在初始化/重置 Application 状态时独占；
- `<HOME>/.workspace-leases/<workspace_key>/.state.lock` 防止两个存活 runtime 拥有同一个
  canonical Workspace path；
- `<HOME>/.workspace-entity-leases/<entity_key>/.state.lock` 还绑定底层 directory identity，
  覆盖 path alias 和 replacement race。

两个 Workspace lease 都必须持有。如果第二次获取失败，第一次会被释放。竞争进程会收到
`operation_busy`，而不是并发运行 recovery 或 mutation。这些 lock directory 是协调 artifact，
不是用户配置；Awesome 运行时不要删除它们。

## Schema 兼容性、迁移与重置

正常访问 database 前，Awesome 会执行只读预检：

| 观察到的状态 | 行为 |
| --- | --- |
| 没有 database，或 version 0 的空 SQLite | 在 exclusive lease 下初始化 schema 7，然后降级为 shared ownership。 |
| Schema 7 | 正常打开。 |
| Schema 1–6 | Migration 不可用；询问用户是否重置本地状态或退出。 |
| Schema 大于 7 | 拒绝并返回 `state_created_by_newer_version`。 |
| 非空 version 0、无效 SQLite 或未知格式 | 以未知/不可用状态拒绝。 |

生产 migration registry 的 floor 是 7、current 是 7，且没有 step。未来受支持的升级必须形成
一条相邻线性链。启动时先在 shared lease 下 preflight，再取得 exclusive state lease、重新检查
schema，并用 SQLite Backup API 创建
`<HOME>/state/application.db.pre-migration.bak`，然后在一个 transaction 内执行完整链。降级为
shared ownership 后才初始化 repository。

Migration 前会独立重新打开并检查 backup。任一步骤失败都会回滚全部 schema 与数据变更，
并保留 backup 供手动恢复。启动绝不会自动 restore 该 backup 或 reset 状态。更新、未知、损坏、
不可读和锁定状态都会 fail closed。

确认后，reset 会验证精确的 `<HOME>/state` 边界不是 symlink，将其重命名到同父 staging
directory，初始化新的 `application.db`，然后移除 staging。初始化失败会恢复旧目录。清理失败
也会尝试恢复并报告有界诊断。

Reset 会移除：

- conversation、Thread、summary 和 usage；
- Workspace 信任与 Workspace MCP enablement；
- checkpoint；
- ChangeSet、undo/redo 历史和 blob。

Reset 保留 `<HOME>/state` 之外的一切：`config.yaml`、`.env`、Provider credential 事务
journal 与 backup、`ui.json`、User Skills、Local Memory 文档/设置和已安装 release。
把 credential 恢复证据放在可重置 namespace 之外，可防止 state reset 抹去一项尚未解决的
跨文件事务。Mem0 已存储的 Cloud Memory 记录位于外部，不会被本地 reset 删除。

## 备份与恢复

要获得一致的离线备份：

1. 退出使用该 `AWESOME_HOME` 的每一个 Awesome 会话；
2. 复制完整 `<HOME>` 目录，包括隐藏文件和整个 `state` 目录；
3. 记录创建它时使用的 Awesome 产品版本；
4. 将备份作为秘密保护，因为它包含 `.env`。

只复制 `application.db` 可能遗漏 WAL 内容、checkpoint 或 Change Journal blob，不是受支持的
一致备份。基于同样原因，应把整个已停止状态快照作为一个单元恢复，并使用接受其 Application
schema 的产品版本。如果只需要 preferences 或 Skills，请单独复制这些用户拥有的文件，并
有意排除 `.env`。

`application.db.pre-migration.bak` 是能够感知 WAL、供手动 migration recovery 使用的安全
快照，而不是完整 Awesome backup：它不包含 checkpoint database、Change Journal blob、
配置或凭据。
