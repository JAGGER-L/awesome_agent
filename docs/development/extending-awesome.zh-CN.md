# 扩展 Awesome

一个成功的扩展会进入现有 authority boundary。Provider 转换模型 I/O，tool 进入
Registry/Policy/Executor 路径，Skill 提供有界指令，MCP server 提供一个通过 Manager 所有的
Registry 临界区发布的完整候选项。所有扩展都不会建立第二 Agent loop、command runtime、
permission system 或 database owner。

## 判断是否应该扩展

添加 abstraction 前，先回答：

1. 哪一项用户工作流无法用现有 provider、tool、command、Skill 或 MCP server 表达？
2. 哪个 package 已经负责这项决策？
3. 这是第二个具体实现，还是只为预测的未来需求？
4. 哪些输入不受信，它们在哪里校验并受限？
5. Timeout、cancellation、partial output 和 process crash 时会怎样？
6. 外部作用是否可安全重试，还是必须标记为 uncertain？
7. 这会改变 Protocol v5、storage schema、permission 或 packaging 吗？

优先在当前契约后实现具体功能。不要为一个假设实现创建通用 provider/backend/plugin 层。

## 添加模型提供商

Provider 实现 `modeling/provider.py` 中的中立 `ModelProvider` protocol：

```python
class ModelProvider(Protocol):
    @property
    def provider_id(self) -> ProviderId: ...

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]: ...
```

Adapter 接收中立 message/tool definition，只产出中立 stream event。它不能导入
Application、Agent、Storage、Context 或 TUI。

### 必需的设计工作

真正新增 provider 通常会改变以下所有封闭契约：

- `modeling/turns.py` 中的 `ProviderId` 与 continuation/error identity；
- `modeling/catalog.py` 中策划后的 model profile、capability、default 与 context limit；
- 严格配置、credential status、environment source 和 help URL；
- `providers/` 下的一个具体 adapter；
- provider factory 与 Application composition；
- `/model`、`/auth`、`/config`、`/status` 和 `/doctor` fact；
- 公共 enum 改变时的 Protocol fixture 和严格 TUI schema/picker；
- 如果需要新的 runtime dependency，则更新 docs、test、dependency inventory、installer/
  release verification。

不要为了避免更新 catalog 而接受任意 model string。封闭 catalog 让受支持 capability 和
context limit 在配置、Agent 与 UI 间保持一致。

### Stream 不变量

Adapter 必须：

- 保留请求 message/tool 顺序；
- 确定性组装碎片化 tool argument；
- 生成稳定 call ID 与 index；
- 规范化 stop reason 与 usage；
- 区分 authentication、rate limit、timeout、connection、transient、invalid-request、
  context-length 和 protocol failure；
- 严格按中立 error 契约标记 retryability；
- 传播 `CancelledError`，不进行转换；
- 最多发出一个 `TurnCompleted` 或一个终态 `TurnFailed`；
- 绝不记录或包含 secret 值或无界原始 provider payload。

`ModelGateway` 只会重试在可见输出或 completion 之前发生的 retryable failure。Adapter
不能在 text、reasoning 或 tool call 可见后增加一套相互竞争的透明 retry。

### Provider 测试

至少覆盖：

- text-only、reasoning、tool call、mixed delta 和 completion stream；
- 碎片化/无效 tool argument 与重复/未知字段；
- 每种规范化 error class 与 retryability；
- iteration 期间取消；
- provider/model identity mismatch；
- usage 与 continuation 处理；
- 可见输出后不重试；
- 凭据缺失/错误时的选择；
- 使用 fake client 的 Gateway 集成测试。

Live credential 只能用于显式启用的 external release test。

## 添加 built-in tool

当 Awesome 本身必须保证行为、policy、Change Journal 集成和跨平台生命周期时，适合使用
built-in。项目特定集成通常更适合 MCP。

### 1. 定义严格参数

使用带长度、范围和形态限制的 Pydantic model。除非现有契约明确允许，否则拒绝额外字段。
昂贵解析或 I/O 必须发生在校验后。

```python
class InspectArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=1_000)
    max_items: int = Field(default=100, ge=1, le=1_000)
```

### 2. 选择唯一 capability

使用最窄的当前 capability：

- `workspace.read`；
- `workspace.write`；
- `workspace.delete`；
- `shell.execute`。

如果都不适用，新增 capability 属于 permission-model 变更。实现前定义它在 Request
approval、Accept edits、Full access、temporary grant、MCP/unknown handling 和 hard
denial 下的行为。

### 3. 实现一个 handler

Handler 接收已校验参数及 `ToolExecutionContext`，返回 `ToolOutput`。预期用户/环境失败
抛出带稳定 `ToolErrorCode` 的 `ExpectedToolFailure`。意外不变量违反应逃逸，使 executor
显式终止 Turn。

Read tool 使用共享 workspace policy 和有界、校验身份的 reader。Write/delete tool 必须
通过 Change Journal 与固定文件系统 primitive 执行 mutation。不要直接调用
`Path.resolve()`、`read_text()`、`write_text()`、`unlink()` 或 subprocess 形成替代作用路径。

### 4. 只注册一次

在 built-in registry composition 中注册一个完整工具，其中包含 `ToolSpec`、严格 input
model、handler、类型化 operation description、hard admission、replay-safety 分类和可选内部
timeout resolver，以及可选 handler cancellation grace。Capability、read-only 状态与展示
metadata 属于 spec。名称必须匹配共享 tool-name pattern，且不能与 built-in 或 extension
namespace 冲突。

模型可见 schema 来自 input model。内部清理预算不能伪装成模型参数。Hard admission
消费已校验参数与执行 context，拒绝不可关闭的不安全情况。只有它成功后，operation
description 才恰好运行一次，返回展示与审批所需的有界事实。它可以为已准入操作执行有界
metadata inspection，但仍发生在审批、handler 或任何外部作用之前。Admission 与 capability
policy 保持分离；权限模式或临时 grant 不能覆盖它。

### 5. 定义生命周期行为

记录并测试：

- 唯一执行顺序：resolve、strict validation、hard admission、一次 typed description、
  capability policy、approval、deadline、handler，再到 result/event/audit；
- 审批、handler 执行或外部作用前的 argument 与 path validation；
- mode grant 之前的 hard-admission rule；
- 从已校验 operation fact 派生的审批文本；
- 总 timeout 和 handler cancellation grace；
- output/content/presentation 边界；
- 一条 ToolActivity 和一个终态 event；
- 不可逆作用前是否记录 attempt；
- 作用完全、部分或完全不可逆；
- replay 是否可被证明安全，未知分类必须 fail closed；
- 崩溃与启动恢复证据。

### 工具测试

为 schema、正常 result、预期 error、类型化描述、未知 capability、所有 permission mode、
hard admission、replay safety、timeout、cancellation 和 output bound 添加单元测试。工具
涉及 workspace identity、Change Journal、进程、Application approval、transcript activity
或 recovery 时添加集成测试。在所属 OS 上测试真实平台 primitive。

## 扩展 shell 行为

不要添加第二个 shell parser，也不要只在 UI 中评估命令。扩展
`core/tools/command_policy.py` 中 dialect-aware 的纯 policy，并在任何 host 上运行其
显式 CMD、POSIX 与 PowerShell 测试矩阵。

一项 policy rule 需要覆盖：

- 规范化 executable 拼写与绝对路径；
- 后缀/大小写变体；
- 复合、pipeline、换行与已知 wrapper 嵌套；
- working-directory 变化；
- 支持处的 encoded 或 literal wrapper payload；
- 包含相同文字的无害命令；
- 三种 permission mode 与 direct execution；
- denial 绝不会启动 Process Runner 的证明。

应把 circuit breaker 描述为已知防误操作机制，而不是通用恶意代码检测。

## 添加斜杠命令

Command 是确定性的 Application 或 Ink 操作，绝不提交隐藏 Agent prompt。

1. 将名称与 owner 添加到 `CommandName`/`COMMAND_OWNERS`。
2. Core 所有的命令添加一个聚焦 command service handler，并在 composition 中接入完整的
   immutable dispatcher。
3. 判断它是无副作用 observation，还是需要 exclusive foreground lease。Observation
   状态是窄并发契约，不是便利 flag。
4. 定义可辨识 `CommandOutcome` payload 和可选权威 state effect。
5. 更新 Protocol fixture、TypeScript schema、catalog、parser/help、exhaustive Presenter
   和 UI-flow test。
6. 记录 input、empty state、interaction、error 与 busy 行为。

不要在 Python 中格式化终端输出，也不要在 TypeScript 中添加通用 object renderer。

## 编写 Skill

Skill 是 package directory，其名称应匹配 `SKILL.md` frontmatter 中的 `name`。Frontmatter
可以声明 description、allowed tools、license、compatibility 和 metadata。Body 与资源是
有界读取的 UTF-8 文本。

Skill 指令属于 context。`allowed-tools` 表示兼容性，不授权工具。需要项目特定 action
的 Skill 应调用已有工具或使用 MCP server，而不是嵌入另一个 executor。

对于 bundled Skill：

1. 在 `src/awesome_agent/extensions/skills/bundled/` 下添加 package；
2. 即使当前 parser 仍会 coercion 部分 scalar value，也使用规范的有界 string/list
   frontmatter type；
3. 解释何时选择 Skill，以及如何停止；
4. 将引用资源保持在 package 内；
5. 添加 discovery、load、resource、token-bound 和 packaging test。

Workspace Skill 代码必须保留 anchor/package identity 重校验。不要为了支持 linked
package 而削弱它；如果用户有意管理 linked layout，请使用 user Skill。

内置 Skill 工具注册项会在派生 target 前 hard-admit catalog membership、可移植词法资源
路径以及 no-follow plain-file 边界。Handler 会在读取时重复安全检查；Workspace Skill 还会
比较 discovery 时捕获的 package identity，并在其被替换时 fail closed。

## 通过 MCP 集成

MCP 是独立运营工具的首选边界。Server 配置不含 secret：只有 command、argument、
environment variable name、source 与 enabled state。Secret value 从 environment 解析，
绝不来自 workspace YAML。

Client 必须完成并编译整个分页 catalog 后，才能发布任意 registry item。支持标准 JSON
Schema constraint，但引用必须保持本地，禁止网络 retrieval。遵守 tool/page/schema/
catalog/depth 的单服务器限制：最多 128 个工具和 128 页，每个 input 或 output schema
256 KiB，每个 catalog 1 MiB，深度 64，并且最终 `mcp.<server>.<tool>` 名称最多 128 个字符。
还必须计入共享 Registry 聚合预算：built-in 与所有 extension 合计最多 128 个工具，规范化、
面向模型的定义合计最多 1 MiB。

Manager 会在持有 server lock 时编译并发布候选项。Registry replacement 会先校验并替换
完整 namespace；随后发布匹配的 generation、client、catalog 和 `CONNECTED`，中间没有
`await`。失败必须关闭候选 client、设置 `ERROR`、移除该服务器 namespace，同时保留无关
namespace。诊断绝不能暴露原始 catalog 数据。

禁止：

- 在 `call_tool()` 中惰性重连；
- restart 时保留旧 registry namespace；
- 注册无效 catalog 中的有效子集；
- 审批或远程 I/O 后才校验参数；
- 用旧 validator 配合新 catalog generation；
- 在同一 Turn 重放超时或断线调用；
- 强制 JSON Schema `format` 检查，却声称使用默认语义；
- 在任一权限模式中把 MCP 分类为隐式允许。

MCP input validation error 必须是通用错误，不能暴露原始 argument 或 schema。Output
validation 与 JSON traversal 在各自 byte、node、depth 和 content-block 边界内执行。

每个 MCP 注册项都显式标记为 non-replayable。恢复通过同一个 Registry 契约消费该
metadata，不得从 `mcp.` 前缀推断安全性。注册项缺失或未知时会 fail closed，进入同一
interaction。两种情况都不会自动重试；只有用户显式选择 Retry 才可继续旧 checkpoint。

## 添加 Memory provider

Awesome 当前有本地 Markdown Memory 和一个可选 Mem0 Cloud adapter。第二个外部 provider
可以证明中立 abstraction 的必要性；在该实现存在前，不要泛化当前 Mem0 client。

Provider 设计必须定义：

- 稳定 user/workspace identity；
- 独立 enablement 与 credential availability；
- 有界 recall，以及相对高优先级本地 memory 的去重；
- untrusted-context label；
- timeout、cancellation、脱敏诊断和 offline 行为；
- 回答后写入是 distilled fact 还是 raw transcript；
- 删除/conflict 语义与用户控制。

Memory 不能 grant Tool capability，也不能成为隐藏 provider fallback。

## 变更存储或恢复

Storage 变更不只是添加 column。应判断字段缺失在当前 Schema 8 中是否有安全解释。如果
没有，则递增 schema identity 并定义旧/新状态的产品行为。把每个受支持的升级作为一个相邻
`N -> N+1` operation 加入 Storage 所属的 migration registry。Registry 必须从显式 floor
到 current 始终保持一条完整线性链；不要添加 branch、gap、历史 adapter，也不要把
migration 逻辑放到该 owner 之外。当前生产链的 floor 是 7、current 是 8，且有一个增加可空
Thread lineage 的 `7 -> 8` step；Schema 1–6 仍然不可迁移。

Migration code 必须保持启动协议：shared-lease read-only preflight、exclusive lease、重新检查
兼容性、在 `application.db.pre-migration.bak` 创建并校验能感知 WAL 的 SQLite backup，然后
在一个 transaction 内执行完整链。只有成功后，启动才能降级 lease 并初始化 repository。
Migration step 只能获得受限的 schema/data connection facade；不得自行 commit、rollback、
创建 savepoint、attach 其它 database，或运行管理 transaction 的 script。步骤失败会回滚
整条链，并保留 backup 供手动恢复；绝不能自动 reset 或 restore 状态。应使用
synthetic multi-step schema 测试 registry，包括数据保留、backup 校验、rollback 和 rollback
结果未知。相同 schema 变更中还要更新 preflight、release contract、双语文档与 recovery test。

为每个新持久化事实说明：

- 所属 package 和 table/path；
- 创建或改变它的 transaction；
- 与 graph checkpoint 和 ChangeSet 的关系；
- crash window 与 compare-and-swap 条件；
- terminal/cancellation cleanup；
- reset 包含或保留规则；
- 有界 serialization 与 forward/legacy interpretation。

绝不能让 Application 重建 LangGraph channel，也不能让 checkpoint 成为产品 transcript。

## 添加 protocol 或 TUI 界面事实

遵循完整 Protocol v5 链：

```text
Python strict model
  -> facade/method/event owner
  -> fixture generator
  -> valid + invalid v3 fixtures
  -> TypeScript strict schema
  -> Surface effect/reducer if authoritative state changes
  -> exhaustive Presenter/component
  -> contract + UI-flow tests
```

未知字段是错误。Optional 与 nullable 不同。保持安全 integer limit 与 frame bound。未来
非 Ink 界面会适配 facade 与 event；它不能直接调用 Agent、tool 或 storage。

## 扩展审查检查清单

- [ ] 一个现有 package 负责新行为。
- [ ] 没有第二 graph、executor、command runtime、policy 或 storage owner。
- [ ] Input 在审批、handler 执行或外部作用前严格且有界。
- [ ] Hard admission 与 capability policy 显式且保持分离。
- [ ] Replay safety 显式，metadata 缺失或未知时 fail closed。
- [ ] Timeout、cancellation、cleanup 与 uncertain outcome 已测试。
- [ ] 持久化事实和恢复规则已有文档。
- [ ] Secret 与原始 payload 不能进入 event、audit、fixture 或 log。
- [ ] 已审查 dependency ownership 与 packaging。
- [ ] 跨越边界时 Protocol/TUI 契约同步更新。
- [ ] 用户与架构文档解释 what、how、why、limit 与 tradeoff。
- [ ] Focused、integration、structural、platform 与 release 证据匹配真实风险。
