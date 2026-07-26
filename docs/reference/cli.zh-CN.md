# CLI 与键盘参考

公共 `awesome` 可执行文件是 Ink 终端客户端。官方安装器打包了私有 Node.js 22.23.1
runtime，因此使用安装器的用户不需要预装 Node。从源码运行 TUI 或直接安装其 npm 包，则需要
Node.js 22.23.1 或更高版本，以及交互式 stdin 和 stdout。客户端会通过其启动环境发现并启动
一个私有 `awesome-core` 进程；Core 执行所有模型、状态和工具操作。

## 启动语法

```text
Usage: awesome [--continue | --resume [thread_id]]

Options:
  --continue            Resume the most recent thread in this workspace
  --resume [thread_id]  Choose a recent thread or resume the given thread
  -V, --version         Print the installed product version
  -h, --help            Show this help
```

| 调用 | 结果 |
| --- | --- |
| `awesome` | 在当前目录对应的 Workspace 中创建并选择一个新 Thread。 |
| `awesome --continue` | 选择该 Workspace 中最近更新的 Thread。 |
| `awesome --resume` | 打开最近 Thread picker。 |
| `awesome --resume <thread_id>` | 恢复一个精确或可接受缩写的 Thread ID。 |
| `awesome -V`、`awesome --version` | 打印数字产品版本并退出。 |
| `awesome -h`、`awesome --help` | 打印帮助并退出。 |

Flag 不能组合，也不接受其他公共启动 flag。未知或格式错误的参数会打印同一份 usage 契约并
以失败退出。

启动目录就是 Workspace。允许普通输入之前，会先解决信任、本地状态兼容性和 Core/TUI
protocol 兼容性。见[文件与状态](files-and-state.zh-CN.md)和 [Protocol v3](protocol.zh-CN.md)。

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

Node 早于 22 或任一终端 stream 不是 TTY 时，CLI 会在启动前退出。Core 丢失、格式错误的
NDJSON、protocol 或版本不兼容，以及意外 UI exception 都会产生 fatal 界面。请求级产品
错误仍是 transcript feedback，不会伪装成进程失败。

命令语法见 [Slash Commands](commands.zh-CN.md)。Shell 安全和超时见
[内置工具](built-in-tools.zh-CN.md)。
