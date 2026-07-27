# 上下文、模型与扩展

Agent 只能基于一段有界、提供商中立的消息序列进行推理。因此，Awesome 把上下文构建
视为一条显式、可检查的流水线，而不是把所有可用文档拼接进提示词。模型提供商和扩展
可以向该流水线提供数据，或向共享 executor 提供工具；二者都不能成为新的策略权威。

## 上下文职责

`src/awesome_agent/context/` 负责通用来源模型、token 估算、排序、去重、预算、显式路径
快照和压缩。`application/context.py` 负责以产品语义访问当前 Thread、Turn、工作区、
Skills 和 Memory。Agent 节点显式调用该服务。

流水线接收冻结的 Turn 标识、配置/模型上限、带标签的来源以及为活动尾部预留的容量。
它输出提供商中立消息、来源 manifest、实测预算事实和压缩信号。它绝不会输出权限 grant
或具体提供商 payload。

```text
Application accepts Turn
  -> capture natural input + explicit path snapshots + local memory
  -> Agent prepare_context
  -> ApplicationContextService builds source list
  -> ContextBuilder orders, deduplicates, budgets, and renders messages
  -> PreparedContext(messages, manifest, limits, compression signal)
  -> Agent checkpoint + model request
```

Context 代码不能导入 Application 或提供商实现。Agent 代码不能自行发现文件，也不能
绕过该服务查询具体仓库。

## 来源模型与顺序

每个 `ContextSource` 都有 kind、稳定的 source ID、内容、provider role、mandatory 标志、
可选 token 预算，以及可选的已覆盖 transcript 序列范围。当前来源顺序为：

1. 产品指令；
2. 工作区根指令；
3. 有界自动 Skill catalog 或一个已选择的 Skill；
4. 用户 memory；
5. 工作区 memory；
6. Mem0 recall；
7. Thread summary；
8. 按序列顺序排列的近期 Turn 和直接命令；
9. 显式路径快照；
10. 当前输入；
11. 压缩或恢复期间未闭合的 assistant/tool 链。

顺序具有语义。指令必须先于对话；summary 不得改变直接命令的顺序；当前输入必须是普通
基础来源中的最后一个。因此，改变 enum 顺序就会改变提示词行为与冻结 manifest 的校验。

以下来源一经选中即为 mandatory：产品和模型身份、工作区指令、有界自动 Skill catalog
或已选择的 Skill、显式路径、当前输入，以及未闭合的工具链。系统绝不会为了让提示词容纳
它们而静默截断；Skill catalog 在成为 mandatory 前已受边界约束。如果 mandatory 来源加
预留上下文超过生效输入上限，Turn 会以 context overflow 失败，而不会改变指令含义或
丢弃工具 observation。

## 预算计算

当前配置的每个模型都声明 262,144-token 上下文上限。生效输入预算会预留输出容量，
并应用 `context/tokens.py` 中的压缩阈值。精确 tokenization 仍是估算，因此系统会留出
余量，而不会宣称计数与提供商完全一致。

长期 memory 是可选项，其额度最多为 16,384 token 与生效输入上限 10% 二者中的较小值。
该额度如下分配：

| 来源 | Memory 额度占比 | 硬上限 |
| --- | ---: | ---: |
| 用户 memory | 25% | 4,096 tokens |
| 工作区 memory | 50% | 8,192 tokens |
| Mem0 | 25% | 4,096 tokens |

可选来源会受到其来源预算和剩余容量的双重截断。Mandatory 来源则按完整内容计量。
显式路径快照在 Turn 捕获时共享最多 25% 的生效输入预算，并分别受到文件、行数、
目录项和路径数量上限约束。

## 去重与来源追踪

每个保留的来源都会生成一个 `ContextManifestItem`，其中包含 kind、source ID、顺序、
估算 token、截断状态、SHA-256 内容哈希，以及它所覆盖的 transcript 序列范围（如有）。
Skill 来源还携带严格的版本化 package identity tuple 与说明性 `allowed-tools` 值。该 tuple
会持久化到 Turn 和 checkpoint，并在压缩后保留。

只有在语义允许时才按内容去重：

- mandatory 来源不会因为另一层包含相同文本而被移除；
- 时间线条目绝不会按内容合并；
- 本地和云端长期 memory 会在它们共享的不受信层内按规范化内容去重；
- 其他可选来源在相同 kind 与 role 内去重。

这确保即使 `AGENTS.md` 与产品或 Skill 文本相同，“它是工作区指令”这一事实仍被保留。
该 manifest 会用于 `/context`、完成与恢复，并非仅供调试。

## 工作区指令

建立信任后，Awesome 在一个会话中只读取一次根目录 `AGENTS.md`。读取会校验身份，
并先限制在 32 KiB，再限制为 8,192 token 与生效输入预算 10% 二者中的较小值。
不安全、二进制、非 UTF-8、读取期间变化或超限的内容会被整份忽略，并生成结构化诊断。

该快照在会话内不可变。这样可以避免规则在 Turn 或恢复中途发生变化，代价是编辑
`AGENTS.md` 后需要启动新的 Awesome 会话。层级指令文件和备用文件名不属于当前契约。

## 压缩

压缩只会概括有界的基础对话上下文。活动的 assistant/tool 尾部会被提取、校验、
在目标预算中预留容量，并在重建的基础内容之后恰好追加一次。

```text
prepared messages near threshold
  -> plan summary range
  -> bounded completion request
  -> persist Thread summary
  -> rebuild mandatory + optional base sources
  -> append unchanged active tool tail
  -> validate messages and manifest
  -> continue model loop
```

压缩与 Turn 共用同一个提供商重试预算。如果 mandatory 基础内容加活动尾部无法容纳，
Turn 会以 `context_unrecoverable` 终止；系统不会丢弃或重放 observation。

## 提供商中立的模型边界

`modeling/` 定义消息、工具 schema 与调用、流事件、错误、usage、catalog profile 和
`ModelProvider`。提供商只实现：

```text
provider_id
stream(ModelRequest) -> async ModelStreamEvent sequence
```

frozen、提供商中立的模型目录只有一种形状和一个实例：

```text
MODEL_CATALOG
  -> ProviderDescriptor
       -> ModelProfile
```

它是受支持模型 identity、capability、context limit、Provider 内默认项、受支持 region 与
credential 关联的唯一来源。当前目录恰好包含两个 Provider 和四个 model profile：

| Provider | `credential_id` | Region（默认） | Model | Context | Tool | Reasoning | Provider 默认项 |
| --- | --- | --- | --- | ---: | --- | --- | --- |
| `deepseek` | `deepseek` | 无 | `deepseek/deepseek-v4-flash` | 262,144 | 是 | 是 | 是 |
| `deepseek` | `deepseek` | 无 | `deepseek/deepseek-v4-pro` | 262,144 | 是 | 是 | 否 |
| `kimi` | `kimi` | `cn`、`global`（`cn`） | `kimi/kimi-k2.6` | 262,144 | 是 | 是 | 是 |
| `kimi` | `kimi` | `cn`、`global`（`cn`） | `kimi/kimi-k2.5` | 262,144 | 是 | 是 | 否 |

Catalog 不表示 credential 已存在，也不选择活动 model 或 region。Credential 来源与存在性、
`providers.default_model`、Thread 选择和已配置的 Kimi region 仍属于动态的
Application/configuration state。Catalog 默认项只在恰好配置一个 model Provider 时作为
确定性 fallback。

具体 DeepSeek 与 Kimi 适配器位于 `providers/`，只由 Application 组装层实例化。
它们将 SDK payload 转换为中立事件并规范化错误；Agent 和 Context 绝不导入 OpenAI
client 或提供商适配器。

Provider 资源组装也保留在 `providers/`。一个 managed factory 会捕获 candidate 配置，并为
每个已配置 provider 最多创建一个可复用的异步 SDK client。`RuntimeResources` 按 provider
和 model 缓存中立的 `ModelGateway`，因此多个模型可以共享 provider client，而无需读取可变
Application 状态。Candidate retirement 会关闭内部持有的 client；注入的 gateway factory
只借用。Credential validation 每次尝试使用独立 client，并在成功、错误、超时或取消时于
有界清理期限内关闭。

`ModelGateway` 冻结一次 catalog 选择，并强制执行流行为。它只会重试在任何可见输出或
完成之前发生的可重试失败，会报告重试事件、保留取消，并要求恰好一个匹配的、已完成的
模型 Turn。一旦文本、reasoning 或工具调用已经可见，透明重放会复制可观察工作，因此
被禁止。

Catalog 只描述受支持模型，不构造 client。具体 factory 与 adapter dispatch 仍位于
`providers/`，显式保留官方 endpoint `https://api.deepseek.com`、
`https://api.moonshot.cn/v1` 与 `https://api.moonshot.ai/v1`。这不是 provider registry 或
DI container，也没有为了抽象而虚构第三个 model Provider。Web Provider selection 和
catalog concern 留在独立的 Web/configuration 边界；Tavily Web search/fetch capability
绝不会进入 `ModelCatalog`。

Application 通过 Protocol v4 `ApplicationState` 发布 catalog，并与动态的
`provider_credentials` 并列。TUI 会校验这些字段，并从中推导 startup 与 credential setup，
不复制 model 或 Provider enum。Application 从同一 catalog 生成 `/model` 的
`CommandSelection` option，TUI 只做通用渲染。在 Python 边界，依赖方向是
`config -> modeling`；`modeling/` 不再导入 configuration。Application 将两者与具体
Provider factory 组合起来。

策划后的 catalog 是封闭的，不接受任意 provider/model 字符串。这会限制灵活性，但能让
配置、能力、上下文上限、身份报告和测试就受支持的产品达成一致。

## Skills

Skills 提供有界的指令包。发现优先级依次为 bundled、user、workspace；后出现的同名来源
会遮蔽先前 descriptor，并产生诊断。禁用的名称会被排除。每个有效 descriptor 都会获得
版本化 identity，由规范化 metadata、已固定的 `SKILL.md` fingerprint 与内容派生。

Workspace Skills 的路径由受信项目控制，因此处理更严格：

```text
workspace anchor
  -> .awesome
  -> skills
  -> package directory
  -> SKILL.md / resource
```

每个组件都必须是普通目录或文件，不能是 symlink、junction 或其他 reparse point。
发现过程会存储 anchor、root、package 身份以及初始 `SKILL.md` fingerprint。加载和资源
读取会重新打开固定的目录树，校验这些身份与包含关系，再进行有界 UTF-8 读取。Bundled
和 User package 固定 package 与 `SKILL.md` identity；Workspace package 还固定完整的受信任
anchor 链。因此，发现后替换 package 会 fail closed。一个无效 package 只产生诊断，不会
抑制有效 package。

发现时 fingerprint 适用于 `SKILL.md`，而非所有资源。一次资源遍历会证明其组件是普通、
受包含的，并在该次受检打开的前后保持稳定；但它不会把普通嵌套目录或资源内容与发现时
身份比较。因此，在资源读取开始前已经安全完成的替换可以被读取到。

本地 User 包管理属于 Application use case，不是 Agent 工具，也不是第二套发现实现：

```text
awesome skills CLI
  -> argument parsing + optional TTY confirmation
  -> private Protocol v4 skill.list / skill.install / skill.remove
  -> Application SkillManagementService
  -> one blocking worker operation
  -> SkillPackageManager validation + recoverable package transaction
  -> <AWESOME_HOME>/skills
```

包 RPC 只在 Application 恰好处于 `UNINITIALIZED` 时准入；一个由 Application 持有的
pre-initialize guard 使三者彼此互斥，也与 `initialize` 互斥。RPC 本身不会构建
`WorkspaceRuntime`、Thread、Turn、graph、model 或 Tool Executor，并且不会改变 phase。Node
launcher 只拥有命令语法、稳定输出和移除确认；manifest、archive、path、size、identity、
locking 与 recovery 规则全部只由 Core 拥有。官方 CLI 收到一个有界 product result 后会关闭
私有 Core。其他私有 client 可以在同一个仍未初始化的 Core 上执行 mutation，随后调用
initialize；discovery 会看到变更后的包。Session 一旦初始化，其 catalog 就保持不可变，绝不
hot-update。

全新安装通过一次同目录 no-replace rename，把完整校验后的 stage 发布到不存在的 target。
替换是两次正向 rename（target 到 quarantine，再由 stage 到 target）组成的可恢复序列，不是
一次原子替换。移除同样先 quarantine target，再执行已发布后的清理。Marker 驱动发布前回滚
与发布后向前清理。调用方取消时会等待 owned worker 收敛，不设 wall-clock 清理 deadline，
随后重新抛出取消。

`auto` 会冻结最多 64 个 identity 的确定性 catalog，并暴露 `load_skill` 与
`read_skill_resource`；它不会执行 Skill。`off` 不冻结 Skill 来源，也不暴露两个工具。
具名模式会 eagerly 冻结正文和 identity 作为 mandatory system context，并且只为该 package
暴露 `read_skill_resource`。

两个工具都使用 `context.read`。Registration 自有的硬准入会在 permission policy 之前把
操作和 package identity 与冻结 Turn scope 匹配，handler 在返回内容前再次检查 identity。
因此，即使重建后的 Runtime 发现了不同 package，恢复仍会保留 checkpoint 的 authority。
`allowed-tools` 只描述预期兼容性，绝不会授予权限或绕过共享 Tool Executor。

## 本地与云端 Memory

本地用户和工作区 Memory 彼此独立、默认禁用，并作为不受信的参考 Markdown 读取。
受管理条目具有稳定标识；用户与工作区来源中规范化后重复的事实，会从优先级较低的
渲染副本中移除。

Mem0 Cloud 是可选适配器，也是目前唯一的外部 memory 提供商。Recall 受到查询边界和
身份范围约束，会与本地 memory 去重，并作为不受信上下文表示。云端失败会成为诊断，
不会导致整个 Turn 配置无效。回答后的 distillation 使用独立策略，绝不会默认上传原始
transcript。

`memory/finalization.py` 拥有 `Mem0PostAnswerFinalizer`，即 Agent 通用
`PostAnswerFinalizer` 端口在 Memory 边界内的实现。Application 只会为已启用且完整的 Mem0
session 装配它；否则注入 Agent 的 disabled 实现。该 adapter 在 Memory 边界内转换 Mem0
identity、distillation status 与 `Mem0Diagnostic`。它返回原回答、distiller 的 model-call/
usage 计费，以及通用 `PostAnswerDiagnostic`：保留原 code，message 固定为
`Optional memory operation did not complete.`。构建该 result 后，它会尝试投影已启用的
Memory status。Status 投影失败时会保留为 `memory_status_projection_failed`，固定 message
为 `Optional memory status projection failed.`，回答和计费不会因此丢失。Status 投影取消
不会转换为 diagnostic；原始取消会传播到 Agent 边界。Agent 看不到任何 Mem0 特定类型。

通用 request 可以携带有序工具 citations，但当前 Mem0 实现不会消费它们、重写 citation
marker 或改变回答。无效输出、预算超限和意外失败会成为 Agent warning，同时保留已经生成
的回答。逃出 adapter 的取消会保留此前 checkpoint 中的回答，不触发另一条 Agent warning，
而是立即重新抛出；详见
[Application 与 Agent](application-and-agent.zh-CN.md#回答后-finalizer-端口)。

Mem0 SDK 当前会在异步 client 构造函数中执行同步 credential validation。Awesome 在支持
取消的 worker 中运行该构造函数，避免阻塞事件循环，并且只把内部创建的 client 注册到
runtime 退出栈；注入 client 只借用。如果 SDK 构造函数超过有界取消清理期限，Python 无法
停止该 worker，因此 Awesome 会及时返回取消、避免无限等待，并通过 late-completion 清理
hook 关闭它最终生成的 client。

Memory 工具有自己的 memory policy。启用 Memory 不会授予工作区、shell 或 MCP 能力。

## MCP catalog 与调用

MCP 扩展共享工具 registry，而不是 Agent graph。一个 server-specific lock 覆盖 catalog
加载、编译与发布：

```text
start stdio client
  -> initialize
  -> consume all tool pages within bounds
  -> compile every input/output JSON Schema without network retrieval
  -> assign generation
  -> validate the final namespaced names
  -> build all generation-bound handlers
  -> Registry validates aggregate bounds and atomically replaces namespace
  -> without awaiting, Manager publishes generation + client + catalog + CONNECTED
```

Catalog compiler 默认使用 JSON Schema Draft 2020-12，只接受受支持的显式 dialect
和 required vocabulary。它会校验标准组合、条件、范围、pattern、数组和属性约束。
`format` 保留 JSON Schema 默认的 annotation 语义。`$ref`、`$dynamicRef` 和
`$recursiveRef` 必须解析到同一个 schema resource 内；validator 能进行 I/O 之前，
远程引用就会被拒绝。

单服务器 catalog 的上限为 128 个工具、128 页、每个 input 或 output schema 256 KiB、
完整 catalog 1 MiB、schema 深度 64，并且最终 `mcp.<server>.<tool>` 名称最多 128 个字符。
共享 Registry 还会单独限制 built-in 与所有 extension namespace 的有效聚合快照：最多
128 个工具，规范化、面向模型的定义（`name`、`description` 与 `input_schema`）合计最多
1 MiB。因此，满足单服务器限制的候选项仍可能无法通过共享预算。

Registry replacement 会先校验完整聚合候选项，再以全有或全无的方式替换 namespace。
这个同步调用成功后，Manager 为同一 generation 连续赋值并最终设置 `CONNECTED`，中间没有
`await`。所以 `CONNECTED` 表示存活 client、已编译 catalog 和完整 Registry namespace
均已发布。重复名称、无效契约、cursor 循环、超时或任一类越界都会关闭新 client、使
generation 失效、移除该服务器 namespace，并只报告一条固定、有界的诊断。系统绝不会
发布有效子集，也不会移除其他服务器已经提交的 namespace。

Handler 会捕获 catalog generation。远程 I/O 之前，Pydantic 使用已编译 schema 校验
参数；随后 manager 校验 server、tool 和 generation 仍是当前值。Restart 会在重连前
移除旧 namespace。`call_tool()` 绝不会惰性重连。

MCP 内层调用期限为 30 秒，Tool Executor 封装期限为 40 秒。超时或连接丢失会使 catalog
失效，并返回 `UNCERTAIN_OUTCOME`：外部服务器可能已经执行操作。Awesome 不会在同一
Turn 中重连或重放。取消会执行有界连接清理，并继续传播取消。

结构化输出会在 schema 遍历和渲染前受限。声明的 `outputSchema` 必须与
`structuredContent` 匹配；否则调用失败，且不会暴露参数或 schema 细节。文本、媒体块
数量、JSON 字节数、节点数和深度都有各自的上限。

## 扩展不变量

每个扩展都必须维持以下规则：

- 上下文有标签、有边界，并具有 manifest 来源信息；
- 扩展文本不是权限或策略；
- 工具进入现有 registry namespace 与共享 executor；
- 审批和外部 I/O 之前完成校验；
- 取消会被传播，而不是转换为普通错误；
- 结果不确定的外部作用不会自动重放；
- 一个有问题的 package 或 server 不会破坏无关来源；
- 新的 provider abstraction 必须由真实的第二个实现来证明其必要性。

## 设计取舍

- 保留 mandatory 来源可能让一个有损提示词构建器原本能勉强处理的 Turn 失败；这样避免
  静默改变规则或工具历史。
- token 估算和预留余量牺牲一些容量，以换取提供商中立的可预测性。
- 封闭模型 catalog 要求为新模型修改代码，却能使能力、限制、凭据和 UI 选择保持一致。
- 完整快照 MCP 发布会延迟工具可用时点，直到 Manager catalog 和 Registry 聚合快照均
  有效；这会防止部分 catalog、部分 namespace、过期 handler 和超长下游名称。
- 不可变工作区指令与固定的 Skill package/`SKILL.md` lineage 要求编辑后启动新会话；
  惰性读取的 Skill 资源则是每次打开都安全，而不是整个 package 的内容快照。

## 源代码与测试索引

- Context：`context/builder.py`、`context/models.py`、`context/tokens.py`、
  `application/context.py`
- 指令与路径：`context/workspace_instructions.py`、`context/path_refs.py`
- 压缩：`context/compression.py`、`agent/nodes.py`
- 模型：`modeling/`、`providers/deepseek.py`、`providers/kimi.py`
- Skills：`extensions/skills/discovery.py`、`loader.py`
- MCP：`extensions/mcp/catalog.py`、`manager.py`、`adapter.py`、`stdio.py`
- Memory：`memory/finalization.py`、`memory/`
- 测试：`tests/unit/context/`、`tests/integration/test_context_pipeline.py`、
  `tests/integration/test_skills_mcp.py`、
  `tests/structural/test_context_architecture.py`、
  `tests/structural/test_extension_architecture.py`
