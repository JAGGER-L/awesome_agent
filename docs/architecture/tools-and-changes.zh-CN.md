# 工具与变更

工具是模型意图对工作区或 host 产生作用的唯一通路。因此，Awesome 在调用 built-in 或
MCP handler 之前，统一处理注册、严格参数校验、工具自有 hard admission、capability 决策、
审批、期限、取消、审计和终态事件。

Change Journal 与工具相邻，但职责独立。它记录并恢复通过受管 built-in 产生的文件
mutation；它无法让任意 shell 或 MCP 副作用变得可逆。

## 工具契约

一个 `RegisteredTool` 组合八项内部事实：

- 提供商可见的 `ToolSpec`，其中包含名称、描述、JSON schema、capability、read-only
  标志和展示 metadata；
- 一个严格的 Pydantic input model；
- 一个 async handler；
- 一个从已校验参数派生有界 operation fact 的类型化描述函数；
- 一个对已校验参数和执行 context 应用不可关闭、工具特定检查的 hard-admission 函数；
- 一个显式 replay-safety 分类；
- 一个可选的动态总超时解析器；
- 一个可选的 handler cancellation grace。

描述、admission、replay、deadline 与 cancellation 事实有意不属于模型可见 schema。
描述函数从显式选择的 operation target 提供有界展示与审批事实；它不会接收未经校验的
原始参数映射。Hard admission 拥有路径或命令安全等事实，任何权限模式或临时 grant 都不能
覆盖它。Description 恰好在 hard admission 后运行
一次：它进行的任何有界 metadata probe 都位于已准入操作内，并且仍发生在审批、handler 与
外部作用之前。Replay safety 为恢复提供显式答案，使其不必识别工具名。Timeout resolver
让 `execute` 可以用 `timeout_seconds + 10` 秒作为总期限；cancellation grace 则对 handler
清理设限，而不向模型泄露 executor 内部机制。

Built-in 基线如下：

| 工具 | Capability | 受管文件变更 | 重放安全性 |
| --- | --- | --- | --- |
| `ls`、`read_file`、`glob`、`grep` | `workspace.read` | 无 | replayable |
| `write_file`、`edit_file` | `workspace.write` | 记入 journal | non-replayable |
| `delete` | `workspace.delete` | 记入 journal | non-replayable |
| `execute` | `shell.execute` | 仅 observation | non-replayable |
| `web_search`、`web_fetch`（启用且配置有效时） | `network.read` | 无；外部 | non-replayable |

Registry 可扩展，八个不是固定上限。MCP namespace 会以原子方式替换，名称形如
`mcp.<server>.<tool>`。

## Core 与 Agent 内部的引用传递

`core/citations.py` 定义最小、提供商中立且不可变的 `Citation` 值：`id`、`title` 与
`url`。严格契约会拒绝未知字段；ID 必须是有界的 `S1` 至 `S999999` 形状；非空单行 title
最多 500 个字符；绝对 HTTPS URL 最多 8,000 个字符，且不得包含空白、控制字符或用户
信息。`ToolOutput.citations` 和 `ToolResult.citations` 都是默认空 tuple。Handler 成功返回
后，Executor 会严格重建每条 citation 与 output，再把 citations 原样复制到规范化 result。
对文本 `content` 设限或截断不会丢弃这些 citations。

`web_search` 与 `web_fetch` 会在一个 Turn 内分配稳定、按 URL 去重的 `S1...Sn` identity。
Fetch hard admission 只接受一个公共 HTTPS URL，拒绝配置的 blocked hostname 及其子域，
随后把目标连接委托给 Tavily，而不是由 Core 打开。Agent 序列化完整 result，并派生有序的
`AgentState.citations` 快照，使两者都能经过 checkpoint recovery。
Finalization 校验模型使用的 `[[S1]]` marker：未知 ID 会产生 warning 且不生成链接；使用 Web
但没有有效引用时，会附加有界且确定性的 Sources 区域并记录 warning。Conversation 将同一
来源随 assistant entry 持久化，Protocol v5 再把它们传给 TUI、headless JSON v2 与后续导出。

## Executor 流水线

```text
ToolRequest
  -> resolve registry item
  -> strict-validate with its registered input model
  -> run its registered hard admission
  -> derive its typed description exactly once
  -> PermissionPolicy for its registered capability: allow | ask | deny
  -> resolve bound approval, when asked
  -> resolve total deadline
  -> invoke handler under deadline
  -> normalize result or expected failure
  -> write one ToolActivity and audit summary
  -> emit one terminal tool event
  -> return one bounded ToolResult
```

每次尝试调用都恰好发出一条 `tool.started`，包括未知工具、无效参数和 hard-admission
失败。若失败发生在类型化描述产生之前，事件只使用注册项的静态展示信息，绝不从不可信值
派生 target；已准入调用则在 capability policy 或 handler 执行前发出类型化、有界的展示。
参数错误、policy denial、timeout 和预期 handler failure 会成为有界 `ToolResult` error。
意外 handler exception 属于不变量失败，会终止 Turn，而不会伪装成模型可修正的错误。

这是唯一的执行顺序。Executor 统一调用注册项拥有的行为，不按具体工具名分支。Hard
admission 与 capability policy 回答不同问题：admission 判断这一项经过校验的具体操作是否
在任何情况下都可接受；policy 判断注册的 capability 在当前 permission session 中应允许、
拒绝还是请求审批。

取消会通过有界清理尝试终结唯一一条 cancelled activity/event，然后重新抛出调用方的
原始取消。忽略取消的 handler task 只有在其宽限期限结束后才会被 detach，其结果仍会被
消费，避免泄漏 task exception。

工具内容进入 Agent state 或 transcript 前会受到边界约束。审计 summary 只保留参数名，
不保留原始参数值。

## 重放安全性

Replay safety 是注册 metadata，而不是由恢复流程推断的属性。只有受管本地语义能够证明
重复调用安全的 built-in 才可标记为 replayable；读取工具满足这一条件。文件修改工具不
满足：崩溃后再次 edit、overwrite 或 delete 可能作用于新的文件系统状态，即使 Change
Journal 已经记录首次作用。因此它们属于 non-replayable，MCP、Web、shell 与其它外部或
未分类作用同样如此。Dispatch 后崩溃会默认 Abort，不会重复该操作。恢复会在当前 Runtime
Registry 中查找同名工具，并消费该注册项的 metadata。Replayable 工作可以继续；
non-replayable、metadata 缺失或未知时会 fail closed，进入恢复 interaction，绝不自动重试。
用户可以显式选择 Retry，而不是默认的 Abort。因此，同名工具的契约变更必须按 checkpoint
compatibility 变更管理。Executor 与恢复流程都不维护另一份特殊工具名列表。

## 权限决策

权限是纯 capability 决策，只在注册的 hard admission 成功后求值。Hard rejection 始终
优先，表中任何一行都不能把它转为允许：

| 模式 | 读取 | 创建/修改 | 删除 | Shell | 网络读取 | MCP/未知扩展 |
| --- | --- | --- | --- | --- | --- | --- |
| Request approval | 允许 | 询问 | 询问 | 询问 | 询问 | 询问 |
| Accept edits | 允许 | 允许 | 询问 | 询问 | 询问 | 询问 |
| Full access | 允许 | 允许 | 允许 | 允许 | 询问 | 询问 |

该表适用于使用选中 Thread permission session 的 Agent 工具调用。直接 `! command` 输入
是用户对该确切命令的显式授权：Application 为其建立独立 Full-access permission
session 的 Direct Operation，因此不会显示普通 shell 审批。直接执行仍经过同一 schema、
command circuit breaker（词法检查和 spawn 前检查）、Process Runner、审计、超时、取消
与脱敏边界。

Allow-once 结果只作用于当前 Tool call。“Allow all edits during this session”只 grant
`workspace.write`；它不能 grant delete、shell 或扩展 capability。网络审批提供默认 deny、
allow once 和 allow for the active Thread。选择其他 Thread、重建 runtime、更改 permission
mode、运行 `/web revoke` 或 `/web off`，以及 shutdown 时，Thread grant 都会被撤销。

Tool Executor 根据经过校验的操作事实创建审批文本。TUI 渲染该类型化请求并返回决策；
它绝不会从提示词文本推断 capability，也不执行操作。

## 工作区路径准入与使用

文件工具接受工作区相对路径。语法校验会拒绝绝对路径、父目录逃逸、敏感凭据/密钥路径
和语义不明确的 Windows 拼写。随后 `resolve_workspace_path()` 检查规范工作区身份，
并对遍历的每个组件使用 `lstat`，拒绝会被跟随的链接和 reparse point。

单次路径准入无法防止之后 mutation 遇到替换竞态。因此受管文件 handler 使用
`core/filesystem.py` 与 `core/changes/filesystem.py` 中绑定身份的 primitive：

```text
validate lexical path
  -> open and pin workspace root
  -> descend and pin parent identities without following links
  -> capture target existence, type, identity, link count, content, and mode
  -> persist mutation intent
  -> mutate through pinned parent
  -> recapture and verify intended after-state
  -> append committed FileChange
  -> clear pending intent
```

具有多个 hard link 的普通文件会被拒绝，因为一个工作区路径无法证明所有 alias 的位置。
Write 和 edit 使用原子 sibling replacement。递归 delete 在首次移除前就对完整目录树进行
inventory 和绑定；任意嵌套 symlink、junction、reparse directory、hard-linked file、
容量越界或可观察身份变化都会中止，并确保预期删除数为零。

POSIX 最终 symlink 节点本身可以在不跟随目标的情况下删除；这并不允许遍历 linked parent，
也不允许通过嵌套链接进行递归 inventory。

这是 fail-closed 的 userspace 防护，而不是文件系统 compare-and-swap 或 kernel jail。
同权限进程仍可在最后一次身份检查后制造竞态。固定的 no-follow 操作能防止跟随替换链接
到工作区外，却无法承诺隔离恶意并发 host writer。

## 命令 circuit breaker

Agent `execute` 与直接 `!` 输入都会调用同一个纯命令 policy。`execute` 注册项的 hard-
admission 检查接收命令、显式 shell dialect 与 workspace。它先把请求 working directory
解析为 pinned workspace 内已存在的 no-follow 目录，再在描述或审批前基于 resolved path
评估命令。Handler 在 spawn 前重复身份解析与同一 policy。共享 evaluator 可以防止规则
漂移；第二次检查使用新鲜的 opened-path 证据，封闭 admission 与进程创建之间的变化。

对 CMD、POSIX shell 和 PowerShell 的有界检查会展开已知 wrapper、复合命令、pipeline
和换行。它会规范化 executable path、大小写与 executable suffix；在目录切换间保守
追踪可能的 working directory；解码 PowerShell encoded command；处理
`Start-Process` elevation alias；并检查部分字面量 Python `-c` 调用中的危险文件系统/
进程 API。

Circuit breaker 始终拒绝可识别的灾难性操作，例如递归删除文件系统根或工作区根、
shutdown/reboot、elevation、磁盘格式化、block-device overwrite 和 fork bomb。输入若
不能在深度与节点上限内被安全解析，也会被拒绝。

该 policy 旨在防止误操作和已知 wrapper。它不是 malware detector，也不声称理解任意
恶意混淆。Full access 无法将其禁用。

## Shell 生命周期与审计

`execute` 会在记录不可逆尝试之前校验参数与 policy。紧接 Process Runner 启动前，它会
向打开的 ChangeSet 追加一条脱敏 `ExecuteObservation`。因此 timeout、cancellation、
spawn failure 和 backend failure 会保守地留下证据；畸形参数、审批拒绝和 hard denial
不会声称发生过执行尝试。

```text
validated + approved execute
  -> record observation
  -> spawn supervisor and root command
  -> concurrently drain bounded stdout/stderr
  -> root completes | requested timeout | cancellation | backend failure
  -> terminate owned process tree when needed
  -> force-kill after grace when needed
  -> bounded pipe drain, cancel inherited readers if needed
  -> return ProcessResult or propagate original cancellation
```

请求的 `timeout_seconds` 覆盖 spawn 和根命令执行。Handler 外层期限额外增加 10 秒，用于
进程树终止和 pipe 清理。命令超时返回带 metadata 的 `TIMEOUT`；外层期限用于兜底违反
契约的 backend。

POSIX 上，每条命令使用绑定 lease 的 session supervisor 和 process group。Windows 上，
等待中的 supervisor 会先被放入嵌套的 kill-on-close Job Object，才可以创建目标。根进程
与 pipe 的生命周期相互独立：持有 stdout 的 descendant 可能导致输出截断，却不能让 Tool
call 永远保持 pending。

有意逃出 POSIX session 的进程或通过外部服务执行的行为超出此清理边界。进程所有权不等于
执行隔离。

## ChangeSet 模型

每个 Turn 或直接命令都会打开一个绑定工作区的 ChangeSet。它从 `OPEN` 开始，封存为
`APPLIED`，可以转换到 `UNDONE`，再回到 `APPLIED`。可逆性分为：

- `FULL`：只有受管文件 mutation；
- `PARTIAL`：受管文件 mutation 加不受管 execution observation；
- `NONE`：只有不受管副作用，或没有可恢复文件状态。

`FileChange` 存储彼此独立的 before/after node type、hash、blob ID、mode 和 mutation
identity。独立 node type 能保留文件、目录、symlink 与不存在状态之间的 transition，
而不会用一侧 type 解释另一侧。

Journal 将单个 ChangeSet 限制为 1,000 条文件记录和 50 MiB 引用内容。按内容寻址的 blob
避免重复存储相同快照。

## 普通 mutation 的崩溃窗口

Journal 在工作区作用之前按顺序持久化 intent：

```text
save before/after blobs
  -> save PendingMutation
  -> mutate workspace
  -> verify actual after-state
  -> save FileChange with mutation_id
  -> delete PendingMutation
```

如果进程在该窗口中停止，启动校正会比较已记录 identity、当前工作区状态以及任何已提交
FileChange。Mutation ID 使“记录已提交但 pending 清理未提交”场景具有幂等性。语义不明
的旧证据会被保留为 conflict，而不会重复或丢弃。

## Undo 与 redo 事务

Undo/redo 会合并同一路径的重复变更，在同一棵固定工作区树中绑定所有目标，并在修改任何
内容前检查每个当前快照。

```text
load and validate blobs
  -> bind all paths + detect conflicts
  -> prepare all inverse/forward intents
  -> persist every pending intent
  -> restore each path through the same pinned tree
  -> commit ChangeSet lifecycle once
  -> clear pending intents
```

如果 lifecycle commit 前发生错误，在固定目录树和原始快照仍可用时，会回滚已经恢复的
路径。如果无法证明该回滚，pending evidence 会被保留。启动恢复会完成已提交操作，或
回滚未提交的部分操作；它绝不会通过全新路径解析进行猜测。

如果当前工作区不再匹配已记录的 after state，Undo 会拒绝执行。它还会报告 shell/MCP
副作用未被恢复。用户可以通过 `/diff`、`/undo` 和 `/redo` 检查这些事实，但不应把 journal
当成版本控制替代品。

## 设计取舍

- 集中式执行给简单工具增加仪式，但让所有工具共享审批、超时、事件和审计语义。
- 严格 no-link 文件操作会拒绝一些合法布局，以换取可解释且可测试的边界。
- Spawn 前记录 shell 尝试，可能会多报一次其实未启动的作用，却避免在不确定错误后错误
  宣称可逆。
- 多路径 undo 偏向保守 conflict，而不是覆盖用户变更。
- Host 执行保留原生开发者工作流，但明确把 OS 隔离留作当前非目标。

## 源代码与测试索引

- 契约与 registry：`core/citations.py`、`core/tools/contracts.py`、`registry.py`
- Policy 与权限：`core/tools/policy.py`、`permissions.py`、`command_policy.py`
- Executor：`core/tools/executor.py`
- Built-ins：`core/tools/builtins/`
- 进程生命周期：`core/tools/process.py`、`core/process_lifetime.py`
- Journal：`core/changes/journal.py`、`core/changes/operations.py`
- 文件系统：`core/filesystem.py`、`core/changes/filesystem.py`
- 测试：`tests/unit/core/tools/`、`tests/unit/core/changes/`、
  `tests/integration/test_application_tools.py`、
  `tests/integration/test_change_journal.py`
