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
- `composition.py`：激活与具体依赖装配；
- `foreground.py` 和 `operations.py`：原子的前台所有权；
- `turns.py`：Turn 执行、终结、取消与恢复；
- `dispatcher.py` 加命令服务：确定性的斜杠命令；
- `interactions.py`：类型化决策与权限绑定；
- `context.py`：每个 Turn 的上下文捕获与冻结 manifest 投影；
- `events.py`：投影到界面的产品事实。

`composition.py` 可能较大，因为它负责装配和启动顺序。它不能成为命令语义、图路由、
任意结果构造或展示格式化的容器。

### Workspace runtime 快照

受信激活成功后会发布唯一的不可变 `WorkspaceRuntime`。该快照包含解析后的配置与稳定的
workspace service graph：Conversation、Turn coordination、command service、Tool
Registry、Model Catalog、context 与 extension、memory 与 MCP，以及 Change Journal
service。Foreground ownership、pending interaction、permission grant、recovery delivery
和进程 shutdown 等可变生命周期协调仍保留在 Application backend 上。

普通请求会在跨越异步边界前只捕获一次 `_runtime`，不会在同一请求后续通过多个 backend
字段重新拼装依赖。Provider 配置 mutation 会保留 service graph，并通过
`dataclasses.replace` 在一次赋值中发布新的配置与 Model Catalog 快照。本阶段的
activation candidate 字段与逐字段 rollback 仍是私有构造机制；失败的构造结果不会成为
请求可见 runtime。

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
- 模型/工具/重试/压缩/活动时间计数器；
- usage、恢复问题、最终回答和终止原因。

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

## 完成与取消

图正常完成时，Application 校验返回的状态、追加 assistant 回答、记录有界 usage，
并完成 Turn。随后它尝试封存 ChangeSet 并删除 checkpoint。失败或取消时，它会记录
稳定的终态产品事实，并执行同样的有界终结。主要终态事实落盘后，清理异常会被有意
抑制；启动校正会重试残留的终态 checkpoint，而不会改变已经完成/取消/失败的结果。

Operation phase 明确定义了取消边界。Commit point 之前，匹配的 cancel 会把 `running` 改为
`cancelling`，唯一终态是 cancelled。成功完成只会在同步持久化 completed Turn 时越过 commit
point；主要失败则会在执行有界的失败事实恢复之前关闭同一边界。一旦 committed，
`operation.cancel` 返回 false，shutdown 会等待而不是再次 cancel；即使 request task 被取消或
event sink 失败，有界且受 shield 保护的 publication 仍会保留已经提交的 Turn 和 Operation
终态。系统不存在 cancellation 和 completion 可以同时胜出的时间窗口。

模型流、工具或终结步骤活动时都可能收到取消。只有为了有界地保全事实，清理才会受到
shield 保护；它不能吞掉原始取消，也不能让前台所有权一直保持活动状态。

## 与恢复的关系

Application 不会反序列化 checkpoint 后就盲目继续。它会校验标识、预算、消息角色、
上下文 anchor、活动工具尾部和终止状态。有效的图状态可以被终结或恢复；无效状态会以
稳定的恢复错误码失败。结果不确定的外部操作需要显式决策。

Application 数据库与 checkpoint 数据库相互独立，因此存在提交窗口。恢复通过严格的
lineage 与 compare-and-swap 使其收敛，而不是实现第二套图。详见
[存储与恢复](storage-and-recovery.zh-CN.md)。

## 依赖规则

Agent 只能导入 Agent、Core、Memory 和 Modeling 包。它不能导入 Application、
Storage、Protocol、providers 或 TUI。Application 是 Python 顶层组装层，可以依赖
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
- 准入：`application/foreground.py`、`application/operations.py`
- Turn 与恢复：`application/turns.py`
- 图与状态：`agent/graph.py`、`agent/state.py`、`agent/nodes.py`
- 预算：`agent/budgets.py`
- 单元测试：`tests/unit/application/`、`tests/unit/agent/`
- 集成测试：`tests/integration/test_agent_turn.py`、
  `tests/integration/test_agent_recovery.py`
- 结构测试：`tests/structural/test_application_architecture.py`、
  `tests/structural/test_agent_architecture.py`
