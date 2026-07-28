# 私有 Core/TUI protocol v5

Awesome 的 Ink 进程与其唯一的 Python Core 子进程通过私有 stdio 使用 newline-delimited
JSON-RPC 2.0 通信。该 protocol 是内部组件边界，不是远程 API：它没有网络 listener、
authentication scheme、compatibility proxy，也不承诺第三方客户端可以独立混用不同版本。

Protocol 版本 **5** 与精确的已安装产品版本配对。当前仓库产品版本是 **1.3.0**。Event
envelope 有独立版本 **1**。两个 contract identifier 都来自
`contract-versions.json`；产品值仍来自 `VERSION`。

## 进程与传输

- Ink 在活动 Workspace 中启动一个 `awesome-core` 子进程。
- Request 和 Core event 在 stdin/stdout 上使用 UTF-8 NDJSON：每行恰好一个 JSON object。
- Core log 发送到 stderr；stdout 专用于 protocol frame。
- Core request reader 在换行前最多接受 1,048,576 字节。空行被忽略；无效 UTF-8、无效
  JSON 或超大 request 会返回 JSON-RPC parse error `-32700`，reader 随后继续。
- TUI 编码 request 和解码 Core output 时采用相同的 1 MiB 限制。Core 在写入 stdout 前按
  紧凑 UTF-8 内容的实际字节数进行检查。超大的 request result 会被替换为有界、不可重试的
  产品错误 `result_too_large`；超大的 event 会在写入任何字节前被拒绝。Core 不会发送随产品
  发布的 TUI 必须因大小而拒绝的 frame。
- Output serialization 是紧凑的 UTF-8 JSON，并保留非 ASCII 文本。非有限数字和无法表示
  为 UTF-8 的文本永远不会写入。已经完成但结果不可表示的 request 会使用同一 request ID
  替换为一个脱敏的 `internal_error` Application failure。
- 有界 stdout queue 保存 64 个 frame；无人 drain/已满的 queue，或五秒内未完成的 write 会
  破坏 channel，而不是允许内存无限增长。
- 如果显式 shutdown 尚未执行，EOF 或 fatal channel error 会关闭 Application。

```text
Ink                     Protocol Host              Application
 |                            |                          |
 |-- initialize(v5, exact) -->|                          |
 |                            |------- initialize ------>|
 |                            |<-- ApplicationResult ----|
 |<---- JSON-RPC response ----|                          |
 |                            |                          |
 |------ turn.submit -------->|                          |
 |                            |-- atomically admit ----->|
 |<---- event: operation.started ------------------------|
 |                            |<-- OperationAccepted ----|
 |<---- JSON-RPC response ----|                          |
 |<---- event: turn.started / deltas / tools ------------|
 |<---- event: turn.completed ---------------------------|
 |<---- event: operation.completed ----------------------|
```

Application 返回 `OperationAccepted` 之前会先写入 `operation.started`，因此在共享 output
stream 上，该 event 一定先于匹配的 JSON-RPC response。一旦 Operation task 被调度，后续
Turn 或 Tool event 也可能与该 response 竞速。随产品发布的 TUI 会在提交工作前启动 event
consumer，并通过 `operation_id`、`turn_id` 和 `client_message_id` 关联这些自带身份的 event；
客户端不能仅仅因为 request promise 尚未 resolve 就丢弃 event。

## JSON-RPC request 形状

```json
{"jsonrpc":"2.0","id":"request-1","method":"application.getState","params":{}}
```

只允许 `jsonrpc`、`id`、`method` 和 `params`。`jsonrpc` 必须是 `"2.0"`；`method` 必须是非空
字符串；`params` 默认为 `{}`，并且对已注册 method 必须是 object。ID 是 1–128 个 Unicode
标量、且不含未配对 UTF-16 surrogate 的 string，或 JavaScript 可互操作的 safe integer；
绝不能是 boolean 或 `null`。Core 与 TUI 执行同一契约。Method 参数中有类型的 integer 字段
采用相同安全范围，并且必须是整数 JSON number。

Safe-integer 规则也覆盖 result、error 和 event 中所有表示整数的 number，包括 sequence、
token counter、duration 和通用 diagnostic data。非整数 number 必须为有限值。Core 在写入
frame 前会递归拒绝不安全整数、非 string object key、无效 Unicode、非 JSON container，以及
深度超过 64 层的结构；TUI 解码时会执行对应的安全数值规则。

Notification 省略 `id`，即使 method/parameter 失败也不接收 response。生产客户端应对生命周期/
控制 method 使用 request，以便观察是否被接受。ID 在活动期间以及最近完成的 4,096 个 ID 中
都不能复用；重复会产生 `-32600 Invalid Request`。

Wire model 是严格的。未知 property 验证失败。可选值通常应省略，而不是发送 `null`；
list/read pagination 字段和 credential `api_key` 会显式拒绝 null。

## 握手状态机

Application 的 `ApplicationBootstrap` 从 `UNINITIALIZED` 开始。Host 没有独立的 bootstrap
phase；它会查询 Application admission，并把拒绝转换为既有握手错误。`initialize` 必须使用：

```json
{
  "jsonrpc": "2.0",
  "id": "init-1",
  "method": "initialize",
  "params": {
    "protocol_version": 5,
    "client_name": "awesome",
    "client_version": "1.3.0"
  }
}
```

Protocol 不匹配返回产品错误 `protocol_version_incompatible`。`client_name` 不是 `awesome`，
或产品版本与 Core 不相等时，返回 `client_version_incompatible`。因此，即使 v3 客户端的包版本
碰巧与 Core 相同，也会明确失败。

成功的 `InitializeResult` 包含产品/protocol 版本、session ID、Workspace 展示信息、
capability 和一种状态：

| 状态 | 含义 | 下一步操作 |
| --- | --- | --- |
| `ready` | Workspace 和状态已激活。 | 允许普通 method。 |
| `trust_required` | 由仓库控制的输入尚未获得信任。 | 解决提供的 interaction；`trust` 会让 Application 进入 ready。 |
| `state_reset_required` | Application 状态早于 schema 7。 | 解决 reset/deny；reset 后再次 initialize。 |

当前 capability 是 `threads`、`turns`、`direct_commands`、`commands`、`tools`、`skills`、
`mcp`、`local_memory`、`mem0_cloud`、`web` 和 `citations`。

Host 绝不会通过解析序列化 result payload 来推进 readiness；类型化 initialize 与 interaction
结果会先更新 Application；该路径属于 Protocol v5 wire contract。

Ready 前，普通 request 会收到 JSON-RPC `-32002`，diagnostic 为 `server_not_initialized` 或
`server_not_ready`。有意保留的例外是 `skill.list`、`skill.install` 与 `skill.remove`：三者都
只在 Application 恰好处于 `UNINITIALIZED` 时准入。Application 会为请求保留一个互斥的
pre-initialize transition；该 transition 活动期间，另一个 Skill 包请求或 `initialize` 会收到
`preinitialize_operation_in_progress`。请求完成不会调用 `initialize`，也不会推进 bootstrap
phase；Application 仍处于 `UNINITIALIZED`。

一旦 initialization 已开始，或 Application 已进入任何后续 phase，三个 Skill 包 method 都会
收到 `skill_management_requires_uninitialized`。因此，私有 client 可以在同一个 Core 上依次
运行包 method，再调用 initialize；初始化会发现变更后的 User 包。这不会热更新已经初始化的
Session 所持有的不可变 catalog。Initialize 运行期间，第二次 initialize 会收到
`initialization_in_progress`。等待期间允许匹配 bootstrap 的 `interaction.respond`。
`operation.cancel` 和 `shutdown` 是紧急 control，在 ready 前仍然允许。

## Method catalog

所有 method result 都使用下文说明的 `ApplicationResult<T>` envelope。长度是 JSON 解码后的
Unicode string 长度。

| Method | 严格 params | 成功 value |
| --- | --- | --- |
| `initialize` | `protocol_version`；1–128 的 `client_name`；1–64 的 `client_version` | `InitializeResult` |
| `skill.list` | `{}` | `{ "skills": [{ "name": string, "description": string }] }`；最多 512 个唯一且按名称排序的条目 |
| `skill.install` | 1–4,096 的 `source_path`，不得有首尾空白、NUL、CR 或 LF；可选严格 Boolean `replace`，默认 false | `{ "name": string, "status": "installed" | "replaced" }`；name 是规范名称 |
| `skill.remove` | 匹配 `[a-z][a-z0-9-]{0,63}` 的规范 `name` | `{ "name": string, "status": "removed" }` |
| `application.getState` | `{}` | 当前 `ApplicationState` snapshot |
| `thread.list` | 可选 1–1,024 的 `cursor`；`limit` 为 1–200，默认 50 | Thread、`has_more`、可选 next cursor |
| `thread.search` | trim 后 1–200 的 `query`；可选 1–1,024 的 `cursor`；`limit` 为 1–50，默认 50 | 与 `thread.list` 相同的 `ThreadListResult` |
| `thread.read` | 1–128 的 `thread_id`；可选 `before_sequence >= 1`；`limit` 为 1–500，默认 100 | Thread view、ChangeSet、反向 pagination marker |
| `turn.submit` | `thread_id`；1–200,000 的 `content`；匹配 `client_[A-Za-z0-9_-]+` 且最多 128 的 `client_message_id` | Operation、Thread、Turn 和 client-message ID |
| `direct.execute` | `thread_id`；`command` 为 1–8,000，与委托的 `execute` 工具一致 | Operation 和 Thread ID |
| `command.execute` | `name`；可选 string array `arguments` | 一个有类型的 `CommandOutcome` |
| `provider.credential.set` | 见下文 | Provider、status、可选 selected source、diagnostic code |
| `interaction.respond` | 1–128 的 `interaction_id`；`decision` enum | Accepted flag 和 status |
| `operation.cancel` | 1–128 的 `operation_id` | Operation ID 以及是否请求了 cancellation |
| `shutdown` | `{}` | `{ "stopped": true }` |

三个 `skill.*` method 是一次性 `awesome skills` CLI 的私有包管理支持，不是 Agent tool。它们
不会创建 Thread 或 Turn，也不会构建 Workspace Runtime。其 phase 与 concurrency 规则属于
bootstrap admission，不是第二套由 Host 持有的状态机。

`direct.execute` 会在预留 Operation 前执行与委托 `execute` 工具相同的 8,000 字符边界。
超限命令会作为 invalid params 同步拒绝，且绝不会启动进程。

### `application.getState`

Snapshot 包含 initialization/session/workspace identity 与 trust、已选 Thread、model catalog
与 model identity、Thinking/Skill/permission mode、活动 operation 与 pending interaction ID、
配置有效性/diagnostic、secret presence 与 credential source 状态、Memory/MCP summary、
usage，以及结构化 workspace-instruction diagnostic。Secret value 从来不属于 state。

`model_catalog` 是静态、提供商中立的
`ModelCatalog -> ProviderDescriptor -> ModelProfile` 目录在 Protocol v5 上的 projection。
Provider descriptor 包含 `id`、`credential_id`、`supported_regions`、可选
`default_region` 及其 model profile。Model profile 包含 `id`、`context_limit`、
`supports_tools`、`supports_reasoning` 和 `is_default`。Provider 与 model ID 唯一，每个
model 都使用所属 Provider 前缀，且每个 Provider 恰好有一个 catalog 默认项。当前值包含
DeepSeek、Kimi 和四个模型；Tavily Web Provider selection data 留在独立的
Web/configuration 边界，绝不会出现在这里。

静态 catalog 与 `provider_credentials`、`model_identity` 不同：credential 存在性/来源、
已配置默认选择、活动 Thread 选择和 Kimi region 选择仍是动态的 Application/configuration
事实。每个 model Provider 的 `credential_id` 都有匹配的 credential status，且
`model_identity` 中的所有 ID 都必须能在 catalog 中解析。

TUI 会校验该 snapshot，并从 `model_catalog` 与 `provider_credentials` 推导 startup 和
provider setup；它没有复制 model/Provider 枚举。`/model` choice 是 Application 从同一
catalog 生成的 `CommandSelection`，TUI 只做通用渲染。

`workspace_instruction_diagnostic` 独立于 `configuration_valid`。例如，超大的 `AGENTS.md`
可以被忽略并发出 warning，而不会使其他方面有效的 YAML 配置不可用。

### Pagination

`thread.list` 使用不透明 cursor；客户端不得解码或合成它。`thread.search` 会 trim query，
只搜索活动 Workspace，并按 `updated_at DESC, id DESC` 排序。其不透明 cursor 通过 hash
绑定该 Workspace 与规范化 query；换 scope 重放会失败，且 cursor 不携带明文 workspace key。
搜索对 Thread 标题与所有持久 transcript entry 内容执行 ASCII 大小写不敏感的字面
substring operation。它排除 ToolActivity、summary、checkpoint 与 metadata，也不提供 FTS、
分词、snippet、relevance ranking 或完整 Unicode case folding。每个 page query 都受
5,000,000 SQLite VM-op scan budget 限制；预算耗尽时返回既有的 `result_too_large` Product
error，client 应缩小 query。RPC 与最多显示 50 条的 `/search` picker 不同，仍可通过
`has_more` 和 `next_cursor` 进行 keyset pagination。`thread.read` 使用
`before_sequence` 向后分页，并在存在更多条目时返回 `next_before_sequence`。显式 null 的
pagination 字段无效，因此“不提供”只有一种无歧义 wire 表示。Application 会动态缩小请求的
page，直到编码结果符合 900 KiB 预算，并为被省略的条目保留 `next_before_sequence`。

Assistant entry metadata 包含有序 `citations` 数组。每个严格 source 包含 `id`（`S1...`）、
有界单行 `title` 与绝对 HTTPS `url`；一个 Turn 内 ID 连续且 URL 唯一。Turn budget 和 usage
还包含非负 `web_requests`，其配置硬上限为八。

### Turn 与 direct 准入

`turn.submit` 和 `direct.execute` 确认的是准入，而不是完成。返回的 `operation_id` 用于关联
后续 event。`client_message_id` 使 Turn submission 在 conversation 边界幂等；客户端为新的
用户意图创建新 ID，并在不确定 response 时保留它。

Core 在持久化 Turn 之前获取 foreground lease，因此 `operation_busy` 不会留下幽灵般的
in-progress Turn。直接命令使用与 Agent `execute` 调用相同的 Operation、schema、shell
hard-deny、process、Change Journal 和审计边界。它们刻意不使用 Thread 普通审批矩阵：精确的
`!` 输入是显式用户权限，Direct Operation 会获得独立的 Full-access permission session。

### `command.execute`

Params 是封闭的 `CommandIntent`：

```json
{"name":"mcp","arguments":["status","repository-index"]}
```

通常只有 Application 拥有的 26 个名称通过此 method 发送。Ink 在本地拥有 `help`、`theme`、
`copy` 和 `quit`。`CommandOutcome` 恰好包含一个分支：有类型的 `result`、有类型的
`interaction` 或稳定的 command `error`。精确语法和 foreground snapshot 例外见
[Slash Commands](commands.zh-CN.md)。

Protocol v5 的每个 Thread projection 都有必需且可空的 `lineage` 字段。根 Thread 的值为
`null`；具有一个直接父级的 Thread 则使用严格 object，其中包含 `kind`（`fork` 或
`retry`）、`source_thread_id` 与 `source_turn_id`。该字段只记录来源；client 不得据此
推断存在共享 transcript DAG，也不能通过它读取历史。

`thread_transition` 携带一份权威 Application/Thread snapshot。其 `reason` 为 `new`、
`resume` 或 `fork`：`new` 要求 lineage 为 null，`fork` 要求 fork lineage，`resume` 可以
选择根 Thread 或物化 Thread。reason 为 `retry` 的普通 transition 无效。Retry 改为返回
严格的组合 `thread_retry` payload：一份 reason 与 lineage 都为 retry 的 transition，以及
包含非空 `operation_id`、`thread_id`、`turn_id` 和 `client_message_id` 的 `operation`。
Transition Thread 必须等于 Operation Thread，且 Operation Turn 必须已存在于该 transition
中。这一原子 result 可以防止界面安装新 Thread 后却不知道它所拥有的前台 Operation。

`thread_export` command result 仅包含 `kind`、`thread_id`、1–1,000 的 `path`、`format`
（`markdown` 或 `json`）、`write_status`（`created`、`updated` 或 `unchanged`）、
`byte_count`，以及可选的 `change_set_id`。创建或更新导出必须带 ChangeSet ID；未变化导出
禁止携带该字段。此 payload 不携带导出内容、workspace identity 或内部 transcript metadata。
导出输出本身限制为 5 MiB；path 在规范化后、mutation 前执行长度检查。失败且没有
reconciliation file evidence 的尝试不会发出空 ChangeSet result。

`/tools` 结果不分页。Catalog 准入会执行自己的聚合边界；如果其他 producer 仍破坏该不变量，
transport 的最终字节检查会返回 `result_too_large`，而不是发送无效 frame。

### `provider.credential.set`

`provider` 为 `deepseek`、`kimi`、`mem0` 或 `tavily`；`action` 为 `add`、`replace` 或 `delete`；
`allow_unverified` 默认为 false。Add/replace 要求非空、最多 20,000 个字符且不含 CR/LF 的
`api_key`。Delete 禁止 key content 和 `allow_unverified: true`。

这是 foreground arbiter 下的 mutation/external operation。Key 会立刻包装为 secret，绝不
复制到 event、error 或 state。对于 DeepSeek 和 Kimi，Core 会执行远程验证；Provider 不可达
时，可能返回明确 save-unverified 重试的确认路径，而被 Provider 拒绝的 key 不会保存。对于
`mem0` 与 `tavily`，Core 不执行远程凭据验证，会保存任何本地有效输入；无效 key 只会在
之后的 Mem0 或 Web 操作到达服务时失败。

只有 Core 能报告已选择的 credential source 时，result 才包含 `source`。成功保存后通常为
`awesome`；invalid、save-unverified confirmation 或 delete 结果可能没有已选择 source，此时
会省略该字段。显式的 `"source": null` 不是合法的 v5 result。

### `interaction.respond`

Decision 值为 `trust`、`reset_state`、`allow_once`、`allow_thread_writes`、
`allow_thread_network`、`enable_full_access`、`retry`、`abort` 和 `deny`。Interaction kind 和公布的 choice 决定哪些值
有效。Core 会重新验证 pending interaction 的 generation，以及它必需的 Thread/Turn/
operation/permission binding。陈旧响应不会修改当前权限。

### `operation.cancel` 与 `shutdown`

Cancel 是 best-effort 且针对特定 identity。True result 表示取消在匹配 Operation 仍可取消
时已经传递，其终态为 `operation.cancelled`。False 也包括未知 ID、已经进入 cancelling 的
Operation，以及处于 `committing` 或终态的 Operation。Commit boundary 之后的取消不能重写
持久化 outcome。已经准入的 durable write 会先等到明确的 COMMIT 或 ROLLBACK，再重新抛出
caller 的第一次 cancellation；shutdown 也会等待同一边界。系统会继续发布原来的
completed/failed terminal event。本地 activity、transcript、ChangeSet 和 checkpoint
finalization 会继续持有 Operation lease，直到结果明确；process/MCP cleanup 与 best-effort
terminal publication 仍有界。

Shutdown 是紧急操作。有效 request 会先取消其他 background request、阻止新 foreground
lease、取消/等待活动 operation 和 mutation、关闭 MCP 与其他资源，然后返回。有效 shutdown
notification 会直接终止，不返回 response。

## Application result envelope

Transport 成功不代表产品成功。每个已注册 method 都恰好返回一个分支：

```json
{"ok":true,"value":{"stopped":true}}
```

或者：

```json
{
  "ok": false,
  "error": {
    "code": "operation_busy",
    "message": "Another foreground operation is active.",
    "retryable": true,
    "data": {}
  }
}
```

`value` 和 `error` 互斥。Error message 限制为 2,000 个字符，`data` 只包含安全的结构化
diagnostic。

产品 error code 为：

| 领域 | Code |
| --- | --- |
| Configuration/workspace | `configuration_invalid`、`workspace_not_trusted`、`model_not_configured`、`provider_not_configured` |
| Conversation/foreground | `thread_not_found`、`turn_not_found`、`turn_busy`、`operation_busy`、`recovery_required` |
| Input/commands | `invalid_arguments`、`command_not_available` |
| Output bounds | `result_too_large` |
| Checkpoints | `checkpoint_missing`、`checkpoint_corrupt` |
| Compatibility/state | `client_version_incompatible`、`protocol_version_incompatible`、`state_created_by_newer_version`、`state_unknown`、`state_unavailable`、`state_reset_busy`、`state_reset_failed` |
| Invariant failure | `internal_error` |

客户端使用 `retryable` flag 和当前 state 判断是否适合重试，不会匹配 message 字符串。

在 Protocol v5 中，Application 级 `state_unavailable` error 可重试，并携带有界的
`state_directory` metadata。它与内置 Memory tool 的 `ToolOutput` 中不可重试的
`state_unavailable` 不同；客户端不能仅按 code 字符串把两个 envelope 归一化。

## JSON-RPC 错误

Application 契约之前或之外的失败使用 JSON-RPC error：

| Code | 含义 |
| ---: | --- |
| `-32700` | 无效 JSON/UTF-8 或行过大（`Parse error`） |
| `-32600` | 无效 request 形状或重复的近期 ID |
| `-32601` | 未知 method |
| `-32602` | Params 未通过严格 method schema |
| `-32603` | 意外 handler failure；data 只包含 `diagnostic_code: core_request_failed` |
| `-32002` | 握手状态不允许此 method |
| `-32000` | Protocol in-flight capacity 已耗尽（`Server busy`） |

意外 request exception 会在内部记录 method、request ID、exception type 和紧凑 stack location，
客户端则收到固定的 `-32603` diagnostic。这样可以避免泄露原始参数、schema、路径或凭据。

## Event

Event 是 JSON-RPC notification：

```json
{
  "jsonrpc": "2.0",
  "method": "event",
  "params": {
    "version": 1,
    "event_id": "event_001",
    "sequence": 1,
    "session_id": "session_...",
    "workspace_key": "ws_...",
    "operation_id": "operation_...",
    "event_type": "operation.started",
    "timestamp": "2026-07-11T08:00:00Z",
    "payload": {"kind":"operation.started","message":""}
  }
}
```

每个 envelope 都包含 version、唯一 event ID、session-local 单调递增 sequence、session/
Workspace identity、UTC timestamp、event type，以及 `kind` 等于 `event_type` 的可辨识
payload。相关时会出现 Thread、Turn、operation 和 client-message ID。Operation lifecycle
event 要求 operation ID；Turn lifecycle event 要求 Thread 和 Turn ID。

| 类别 | Event type | 关键 payload 字段 |
| --- | --- | --- |
| Operation | `operation.started`、`.completed`、`.failed`、`.cancelled` | 有界 message |
| Turn | `turn.started`、`.completed`、`.failed`、`.cancelled` | 可选 reason；终态 duration |
| Assistant stream | `assistant.text.delta`、`assistant.reasoning.delta` | 非空 text，每个 event 最多 30,000 |
| Provider retry | `provider.retrying` | attempt 2–7、maximum 1–7、delay 0–30 秒、error code |
| Tool | `tool.started`、`.completed`、`.failed`、`.cancelled` | call/name/verb/target；终态 outcome、summary/detail、duration、可选 error code |
| Context | `context.prepared`、`context.compressed` | source count 和 estimated token |
| Usage | `usage.updated` | 非负 input/output/reasoning/cache token 与 Web-request counter |
| Memory | `memory.status` | `local` 或 `external`、enabled flag、status |
| Interaction | `interaction.required`、`interaction.resolved` | 绑定的 ID/kind/prompt/operation/target/capability/choice 或 decision |
| Warning | `warning` | 稳定 code 和有界 message |

Emitter 会为指定 Operation 或 Turn 强制一次 start 和至多一次 terminal lifecycle event。Tool
Executor 同样为每次调用终结一条 ToolActivity 和一个 terminal tool event。Consumer 应按
sequence 和 correlation ID 渲染，而不能假定并发 request 之间 response/event 的到达顺序。
尤其是，被接受的 Turn 或 Direct command 的 `operation.started` 会先于 acceptance response，
后续 event 也可能在该 response 被处理前到达。

`thread_retry` 也遵循相同顺序，但在组合 command response 安装前，其 Thread identity 尚不
存在于界面。Protocol v5 界面因此会在发出命令前打开本地 retry gate，按 sequence 缓存
event，安装返回的 transition，把新 generation 绑定到返回的 Operation/Thread/Turn
identity，再重放缓存。Gate 最多接受 1,024 个 event 和 4 MiB 编码内容。容量或 identity
违规属于 protocol desynchronization，必须 fail closed；不能仅因为 event 先到就把它
渲染到来源 Thread。

## 并发与背压

Host 最多允许 128 个普通 in-flight request 和 16 个 background control request。
`initialize` 和 `interaction.respond` 使用 control pool；`operation.cancel` 和 `shutdown` 紧急
处理。调度被接受的 request 后，reader 会 yield 一次，使其可以在后续 control request 之前
进入 Application 边界。

这种 transport concurrency 不允许并发产品修改。Application Foreground Arbiter 以原子方式
协调 Turn、直接命令、状态修改/外部命令、凭据修改、非 tool interaction resolution 和
shutdown。只有命令契约明确允许时，snapshot command 才能在 Operation 期间运行。

## Fixture 与兼容性测试

`protocol/fixtures/v5/` 是跨语言 source of truth。它包含有效和无效 method、command result、
event、产品失败，以及记录 file hash、method name、event name、产品版本和 protocol 版本的
manifest。Python Pydantic model 与 TUI 的严格 TypeScript/Zod schema 都会验证这些 fixture。

更改 wire 契约时：

1. 同时更改 Core 和 TUI schema；
2. 更新有效与负面 fixture 以及 manifest hash；
3. 保留固定/脱敏 diagnostic 和严格 unknown-field rejection；
4. 对不兼容的形状或语义变化递增 protocol version；
5. 验证上一个 protocol 版本会在握手时明确失败。

不要只为让不匹配的私有组件继续工作而添加 compatibility adapter。Launcher 把它们作为一个
版本化单元发布，因此 fail-fast 检测比含糊的局部兼容更安全。
