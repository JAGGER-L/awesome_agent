# Slash Command 参考

Awesome 有一个封闭的命令 catalog。其中二十六条命令属于 Python Application，四条属于
Ink。Slash Command 是产品控制输入：它会显示在终端 transcript 中，但不会作为模型对话
消息存储。

## Application 命令

| 命令 | 精确公共语法 | 效果 |
| --- | --- | --- |
| `/new` | 无参数 | 创建并选择新 Thread；重置 Thread 作用域的权限状态。 |
| `/rename <title>` | 一个或多个标题 token | 为选中的 Thread 持久化手动标题。 |
| `/resume [thread_id]` | 零或一个 ID/prefix | 打开 picker 或选择一个 Workspace Thread。 |
| `/fork [turn_id]` | 零或一个 Turn ID | 将截至一个终态 Turn 的前缀物化为独立 Thread，并选中它。 |
| `/retry [turn_id]` | 零或一个 Turn ID | 在一个终态 Turn 之前物化分支，并全新执行其请求。 |
| `/search <query> [thread_id]` | 一个 query token（多词时加引号），随后可选精确结果 ID | 搜索当前 Workspace、打开 picker，或恢复选中的匹配 Thread。 |
| `/context` | 无参数 | 显示最新的活动上下文类别、实际 token 数和预算。 |
| `/compact` | 无参数 | 立即构建并持久化新的对话摘要。 |
| `/auth [deepseek\|kimi\|mem0\|tavily]` | 正常使用时为零或一个 service | 通过 picker 选择并管理 Environment 或 Awesome API-key 来源。 |
| `/model [deepseek\|kimi]` | 正常使用时为零或一个 Provider | 选择 Provider/model；更新当前 Thread 和用户默认值。 |
| `/thinking [on\|off]` | 零或一个值 | 显示 picker，或设置当前 Thread 后续 Turn 的 Thinking。 |
| `/permissions [request_approval\|accept_edits\|full_access]` | 零或一个 mode | 检查或更改会话权限模式；Full access 需要单独确认。 |
| `/workspace` | 无参数 | 显示活动 Workspace 的展示路径。 |
| `/diff [change_set_id]` | 零或一个 ID | 渲染最新或指定 ChangeSet 的 diff。 |
| `/export <workspace-relative-path> [markdown\|json]` | 一个路径和可选 format；默认 `markdown` | 通过安全且进入 journal 的工作区写入确定性导出当前 Thread。 |
| `/undo [change_set_id]` | 零或一个 ID | 恢复已应用且可逆的 ChangeSet 的 before-state。 |
| `/redo [change_set_id]` | 零或一个 ID | 恢复已撤销 ChangeSet 的 after-state。 |
| `/tools` | 无参数 | 列出有效 catalog，以及当前模式下是否需要审批。 |
| `/skills [auto\|off\|name]` | 零或一个 mode/name | 检查或设置当前 Thread 的 Skill mode。 |
| `/mcp [status [id]\|enable <id>\|disable <id>\|restart <id>]` | 如左所示 | 检查或管理 MCP 服务器。 |
| `/web [on\|off\|status\|revoke]` | 零或一个 action | 检查或原子启用/关闭 Tavily Web 工具，或清除当前 Thread network grant。 |
| `/memory [local ...\|mem0 ...]` | 见下文 | 检查、启用、搜索或修改 Memory。 |
| `/status` | 无参数 | 显示已选择 Thread 和 runtime 状态快照。 |
| `/usage` | 无参数 | 显示已选择 Thread 累计观察到的 usage。 |
| `/doctor` | 无参数 | 检查配置、状态/checkpoint、Workspace instructions 和已配置 Provider。 |
| `/config` | 无参数 | 显示来源类别以及凭据是否存在/已选择，绝不显示秘密值。 |

`/rename` 用空格连接解析后的 token。空标题会被拒绝。超过 100 个可见字符的标题会被拒绝，
而不是截断。在 `/rename` 设置手动标题之前，第一条被接受的自然语言消息会提供一个最多
48 个可见字符的自动标题。

`/resume` 接受精确 Thread ID，或由 8–32 个小写十六进制数字组成且无歧义的 `thread_`
prefix。有歧义的 prefix 会打开 picker；绝不会选择其他 Workspace 的 Thread。

`/fork` 与 `/retry` 最多接受当前 Thread 中一个精确 Turn ID。省略时，按 transcript 顺序
选择最近的终态 Turn；进行中的目标会被拒绝。`/fork` 会物理复制截至目标的持久前缀；
`/retry` 只复制目标之前的前缀，以新的 entry/client identity 追加目标用户请求，并启动
全新的 Turn。即使源 Thread 的设置后来变化，新 Turn 仍冻结原目标的 Provider、模型、
Thinking、Skill 和完整预算快照。每个被复制的 Thread、entry 和 Turn 都获得新 identity，
新 Thread 只记录直接来源 Thread/Turn 的 lineage，不构造共享 DAG。Summary、checkpoint、
ToolActivity 和 ChangeSet record 都不会复制。Retry 经过普通 Turn 路径重新执行，不会重放
旧工具调用，也不会自动撤销它们之前产生的副作用。

`/search` 接受一个 query 参数。多词 query 必须加引号，例如
`/search "provider retry"`；选择后，TUI 会把选中的精确 Thread ID 追加到原始 query。
搜索仅限活动 Workspace，并对 Thread 标题及所有持久 transcript entry 内容执行 ASCII
大小写不敏感的字面 substring match，包括 user、assistant 和 direct-command entry。它不搜索
ToolActivity、summary、checkpoint 或 metadata，也不提供 FTS、分词、snippet、relevance
ranking 或完整 Unicode case folding。结果按 `updated_at DESC, id DESC` 排序。Picker 最多
显示最近更新的 50 条匹配；存在更多结果时，prompt 会要求缩小 query。每次搜索和选中结果
revalidation 都受 5,000,000 SQLite VM-op scan budget 限制；预算耗尽时返回
`result_too_large`。Protocol client 可改用 `thread.search` 返回的 keyset cursor 继续读取匹配
page。

`/export` 接受 Workspace 相对目标路径，以及可选的 `markdown` 或 `json` format；默认
Markdown。同一 Thread 会生成最多 5 MiB 的确定字节，渲染在 event loop 外执行。Citation
保留在各自 assistant entry 上：有引用的 Markdown entry 会渲染自己的 Sources 区域，JSON
assistant entry 始终公开 `citations` list。导出只包含公开会话数据，绝不暴露内部 workspace
key 或私有 entry metadata。目标会经过通常的工作区 identity 与 safe-write 校验，且规范化
路径必须在 mutation 前满足 1–1,000 字符。创建或更新文件时会记录 ChangeSet，可用 `/undo`
恢复；内容未变化时不记录 ChangeSet。失败且 reconciliation 后没有文件 evidence 的尝试不会
发布空 ChangeSet；若字节已落盘，恢复会保留其真实 evidence。

`/auth` 绝不接受 key 作为命令参数。Picker 可能为来源选择、替换和删除生成内部 continuation
token；请使用遮罩交互，而不是编写脚本调用这些 token。保存 DeepSeek 或 Kimi key 会执行
一次短 Provider 验证请求。模型 Provider 不可达时会明确提供 save-unverified 选项；被模型
Provider 拒绝的 key 不会保存。Mem0 与 Tavily 不同：`/auth mem0` 和 `/auth tavily` 只执行
本地输入/存储验证，无需远程凭据检查就会保存该值。无效 key 会在后续云端 Memory 或 Web
请求真正连接服务时才被发现。

`/model` 在指定 Provider 时，首先确保该 Provider 有可用且已选中的凭据，随后只提供
[配置参考](configuration.zh-CN.md)中的精选 model。

`/status` 的 `Changes` 行表示与当前 Thread 关联的最新已封存 Agent ChangeSet 中的唯一
路径数。它不包含 direct shell operation；该 ChangeSet 被撤销后显示为零；它不是 Git
工作树 dirty 数。若无法确认 runtime readiness，`/doctor` 会报告 `Unverified`，而不会
假定配置、Application SQLite 或 checkpoint 服务健康。Application SQLite 会通过进程级
owning connection 运行有界只读 `quick_check`，checkpoint readiness 则通过 checkpoint
saver 检查；两者都不会修复或重写状态。

## Memory 子命令

| 语法 | 结果 |
| --- | --- |
| `/memory` | 选择 Local 或 Cloud Memory，再选择 On/Off。 |
| `/memory local` | 打开 Local Memory On/Off picker。 |
| `/memory local on\|off` | 持久化并应用本地 Memory enablement。 |
| `/memory list user\|workspace` | 返回条目和当前 content hash。 |
| `/memory add user\|workspace <content>` | 添加一条通过策略的本地条目。 |
| `/memory replace user\|workspace <entry_id> <content>` | 替换一条本地条目。 |
| `/memory remove user\|workspace <entry_id>` | 删除一条本地条目。 |
| `/memory mem0` | 打开 Mem0 On/Off picker。 |
| `/memory mem0 on\|off` | 持久化并应用 Mem0 enablement。 |
| `/memory mem0 search <query>` | 搜索对应作用域的 Mem0 记录。 |
| `/memory mem0 remove <memory_id>` | 验证所有权/作用域，然后删除一条云端记录。 |

本地命令修改会在应用 compare-and-swap 更新前立即为文档生成快照。Agent Memory 工具会
显式暴露 content hash。见 [Memory](../extensions/memory.zh-CN.md)。

## MCP 子命令

| 语法 | 结果 |
| --- | --- |
| `/mcp`、`/mcp status` | 显示每一台服务器。 |
| `/mcp status <id>` | 显示一台服务器或 `mcp_server_not_found`。 |
| `/mcp enable <id>` | 为 Workspace 服务器持久化与 config-hash 绑定的 enablement。 |
| `/mcp disable <id>` | 移除 Workspace enablement 及其 registry namespace。 |
| `/mcp restart <id>` | 移除当前 namespace/client，并在有效时重新连接。 |

User 服务器只能通过 User YAML 启用；对它们执行 `enable` 和 `disable` 会返回
`user_config_required`。见 [MCP](../extensions/mcp.zh-CN.md)。

## Web 子命令

| 语法 | 结果 |
| --- | --- |
| `/web`、`/web status` | 显示 enablement/runtime availability、Tavily credential 与显式 proxy 是否存在、当前 Thread authorization、请求 budget、diagnostic code 和披露。 |
| `/web on` | 要求 `TAVILY_API_KEY`，校验显式 proxy，原子持久化 `web.enabled: true`，并在报告成功前重建 runtime。 |
| `/web off` | 原子持久化 `web.enabled: false`，重建不含 `web_search` 或 `web_fetch` 的 runtime，并清除全部 Thread network grant。 |
| `/web revoke` | 清除选中 Thread 的 `network.read` grant，但不关闭 Web。 |

启用 Web 会披露 Search query 与请求的 Fetch URL 将依据 Tavily 的
[隐私政策](https://www.tavily.com/privacy)与[平台条款](https://www.tavily.com/terms)
发送给 Tavily。Apply 失败会回滚 user config；如果
无法证明已安全恢复，后续 Web mutation 会被 `web_configuration_recovery_required` 隔离。

## Ink 本地命令

| 命令 | 语法 | 效果 |
| --- | --- | --- |
| `/help [command]` | 零或一条 command，可选前导 `/` | 渲染完整 catalog 或一条聚焦帮助行。 |
| `/theme [system\|dark\|light]` | 零或一个 theme | 显示 picker 或以原子方式更新 `ui.json`。 |
| `/copy` | 无参数 | 复制最新的持久 assistant 消息；不会复制尚未完成的实时文本。 |
| `/quit` | 无参数 | 关闭 Core 并退出。 |

Ink 本地所有权意味着没有 `command.execute` RPC，并不意味着命令可以违反生命周期规则：
`/quit` 仍会等待协调后的 shutdown，UI preferences 也仍是有界本地状态。

## 前台准入

Core 以原子方式准入一个前台 Operation，或一个会改变状态/调用外部系统的命令。准入发生在
创建 Turn 或修改状态之前。存在活动 Operation 时，只允许以下 Application snapshot：

```text
/context
/workspace
/tools
/mcp
/mcp status
/mcp status <id>
/web
/web status
/status
/usage
/config
```

`/diff` 被排除，因为它可能在 ChangeSet 变化期间读取它。`/doctor` 被排除，因为它可能联系
Provider。其他所有 Application 命令如果并发到达 Core，都会返回 `operation_busy`；TUI
通常会将其排队。

Pending interaction 会阻止新 Operation 和状态变更。Snapshot 命令、取消和匹配的
`interaction.respond` 除外。Tool approval 是现有 Operation 的 continuation，因此只有在
Thread、Turn、operation 和 interaction 身份全部匹配后，才会绕过普通 exclusive gate。

## 结果形式

Application 命令只返回一个 `CommandOutcome` 分支：

- `result`：有类型的 payload，例如 `status`、`diff`、`tools` 或 `thread_transition`；
- `interaction`：有类型的 selection、secret prompt 或 Application interaction，可选附带
  context payload；
- `error`：稳定的 code 和有界的用户可见消息。

命令输入和结果保持为两个独立的终端 block。因此 picker 取消、无效参数和 Core 错误都会
保留精确的已提交命令。完整 wire schema 见 [Protocol v5](protocol.zh-CN.md)。
