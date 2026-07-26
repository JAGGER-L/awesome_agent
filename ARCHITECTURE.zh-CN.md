# Awesome 架构

Awesome 是一个终端 AI coding assistant。一个 `awesome` launcher 会启动 Ink 界面和私有
Python 进程；所有产品行为都保留在 Python Core 中，TUI 只提交意图并渲染类型化事件。

本文档是权威技术概览。[`docs/architecture/`](docs/architecture/README.zh-CN.md) 下的专题
文档会解释各个边界，但不会重新定义系统。

## 系统概览

```text
┌───────────────────────────────────────────────────────────────────────────┐
│                          入口与展示                                       │
│                                                                           │
│  awesome launcher                     Ink + React TUI                     │
│  CLI 参数                              输入 / 渲染 / 键盘 / UX             │
└───────────────────────────────────┬───────────────────────────────────────┘
                                    │
                                    │ stdio 上的 JSON-RPC 2.0 / NDJSON
                                    ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                       Python Application Host                             │
│                                                                           │
│  ApplicationFacade   命令           Interaction      事件投影             │
│  工作区信任          Thread/Turn    取消             组装                 │
└──────────────┬──────────────────┬──────────────────┬──────────────────────┘
               │                  │                  │
               ▼                  ▼                  ▼
┌────────────────────────┐ ┌───────────────────┐ ┌──────────────────────────┐
│ Agent Core             │ │ 扩展              │ │ 本地状态                 │
│ LangGraph              │ │ Skills / MCP      │ │ Application SQLite       │
│ 上下文组装             │ │ 本地 Memory       │ │ LangGraph Checkpoint     │
│ 模型 / 工具循环        │ │ Mem0 Cloud        │ │ Change Journal           │
│ 压缩 / 预算            │ │                   │ │ TUI 偏好                 │
└───────────┬────────────┘ └─────────┬─────────┘ └──────────────────────────┘
            │                        │
            ├──────────────┬─────────┘
            ▼              ▼
┌────────────────────┐  ┌──────────────────────────────────────────────────┐
│ 模型提供商         │  │ 工具系统                                         │
│ DeepSeek / Kimi    │  │ Registry -> Policy -> Executor -> Result/Event   │
└────────────────────┘  └────────────────────────┬─────────────────────────┘
                                                 │
                                                 ▼
                                   ┌────────────────────────────┐
                                   │ 工作区与 Host              │
                                   │ 文件 / Shell / Git         │
                                   │ 测试 / 构建工具             │
                                   └────────────────────────────┘
```

Application Host 是组装与生命周期边界。它直接调用编译后的 LangGraph；不会实现第二个
graph engine，也不会复制 graph channel state。模型调用使用提供商中立契约，每次工具
调用都经过同一条 Registry、Policy 与 Executor 路径。

## 目录结构

```text
awesome_agent/
├── src/awesome_agent/
│   ├── agent/          # LangGraph 状态、节点、路由、预算
│   ├── application/    # 生命周期、命令、Operation、组装
│   ├── config/         # 用户/工作区配置与优先级
│   ├── context/        # 上下文组装、token 估算、压缩
│   ├── conversation/   # Thread、Turn、transcript、repository 契约
│   ├── core/
│   │   ├── changes/    # Change Journal 与 undo/redo 契约
│   │   ├── tools/      # tool registry、policy、executor、built-in
│   │   ├── workspace/  # 工作区身份与信任模型
│   │   ├── filesystem.py       # 绑定身份的文件系统 primitive
│   │   └── process_lifetime.py # Core 进程树所有权
│   ├── extensions/
│   │   ├── mcp/        # MCP stdio client 与 tool adapter
│   │   └── skills/     # Skill 发现、加载与工具暴露
│   ├── memory/         # USER.md、MEMORY.md、Mem0 Cloud、memory tool
│   ├── modeling/       # 提供商中立消息与 model gateway
│   ├── protocol/       # JSON-RPC 类型与私有 stdio Host
│   ├── providers/      # DeepSeek 与 Kimi adapter
│   ├── safety/         # 脱敏 helper
│   ├── storage/        # 嵌入式 SQLite 与 checkpoint adapter
│   ├── paths.py        # AWESOME_HOME 路径所有权
│   └── version.py      # 产品版本 reader
├── tui/                # Ink + React 展示 package
├── protocol/fixtures/  # 跨语言 protocol fixture
├── scripts/release/    # release bundle builder
├── tests/              # unit、integration、E2E、packaging、structural
├── install.sh
├── install.ps1
├── pyproject.toml
└── VERSION
```

生成环境、cache、开发计划和用户 secret 不属于产品源代码树。

## 推荐阅读顺序

从问题所需的详细程度开始：

1. 阅读 [Awesome 如何工作](docs/concepts/README.zh-CN.md)，建立产品心智模型。
2. 阅读[请求生命周期](docs/architecture/request-lifecycles.zh-CN.md)，了解启动、Turn、
   直接命令、审批、取消和恢复序列。
3. 阅读[架构阅读路径](docs/architecture/README.zh-CN.md)，选择专题子系统指南。
4. 阅读本文档，了解完整拓扑、依赖方向和状态所有权契约。

研究源代码时，继续按依赖顺序阅读：

1. `src/awesome_agent/application/facade.py` —— 面向界面的产品 API。
2. `src/awesome_agent/application/composition.py` —— 具体依赖装配。
3. `src/awesome_agent/application/turns.py` —— Turn 生命周期与恢复。
4. `src/awesome_agent/agent/graph.py` —— 唯一 graph compiler。
5. `src/awesome_agent/agent/nodes.py` —— 模型/工具循环与终结。
6. `src/awesome_agent/context/builder.py` —— 提示词上下文组装。
7. `src/awesome_agent/modeling/` 与 `src/awesome_agent/providers/` —— 模型契约与受支持
   adapter。
8. `src/awesome_agent/core/tools/` —— Registry、Policy、Executor 与 built-in。
9. `src/awesome_agent/conversation/` 与 `src/awesome_agent/storage/` —— 产品记录和嵌入式
   adapter。
10. `src/awesome_agent/protocol/stdio.py` —— 私有进程边界。
11. `tui/src/app/App.tsx` —— 展示组装。

## 数据流

### 启动与工作区信任

```text
awesome <current directory>
        │
        ▼
Ink starts awesome-core
        │ initialize(workspace, protocol version)
        ▼
Application resolves canonical workspace
        │
        ├── state current/new ───► continue to workspace trust
        │
        ├── state older ─────────► explicit reset-or-exit interaction
        │                          confirmed reset -> exclusive lease
        │                          atomic state replacement -> trust
        │
        ├── state newer ─────────► stop and ask user to upgrade
        │
        ├── trusted ─────────────► load user/workspace configuration
        │                          snapshot bounded root AGENTS.md
        │                          load Skills and MCP declarations
        │                          create or resume a Thread
        │
        └── not trusted ─────────► interaction.required
                                   Yes -> persist trust -> continue
                                   No  -> exit without persisting denial
```

建立信任前，不会加载项目控制的配置、指令、Skills 和 MCP 声明。

激活还会取得两个独占 session lease：一个对应规范 workspace path key，另一个对应已打开
根目录的 physical identity。Path lease 在该 pathname 被替换后仍有效，而 entity lease
会折叠同一目录的其他拼写。这样可以防止第二个 Core 通过替换根或 path alias，把活动
Turn 当作崩溃恢复。

根目录 `AGENTS.md` 是只在信任建立后加载的不可变会话来源。有界且校验身份的读取要么
提供完整 mandatory instruction source，要么不提供内容并生成结构化诊断；绝不会进行
改变含义的截断。该诊断不会使配置无效。

Application state preflight 是只读的，运行在建立信任、访问 checkpoint 或可写存储之前。
当前格式是 Schema 7。产品版本与 schema 版本相互独立：schema identity 只随持久化语义
变化，并单调递增。Migration catalog 的 floor 是 7、current 是 7，且没有生产 migration
step。因此 Schema 1–6 只提供类型化 reset-or-exit interaction；更新、未知、损坏、不可读
或锁定的状态绝不会被静默删除。

未来已注册的 migration 只会在 shared-lease preflight、取得 exclusive lease 并再次检查
兼容性之后执行。Storage 会校验并原子发布能感知 WAL 的 SQLite backup，在一个 transaction
中执行完整的相邻迁移链，降级 lease，然后才初始化 Application repository。失败会回滚
transaction 并保留 backup 供手动恢复；启动绝不会自动 reset 或 restore。

### 对话 Turn

```text
User Message
    │
    ▼
turn.submit -> ApplicationFacade.submit_turn
    │
    ├── atomically acquire the foreground Operation lease
    ├── resolve Thread configuration
    └── create Turn and user transcript entry
             │
             ▼
       LangGraph Agent
             │
       prepare context
             │
       call ModelGateway
             │
       ┌─────┴─────┐
       │ Tool Call │── No ──► finalize answer
       └─────┬─────┘
             │ Yes
             ▼
       Tool Executor
             │
       observation + checkpoint
             │
             └──────────────► call model
```

图返回最终回答后，Application 会完成 Turn、追加 assistant transcript entry，并记录
有界 usage。随后它尝试封存 ChangeSet 并删除已完成 checkpoint，再发出 completion event。
清理失败不会覆盖已持久化的主要 Turn 终态；启动校正会重试残留终态证据。

### 工具调用

```text
Model ToolCall
    -> ToolRegistry lookup
    -> schema validation
    -> workspace and command policy
    -> ToolExecutor timeout/cancellation/event envelope
    -> built-in or MCP adapter
    -> normalized ToolResult
    -> bounded activity summary + Agent observation
```

改变文件的 built-in 会通过 Change Journal 和共享的 identity-bound filesystem primitive
写入。词法包含只负责准入：实际 mutation 会固定 workspace 与 parent directory chain，
校验已打开对象的 identity，并拒绝无法证明只指向一个 workspace object 的 link/reparse
或 hard-link alias。这样可以缩小路径替换竞态，但并非 filesystem compare-and-swap：
同权限 host 进程仍可在最后一次 identity check 后、replace/remove 前替换工作区内目标；
在 POSIX 上，它还可在 reachability check 后移动已打开目录。固定 parent 与 no-follow
操作会防止这些竞态沿链接到达外部目标，但更强的并发 host 威胁模型需要 OS sandbox 或
mount boundary。

`execute` 在 host 上运行，不是 sandbox。对于 Agent 工具调用，Request approval 会在
write、delete、shell、MCP 和未知 capability 前询问。Accept edits 只允许普通 workspace
write。经确认的 Full access 会为其绑定 Thread 允许已知 built-in 本地 write、delete
与 shell；MCP 和未知 capability 仍会询问。

直接 `! command` 是该提示矩阵的有意例外：用户输入的确切内容就是授权，因此 Application
为该 Direct Operation 提供独立 Full-access permission session，不会打开普通 shell
审批 interaction。它仍经过同一 schema、词法/spawn 前 circuit-breaker 检查、Process
Runner、journal、脱敏、timeout、cancellation 与 terminal-event 路径。Circuit breaker
用于阻止可识别的误操作，不针对任意恶意混淆。Shell 作用可能逃出工作区，也无法由 journal
撤销。

### 斜杠命令

```text
/command
   ├── Ink-owned presentation command -> local UI state
   └── Application command -> command.execute -> typed result
```

权威 Core 命令路径为：

```text
Ink command controller
    -> Protocol v3 command.execute
    -> LocalApplication facade
    -> complete CommandDispatcher
    -> one focused command service
    -> CommandOutcome
    -> exhaustive TUI Presenter
    -> current transcript path
```

Immutable dispatcher 负责每个 Core 命令。Ink-owned command 仍是本地展示 action，绝不
进入 Core RPC。Composition 只装配 command service；不包含 command 语义，也不构造
command result。斜杠命令是确定性的产品操作，绝不会提交隐藏的 model prompt；只有自然
语言输入会启动 Agent Turn。

`LocalApplication` 是唯一面向界面的 Application host。Python 生成 Protocol v3
discriminated outcome，TypeScript 对其进行严格校验并穷尽展示。Command progress 是
pending Surface lifecycle state，不是第二种持久化 operation model。

### Resume 与恢复

`--continue` 选择最近使用的工作区 Thread；`--resume <id>` 选择精确或前缀唯一的 Thread，
不带参数的 `--resume` 打开近期 Thread picker。启动时，Application 会将未完成产品 Turn
与 LangGraph checkpoint 校正：

- 已完成 graph state 会被终结为产品记录；
- 有效未完成 checkpoint 可以恢复；
- checkpoint 缺失或损坏时，Turn 以稳定错误码失败；
- 不确定 shell 或 MCP 副作用要求显式 retry/abort interaction，安全默认是 Abort；
- 终态产品 Turn 残留的 checkpoint 会被删除。

对于未完成 Turn，最新且经过严格校验的 checkpoint 是恢复事实来源。只有当 Application
SQLite 投影为空或其不可变 source anchor 共享 lineage，且旧投影仍匹配显式
compare-and-swap 预期时，其冻结 manifest 才可以修复该投影。这样可收敛彼此独立的
Application 与 LangGraph 数据库之间不可避免的提交窗口，而不会把自洽但无关的 snapshot
当作权威。

## 主要子系统

### Application Host

- **职责：** 工作区初始化、配置解析、Thread/Turn 生命周期、命令、前台 operation
  串行化、interaction、取消、事件投影、恢复和组装。
- **不负责：** 模型推理、图路由、工具实现或 UI 渲染。
- **主要文件：** `application/facade.py`、`application/composition.py`、
  `application/turns.py`、`application/operations.py`。
- **依赖：** Agent Core、当前 adapter、Conversation、Storage、Core、Context、Extensions
  和 Memory。

受信激活完成后，backend 会发布一个 frozen、slotted 的 `WorkspaceRuntime`。它是请求可见
的快照，统一包含已解析配置以及组装后的 Conversation、Turn、command、tool、model
catalog、context、extension、memory、MCP、Change Journal 和 `RuntimeResources`。每个请求
只绑定一次该对象；被 await 的 callback 和由 foreground 持有的子 task 始终沿同一 service
graph 执行。替换时会先完全使用局部变量构建 candidate，完成校验和前台 Operation 所有权
检查后，再通过一次指针赋值发布。随后新请求绑定新 runtime；已准入的 reader 继续使用旧
资源 generation，完成后才关闭它。每个 generation 的 `AsyncExitStack` 持有可复用的
provider client、内部创建的 Mem0 client 和 MCP，并按 MCP、Mem0、provider client 的逆序
恰好关闭一次。注入的 gateway 和 Mem0 client 只借用，绝不注册关闭。启动 recovery 通知
发生在发布之后，失败也不会回滚已 ready 的 runtime。Candidate 构建失败或取消只关闭候选
资源，不影响此前发布的 runtime。Provider 与
credential mutation 复用同一条完整 candidate 发布路径，但不重复 startup recovery，并保留
已选择的 Thread。前台所有权、interaction、permission session、recovery delivery、
checkpoint saver、进程级 Application SQLite worker、state lease 和其他进程生命周期资源
仍由另一套 Application `AsyncExitStack` 持有，而不是 workspace snapshot 字段。一个有界
FIFO worker thread 持有长期 Application database connection；面向 Application 的
repository 只暴露 async method，SQLite 所有的值不会跨越该边界。

Application invocation diagnostics 同样属于进程/会话，不在 `WorkspaceRuntime` 内。有界、
非阻塞 writer 将结构化 JSON line 追加到 `<AWESOME_HOME>/logs/application.jsonl`；每个文件
上限为 5 MiB，并轮转保留 `application.jsonl.1` 至 `.4`。它会 fail open，因此诊断 I/O 不会
改变 Application 结果。Record 只从显式 allowlist 构造：`version`、`timestamp`、
`session_id`、`correlation_id`、`operation`、`outcome`、`duration_ms`，以及可选的
`error_code` 与 `usage`。它绝不包含 prompt、模型或工具正文、query、URL、path、secret 或
任意 request/result payload。记录的 outcome 只属于被观测的 Application invocation；成功
启动后台 Agent 工作的请求，并不表示其异步 Turn 后来成功完成。

共享 foreground arbiter 向 Agent Turn、直接命令、改变状态的命令、credential mutation、
非 Tool interaction resolution 或 shutdown 授予唯一原子 lease。准入发生在 Turn 持久化
之前。活动 Operation 期间，显式例外是只读 snapshot command；pending interaction 会
阻止新 Operation 与 mutation，而匹配的 Tool approval 会继续其所属 Operation。Shutdown
会关闭准入、取消仍可取消的活动工作、等待 durable commit 和 lease 清理，回收 runtime
resource，再关闭 checkpoint saver 和 Application SQLite worker，最后释放 state lease。

### Agent Core 与 LangGraph

- **职责：** `AgentState`、节点路由、上下文/模型/工具循环、消息修复、压缩、retry
  计数、预算和终结。
- **不负责：** 产品 Thread record、具体存储装配或界面状态。
- **主要文件：** `agent/state.py`、`agent/graph.py`、`agent/nodes.py`、
  `agent/budgets.py`。
- **依赖：** 提供商中立 Modeling、Core tool 和注入的 Memory service。

### 上下文管理

- **职责：** 确定性提示词组装、显式路径引用、token 估算、Thread summary、Skills、
  memory recall 和压缩输入。
- **不负责：** 图路由或隐藏持久化。
- **主要文件：** `context/builder.py`、`context/compression.py`、
  `context/path_refs.py`、`context/tokens.py`。

压缩只能替换有界基础上下文。活动 Turn 的 assistant/tool 尾部会作为唯一 protocol chain
校验，在输入预算中预留，并恰好追加一次；其 pending-call 和 result index 保持不变。
每个发出的 tool call 都会在下一次 model request 前收到一条有序 observation，包括循环
预算耗尽时对跳过调用生成的确定性“未执行” observation。

### Model Gateway

- **职责：** 提供商中立 message、tool、streaming event、error、usage、模型选择、retry
  报告和受支持 adapter 调用。
- **不负责：** 工具、图状态或产品生命周期。
- **主要文件：** `modeling/gateway.py`、`modeling/provider.py`、
  `modeling/turns.py`、`providers/deepseek.py`、`providers/kimi.py`。

### 工具系统

- **职责：** 工具注册、schema、workspace/process policy、execution context、
  cancellation、timeout、规范化 failure、event 和有界 result。
- **不负责：** 模型路由或界面 prompt。
- **主要文件：** `core/tools/registry.py`、`core/tools/policy.py`、
  `core/tools/executor.py`、`core/tools/builtins/`。

初始 built-in 为 `ls`、`read_file`、`write_file`、`edit_file`、`delete`、`glob`、
`grep` 和 `execute`。这是一组初始 baseline，不是工具数上限。

### Conversation 与 Storage

- **职责：** Thread、Turn、transcript、summary、tool activity、trust、ChangeSet metadata、
  checkpoint 访问与 SQLite transaction。
- **不负责：** graph node transition 或 TUI transcript state。
- **主要文件：** `conversation/models.py`、`conversation/service.py`、
  `storage/application_sqlite.py`、`storage/database.py`、
  `storage/compatibility.py`、`storage/state_lease.py`、
  `storage/state_recovery.py`、`storage/conversations.py` 和
  `storage/checkpoints.py`。

可重置边界恰好是 `<AWESOME_HOME>/state`。Storage 在 exclusive lease 下执行原子替换；
Application 负责确认和继续启动；Protocol 传输类型化事实；Ink 只展示和路由决策。配置、
凭据、Skills、Memory、UI 偏好和工作区文件都位于该边界之外，会在确认 reset 后保留。

这里的原子替换描述文件系统 namespace transition，不表示撤销在 Awesome lease protocol
之外打开的任意 handle。打开的数据库 handle 会在 Windows 上阻止 rename。POSIX 允许
rename 与 unlink；这样的既有 handle 会继续连接到已分离的旧 inode，直到关闭，而规范
路径指向全新状态。

### Change Journal

- **职责：** 受控 before/after snapshot、conflict detection、crash reconciliation、
  diff、undo、redo 和 reversibility classification。
- **不负责：** `execute` 产生的任意 host 副作用。
- **主要文件：** `core/filesystem.py`、`core/changes/filesystem.py`、
  `core/changes/journal.py`、`core/changes/operations.py`、
  `storage/changes.py`。

Undo 与 redo 是多路径 restore transaction。它们先绑定并预检所有 target，在第一次
restore 前持久化每一条 pending intent，通过同一固定工作区树应用 restore，最后只改变
一次 ChangeSet lifecycle。如果 lifecycle commit 前失败，会在仍持有原 directory identity
期间回滚已经应用的 path；如果无法证明该回滚，pending evidence 会被保留。启动校正会
校验并完成已提交 operation，完整回滚未提交 operation；identity、content 或 lifecycle
conflict 会保留 pending evidence，而不是猜测。

每个普通 file mutation 都携带持久 mutation identity 和不同的 before/after node type，
因此 directory、file 或 symlink transition 在 merge 后和 undo/redo 恢复中仍可表达。
Turn 和 direct finalization 只校正自己的 ChangeSet，并且只有 sealing 成功后才释放内存
owner。在这些可选 JSON 字段出现前创建的 Schema 7 history 仍可读取；如果没有 mutation
identity 的旧记录与被中断的 pending mutation 无法区分，恢复会保留 pending evidence
并 fail closed。

工作区文件及其 diff 是生成的工作成果；普通文件变更不存在并行 output object。

### Skills 与 MCP

- **职责：** 发现受信 bundled/user/workspace Skills、加载有界指令、连接配置的 MCP
  stdio server，并将 MCP tool 适配到共享 registry。
- **不负责：** 权限或替代执行路径。
- **主要文件：** `extensions/skills/discovery.py`、
  `extensions/skills/loader.py`、`extensions/mcp/manager.py`、
  `extensions/mcp/adapter.py`。

Workspace Skill path 与已打开 identity 会在不跟随 link 或 reparse point 的情况下重新
校验；一个无效 package 仍是隔离诊断。MCP 会在 page、tool-count、byte 与 deadline 边界
下消费完整分页 catalog，编译所有 JSON Schema 和完整 namespaced tool name，再构建全部
generation-bound Registry entry。Manager 持有 server lock 时同步替换完整 Registry
namespace，并且不经过新的 `await` 就发布相匹配的 client、catalog、generation 与
`CONNECTED` 状态。因此发布是全有或全无：`CONNECTED` 能证明同一 generation 的完整
namespace 已安装。引用必须在同一个 schema 内保持本地。Input argument 在审批或远程
I/O 前校验；声明的 `outputSchema` 会校验 `structuredContent`，没有 text 的结构化输出会
渲染为有界 JSON。Restart 会先移除旧 namespace；timeout、disconnect 或 cancellation
会使 generation 失效；调用绝不会惰性重连，也不会在同一 Turn 重放结果不确定的外部
action。

Compiler 会在 Registry 发布前，根据最严格的下游 128 字符限制校验完整
`mcp.<server>.<tool>` 名称。无效名称、schema、重复名称或 Registry 聚合上限会关闭新
client、使候选 generation 失效、移除该 server namespace，并发布脱敏的 `ERROR` 状态，
不会暴露任何有效子集。

### Memory

- **职责：** 独立本地文件（`USER.md`、工作区 `MEMORY.md`）和可选 Mem0 Cloud recall/
  distilled write。
- **不负责：** policy、trust、raw transcript upload 或 provider routing。
- **主要文件：** `memory/local_file.py`、`memory/service.py`、
  `memory/mem0_cloud.py`、`memory/distiller.py`。

两层 memory 均独立启用且默认关闭。Mem0 Cloud 是当前唯一受支持的外部 memory adapter。

### Protocol 与 Ink TUI

- **职责：** 版本化 JSON-RPC 请求、类型化事件、有界 NDJSON、终端输入、渲染、键盘行为、
  transcript 投影、主题、剪贴板、仅会话 pending input 和本地展示偏好。
- **不负责：** 模型、LangGraph、tool、storage、Memory、Skills 或 MCP。
- **主要文件：** `protocol/jsonrpc.py`、`protocol/stdio.py`、
  `tui/src/core/process.ts`、`tui/src/app/App.tsx`。

`TerminalInput.tsx` 是唯一 keyboard subscriber。一个可辨识 UI mode 路由 Enter、Escape、
Tab、方向键和全局取消，不会有相互竞争的 component listener。乐观 user message 使用
`client_message_id` 作为 key；Thread generation 在 replacement 后拒绝过期 event。活动
Turn 是一条有序 Thinking/tool/answer timeline，已完成 answer 使用 terminal Markdown
渲染。

stdio Host 读取一条有界 NDJSON stream，却将普通请求作为独立 task 调度。固定 in-flight
上限、有界 recent request-ID history，以及有边界和 deadline 保护的 stdout queue 会限制
内存与停滞 consumer 风险。Initialize、interaction response、cancellation 和 shutdown
仍是绕过普通饱和的 control request。这种 wire concurrency 不会创建第二 mutation
scheduler：Application foreground arbiter 仍串行化改变状态的工作。

进程生命周期有两个所有者。在 POSIX 上，TUI 在自己的 session/process group 中启动 Core，
并以该 group 为终止目标。每个 `execute` 由独立 session supervisor 启动，其 lease pipe
由 Core 持有；Core 退出会关闭 lease，supervisor 随后终止其余 group。在 Windows 上，
Core 安装 kill-on-close lifetime Job Object，并在异步启动前把自己加入；无法建立该不变量
就会中止启动。每个 `execute` 还会建立嵌套 kill-on-close Job Object，以及等待 event 的
私有 supervisor。Core 在允许等待中的 supervisor 创建 target 前，先把它加入 command
job，因此 target 和所有 descendant 会在没有 spawn race 的情况下继承 command job。
Root completion、timeout、cancellation 或 setup failure 会终止该 command job；正常或
异常 Core 退出仍由外层 lifetime job 处理。Runner 会独立等待 root process 与
stdout/stderr EOF，然后给继承 pipe 一个有界 drain 阶段；持有 pipe 的 descendant 可能
截断捕获输出，却不能使 Tool call 永远 pending。

POSIX 保证覆盖仍留在 supervisor session 和 process group 内的 descendant。有意用
`setsid()` 或类似方式 daemonize 并逃逸的命令超出此清理边界；两种平台机制都不是执行
sandbox。

单一 Core Operation 活动时，TUI 最多可以排队三个终端输入。Queue 只属于会话，位于
Thread Surface state 之外：它会跨 `/new` 与 `/resume` 保留；每个 head 只在被提升时
解析；按 FIFO 执行；Composer 为空时按 Up 可以召回 tail；排队的 `/quit` 被视作终止
barrier。它绝不会成为 Runtime、protocol method、database record 或第二执行权威。

### 安全

- **职责：** 文件工具的 workspace containment、sensitive-path rejection、
  identity-bound mutation、进程树清理、Agent shell 执行的显式审批、command policy、
  脱敏和工具输出边界。
- **主要文件：** `core/filesystem.py`、`core/process_lifetime.py`、
  `core/tools/policy.py`、`core/tools/command_policy.py`、
  `core/tools/process.py`、`safety/redaction.py`。

Full access 是审批模式，不是隔离边界。当前产品不提供操作系统 sandbox；工作区信任、权限
和 command circuit breaker 仍是 host 执行之上的不同 policy layer。

## 设计原则

1. Python Core 是产品行为的唯一权威。
2. Ink 只负责交互和渲染。
3. LangGraph 负责图执行、图状态、路由和 checkpoint。
4. Application 负责产品生命周期，但不重新实现图执行。
5. 每个工具都经过同一条 Registry、Policy 与 Executor 路径。
6. 工作区文件是主要工作成果。
7. 在证据充分时，执行可见、可取消、有边界且可恢复。
8. 产品状态使用位于解析后 Awesome 路径下的嵌入式本地存储。
9. Skills、MCP、Memory 和工作区指令都是不受信上下文，不能绕过 tool policy。
10. 新 abstraction 需要具体的第二实现或经过证明的产品用途。

## 文件依赖链

Python package graph 是显式 importer-to-allowed-dependency 契约。它不是简单垂直 DAG：
Storage 等 adapter 会实现 Agent、Conversation、Core 和 Extensions 所拥有的契约，而
Application 是 composition root，可以依赖其装配的所有具体所有者。

| 导入方 package | 可以导入的 Awesome package root |
| --- | --- |
| `agent` | `agent`、`core`、`memory`、`modeling` |
| `application` | `agent`、`application`、`config`、`context`、`conversation`、`core`、`extensions`、`memory`、`modeling`、`paths`、`providers`、`safety`、`storage`、`version` |
| `config` | `config`、`paths` |
| `context` | `context`、`conversation`、`core`、`memory`、`modeling` |
| `conversation` | `config`、`conversation` |
| `core` | `core`、`safety` |
| `extensions` | `context`、`core`、`extensions` |
| `memory` | `config`、`core`、`memory`、`modeling`、`paths`、`safety` |
| `modeling` | `config`、`modeling` |
| `protocol` | `application`、`core`、`paths`、`protocol`、`version` |
| `providers` | `config`、`modeling`、`providers` |
| `safety` | `modeling`、`safety` |
| `storage` | `agent`、`conversation`、`core`、`extensions`、`storage` |

`tests/structural/test_dependency_architecture.py` 是该精确 adjacency table 和外部 framework
所有权的可执行来源。TUI 是独立 TypeScript 进程，只通过 Protocol v3 访问 Python。

具体 provider 与 storage adapter 在 `application/composition.py` 中装配。Agent 导入
提供商中立契约，protocol 导入 Application facade，而不是各个子系统。

## 状态所有权

| 状态 | 所有者 | 位置 | 生命周期 |
| --- | --- | --- | --- |
| 工作区信任 | Application Storage | `state/application.db` | 直到用户数据被移除 |
| Thread、Turn、transcript、summary | Conversation + Storage | `state/application.db` | 持久本地历史 |
| Tool activity summary | Storage | `state/application.db` | 有界本地历史 |
| Agent graph channel | LangGraph | `state/checkpoints.db` | 仅未完成 Turn |
| ChangeSet metadata | Change Journal + Storage | `state/application.db` | 持久本地历史 |
| Change blob | Change Journal | `state/change-journal/` | 被引用期间 |
| Provider model transaction intent | Application + Config | `state/provider-model-transaction.json` | 直到 reconciliation 验证完成 |
| 用户 memory | Memory | `memory/USER.md` | 用户控制 |
| 工作区 memory | Memory | `workspaces/<key>/MEMORY.md` | 工作区范围 |
| 云端事实 | Mem0 Cloud | 外部账户 | 仅启用时 |
| UI 偏好 | Ink TUI | `ui.json` | 用户控制 |
| Application invocation diagnostics | Application 进程/会话 | `logs/application.jsonl{,.1,.2,.3,.4}` | 有界本地运行历史 |
| 工作区文件 | 用户和工具 | workspace | 主要项目状态 |

Token delta、spinner、原始 provider payload、无界 shell output 和 credential 不会作为产品
历史保存。未完成 Turn 所需的 tool observation 保留在 LangGraph checkpoint；面向用户的
activity history 存储有界 summary。

## 错误、取消与恢复

- 预期工具 failure 会成为模型可在剩余预算内处理的规范化 observation。
- 意外工具或 graph failure 会以稳定产品错误和可见事件终止 Turn。
- Provider adapter 会分类错误并报告 retry usage；Agent 强制配置的 retry 与 model-call
  上限。
- 取消会经过 foreground operation、model call 和 tool execution 传播。Application 将
  Turn 标记为 cancelled，随后继续持有 foreground lease，直到本地 activity、transcript、
  ChangeSet 与 checkpoint cleanup 得到明确结果。清理失败会保留主要 cancelled 事实；启动
  校正会重试残留终态 checkpoint evidence。只有进程清理与 best-effort event delivery 有界。
- 终态 event 允许 TUI 提升一个 pending input。类型化 busy 竞态会把同一 identity 重新
  放回队首，不产生重复 failure text。
- TUI cancellation 与 interaction controller 会在下一个 Operation 或 Interaction 前
  释放已完成 request identity。Nonfatal failure 保持可见且可重试；Core 退出是 fatal，
  会禁用 Composer input。
- Graph checkpoint 以 Turn ID 为 key。Application 产品记录引用相同 key，但不复制
  graph channel。
- 启动恢复只根据产品记录和 checkpoint 中的证据行动。结果不确定的外部副作用需要用户
  决策，而不是自动重放。
- 上下文压缩、消息修复、预算耗尽和终结是 Agent 不变量，不是可选 middleware。

## 扩展点

当前扩展点被有意保持得很窄：

- 新 model adapter 实现现有 provider 契约，并在 Application 边界组装；
- 新 built-in 或 MCP tool 进入现有 Registry/Policy/Executor 路径；
- 新 Skill 遵循当前 manifest schema 与受信发现顺序；
- 第二个外部 memory service 必须证明共享 provider abstraction 的必要性；
- 未来界面适配 `ApplicationFacade` 和类型化 event，而不是重新实现 Core 行为。

产品 roadmap 还包含单命令 Skills 安装、Multi-Agent delegation、search tool、Cron task、
Gateway messaging 和可选 Docker tool backend。这些是未来 capability，不属于当前系统图。
Docker backend 会位于 Tool Executor policy 之下，不会替代 workspace trust。
