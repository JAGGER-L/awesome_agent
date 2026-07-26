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
├── .state.lock
├── config.yaml
├── ui.json
├── skills/
├── memory/
│   └── USER.md
├── workspaces/
│   └── <workspace_key>/
│       └── MEMORY.md
├── state/
│   ├── application.db
│   ├── checkpoints.db
│   └── change-journal/
│       └── blobs/
├── .workspace-leases/
│   └── <workspace_key>/.state.lock
└── .workspace-entity-leases/
    └── <entity_key>/.state.lock
```

目录和文件按需创建。它们不存在通常是正常默认状态，并非损坏。

## 用户拥有的文件

### `<HOME>/config.yaml`

严格的 User 配置 schema 版本 `1`：Provider 默认值、凭据来源选择、预算、Memory 开关、
禁用的 Skills 和 User MCP 声明。它不包含秘密值。见[配置](configuration.zh-CN.md)。

### `<HOME>/.env`

由 Awesome 管理的 `DEEPSEEK_API_KEY`、`MOONSHOT_API_KEY` 和 `MEM0_API_KEY` 凭据存储。
`/auth` 通过同目录 temporary file 写入、flush，然后以原子方式替换目标。在 POSIX 上，
Awesome 创建目录时只允许 owner 访问，创建文件时只允许 owner 读写。

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
`PRAGMA user_version` 是 **7**。连接会启用 foreign key、五秒 busy timeout、WAL journal
mode 和 normal synchronous mode。

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

## Schema 兼容性与重置

正常访问 database 前，Awesome 会执行只读预检：

| 观察到的状态 | 行为 |
| --- | --- |
| 没有 database，或 version 0 的空 SQLite | 在 exclusive lease 下初始化 schema 7，然后降级为 shared ownership。 |
| Schema 7 | 正常打开。 |
| Schema 1–6 | 询问用户是否重置本地状态；不执行自动 migration。 |
| Schema 大于 7 | 拒绝并返回 `state_created_by_newer_version`。 |
| 非空 version 0、无效 SQLite 或未知格式 | 以未知/不可用状态拒绝。 |

本 release 刻意没有原地 database migration layer。重置必须显式进行，因为静默解释旧恢复数据
可能比丢失本地对话历史更危险。

确认后，reset 会验证精确的 `<HOME>/state` 边界不是 symlink，将其重命名到同父 staging
directory，初始化新的 `application.db`，然后移除 staging。初始化失败会恢复旧目录。清理失败
也会尝试恢复并报告有界诊断。

Reset 会移除：

- conversation、Thread、summary 和 usage；
- Workspace 信任与 Workspace MCP enablement；
- checkpoint；
- ChangeSet、undo/redo 历史和 blob。

Reset 保留 `<HOME>/state` 之外的一切：`config.yaml`、`.env`、`ui.json`、User Skills、
Local Memory 文档/设置，以及已安装 release。Mem0 已存储的 Cloud Memory 记录位于外部，
不会被本地 reset 删除。

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
