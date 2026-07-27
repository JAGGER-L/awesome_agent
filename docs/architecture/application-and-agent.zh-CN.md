# Application 与 Agent

Awesome 将产品生命周期与推理执行分离。Application 回答的是：“这项工作能否开始，
以及它如何成为持久化的产品事实？”Agent 回答的是：“对于一个已经准入的 Turn，
接下来要发生哪一次模型/工具状态转移？”

如果合并这两类职责，协议问题会渗入图状态，图路由也会成为第二个产品调度器。
保持二者分离，才能得到唯一的取消点、唯一的操作权威和唯一的 checkpoint 所有者。

## 职责矩阵

| 关注点 | Application | Agent |
| --- | --- | --- |
| 工作区信任与激活 | 负责 | 不检查 |
| bootstrap phase 与 ready 前准入 | 负责 | 不检查 |
| 已选择的 Thread 与生效配置 | 负责 | 接收冻结值 |
| Turn 创建与终态 | 负责 | 返回图执行结果 |
| 前台准入与取消 | 负责 | 配合取消 |
| 交互展示与决议 | 负责 | 工具调用通过注入的 executor 等待 |
| 图拓扑与路由 | 调用 | 负责 |
| 提供商消息/工具链有效性 | 观察结果 | 负责 |
| 模型/工具/压缩预算 | 提供配置 | 负责计数器与路由 |
| 图 checkpoint | 协调生命周期 | LangGraph 写入图 channel |
| transcript 与有界 activity | 负责产品记录 | 产出待持久化事实 |
| 具体提供商/存储/扩展 | 组装 | 依赖中立契约 |

## Application 边界

`LocalApplication` 实现 `ApplicationFacade`，后者是唯一面向界面的产品 API。
它把预期的 `ApplicationFailure` 异常转换为类型化的 `ApplicationResult` 值。
具体组装仍隐藏在其 backend 之后；协议无法直接访问仓库、提供商或工具。

```text
Protocol dispatcher
  -> ApplicationFacade
  -> LocalApplication
  -> composed backend
       -> lifecycle/command service
       -> Conversation or Storage port
       -> compiled Agent graph
```

Application 的职责被有意拆分到聚焦的模块中：

- `facade.py`：稳定的界面契约与预期失败封装；
- `bootstrap.py`：bootstrap phase 转移与 ready 前准入；
- `composition.py`：激活与具体依赖装配；
- `foreground.py` 和 `operations.py`：原子的前台所有权；
- `turns.py`：Turn 执行、终结、取消与恢复；
- `dispatcher.py` 加命令服务：确定性的斜杠命令；
- `interactions.py`：类型化决策与权限绑定；
- `context.py`：每个 Turn 的上下文捕获与冻结 manifest 投影；
- `events.py`：投影到界面的产品事实。

`composition.py` 可能较大，因为它负责装配和启动顺序。它不能成为命令语义、图路由、
任意结果构造或展示格式化的容器。

### Bootstrap phase 所有权

`LocalApplication` 持有一个具体的 `ApplicationBootstrap`；Protocol 或 UI 组件都不能改变
`BootstrapPhase`。Coordinator 初始为 uninitialized。Initialize invocation 会在异步工作
开始前把它移到 initializing，然后消费类型化 `ApplicationResult[InitializeResult]`：ready、
trust-required 与 state-reset-required result 会选择对应 phase，失败或取消则恢复此前 phase。
进入 ready 后重复 initialize 的全过程仍保持 ready，因此界面重试不会关闭已经激活的
Application。

Coordinator 还会把 bootstrap interaction 绑定到精确 identity。只有类型化 interaction result
确认接受，并且 backend activation 已成功返回后，trust response 才能进入 ready。陈旧、
拒绝、失败或被取消的 response 都不能推进 phase。接受 state reset 后，Application 仍保持
non-ready，直到后续 initialize 完成。

界面在 dispatch 前向 Application 查询提供商中立的 admission decision。stdio Host 会把拒绝
映射成既有 Protocol v4 `-32002` diagnostic，但绝不维护第二套状态机，也不解析序列化的
request/result payload 来推断 readiness。Cancellation 与 shutdown 在每个 phase 都保持准入。
Protocol v4 的 wire shape、status value 和 error 语义仍由 Application 统一拥有。

### Workspace runtime 快照

受信激活成功后会发布唯一的不可变 `WorkspaceRuntime`。该快照包含解析后的配置与稳定的
workspace service graph：Conversation、Turn coordination、command service、Tool
Registry、Model Catalog、context 与 extension、memory 与 MCP，以及 Change Journal
service 和一个 `RuntimeResources` owner。Foreground ownership、pending interaction、
permission grant、recovery delivery 和进程 shutdown 等可变生命周期协调仍保留在
Application backend 上。

顶层请求会在 Application 持有的 request context 中只绑定一次 `_runtime`。被 await 的
callback 和由 foreground 持有的子 task 会继承这份不可变快照，不会通过多个 backend 字段
重新拼装依赖。Detached task 不会取得独立的 runtime reader lease，因此不是受支持的所有权
边界。发布遵循唯一顺序：
完全使用局部值构建 candidate，完成校验，在 workspace activation 时校正 startup state，
确认没有前台 Operation，再通过一次赋值更新 runtime 指针。随后针对已发布 candidate 发送
recovery 通知；通知失败会被报告，但不会恢复旧 runtime。

发布前已准入的请求继续看到旧 runtime，新请求则看到新 runtime。`RuntimeResources` 为每个
candidate 提供独立的 generation identity 和 reader 计数；retirement 会先排空该 generation，
再关闭其 `AsyncExitStack`，因此不会在暂停的 reader 下方关闭资源。退出栈持有可复用的
provider client、内部创建的 Mem0 client 和 MCP；注册顺序保证按 MCP、Mem0、provider 的
逆序恰好关闭一次。注入的 gateway 和 Mem0 对象只借用，Awesome 绝不关闭。Candidate 构建
失败或取消会关闭整个候选退出栈，但不改变旧 runtime 或请求权威。Candidate 构建会清除调用
方的 runtime binding，而 retirement 与 close task 使用干净 context，因此长期资源不会保留
旧 generation。Provider 与 credential mutation 会从已提交快照构建完整 candidate，但不重复
startup reconciliation；它把已选择的 Thread 静默带入 candidate，不触发 selection callback，
完成原子发布后，等绑定旧 runtime 的 mutation 请求退出再回收旧 runtime。资源关闭失败会被
报告，但不会覆盖 candidate 的主要失败或跳过进程清理。跨 runtime generation 复用的
checkpoint saver、一个进程级 `ApplicationSQLite` worker 和 state lease 始终由另一套进程
生命周期 Application `AsyncExitStack` 持有。Database worker 会串行执行面向 Application
的 repository 调用，而不会占用 event-loop thread。

### Application invocation diagnostics

诊断 sink 属于进程/会话级 Application 生命周期，而不属于 `WorkspaceRuntime`。因此，
runtime 发布不会让一次 invocation 跨越两个日志 owner，替换 workspace runtime 也不会关闭
writer。Writer 使用有界 queue，并在 caller 之外执行文件 I/O。Queue 已满或日志写入失败时
会 fail open：它可能丢失一条诊断记录，但不能延迟、使 Application invocation 失败或改变
其结果。

`ObservationalMiddleware` 为每个已完成的 facade invocation 记录一个经过 allowlist 的 JSON
object。字段包括 `version`、`timestamp`、`session_id`、`correlation_id`、`operation`、
`outcome` 和 `duration_ms`，以及可选的 `error_code` 与有界 `usage`。它不会序列化任意
argument、result、exception 或 event；尤其不会让 prompt、模型与 Tool 正文、query、URL、
path、secret 或任意 payload 进入该日志。

Invocation outcome 只描述 middleware 观测到的 facade 调用。有些调用会准入异步 Agent
工作，并在 Turn 到达终态之前返回。因此，成功的 invocation 不是 Agent Turn 成功记录；
Turn lifecycle event 与持久 Conversation state 才是该终态的权威来源。

## 前台串行化

`ForegroundArbiter` 有三类 lease：Operation、exclusive 和 resolving
interaction。它记录所属的 `asyncio.Task`，拒绝任何第二个所有者，并在开始关闭后拒绝
所有新 lease。

`OperationController.reserve()` 会在 Turn coordinator 能够持久化新 Turn 之前，
同步取得 lease。`start_reserved()` 发出 `operation.started`，启动唯一任务，并在
`finally` 中负责终态事件的交付和 lease 释放。

准入不是在取得 lease 之前执行的一次检查。`reserve()` 拿到 lease 后、发布活动 Operation
ID 前，会同步重新验证当前 pending interaction。普通 Turn 和 Direct command 要求没有
pending interaction。唯一例外是 recovery resume：它携带内部 continuation，并精确绑定当前
recovery interaction ID、interaction generation、Thread 和 Turn。陈旧或部分匹配的 token
不能退化为通用 bypass。

这个进程内 arbiter 与存储 lease 不同：

- 前台 lease 在单个 Core 会话内串行化语义工作；
- 状态 lease 跨 Core 进程协调状态替换；
- 工作区路径与实体 lease 防止两个会话把同一工作区 generation 当成彼此独立的
  恢复域。

它们都不是操作系统沙箱。

## Agent graph

`agent/graph.py` 是唯一导入并构建 LangGraph `StateGraph` 的模块。编译后的拓扑被
刻意保持得很小：

```text
START
  -> prepare_context
       | enough context
       +-----------------> call_model
       | compression needed
       +-> compress_context -> call_model | finalize

call_model
  | tool calls -> execute_one_tool --+
  | compression ----------------------|-> compress_context
  | answer or terminal budget --------+-> finalize -> END

execute_one_tool
  | more pending calls -> execute_one_tool
  + next model step ----> call_model
```

该图运行在 `AgentState` 上；这是一个严格的 checkpoint 契约，包含：

- Thread、Turn、工作区、提供商、模型和 Thinking 标识；
- 上下文 manifest、token 估算、生效上限和压缩请求；
- 提供商中立消息和 continuation 状态；
- 待处理工具调用、下一调用索引和结果；
- 模型/工具/Web/重试/压缩/活动时间计数器；
- usage、恢复问题、最终回答和终止原因。

`tool_results` 存储每个完整序列化的 `ToolResult`，包括其中由 Core 定义的最小
`Citation` tuple。每个 result 完成后，Agent 会在 `AgentState.citations` 中派生有序 Turn
快照；该快照和 `web_requests` 计数都会经过 checkpoint recovery。Finalization 校验
`[[S1]]` marker，Conversation 把同一来源随 assistant entry 持久化，Protocol v4 再投影给
TUI 与 headless 界面。

新增 channel 会改变 checkpoint 兼容性和恢复校验。这里不是放置任意 UI 或产品状态
的便捷容器。

## 模型/工具循环不变量

Agent 必须在每条路由上维持以下属性：

1. 第一次模型请求之前已经准备好上下文。
2. 提供商消息只使用 `awesome_agent.modeling` 契约。
3. 来自同一 assistant 消息的工具调用按顺序被观察。
4. 每个已发出的工具调用在下一次 assistant 请求前恰好得到一条 observation。
5. 因预算跳过的调用会得到确定性的“未执行”错误 observation；它不会被静默丢弃。
6. 预期内的工具失败是 observation；不变量失败则停止 Turn。
7. 压缩恰好保留一次活动的 assistant/tool 尾部。
8. 当正常循环进展耗尽时，终结过程会保留一次模型调用。

每次只执行一个工具节点是一项正确性选择。并行执行可以降低延迟，但必须先定义顺序、
审批并发、ChangeSet 冲突、取消扇出和确定性重放。Awesome 目前并不声称提供这些语义。

## 预算

`TurnBudget` 的默认值和硬上限由 Agent 代码强制执行：

| 预算 | 默认值 | 最大值 |
| --- | ---: | ---: |
| 模型调用 | 32 | 256 |
| 工具调用 | 64 | 512 |
| 活动执行 | 1,800 秒 | 21,600 秒 |
| 提供商重试 | 2 | 6 |
| 压缩次数 | 2 | 10 |

活动执行时间只在模型、工具和压缩阶段计费；用户考虑是否审批时的墙钟时间不计入。
最后一次模型调用容量可以在禁用工具的情况下保留，从而仍能生成有界的最终响应。

预算计数器会写入 checkpoint。恢复过程会对照产品 Turn 校验这些计数器；遇到不可能的
值或未闭合的消息链时会拒绝恢复，而不会从推断出来的状态重新启动。

## 上下文捕获与图调用

Application 在执行图之前捕获显式路径快照和已启用的本地 memory；随后 Agent 的
`prepare_context` 节点调用注入的 context service。准备好的 manifest 既作为产品
投影记录，也写入图状态。后续压缩可以重建有界基础上下文，同时保留活动的工具尾部。

```text
Application accepts Turn
  -> capture natural input / explicit paths / local memory
  -> create initial AgentState
  -> graph.ainvoke(..., thread_id=turn.id)
  -> Agent prepare_context asks injected service
  -> manifest + messages enter checkpoint
```

Application service 负责访问 Conversation 和工作区快照；Agent 不知道具体的 SQLite
仓库或文件系统发现逻辑。

## 回答后 Finalizer 端口

Agent 拥有唯一的提供商中立 `PostAnswerFinalizer` 端口，以及严格、不可变的
`PostAnswerFinalizationRequest` 和 `PostAnswerFinalizationResult` 值。Request 包含用户
文本、已经生成的回答、所选模型、工作区标识、剩余模型/retry 预算，以及从 `tool_results`
按顺序收集的 citations。收集过程保留首次出现顺序、折叠 ID 与值均相同的重复项，并把同一
ID 对应不同值视为不变量失败。Turn 中唯一 citation 超过 128 条同样属于聚合不变量失败，
并发生在 finalizer 运行前。Request 携带这个有界 tuple，而不会增加 `AgentState` channel。

Result 包含去除首尾空白后仍非空的回答、零或一次主要模型调用、有界 `ModelUsage`，以及
最多 32 条通用 `PostAnswerDiagnostic`。只有报告一次模型调用时，usage 才能非零。Agent
会严格重建返回值；若 provider retry 超过剩余 retry 预算，或主要调用加 retry 超过剩余
model-call 预算，则拒绝结果。Active-time 耗尽会把 request 的剩余模型调用强制设为零，
但 finalizer 仍会运行，使不调用模型的实现可以完成。有效结果会替换回答、计入一次主要
调用及其 provider retry、合并 usage，并投影每条 diagnostic 的原始 code 与 message。

`DisabledPostAnswerFinalizer` 原样返回现有回答。Application 也可以注入
`memory.Mem0PostAnswerFinalizer`；Agent 不导入 Memory，也不知道 Mem0 identity、adapter、
status 或 diagnostic。若注入的 finalizer 抛出异常、返回无效值或超出预算，Agent 会发出
`answer_finalization_failed`，并保留已经生成的回答及原有 usage。取消时 Agent 不投影
warning；它会保留此前 checkpoint 中的回答，并立即把调用方的原始取消重新抛给 Application
生命周期。该路径不是正常 completion。可选 finalization 不能把回答变成空值或部分更新结果。

## 完成与取消

图正常完成时，Application 校验返回的状态、追加 assistant 回答、记录有界 usage，
并完成 Turn。随后它尝试封存 ChangeSet 并删除 checkpoint。失败或取消时，它会记录
稳定的终态产品事实，并执行同样的 durable finalization。本地 transcript、activity、
ChangeSet 与 checkpoint 工作会保持所有权，直到得到明确结果。主要终态事实落盘后，清理
异常会被有意抑制；启动校正会重试残留的终态 checkpoint，而不会改变已经完成/取消/失败
的结果。

Operation phase 明确定义了取消边界。Commit point 之前，匹配的 cancel 会把 `running` 改为
`cancelling`，唯一终态是 cancelled。Durable finalization 会先把 `running` 改为
`committing`，再请求 Application SQLite worker 持久化 completed 或 failed Turn。此时匹配的
`operation.cancel` 返回 false，shutdown 会等待而不是再次 cancel。如果 request task 在 durable
write 已准入后被取消，Core 会等待 worker 给出明确的 COMMIT 或 ROLLBACK 结果，再重新抛出
caller 的第一次 cancellation。即使 event sink 失败，有界且受 shield 保护的 publication 仍会
保留已经提交的 Turn 和 Operation 终态。系统不存在 cancellation 和 completion 可以同时胜出
的时间窗口。

模型流、工具或终结步骤活动时都可能收到取消。本地 durable fact preservation 会受到
shield 保护直到得到明确结果，并在此期间继续持有 foreground lease。只有外部进程清理与
best-effort event delivery 使用有界 deadline；两条路径都不能吞掉原始取消。

## 与恢复的关系

Application 不会反序列化 checkpoint 后就盲目继续。它会校验标识、预算、消息角色、
上下文 anchor、活动工具尾部和终止状态。有效的图状态可以被终结或恢复；无效状态会以
稳定的恢复错误码失败。结果不确定的外部操作需要显式决策。

Application 数据库与 checkpoint 数据库相互独立，因此存在提交窗口。恢复通过严格的
lineage 与 compare-and-swap 使其收敛，而不是实现第二套图。详见
[存储与恢复](storage-and-recovery.zh-CN.md)。

## 依赖规则

Agent 只能导入 Agent、Core 和 Modeling 包。它不能导入 Application、Memory、Storage、
Protocol、providers 或 TUI。Application 是 Python 顶层组装层，可以依赖
当前适配器。`tests/structural/test_dependency_architecture.py` 和
`tests/structural/test_product_architecture.py` 会强制这些方向，并确保只有一个
`StateGraph` 所有者。

## 取舍

- **一张图，更多 Application 协调：** 生命周期代码更加显式，但不会出现语义模糊的
  第二运行时。
- **一次一个工具，确定性恢复：** 并发更低，但 observation 与 ChangeSet 的职责更简单。
- **数据库分离，所有者清晰：** 不存在跨数据库事务，因此需要严格恢复。
- **类型化事件，更多契约工作：** 新事实必须同步更新 Python、fixtures、TypeScript
  schema 和展示，而不能落入通用渲染器。

## 源代码与测试索引

- Facade 与组装：`application/facade.py`、`application/composition.py`
- Invocation diagnostics：`application/middleware.py`、
  `application/diagnostics.py`
- 准入：`application/foreground.py`、`application/operations.py`
- Turn 与恢复：`application/turns.py`
- 图、状态与 finalizer 端口：`agent/graph.py`、`agent/state.py`、`agent/nodes.py`、
  `agent/finalization.py`
- 预算：`agent/budgets.py`
- 单元测试：`tests/unit/application/`、`tests/unit/agent/`
- 集成测试：`tests/integration/test_agent_turn.py`、
  `tests/integration/test_agent_recovery.py`
- 结构测试：`tests/structural/test_application_architecture.py`、
  `tests/structural/test_agent_architecture.py`
