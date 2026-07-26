# 存储与恢复

Awesome 将产品状态保存在解析后的 `AWESOME_HOME` 本地目录。之所以使用嵌入式存储，
是因为终端 coding agent 需要确定性的所有权和离线启动，而不是另行运维一项服务。
设计仍按语义分隔状态：产品记录、图 checkpoint、可逆文件 blob、配置、secret、memory
和 UI 偏好拥有不同的所有者与恢复规则。

## 状态布局与所有者

| 路径 | 所有者 | 内容 | 生命周期 |
| --- | --- | --- | --- |
| `state/application.db` | Application Storage | trust、Thread、entry、Turn、summary、tool activity、ChangeSet、pending mutation、MCP enablement | 持久化本地历史 |
| `state/checkpoints.db` | LangGraph adapter | 未完成的 `AgentState` channel | 仅未完成 Turn |
| `state/change-journal/` | Change Journal | 按内容寻址的 before/after blob | 被引用期间 |
| `config.yaml` | Config | 用户管理的非 secret 配置 | 由用户控制 |
| `.env` | Config secret loader | 显式持久化的凭据 | 由用户控制 |
| `memory/USER.md` | local Memory | 可选用户事实 | 由用户控制 |
| `workspaces/<key>/MEMORY.md` | local Memory | 可选工作区事实 | 限定于工作区 |
| `skills/` | Skills | 用户扩展 package | 由用户控制 |
| `ui.json` | Ink | 主题和本地展示偏好 | 由用户控制 |
| 工作区文件 | 用户和工具 | 主要项目工作 | 项目生命周期 |

可重置边界恰好是 `<AWESOME_HOME>/state`。配置、凭据、Memory、Skills、UI 偏好和项目文件
都位于边界之外。

## 为什么使用两个 SQLite 数据库

Application SQLite 存储产品事实：用户接受了一条消息、Turn 正在运行、回答已完成，或
ChangeSet 已应用。LangGraph 原生 SQLite saver 存储恢复执行所需的图 channel。两个
所有者都不会复制对方的内部状态。

分开数据库可以避免产品 schema 与框架 checkpoint 形态耦合，也允许删除终态 checkpoint
而不删除历史。代价是产品完成与 checkpoint 删除不能共享事务；恢复会显式收敛这一
提交窗口。

## Application 数据库事务

Application SQLite 使用 WAL。多查询读取使用 deferred transaction，使一个 Thread
页面能观察一致快照，同时不预留唯一 writer。Mutation 使用 `BEGIN IMMEDIATE`，在校验
和改变产品状态前先取得 writer reservation。

一个重要事务是接受首条消息：

```text
BEGIN IMMEDIATE
  -> update automatic Thread title
  -> append user transcript entry
  -> create running Turn
COMMIT
```

任一写入失败，三个事实都不存在。一旦接受，后续模型失败或取消会终止 Turn，但不会
抹去用户消息或标题更新。

原始 provider payload、token delta、spinner、无界 shell 输出和凭据值都不是产品历史。
Application 数据库存储有界 activity summary。未完成执行所需的完整 observation 保留在
checkpoint 中。

## Schema 身份

Application schema 身份与产品版本相互独立。当前 bootstrap schema 是 Schema 7。只有
当所需 table、payload 解释或跨记录不变量无法被当前代码安全读取时，schema 身份才会
改变，并且只单调递增。

当缺失具有安全的旧记录含义时，可选字段可以留在同一个 schema 中。Change mutation
身份以及分离的 before/after node type 遵循这一规则。没有 mutation ID 的旧版已完成
记录仍可读取；崩溃窗口中语义不明的证据仍保持 pending，供诊断使用。

Awesome 有意不提供通用迁移框架或历史 adapter chain。这会减少隐式兼容行为，却意味着
旧 schema 必须显式重置，而不能自动迁移。

## 只读启动预检

建立信任、访问 checkpoint/Change Journal 或进行可写数据库设置之前，Storage 会对现有
Application 数据库分类：

| 分类 | 启动行为 |
| --- | --- |
| new | 在独占 state lease 下创建当前 schema |
| current | 保留共享 state lease 并继续 |
| older | 展示 reset-or-exit interaction |
| newer | 停止并要求用户升级 |
| unknown/corrupt/unreadable/locked | 停止并显示诊断 |

只有旧 schema 提供 reset。若把更新或未知 schema 当作可丢弃内容，可能会破坏当前 binary
只是尚不能理解的状态。

## State lease 与重置

普通会话持有共享跨进程 state lease。重置需要独占 lease，因此不能与另一个配合协议的
Awesome 会话竞态。

```text
typed state-reset confirmation
  -> bootstrap lock
  -> foreground resolving lease
  -> acquire exclusive state lease
  -> validate target == <AWESOME_HOME>/state
  -> atomically rename old state directory
  -> create and validate fresh Schema 7
  -> remove replaced state
  -> downgrade to shared lease
  -> continue to workspace trust
```

如果全新初始化失败，Storage 会恢复原目录。Reset 会移除 trust、conversation、checkpoint
和 Change Journal 历史；它保留 `state` 外的所有路径。

原子替换描述的是规范文件系统 namespace，而不是撤销不配合协议的 OS handle。在 Windows
上，打开的数据库 handle 通常会阻止 rename。在 POSIX 上，rename/unlink 可能成功，
而先前已有的 handle 仍连接到已分离的旧 inode；通过规范路径重新打开时只会看到新状态。
State lease 是配合协议的进程契约。

## 工作区会话 lease

激活时会持有两个独占 lease：

- 规范工作区拼写对应的 path-key lease；
- 从已打开根目录身份派生的 entity-key lease。

路径名被替换后，前者仍保持稳定；后者会把打开到同一个目录的 alias 折叠到一起。二者
共同防止另一个 Core 通过替换路径或使用其他拼写，把活动会话误认为需要崩溃恢复。

激活和文件访问前都会重新校验工作区身份。若根对象发生变化，会话会失败，而不是在旧
决策下信任新对象。

## Turn checkpoint 契约

LangGraph checkpoint 以 Turn ID 为 key。可恢复 checkpoint 包含严格 `AgentState`，
其中包括 identity、budget、message、冻结上下文 manifest、pending tool progress、usage
和 termination fact。

恢复校验远不止“JSON 可以解析”：

- Thread、Turn、workspace、provider、model 和 Thinking 身份；
- 对照产品 Turn 配置预算校验计数器值；
- message role、tool-call/result 顺序和活动尾部索引；
- 上下文 manifest 形态、内容 hash、token 估算和 transcript 覆盖；
- final answer 与 termination 字段能否构成合法状态；
- pending tool 能否表示结果不确定的外部操作。

无效 checkpoint 会以稳定错误码使对应 Turn 失败。系统绝不会通过猜测缺失的图 transition
来修复它。

## 跨数据库收敛

Application 数据库存储冻结上下文 manifest 投影，因为它是产品可见事实。Checkpoint
存储同一 manifest，因为图必须由此恢复。崩溃可能导致其中一个先提交。

对于未完成 Turn，最新且严格有效的 checkpoint 是事实来源。只有满足以下条件时，其投影
才能更新 Application 状态：

1. 产品投影为空，或其不可变 source anchor 具有相同 lineage；
2. 当前 row 仍等于预期旧值；
3. 候选状态通过完整 Turn 与上下文校验。

```text
read unfinished Turn + latest checkpoint
  -> validate checkpoint identity and graph invariants
  -> compare frozen source anchors
  -> compare-and-swap Application projection
  -> finalize, resume, ask, or fail
```

并发第三个值会产生 `context_snapshot_conflict`。恢复会继续处理其他 Turn，而不会把一个
损坏记录当成全局数据库失败。

这是崩溃收敛，而不是对恶意本地状态的证明。如果攻击者能同时替换 checkpoint 内容和
匹配的 hash，系统没有第二个外部权威来认证它们。

## 恢复决策

Coordinator 会对未完成 Turn 分类：

- 已完成且有效的图状态：完成产品持久化并删除 checkpoint；
- 有效未完成状态：提供或执行有界 resume 流程；
- 不确定的 `execute` 或 MCP 边界：要求显式 Abort/Retry，并把 Abort 放在首位；
- 缺失、损坏、不一致或不可恢复状态：以稳定诊断标记失败；
- 属于已终态 Turn 的 checkpoint：移除残留 checkpoint。

打开 Thread 并不隐含 Retry。重放不确定的 shell 或 MCP 调用可能复制外部作用，因此选择
必须绑定到该 Thread 和 Turn，并显式作出。

## Change Journal 持久性

Change metadata 与 pending intent 位于 Application SQLite；blob 位于
`state/change-journal`；作用发生在项目文件系统。这三个位置不能共享同一个事务。

Journal 会在发布 blob ID 前写入内容 blob，在 mutation 前持久化 intent，校验结果，
再记录已提交 change。Undo 和 redo 会在第一次 restore 前持久化所有 intent，并在所有
restore 成功后提交一次 lifecycle transition。启动校正利用 pending evidence 去完成或
回滚它能证明的状态。

SQLite 使用 WAL 和 `synchronous=NORMAL`。Blob 文件会在替换前同步，但数据库、blob
目录和工作区之间没有共享 directory-fsync 边界。突然断电可能留下保守 pending conflict
或不可恢复的持久性缺口。Journal 保证普通进程崩溃校正，不声称实现整机断电原子性。

## 失败与恢复表

| 失败点 | 保留的证据 | 恢复方式 |
| --- | --- | --- |
| Turn 事务提交前 | 无首条消息事实 | 可以重试 submit |
| running Turn 提交后、checkpoint 前 | 产品 Turn 没有有效 checkpoint | 以稳定恢复错误码失败 |
| checkpoint 提交但投影未提交 | 已校验的冻结 manifest | 受 lineage 约束的 compare-and-swap |
| answer 持久化后 checkpoint 仍存在 | 终态产品 Turn | 移除残留 checkpoint |
| mutation intent 持久化，作用不确定 | PendingMutation + blob | 校验、完成或回滚 |
| shell/MCP transport 调度后失败 | 保守 observation / 不确定工具状态 | 显式 Abort 或 Retry |
| state reset 的全新初始化失败 | 已改名的原目录 | 恢复原 namespace |

## 设计取舍

- 嵌入式 SQLite 消除了服务运维，却使本地文件所有权和锁成为产品契约的一部分。
- 产品/checkpoint 数据库分离保持边界，但要求严格收敛。
- 显式破坏性 reset 不如 migration 方便，却避免静默重新解释状态。
- WAL 与 `synchronous=NORMAL` 偏向交互性能，不宣称在数据库和工作区文件之间具有断电
  原子性。
- 保守 pending evidence 可能需要人工诊断；删除它会抹去不确定 mutation 的唯一证据。

## 源代码与测试索引

- 数据库 schema：`storage/database.py`
- Conversation 与 trust：`storage/conversations.py`、`storage/trust.py`
- Checkpoint：`storage/checkpoints.py`
- 兼容与重置：`storage/compatibility.py`、`storage/state_recovery.py`
- 跨进程 lease：`storage/state_lease.py`
- 变更持久化：`storage/changes.py`、`core/changes/`
- Turn 恢复：`application/turns.py`
- 测试：`tests/unit/storage/`、`tests/integration/test_sqlite_checkpoints.py`、
  `tests/integration/test_agent_recovery.py`、
  `tests/integration/test_state_reset_concurrency.py`、
  `tests/structural/test_storage_architecture.py`
