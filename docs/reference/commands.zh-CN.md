# Slash Command 参考

Awesome 有一个封闭的命令 catalog。其中二十一条命令属于 Python Application，四条属于
Ink。Slash Command 是产品控制输入：它会显示在终端 transcript 中，但不会作为模型对话
消息存储。

## Application 命令

| 命令 | 精确公共语法 | 效果 |
| --- | --- | --- |
| `/new` | 无参数 | 创建并选择新 Thread；重置 Thread 作用域的权限状态。 |
| `/rename <title>` | 一个或多个标题 token | 为选中的 Thread 持久化手动标题。 |
| `/resume [thread_id]` | 零或一个 ID/prefix | 打开 picker 或选择一个 Workspace Thread。 |
| `/context` | 无参数 | 显示最新的活动上下文类别、实际 token 数和预算。 |
| `/compact` | 无参数 | 立即构建并持久化新的对话摘要。 |
| `/auth [deepseek\|kimi\|mem0]` | 正常使用时为零或一个 service | 通过 picker 选择并管理 Environment 或 Awesome API-key 来源。 |
| `/model [deepseek\|kimi]` | 正常使用时为零或一个 Provider | 选择 Provider/model；更新当前 Thread 和用户默认值。 |
| `/thinking [on\|off]` | 零或一个值 | 显示 picker，或设置当前 Thread 后续 Turn 的 Thinking。 |
| `/permissions [request_approval\|accept_edits\|full_access]` | 零或一个 mode | 检查或更改会话权限模式；Full access 需要单独确认。 |
| `/workspace` | 无参数 | 显示活动 Workspace 的展示路径。 |
| `/diff [change_set_id]` | 零或一个 ID | 渲染最新或指定 ChangeSet 的 diff。 |
| `/undo [change_set_id]` | 零或一个 ID | 恢复已应用且可逆的 ChangeSet 的 before-state。 |
| `/redo [change_set_id]` | 零或一个 ID | 恢复已撤销 ChangeSet 的 after-state。 |
| `/tools` | 无参数 | 列出有效 catalog，以及当前模式下是否需要审批。 |
| `/skills [auto\|off\|name]` | 零或一个 mode/name | 检查或设置当前 Thread 的 Skill mode。 |
| `/mcp [status [id]\|enable <id>\|disable <id>\|restart <id>]` | 如左所示 | 检查或管理 MCP 服务器。 |
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

`/auth` 绝不接受 key 作为命令参数。Picker 可能为来源选择、替换和删除生成内部 continuation
token；请使用遮罩交互，而不是编写脚本调用这些 token。保存 DeepSeek 或 Kimi key 会执行
一次短 Provider 验证请求。模型 Provider 不可达时会明确提供 save-unverified 选项；被模型
Provider 拒绝的 key 不会保存。Mem0 不同：`/auth mem0` 只执行本地输入/存储验证，无需远程
凭据检查就会保存该值。无效 Mem0 key 会在之后启用或调用云端 Memory 时才被发现。

`/model` 在指定 Provider 时，首先确保该 Provider 有可用且已选中的凭据，随后只提供
[配置参考](configuration.zh-CN.md)中的精选 model。

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
保留精确的已提交命令。完整 wire schema 见 [Protocol v3](protocol.zh-CN.md)。
