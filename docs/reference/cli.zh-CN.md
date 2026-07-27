# CLI 与键盘参考

公共 `awesome` 可执行文件提供 Ink 终端界面与 headless 单 Turn 模式。官方安装器打包了
私有 Node.js 22.23.1
runtime，因此使用安装器的用户不需要预装 Node。从源码运行或直接安装 npm 包，需要
Node.js 22.23.1 或更高版本。Ink 界面还需要交互式 stdin 和 stdout；`awesome run` 是受支持的
非交互界面。客户端会通过其启动环境发现并启动
一个私有 `awesome-core` 进程；Core 执行所有模型、状态和工具操作。

## 启动语法

```text
Usage: awesome [--continue | --resume [thread_id]]
       awesome run <prompt> [--new | --thread <id>] [options]

Options:
  --continue            Resume the most recent thread in this workspace
  --resume [thread_id]  Choose a recent thread or resume the given thread
  -V, --version         Print the installed product version
  -h, --help            Show this help

Headless run options:
  --new                  Create a new thread (default)
  --thread <id>          Run in the selected existing thread
  --format <text|json>   Select final output format (default: text)
  --trust-workspace      Trust this workspace for the current startup flow
  --permission-mode <request_approval|accept_edits|full_access>
                         Select the process-local permission mode
  --allow-network        Declare network intent for this process only
```

| 调用 | 结果 |
| --- | --- |
| `awesome` | 在当前目录对应的 Workspace 中创建并选择一个新 Thread。 |
| `awesome --continue` | 选择该 Workspace 中最近更新的 Thread。 |
| `awesome --resume` | 打开最近 Thread picker。 |
| `awesome --resume <thread_id>` | 恢复一个精确或可接受缩写的 Thread ID。 |
| `awesome run "<prompt>"` | 在新 Thread 中运行一个 Turn，并打印最终回答。 |
| `awesome run "<prompt>" --thread <id>` | 在精确指定的现有 Thread 中运行一个 Turn。 |
| `awesome -V`、`awesome --version` | 打印数字产品版本并退出。 |
| `awesome -h`、`awesome --help` | 打印帮助并退出。 |

交互启动 flag 不能组合。Headless 选项只在 `run` 后有效；`--new` 与 `--thread` 互斥。
不接受其他公共启动 flag。未知或格式错误的参数会把同一份 usage 契约写入 stderr，并以
退出码 2 退出。

启动目录就是 Workspace。允许普通输入之前，会先解决信任、本地状态兼容性和 Core/TUI
protocol 兼容性。见[文件与状态](files-and-state.zh-CN.md)和 [Protocol v4](protocol.zh-CN.md)。

## Headless 运行

`awesome run` 不启动 Ink，只执行一个自然语言 Agent Turn：

```text
awesome run "Summarize the failing tests" --trust-workspace
awesome run "Continue the analysis" --thread <thread_id> --format json
awesome run "Apply the reviewed fix" --permission-mode accept_edits
```

带引号的 prompt 是一个必需参数。默认创建新 Thread；`--thread <id>` 改为选择一个精确的
现有 Thread。启动过程复用交互界面的同一套 trust、状态预检、配置、Thread/Turn 生命周期、
私有 Core 和 Application facade，不会创建第二套 runtime 或公共远程 API。

`--trust-workspace` 接受规范启动 Workspace 的信任提示。如果没有该 flag，所需的信任或
其他任何未解决启动 interaction 会以退出码 3 退出。`--permission-mode` 为选中的 Thread
请求三种正常模式之一。`full_access` 这个拼写本身就是本次 headless 进程对警告的显式确认；
它仍然只在 Thread/Session 范围有效，也不能覆盖硬拒绝。如果 Turn 随后需要 runner 无法
解决的 interaction，Awesome 会请求取消并以退出码 3 退出。

`--allow-network` 只授权本进程把当前 headless Turn 精确匹配的 `network.read` prompt
解析为 `allow_once`。它本身不会启用 Web，不能创建 Thread grant 或处理其他 interaction，
也绝不能绕过硬拒绝。

使用 `--format text` 时，stdout 只包含持久化的最终 assistant 文本，后跟一个换行符。
使用 `--format json` 时，stdout 只包含一行紧凑 JSON 文档，后跟一个换行符：

```json
{"version":2,"type":"awesome.run.result","thread_id":"...","turn_id":"...","text":"... [[S1]]","citations":[{"id":"S1","title":"Example","url":"https://example.com/source"}],"termination_reason":null,"usage":{"input_tokens":0,"output_tokens":0,"reasoning_tokens":0,"cache_read_tokens":0,"cache_write_tokens":0,"model_calls":0,"tool_calls":0,"provider_retries":0,"compressions":0,"web_requests":1,"active_execution_seconds":0}}
```

该 JSON 文档独立于 Protocol v4 进行版本管理。Version 2 新增有序 `citations` 数组和
`usage.web_requests`；它报告持久化回答与 Turn 事实，不是 protocol event stream。任何非零
退出时 stdout 都为空，诊断写入 stderr。

| 退出码 | 含义 |
| ---: | --- |
| `0` | Turn 已完成，并写出最终文本或 JSON 文档。 |
| `1` | 运行失败，包括意外 Core 启动、模型/配置、Turn、传输或持久化结果失败。 |
| `2` | 参数或已知 CLI/runtime 前提无效，包括可识别的 Core executable 启动失败。 |
| `3` | 信任、状态重置、Thread 选择、审批或其他 interaction 仍未解决。 |
| `130` | 收到 SIGINT；Awesome 会先请求取消活动 Operation，再关闭 Core。 |

SIGINT 绝不会打印部分回答。Runner 会在有界期限内尝试确认取消，再返回 130；如果确认超时，
stderr 会报告该事实，launcher 随后对该 Turn 使用的同一个 Surface 和 Core 进程执行有界关闭。

## 输入分类

Composer 根据第一个非空白字符分类：

| 输入 | 路由 |
| --- | --- |
| 普通文本 | `turn.submit`；模型收到一个新的 Agent Turn |
| `/name ...` | Slash Command catalog；路由到 Application 或 Ink 所有权 |
| `! command` | `direct.execute`；不会调用模型，也不是普通 shell prompt——精确输入是独立于 Thread mode 的直接授权；schema、hard-deny policy、Change Journal、超时、取消和审计仍然适用 |
| 空输入/纯空白 | 不提交 |

Slash 参数支持单引号、双引号和反斜杠转义：

```text
/rename "Investigate startup race"
/memory add user 'Prefer tests near the changed boundary.'
```

未闭合引号或末尾转义会产生 `invalid_arguments`。引号由 TUI command tokenizer 处理，而
不是由 shell 处理。对于 `! command`，`!` 之后的全部内容作为一个直接命令字符串发送给
host shell policy。

自然语言输入中的 `@path` 引用由 Core 解析，并把有界的 Workspace 文件或目录选择快照放入
Turn 上下文。它们不会改变启动工作目录。

## Composer 编辑

| 按键 | 行为 |
| --- | --- |
| Enter | 提交当前输入。 |
| Shift+Enter 或 Ctrl+J | 插入换行。 |
| Left / Right | 移动一个 grapheme。 |
| Home / End | 移到当前视觉行的行首或行尾。 |
| Ctrl+A / Ctrl+E | 移到整个 buffer 的开头或结尾。 |
| Backspace / Delete | 删除光标之前或光标位置的内容。 |
| Ctrl+W | 删除前一个词。 |
| Ctrl+U / Ctrl+K | 删除到当前行的行首或行尾。 |
| Composer 为空时按 Up / Down | 浏览已提交历史，或按照当前所有权召回 pending input。 |

文本处理能识别 grapheme 和显示宽度。使用真实终端光标；IME 预编辑渲染仍由终端宿主负责。

## 命令菜单与交互

- 输入 `/` 打开可搜索的命令候选列表。
- Up/Down 移动选中项，也可穿过滚动的十行窗口。
- Tab 只补全规范 `/command` 名称。
- Enter 执行选中命令一次。
- Escape 关闭命令列表，不改变草稿。
- Picker 以及 Trust、Approval、Auth、Secret、Recovery 和 Fatal 界面显示时独占输入。
  Up/Down 选择，Enter 确认，Escape 根据该界面取消或拒绝。
- Ctrl+O 切换所有有界、可展开详情，包括 Thinking、工具序列、诊断、Diff、Undo 和 Redo
  输出。

## 取消、pending input 与退出

Ctrl+C 的行为取决于上下文：

1. 存在活动 Operation 时，请求取消；
2. Composer 非空时，清除草稿；
3. 空闲且 Composer 为空时，显示退出提示；
4. 两秒内第二次空闲 Ctrl+C 会退出。

只有 Composer 为空时，Ctrl+D 才退出。`/quit` 执行正常的 Core shutdown 路径。

Operation 活动期间，TUI 在仅限当前会话的 FIFO 中最多保存三项 pending submission。自然
语言消息、Slash Command 和直接命令共享该队列。它们在被提升前不会被解析、发送、绑定到
Thread 或写入 transcript 历史。Composer 为空时，Up 会先召回最新的 pending item；召回
顺序是 LIFO，但执行顺序仍是 FIFO。

排队的 `/new` 或 `/resume` 会先完成其权威 Thread 转换，之后才解析下一项 pending item。
排队的 `/quit` 是一道 barrier：除非 `/quit` 在执行前被召回，否则不再接受更后的条目。

## 终端与进程失败

Node 早于 22 时，CLI 会在启动前退出。交互启动还要求两个 terminal stream 都是 TTY；
`awesome run` 不要求。Core 丢失、格式错误的 NDJSON、protocol 或版本不兼容，以及意外 UI
exception 都会产生 fatal 界面。请求级产品
错误仍是 transcript feedback，不会伪装成进程失败。

命令语法见 [Slash Commands](commands.zh-CN.md)。Shell 安全和超时见
[内置工具](built-in-tools.zh-CN.md)。
