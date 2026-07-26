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
3. 已选择的 Skill；
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

以下来源一经选中即为 mandatory：产品和模型身份、工作区指令、已选择的 Skill、显式
路径、当前输入，以及未闭合的工具链。系统绝不会为了让提示词容纳它们而静默截断。
如果 mandatory 来源加预留上下文超过生效输入上限，Turn 会以 context overflow 失败，
而不会改变指令含义或丢弃工具 observation。

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

具体 DeepSeek 与 Kimi 适配器位于 `providers/`，只由 Application 组装层实例化。
它们将 SDK payload 转换为中立事件并规范化错误；Agent 和 Context 绝不导入 OpenAI
client 或提供商适配器。

`ModelGateway` 冻结一次 catalog 选择，并强制执行流行为。它只会重试在任何可见输出或
完成之前发生的可重试失败，会报告重试事件、保留取消，并要求恰好一个匹配的、已完成的
模型 Turn。一旦文本、reasoning 或工具调用已经可见，透明重放会复制可观察工作，因此
被禁止。

策划后的 catalog 是封闭的，不接受任意 provider/model 字符串。这会限制灵活性，但能让
配置、能力、上下文上限、身份报告和测试就受支持的产品达成一致。

## Skills

Skills 提供有界的指令包。发现优先级依次为 bundled、user、workspace；后出现的同名来源
会遮蔽先前 descriptor，并产生诊断。禁用的名称会被排除。

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
读取会重新打开固定的目录树，校验这些身份与包含关系，再进行有界 UTF-8 读取。因此，
发现后替换 package 会 fail closed。一个无效 package 只产生诊断，不会抑制有效 package。

发现时 fingerprint 适用于 `SKILL.md`，而非所有资源。一次资源遍历会证明其组件是普通、
受包含的，并在该次受检打开的前后保持稳定；但它不会把普通嵌套目录或资源内容与发现时
身份比较。因此，在资源读取开始前已经安全完成的替换可以被读取到。

Bundled 和 user Skills 保留原有、更宽松的来源行为。严格 reparse 策略被有意限制在
workspace 内容，以免意外重新定义用户管理的扩展布局。

选中的 Skill 会成为 mandatory system context。它的 `allowed-tools` metadata 描述预期
兼容性，但绝不会授予权限或绕过共享 Tool Executor。

## 本地与云端 Memory

本地用户和工作区 Memory 彼此独立、默认禁用，并作为不受信的参考 Markdown 读取。
受管理条目具有稳定标识；用户与工作区来源中规范化后重复的事实，会从优先级较低的
渲染副本中移除。

Mem0 Cloud 是可选适配器，也是目前唯一的外部 memory 提供商。Recall 受到查询边界和
身份范围约束，会与本地 memory 去重，并作为不受信上下文表示。云端失败会成为诊断，
不会导致整个 Turn 配置无效。回答后的 distillation 使用独立策略，绝不会默认上传原始
transcript。

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
- Memory：`memory/`
- 测试：`tests/unit/context/`、`tests/integration/test_context_pipeline.py`、
  `tests/integration/test_skills_mcp.py`、
  `tests/structural/test_context_architecture.py`、
  `tests/structural/test_extension_architecture.py`
