# 请求生命周期

Awesome 只有一个产品界面，却有多种请求。它们共享身份、准入、事件、取消和关闭规则；
但并非全部都会成为 Agent Turn。

## 生命周期术语

| 术语 | 含义 | 是否持久化？ |
| --- | --- | --- |
| Workspace | 规范项目根目录加稳定 workspace key | 信任与历史会持久化 |
| Thread | 已选择的对话及其生效设置 | 是 |
| Turn | 一个已接受的自然语言请求及其结果 | 是 |
| Operation | 一个前台 Turn 或直接 shell 执行 | 身份属于会话；生命周期事件为实时事件 |
| Exclusive action | 改变状态的命令、凭据变更、interaction 决议或 shutdown | 取决于 action |
| Interaction | 继续执行前所需的类型化用户决策 | pending 状态属于会话 |
| ChangeSet | 与工作关联的文件 mutation 和保守 shell observation | 是 |

Foreground arbiter 同一时刻只准入一个 Operation、exclusive action 或 interaction
resolution。准入必须发生在 Turn 持久化之前；否则竞态失败方会留下从未运行的幽灵 Turn。
决定性的 pending-interaction 检查会在取得 Operation lease 后、任何异步 preflight 之后执行。
只有 recovery resume 可以携带 continuation，并且必须精确匹配当前 interaction ID、generation、
Thread 和 Turn。

## 输入、输出与不变量

| 请求 | 权威输入 | 成功输出 | 持久化作用 |
| --- | --- | --- | --- |
| initialize | 工作区和兼容客户端身份 | ready 状态或类型化 bootstrap interaction | 接受时设置 trust/state |
| Turn | Thread、自然语言输入、client message ID | Operation ID、事件、最终 transcript | Turn、entry、usage、ChangeSet |
| 直接命令 | Thread 和命令 | Operation ID 与有界 transcript entry | direct entry、audit、ChangeSet |
| 斜杠命令 | 类型化 `CommandIntent` | 可辨识 outcome | 取决于命令 |
| interaction | interaction ID 和允许的决策 | accepted/rejected/stale 状态 | 取决于决策 |
| cancellation | 活动 Operation ID | 取消确认 | 终态事实与已知变更 |
| shutdown | 空请求 | stopped 确认 | 干净地关闭资源 |

所有生命周期都显式携带身份、不准入第二个前台 mutation、对预期 busy/failure 使用类型化
状态、传播取消，并且绝不在没有用户决策的情况下重放结果不确定的外部 action。

## 启动与信任

```text
awesome [workspace]
  -> Ink starts one private awesome-core
  -> initialize(protocol=3, client identity)
  -> resolve candidate workspace identity
  -> shared-lease read-only state preflight
  -> if migration_required:
       exclusive lease -> recheck -> SQLite backup -> one transaction
       -> downgrade to shared lease -> initialize repositories
  -> trust lookup
  -> trust decision, if required
  -> after acceptance/already trusted: acquire path + entity leases
  -> recheck workspace root identity under those leases
  -> load project-controlled configuration and extensions
  -> snapshot root AGENTS.md
  -> reconcile unfinished Turns
  -> select/create Thread
  -> ready ApplicationState
```

这个顺序与安全有关。信任前可以检查状态兼容性，因为该过程不会读取项目指令。只有建立
信任后，才会激活工作区配置、Workspace Skills、MCP 声明、`AGENTS.md` 和工具。
不受信候选工作区不会在用户决策期间持有 workspace lease。接受信任（或已有信任记录）
后，系统取得两种 lease，并在激活前重新校验身份。拒绝信任会退出，且不会持久化为
denial。

生产 migration floor 与当前 schema 都是 7，并且没有注册 step。因此 Schema 1–6 会生成
类型化 reset-or-exit interaction。未来需要 migration 的 schema 会遵循上面的独占序列，并
保留 `application.db.pre-migration.bak` 供手动恢复。Migration 绝不会触发自动 reset 或
restore。更新、未知、损坏、不可读或锁定的状态都会安全停止，绝不会被静默删除。确认 reset
后，会在 bootstrap lock、前台 interaction-resolution lease 和独占跨进程 state lease 下执行。

进入 `ready` 前的失败会恢复或保持 Application-owned `ApplicationBootstrap` 的 non-ready
phase。协议握手只是该事实的 admission projection，并会保持关闭。状态分类详见
[存储与恢复](storage-and-recovery.zh-CN.md)，bootstrap admission 详见
[协议与 TUI](protocol-and-tui.zh-CN.md)。

## 自然语言 Turn

```text
turn.submit(thread_id, content, client_message_id)
  -> validate input and selected Thread
  -> reject a pending interaction
  -> validate configured provider
  -> reserve foreground Operation and revalidate pending interaction atomically
  -> begin Turn + append user entry in Application SQLite
  -> prepare immutable per-Turn inputs
  -> start Operation task and emit operation.started
  -> invoke compiled Agent graph with thread_id == Turn ID
       -> prepare context
       -> call model
       -> execute zero or more tools
       -> finalize answer
  -> complete/cancel/fail product Turn
  -> attempt to seal its ChangeSet
  -> attempt to remove terminal checkpoint
  -> emit exactly one Operation terminal event
  -> release foreground lease
```

主要终态事实之后的本地 durable cleanup 会保持所有权，直到结果明确。清理失败不会改写
已完成、已取消或失败的 Turn；启动校正可以重试残留 checkpoint 的清理。外部进程清理与
best-effort event publication 仍使用有界 deadline。

由 worker 持有的 durable Turn transition 与 Operation phase 共同构成一个 commit point。在它之前，
cancellation 胜出；在它之后，cancellation 会被拒绝，有界 terminal publication 会保留已经
提交的 completed 或 failed outcome。Shutdown 观察同一 phase，只会等待而不会发出第二次
cancellation。

TUI 的 `client_message_id` 将乐观显示的消息与权威的已准入 Turn 关联。`operation_id`、
`thread_id` 和 `turn_id` 把事件绑定到同一次执行。TUI 侧的 Thread generation 会防止
前一个选中项的迟到事件改变新界面。

第一条被接受的消息会在同一个 Application 事务中更新自动标题、追加 user entry 并创建
Turn。后续模型失败不会抹去已接受的用户消息，而会显式终止该 Turn。

### 工具审批是 continuation

当 policy 返回 `ASK` 时，Tool Executor 创建一个绑定到活动 Thread、Turn、Operation
和 permission generation 的 interaction。Operation 在等待响应期间仍是所有者：

```text
Agent tool call
  -> policy asks
  -> interaction.required
  -> TUI returns interaction.respond
  -> verify interaction + Thread + Turn + Operation identities
  -> allow once / grant thread writes / deny
  -> continue the same Tool call
```

工具审批会有意绕过普通 exclusive admission。若取得第二个 foreground lease，Operation
会在等待自己的决策时死锁。其他所有 interaction resolution 都使用 resolving lease。

## 直接 shell 命令

以 `!` 开头的输入不会创建 Agent Turn，也绝不会隐藏在模型提示词中。它仍会创建前台
Operation 和打开的 ChangeSet：

```text
! command
  -> direct.execute
  -> validate Thread and pending interaction
  -> reserve Operation
  -> ToolExecutor(execute, origin=direct)
       -> strict-validate registered execute arguments
       -> registered hard admission
       -> typed description exactly once
       -> emit tool.started
       -> Direct capability policy
       -> deadline + Process Runner + bounded process cleanup
  -> bounded transcript entry + ChangeSet observation
  -> seal ChangeSet and emit terminal Operation event
```

直接执行与 Agent `execute` 调用使用相同的注册 admission、命令 policy、process runner、
脱敏、deadline、取消和审计路径。即使它有 ChangeSet，也不代表执行变得可逆；任意 shell
副作用仍不受管理。

它与选中 Thread 的 permission mode 无关。用户输入的确切 `! command` 本身就是授权，
因此 Application 为该 Direct Operation 提供自己的 Full-access permission session，
不会打开普通 shell 审批 interaction。Hard denial 和上文所有执行/清理边界仍然有效。

Transcript 持久化与 ChangeSet 封存都是保留主 outcome 的 finalizer。如果执行已经
失败或取消，后续持久化或封存失败会被报告，但不会取代原始异常。因此即使两个
finalizer 都失败，已取消的 Direct Operation 仍发出 `operation.cancelled`；针对成功、
失败和取消路径都有聚焦回归测试。

## 斜杠命令

斜杠命令是确定性的产品操作，绝不会提交自然语言 Turn：

```text
/command
  -> Ink parser and owner catalog
  -> Ink-owned command: local presentation action
     or
  -> command.execute
  -> immutable Core CommandDispatcher
  -> focused command service
  -> discriminated CommandOutcome
  -> optional authoritative state effect
  -> exhaustive Presenter
```

一个 Operation 活动期间，只有以下无副作用观察可以穿过 Core gate：

- `/context`
- `/workspace`
- `/tools`
- `/mcp` 和 `/mcp status [id]`
- `/status`
- `/usage`
- `/config`

`/diff` 被排除，因为它会读取正在变化的 ChangeSet；`/doctor` 被排除，因为诊断可能联系
提供商。Pending interaction 允许相同的观察，但阻止状态变更。该白名单是 Core/私有协议
能力。当前 Ink composer 在 Operation 活动时连白名单输入也不会立即调度，而是把所有已
提交输入排队等待后续提升。该 queue 只属于展示层，不会创建第二个 Core scheduler。

## 权限变更

Request approval 与 Accept edits 可以通过 exclusive command 应用。选择 Full access 时，
会先创建一个安全默认的确认，并绑定到已选 Thread 和当前 permission generation：

```text
/permissions full_access
  -> exclusive command lease
  -> create Full access confirmation
     default: keep current mode
  -> release command lease
  -> interaction.respond
  -> resolving lease
  -> recheck selected Thread + permission generation
  -> emit resolved event
  -> apply Full access only for a matching confirmation
```

切换 Thread 或 mode 会使旧确认失效并清空临时 capability grant。即使在 Full access 下，
MCP 和未知扩展能力仍需逐次审批。

## 取消

`operation.cancel` 指向一个 Operation ID。取消通过 Operation task 传播到模型流或 Tool
Executor。对于 Turn，Application 随后记录取消事实，并继续持有 foreground ownership，直到
本地 ToolActivity、transcript、ChangeSet sealing 和 checkpoint deletion 得到明确结果；之后
才发出 `operation.cancelled` 并释放 lease。清理失败不会取代已取消这一终态事实，并可在
启动校正期间重试。对于 shell 进程，Process Runner 执行有界进程树与 pipe 清理，再重新抛出
原始 `CancelledError`。Direct transcript 与 ChangeSet finalizer 会在该取消穿过 Application
边界时继续保留它作为主 outcome。Event delivery 是有界 best-effort，不会缩短本地 durable
ownership。

True cancellation acknowledgement 表示匹配的 Operation 尚未越过 commit point；这包括
`operation.started` 已可见但 acceptance response 尚未返回的窗口。在这个 starting 窗口中，
cancellation 会阻止 factory 启动。Completion 或 failure 一旦 committed，cancellation 会返回
false，且不能替换其 terminal event。

取消不是回滚。Journal 已捕获的文件变更仍然可见，之后可以检查或撤销。外部 shell 或
MCP 副作用可能已经发生。

## Resume 与崩溃恢复

启动过程会将每个非终态产品 Turn 与其 checkpoint 对齐：

```text
unfinished Turn + checkpoint
  |-- completed valid graph state -> finalize product records
  |-- resumable + currently registered replayable tool -> resume
  |-- non-replayable, missing, or unknown metadata -> interaction: Abort | explicit Retry
  |-- missing, corrupt, or conflicting state -> fail Turn with stable code
```

Checkpoint 的冻结 manifest 只有通过 compare-and-swap，才能修复空的或 lineage 匹配的
Application 投影。自洽但无关的 checkpoint 不会被接受为权威。即使一个 Turn 恢复失败，
恢复过程仍会处理其他 Turn。

对于中断的工具调用，恢复流程会在当前 Runtime Registry 中查找同名工具，并使用该注册项的
replay-safety metadata；它不按具体工具名分支。经证明的本地 built-in 可以是 replayable。
MCP 为 non-replayable，metadata 缺失或未知时 fail closed。这些情况绝不会自动重试：
interaction 默认选择 Abort，而显式 Retry 可以继续旧 checkpoint 并重复 pending call。因此，
变更同名工具契约时必须考虑 checkpoint compatibility。

## Shutdown

Shutdown 会先关闭前台准入。它只在活动 Operation 仍处于 running 时取消该 Operation，等待
任何 committing Operation 和另一个 exclusive 所有者，再等待 arbiter idle。随后它按逆序
回收 workspace runtime，关闭 checkpoint saver，排空并关闭进程级 Application SQLite
worker，最后在 bootstrap lock 下释放 workspace 与 state lease。

```text
shutdown request
  -> foreground.begin_closing()
  -> cancel RUNNING Operation / await COMMITTING Operation
  -> cancel exclusive owner if external
  -> wait_idle()
  -> retire runtime (MCP, Mem0, providers)
  -> close checkpoint saver
  -> drain and close ApplicationSQLite
  -> release workspace and state leases
  -> mark Application closed
```

该顺序防止数据库 teardown 已开始后又启动新的 mutation。

## 失败语义

- 畸形输入在准入或持久化之前失败；
- 前台 busy 返回类型化、可重试的 busy 结果；
- 预期工具错误成为有界 Agent observation；
- 意外图/工具错误会显式终止 Operation；
- 一个 Operation 只产生一个终态生命周期事件；
- 结果不确定的外部作用绝不会自动重放；
- 持久化完成后事件交付失败会被记录，而不会改变已经完成的产品事实。

## 设计取舍

- 串行前台工作限制吞吐量，却让审批、ChangeSet、取消和恢复拥有唯一明确的所有者。
- 会话内 interaction 避免持久化过期提示，但重启后的会话必须从持久化事实重建恢复决策。
- TUI 输入排队改善交互流程，却不承诺 Core 侧并行。
- 显式不确定结果决策增加摩擦，以避免重复外部作用。

## 源代码与测试索引

- 准入：`application/foreground.py`、`application/operations.py`
- Turn 生命周期：`application/turns.py`、`conversation/service.py`
- 直接执行：`application/direct.py`
- 命令：`application/commands.py`、`application/dispatcher.py`
- Interaction：`application/interactions.py`、`application/composition.py`
- 恢复：`application/turns.py`、`storage/checkpoints.py`
- 测试：`tests/unit/application/`、`tests/integration/test_agent_recovery.py`、
  `tests/integration/test_recovery_interactions.py`、
  `tests/integration/test_state_reset_concurrency.py`
