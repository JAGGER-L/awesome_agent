# 命令与交互

本页说明如何启动 Awesome、控制实时 Session，以及理解命令行为。它是一份面向任务的指南；权威命令清单和参数语法见[命令参考](../reference/commands.zh-CN.md)。

## 启动命令

从应成为 Workspace 的项目目录运行 Awesome：

| 调用方式 | 结果 |
| --- | --- |
| `awesome` | 在当前目录中启动新 Thread。 |
| `awesome --continue` | 恢复当前 Workspace 最近更新的 Thread。 |
| `awesome --resume` | 打开近期 Workspace Thread 选择器。 |
| `awesome --resume <thread_id>` | 恢复一个匹配的 Thread。 |
| `awesome -V` 或 `awesome --version` | 打印数字格式的产品版本并退出。 |
| `awesome -h` 或 `awesome --help` | 打印启动帮助并退出。 |

不支持其他公开启动 flag。发布版 `awesome` 命令始终启动 Ink TUI，再由它启动一个私有 Python Core 进程。Core 不是需要单独管理的服务。

## 三类输入

Composer 会去掉前导空白，并按第一个非空白字符路由文本：

```text
自然语言              -> Agent Turn
/command              -> slash command
! shell command       -> direct execute Operation
```

自然语言中的 `@path` 会添加显式 Workspace 路径引用：

```text
比较 @src/config.py 与 @tests/test_config.py，并解释不一致之处。
```

`!` 不会要求模型选择或改写命令。它表示显式用户权限，跳过普通 shell 审批，同时仍经过 Core 的 hard-deny 策略、有界 process runner、脱敏、审计和 Change Journal observation。

## 会话命令

| 命令 | 作用 |
| --- | --- |
| `/new` | 在当前 Workspace 创建并选择一个新 Thread。 |
| `/rename <title>` | 为当前 Thread 持久化用户选择的标题。 |
| `/resume [thread_id]` | 选择或指定当前 Workspace 中之前的 Thread。 |
| `/thinking [on\|off]` | 检查或设置当前 Thread 未来 Turn 的 Thinking。 |
| `/model [deepseek\|kimi]` | 为当前 Thread 和用户默认值选择 Provider 与模型。 |
| `/skills [auto\|off\|name]` | 检查或选择未来 Turn 的 Skill 模式。 |

新 Thread 接受的第一条自然语言消息会提供一个长度受限的自动标题。`/rename` 要求标题非空，标题过长时会拒绝，而不是静默修改。`/new` 不接受标题参数。

选择 Thread 会恢复其持久消息、Turn、模型、Thinking 和 Skill 选项，同时将 Session 权限重置为 Request approval 并清除临时 grant。之前的 Thread 仍可通过 `/resume` 访问。

## 上下文与检查命令

| 命令 | 显示或更改的内容 |
| --- | --- |
| `/context` | 最近有意义的 context manifest、估算和预算。 |
| `/compact` | 总结当前 Thread 中符合条件的较早已完成 Turn。 |
| `/workspace` | 显示用于启动活动 Workspace 的路径；Core 在内部跟踪规范路径和物理身份。 |
| `/status` | 产品、Thread、模型、权限、扩展、Operation 和变更摘要。 |
| `/usage` | 累计观测到的 Token、模型、工具、重试、压缩和活动时间用量。 |
| `/config` | 配置来源和凭据存在性诊断；绝不显示 secret 值。 |
| `/doctor` | 本地状态检查、Workspace 指令诊断和按需 Provider 验证。 |
| `/tools` | 有效的内置/扩展工具 catalog 和审批状态。 |

`/context` 和 `/usage` 回答不同的问题。前者解释为模型组装了什么；后者统计 Thread 消耗了什么。`/config` 有意不提供原始配置 dump。`/doctor` 可能发起 Provider 网络请求，并为失败检查呈现有界细节。

没有足够的已完成历史时，`/compact` 可能不做任何更改。运行期间，TUI 会保留一条待处理结果行，并用终态结果替换该行。

## 变更命令

| 命令 | 作用 |
| --- | --- |
| `/diff [change_set_id]` | 渲染最近或指定的已记录文件 delta。 |
| `/undo [change_set_id]` | 恢复所选 applied ChangeSet 的已记录文件状态。 |
| `/redo [change_set_id]` | 重新应用已经成功撤销的 ChangeSet。 |

这些命令使用精确的 ChangeSet 生命周期和冲突检查，不声称能够撤销任意 shell 或 MCP 影响。参见[审查、撤销与重做](changes.zh-CN.md)。

## Provider 与凭据命令

运行 `/model` 选择 DeepSeek 或 Kimi。如果 Provider 没有选中且可用的凭据，Awesome 会先打开遮罩 secret 输入，再显示模型选择器。成功选择模型会更新当前 Thread 和未来 Thread 的用户默认值，但不会改写其他已有 Thread。

使用：

```text
/auth
/auth deepseek
/auth kimi
/auth mem0
```

`/auth` 会分别显示 Environment 和 Awesome 管理的凭据来源。命令参数绝不接受密钥。已知无效的 Provider 密钥不会被保存。当验证无法访问 Provider 时，TUI 会询问是否把密钥保存为 unverified。删除 Awesome 管理的密钥不会在 Provider 侧吊销它，也不会静默选中 Environment 值。

## 权限与扩展命令

| 命令 | 作用 |
| --- | --- |
| `/permissions [request_approval\|accept_edits\|full_access]` | 检查或选择活动 Thread 的 Session 权限。 |
| `/memory` | 选择 Local Memory 或 Mem0 Cloud，并检查状态。 |
| `/mcp [status [id]\|enable <id>\|disable <id>\|restart <id>]` | 检查或管理已配置的 MCP server。 |

Full access 需要第二次、与 Thread 绑定的确认。Memory 的显式 list/add/replace/remove 和 Mem0 search/remove 形式见 [Memory](../extensions/memory.zh-CN.md)。Workspace MCP 启用状态和用户配置的 server 规则见 [MCP](../extensions/mcp.zh-CN.md)。

## TUI 本地命令

这些命令由 Ink 实现，不会要求 Core 修改产品状态：

| 命令 | 作用 |
| --- | --- |
| `/help [command]` | 在 transcript 中渲染整个命令 catalog 或一个匹配行。 |
| `/theme [system\|dark\|light]` | 检查或设置本地显示主题。 |
| `/copy` | 将最近的助手回答复制到剪贴板。 |
| `/quit` | 协调 Core 关闭并退出 TUI。 |

`/help` 是 transcript 内容而非 modal，因此不会遮住会话。`/copy` 复制最近一个已完成的助手回答，而不是活动 stream 或 Tool 细节。

## 键盘所有权

- 输入 `/` 会打开命令候选项。Up/Down 更改选项，Tab 补全规范命令，Enter 提交一次，Escape 关闭菜单但不替换 draft。
- 可见的 picker、Trust prompt、Approval prompt 或 Auth prompt 会拥有键盘。Up/Down 选择，Enter 确认，Escape 按相应 interaction 的定义取消或拒绝。
- Ctrl+C 请求取消活动 Operation。收到终态事件后才恢复输入；清理失败时，错误会保持可见。
- Ctrl+O 展开或折叠 Thinking、Tool sequence 和 Undo/Redo path 的有界细节。细节默认折叠。
- Composer 为空时，Up 会把最新的排队输入召回 draft。重复操作会从最新向最早移动。

## 排队与前台顺序

Operation 运行期间，TUI 最多排队三个后续输入。自然语言、slash command 和 direct command 按提交顺序启动。排队的 `/quit` 在被召回或到达队首前，会拒绝新增队列项。

Core 自身以原子方式只允许一个可变前台所有者。如果两个 Operation 发生竞争，失败方会在持久化 Turn 或状态 mutation 之前收到 `operation_busy`。待处理 interaction 会以 `interaction_busy` 阻止新 Operation 和状态变更，直到得到解决。

在私有 Core 命令边界，活动 Operation 期间只允许以下无副作用快照：

```text
/context  /workspace  /tools  /mcp  /mcp status [id]
/status   /usage      /config
```

`/diff` 被排除，因为活动 ChangeSet 可能仍在变化。`/doctor` 被排除，因为它可能联系 Provider。当前 Ink TUI 不会并发提交这些例外：它会把包括这些命令在内的每个后续输入排队，并在 Operation 完成后显示结果。Core allowlist 是协议/并发契约，而不是当前 UI 的实时监控承诺。

## 从状态开始诊断

行为出乎预期时运行 `/status`。它会标识产品版本、Workspace、Thread 及其 ID、模型、所选凭据可用性、权限模式、上下文使用、Thinking/Skill 模式、Memory 状态、MCP readiness、前台 Operation，以及存在时记录的文件变更数。

然后缩小问题范围：

- 上下文不匹配：`/context`；
- 预算耗尽：`/usage`；
- 意外审批：`/tools` 和 `/permissions`；
- 扩展失败：`/mcp` 或 `/memory`；
- 环境或 Provider 问题：`/config` 和 `/doctor`；
- 文件影响：`/diff`。

精确语法和所有权见 [CLI 参考](../reference/cli.zh-CN.md)与[命令参考](../reference/commands.zh-CN.md)。
