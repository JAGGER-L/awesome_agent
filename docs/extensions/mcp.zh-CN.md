# MCP 服务器

Awesome 支持把本地 stdio Model Context Protocol 服务器用作外部工具 Provider。当某项能力
必须在另一个进程中运行，或需要复用现有 MCP 实现时，MCP 很合适。静态指令
（[Skills](skills.zh-CN.md)）或持久事实（[Memory](memory.zh-CN.md)）不需要 MCP。

MCP 服务器及其输出是不受信任的扩展。服务器成功连接并不会获得 Workspace 信任、权限模式
的授权，也不能绕过 Tool Executor。每次 MCP 调用都需要单次审批，即使处于 Full access
也是如此。

## 声明 User 服务器

在 `<AWESOME_HOME>/config.yaml` 中添加服务器：

```yaml
version: 1
mcp_servers:
  - id: issue-tracker
    command: python
    args: ["-m", "my_issue_tracker_mcp"]
    env: ["ISSUE_TRACKER_TOKEN"]
    enabled: true
```

User 服务器仅由其 `enabled` 字段控制。`/mcp enable` 和 `/mcp disable` 会刻意拒绝 User
服务器，并引导你回到用户配置。修改声明后请重启 Awesome。

## 声明 Workspace 服务器

在 `<workspace>/.awesome/config.yaml` 中添加声明：

```yaml
version: 1
mcp_servers:
  - id: repository-index
    command: python
    args:
      - -m
      - repository_index_mcp
      - --root
      - .
    env: ["REPOSITORY_INDEX_TOKEN"]
```

Workspace 声明没有 `enabled` 字段。Workspace 获得信任前它会被忽略，之后则变成
`enablement_required`。审查命令、参数和环境 allowlist，然后为当前 Workspace 启用它：

```text
/mcp status repository-index
/mcp enable repository-index
```

Enablement 与 workspace key、server ID，以及 `id`、`command`、`args` 和排序后环境变量名称
的哈希一同存储在 Application SQLite 中。任何对这项承载权限的声明所作的修改都会使旧审批
失效，并要求重新启用。

Server ID 必须以小写字母开头，并且最多包含 64 个小写字母、数字、下划线或连字符。User
和 Workspace 声明合并后，ID 必须唯一。`command` 是直接的可执行文件名或路径，而不是
shell 表达式；`args` 是参数数组。

`env` 只包含名称。名称必须匹配 `[A-Z_][A-Z0-9_]*` 且不可重复。子进程会收到最小的平台
环境（`PATH`、临时目录变量和必要的 Windows 进程变量），再加上 Core 进程环境中被明确
点名的值。仅保存在 Awesome `.env` 中的 Provider 凭据值不会自动导出给 MCP 子进程。

## 操作服务器

```text
/mcp                         # all status records
/mcp status                  # same snapshot form
/mcp status <id>
/mcp enable <workspace-id>
/mcp disable <workspace-id>
/mcp restart <id>
```

| 状态 | 含义 |
| --- | --- |
| `disabled` | User 声明设置了 `enabled: false`。 |
| `untrusted` | Workspace 声明存在，但 Workspace 不受信任。 |
| `enablement_required` | 受信任的 Workspace 声明没有匹配当前 config-hash 的审批。 |
| `configured` | 有效声明具备连接条件，但当前尚未连接。 |
| `connected` | Manager 拥有一个存活 client 和一代完整、已编译的 catalog。Registry 同步是后续 Application 步骤。 |
| `error` | 连接、catalog、调用或清理失败，Manager catalog 已失效。Application 在同步或收到绑定的失效回调时移除匹配的 namespace。 |

`/mcp restart <id>` 会先丢弃旧 client 和 catalog，从而移除旧的
`mcp.<server-id>.*` registry namespace，然后执行全新连接。`call_tool()` 内部没有惰性重连。
重连只会在 Turn 准备期间或显式 restart 流程中发生，因此不确定的调用绝不会被静默重放。

## Catalog 与 Registry 发布

Awesome 不会逐页注册工具：

```text
spawn + initialize stdio client
  -> list every catalog page
  -> enforce page/tool/byte limits
  -> compile every input and output JSON Schema
  -> Manager atomically publishes client + catalog generation + CONNECTED
  -> Application builds every RegisteredTool for that generation
  -> Registry atomically replaces the complete server namespace
```

如果任何工具名称、契约、schema、分页 cursor 或资源限制无效，新 client 会关闭，Manager
catalog 会失效，状态变为 `error`。同步时，Application 会移除该服务器的 namespace。
Manager catalog 或 Registry namespace 的有效子集绝不会被发布。

这两次原子替换并不是一个跨组件事务。`connected` 描述 Manager 状态，它本身不能证明
namespace 已安装。当前还存在一个契约缺口：catalog 接受长度无上限的上游组件工具名，
而 `/tools` payload 中完整的 namespaced name 限制为 128 个字符，在 model/event 契约中
则限制为 200 个字符。因此，一个超长但看似有效的名称可能让 Manager 保持 `connected`，
同时 Registry 适配、模型暴露或 `/tools` 展示失败。应把这种状态视为不可用，修正服务器
catalog 后重启；运行时加固应在 catalog 编译期间，按最严格的下游使用者验证完整的
`mcp.<server>.<tool>` 名称。

Catalog 限制如下：

| 资源 | 限制 |
| --- | ---: |
| 分页页数 | 128 |
| 每个服务器的工具数 | 128 |
| 单个 input 或 output schema | 256 KiB |
| 完整 catalog | 1 MiB |
| Schema 嵌套 | 64 层 |
| 工具 description | 500 个字符 |

Input 和 output schema 默认使用 JSON Schema Draft 2020-12。显式 `$schema` 可以选择已安装
`jsonschema` runtime 支持的 dialect。标准组合、条件、范围、pattern、数组、
`additionalProperties` 和 `unevaluatedProperties` 都会强制执行。`format` 保留 JSON
Schema 默认的 annotation 语义。Awesome 不为任何受支持 dialect 安装 `FormatChecker`，
因此在 MCP 参数或结果验证期间不会断言 format。

`$ref`、`$dynamicRef` 和 `$recursiveRef` 会经过预检。引用只能指向同一 schema resource 中
的 fragment。缺失 fragment、远程引用、重复 anchor 或 resource ID、未知 dialect，以及
必需但未知的 vocabulary 都会拒绝 catalog。Schema 编译绝不会从网络获取内容。

## 绑定 generation 的调用

每个成功的 catalog 都会获得一个 generation。Registry handler 会捕获该 generation 和对应
validator。调用前，Manager 会检查服务器已连接、工具仍然存在，并且捕获的 generation 等于
当前 catalog。

```text
model arguments
  -> compiled input validator
  -> Tool Executor permission prompt
  -> Manager generation check
  -> exactly one remote call
  -> output resource preflight
  -> declared outputSchema validation
  -> bounded normalized ToolResult
```

Input 验证发生在审批和远程 I/O 之前。验证错误会变成通用 `invalid_arguments` 结果，不会
回显原始参数或 schema。

如果服务器声明了 `outputSchema`，成功的 `structuredContent` 就是必需的，并且必须通过验证。
在 schema 遍历前，结构化 JSON 会以 64 KiB、4,096 个节点和 64 层进行预检。响应最多可包含
1,024 个 content block。渲染文本限制为 30,000 个字符，必要时保留首尾，并用明确标记
注明省略的字符数。无效或缺失的结构化输出会变成经过脱敏、不可重试的执行失败。

## 超时、取消与不确定结果

初始化和 catalog listing 的期限为 30 秒，连接清理限制为五秒。工具调用的 Manager 期限是
30 秒，Tool Executor 外层 guard 为 40 秒。

远程调用开始后，超时或传输中断无法证明服务器是否已执行操作。因此 Awesome 会：

1. 取消本地等待，并限制连接清理时间；
2. 使 client、catalog generation 和 registry namespace 失效；
3. 将服务器标记为 `error`；
4. 返回不可重试的 `uncertain_outcome`，并明确提供 retry/abort 恢复选择；
5. 绝不在同一个 Turn 中重新连接或重放调用。

用户取消会执行相同的失效与有界清理，随后继续传播原始取消。恢复时，不确定的外部工作默认
选择 Abort 而不是 Retry，因为重复执行可能重复副作用。见
[变更与恢复](../concepts/changes-and-recovery.zh-CN.md)。

## 安全与运维取舍

- MCP 子进程是宿主机进程，不是操作系统沙箱。启用前请审查可执行文件及其依赖。
- 环境 allowlist 减少意外继承秘密的风险；它不会限制子进程以当前操作系统账户访问的文件或
  网络资源。
- MCP annotation 是展示元数据，不是权限。Awesome 当前把 MCP 工具分类为 `mcp.invoke`、
  非只读，并且总是询问。
- 原子 catalog 牺牲局部可用性来换取一致契约：模型绝不会看到服务器 schema generation
  的一半。
- 禁止透明重试牺牲了便利性，换来 Awesome 边界上的 at-most-one-call 行为。

发生失败时，运行 `/mcp status <id>`，修正声明或服务器 catalog，然后使用
`/mcp restart <id>`。在检查远程系统中第一次调用的影响之前，不要重试已经超时的修改工具。
