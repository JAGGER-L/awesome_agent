# 协议与 TUI

Ink + React 包是 Awesome 的终端展示界面。它负责输入、布局、临时 UI 模式、transcript
投影、主题、剪贴板和 Core 进程生命周期。它不负责模型调用、图执行、工具、产品持久化、
Memory、Skills 或 MCP。

一个私有 `awesome-core` 进程通过 JSON-RPC 2.0 消息暴露 Application facade，消息在
stdio 上以换行分隔的 JSON 作为 frame。Core stdout 只承载协议；日志写入 stderr。

协议接收类型化用户意图和控制请求，输出类型化 Application 结果以及有序事件事实。
TUI 把这些事实转换为临时状态和渲染状态。两个边界都不接受任意 Python 对象，也不允许
展示状态反向成为产品权威。

## 为什么使用私有协议

把 Node 展示与 Python 行为分离，可以让每种语言利用自己最强的生态，同时不复制产品
权威。带版本的严格协议会显式暴露进程崩溃、过期客户端和跨语言类型漂移。

该协议不是公共网络 API。它假设本地只有一个由 launcher 管理的对等端，但仍会校验
每个请求并施加明确的输入/背压边界，因为畸形或不匹配的本地组件也不能破坏状态。
Frame 限制目前并不对称，具体见下文。

## Protocol v3 契约

初始化要求字面量 `protocol_version: 3`、客户端名称 `awesome`，以及与 Core 相同的产品
版本。即使产品版本相同，v2 客户端也会明确失败。协议版本与产品版本回答不同问题：
线缆兼容性与发布身份。

请求 ID 是 JSON/JavaScript 安全的整数或 JSON-RPC parser 允许的字符串。数字 ID 必须
是整数、有限值、非 Boolean，并位于 `-(2^53 - 1)..2^53 - 1`。可选字段不存在时应省略；
只有 schema 明确声明可为 null 时才接受显式 `null`。

当前请求方法如下：

| 方法 | 用途 |
| --- | --- |
| `initialize` | 协商身份并执行启动/bootstrap |
| `application.getState` | 读取权威 Application 状态 |
| `thread.list` | 分页读取工作区 Threads |
| `thread.read` | 分页读取一个 Thread 及其 transcript 投影 |
| `turn.submit` | 准入自然语言前台工作 |
| `direct.execute` | 准入直接 shell Operation |
| `command.execute` | 执行 Core 管理的斜杠命令 |
| `provider.credential.set` | 添加、替换或删除已选择的凭据来源 |
| `interaction.respond` | 决议一个类型化 pending interaction |
| `operation.cancel` | 取消一个活动 Operation ID |
| `shutdown` | 关闭准入并干净地停止 Core |

Core 发送带严格 envelope 的 `event` notification。事件族涵盖 Operation 与 Turn
生命周期、assistant 文本/reasoning delta、提供商重试、工具生命周期、上下文准备/压缩、
usage、Memory 状态、interaction 和 warning。

## 跨语言证据

Python 负责序列化方法结果、`CommandOutcome` variant 和事件。
`scripts/generate_protocol_fixtures.py` 在 `protocol/fixtures/v3/` 下写入确定性的有效与
无效 fixture。TypeScript Zod schema 校验同一语料库。

```text
Python Pydantic contracts
  -> generated v3 fixtures + manifest hashes
  -> TypeScript strict Zod schemas
  -> protocol contract tests
  -> exhaustive reducers/presenters
```

不存在通用 JSON fallback。新增 payload variant 时，必须更新 Python 类型、fixture
generator、TypeScript schema、必要时的 reducer/effect、Presenter 以及测试。
TypeScript exhaustive switch 会在编译时暴露遗漏的 case。

## 握手状态机

stdio Host 会控制 Application 访问：

```text
UNINITIALIZED
  -> initialize in flight: INITIALIZING
  -> ready result: READY
  -> trust_required/state_reset_required: BOOTSTRAP_INTERACTION
  -> failure: previous state

BOOTSTRAP_INTERACTION
  -> matching interaction.respond
  -> trust accepted: READY
  -> reset accepted: initialize again
```

进入 `READY` 之前，普通请求会收到稳定的 server-not-initialized 或 server-not-ready 错误。
第二个并发 initialize 会收到 `initialization_in_progress`。Bootstrap 期间，只准入匹配的
interaction、另一次 initialize、取消和 shutdown。畸形或 v2 initialize 绝不会打开 gate。

进入 `READY` 后仍可重复初始化，因此界面重试可以观察当前快照，而不会创建第二个
Application。

## Frame 与调度边界

Core 把每条请求行限制为 1 MiB。TUI 对编码后的请求和解码后的 Core frame 施加相同限制。
Core output writer 会将一整行串行写出，但目前不会拒绝超过 1 MiB 的输出 frame。Core
读取一条顺序字节流，而准入的普通请求在独立 task 中运行。这样，缓慢的提供商或命令
不会阻塞紧急控制请求的解析。

| 资源 | 上限 |
| --- | ---: |
| Core 请求行 | 1 MiB |
| TUI 编码/解码 frame | 1 MiB |
| Core 输出 frame | 无显式预检上限 |
| 普通 in-flight 请求 | 128 |
| 后台控制请求 | 16 |
| 活动/近期请求 ID | 4,096 |
| stdout writer queue | 64 条消息 |
| stdout 写入期限 | 5 秒 |

`initialize` 和 `interaction.respond` 使用后台控制 lane。`operation.cancel` 与 `shutdown`
是紧急请求，会绕过两个上限。Shutdown 请求必须先通过常规 JSON-RPC 形态和 ID 校验；
无效输入不能取消合法工作。

过载请求会收到稳定的 busy 错误。Notification 没有响应，因此过载的普通 notification
会被丢弃。活动与近期 ID 可以拒绝重复工作，同时不保留无界历史。

所有 response 与 event 共用一个 writer lock 和一个有界 queue。每一行都会被整体
序列化，所以并发完成的请求不会交错 JSON 字节。消费端阻塞或写入失败时，系统会关闭
传输路径，而不是积累无界内存。

不对称的 frame 上限可以被用户观察到：schema 有效的 `thread.read` 页面可以包含最多
500 个大条目，而 `/tools` 没有总量分页。任一 response 都可能超过 1 MiB，被 Core 写出
后又被随产品发布的 TUI 当作致命超大 frame。调用方应使用较小的 transcript 页面；
`/tools` 目前没有调用方缓解措施。运行时修复需要明确的 Core 输出策略——拒绝、分页或
分块——以及跨语言回归 fixture，而不是只在文档里给出承诺。

线缆并发不等于 mutation 并发。Application foreground arbiter 仍决定哪些 Turn、直接
命令、状态变更、interaction 或 shutdown 可以运行。

## 语义展示流水线

Core 发送事实，而不是预格式化的终端 widget：

```text
Application fact
  -> Protocol v3 payload
  -> optional authoritative Surface effect
  -> exhaustive presentCommandPayload()
  -> CommandPresentation
  -> shared terminal components
  -> transcript
```

Effect 与 Presenter 相互分离。Effect 可以安装权威 Thread replacement 或 title，但不能
格式化输出。Presenter 把一个类型化 payload 映射为 row、notice、panel 或 empty state，
而不改变产品状态。

共享组件负责边框、换行、对齐、符号和语义颜色。它们不接受任意 record 并将其字符串化。
这会增加新命令的代码量，但能防止意外泄露内部 JSON 或 secret。

## 界面状态与展示状态

`SurfaceState` 是 Core 事实的投影：

- 连接与 fatal 状态；
- 当前 Application 与 Thread 快照；
- Thread generation 与 event sequence；
- 活动 Operation/Turn 时间线；
- pending interaction、usage、warning 和已提交 transcript。

主题、composer buffer/history、cursor metric、展开的详情、picker selection、secret-entry
文本和 pending-input queue 都是展示状态。它们不会进入协议或产品数据库。

这种区分确保恢复 Thread 时不会同时恢复过时的 UI 控件。新界面可以适配相同的
facade/event，同时选择不同的展示模型。

## 输入所有权

`TerminalInput.tsx` 是唯一的 Ink `useInput` subscriber。唯一的根 key router 将按键分发
给一个可辨识 UI mode：

- Composer；
- command menu；
- picker；
- secret input；
- approval；
- workspace trust 或 state reset；
- fatal flow。

恰好一个 mode 负责 Enter、Escape、Tab、方向键和全局取消。组件只渲染选定状态；
它们不会安装相互竞争的键盘 listener。这可以避免重复提交和依赖 mode 的竞态 bug。

Command menu 是 Composer 的 accessory，因此 draft 保持可见，其 cursor 保持活动。
Picker、approval、trust、secret 和 fatal mode 是独占的，会隐藏 Composer cursor。

## 终端光标与布局

Composer 使用 Ink 的物理光标，而不是打印出来的块状 glyph：

```text
grapheme-aware logical cursor
  -> display-width-aware viewport row/column
  -> React TerminalFrameMetrics
  -> InkCursorBridge
  -> useCursor physical terminal position
```

该 bridge 隔离了 Ink 7.1 的一个全屏约定：填满 viewport 的 frame 会省略末尾换行。
终端 frame metric 仍是本地展示状态。未来升级 Ink 后，只有当低于、等于和高于 viewport
的 ANSI 回归仍全部通过，才能移除该 bridge。

IME preedit 仍由终端 host 负责。Composer 逻辑处理已经提交的 grapheme 输入，不尝试自行
渲染平台 IME 状态。

自然的终端流依次为 Welcome、已提交 transcript、活动 Turn、pending input、notice、
command menu、Composer 或独占 interaction，最后是 status。当前 Thread 是动态 React
状态，而不是永久打印的终端输出。

## Pending input queue

一个 Core Operation 活动时，TUI 最多可以保存三个已经提交的终端输入。该 queue 被有意
限制为会话状态：

- item 按 FIFO 执行；
- 每个 head 只在被提升时解析；
- Composer 为空时，按 Up 可以召回 tail；
- picker 或 approval 会暂停提升；
- `/new` 与 `/resume` 会改变随后 item 的目标 Thread；
- 排队的 `/quit` 是一个有序终止 barrier；
- 可重试的 busy 竞态会把相同 identity 重新放回队首。

该 queue 不会变成 Runtime、协议方法、Thread record 或第二执行权威。Core 继续只准入一个
前台动作。

## Transcript 与事件校正

活动 Turn 是由 Thinking、工具 activity 和 assistant 输出组成的唯一有序时间线。
Delta 更新实时投影；已完成回答使用终态 Markdown。工具事件携带语义 verb、target、
outcome、summary、可选 detail、duration 和 error code。

`client_message_id` 用于校正乐观显示的用户输入与已准入的持久化条目。Thread replacement
会递增 generation、清空活动 frame、安装权威 Application/Thread 快照，并拒绝前一
generation 的迟到事件。Event sequence 用于检测重复和缺口。

重连或 resume 后，`thread.read` 是持久化来源。实时投影按稳定 identity 合并，而不是
盲目追加，从而避免 event 与 hydration transcript 描述同一事实时产生重复消息。

非致命请求失败会继续显示在当前 transcript 中，Composer 仍可使用。畸形 Core event、
传输损坏或 Core 退出则是 fatal，因为界面无法继续证明其投影；此时会禁用输入，而不会
假装本地状态是权威状态。

## 进程所有权

TUI 负责整棵 Core 进程树。在 POSIX 上，它在独立 session 中启动 Core 并终止 process
group。在 Windows 上，它可以使用 `taskkill /T /F`；同时 Core 在异步启动前独立安装
kill-on-close lifetime Job Object。Core 无法建立这一不变量时会 fail closed。

每条 shell 命令都有自己的嵌套 lifetime domain，详见
[工具与变更](tools-and-changes.zh-CN.md)。外层 Core domain 处理异常 Core 退出；内层
命令 domain 处理根进程完成、超时和取消。这些机制会限制孤儿进程，但不限制其文件系统
或网络访问。

## 失败边界

| 失败 | 所有者响应 |
| --- | --- |
| 畸形/超大 NDJSON | 协议错误或关闭传输 |
| 协议/产品版本不兼容 | 初始化失败；gate 保持关闭 |
| 普通请求饱和 | 类型化 busy 响应 |
| stdout 消费端停滞 | 有界写入期限后传输失败 |
| Thread replacement 后的过期事件 | generation 校验将其拒绝 |
| 无效类型化 payload | fatal 界面校验错误 |
| Core 健康时的请求错误 | 可见的非致命结果 |
| Core 进程退出 | fatal 状态并禁用 Composer |

## 设计取舍

- 私有进程边界增加 fixture 和生命周期工作，但能防止 Node 展示层导入 Python 执行内部。
- 严格 schema 要求两种语言协同变更；它会在用户看到畸形 panel 之前暴露漂移。
- 并发请求调度提高取消响应速度，而 Application arbiter 仍保持确定性的 mutation 顺序。
- 使用 host terminal scrollback 与自然流，可以避免第二个虚拟终端，但动态 Thread 状态
  需要显式校正。
- 小型会话内 input queue 能提高交互性，同时不会假装 Core 在执行多个前台动作。

## 源代码与测试索引

- Python schema 与方法：`protocol/jsonrpc.py`
- Host framing 与调度：`protocol/stdio.py`
- Fixtures：`protocol/fixtures/v3/`、`scripts/generate_protocol_fixtures.py`
- Core 进程适配器：`tui/src/core/process.ts`
- TypeScript schema：`tui/src/protocol/`
- 界面 reducer：`tui/src/state/`
- 输入 mode：`tui/src/interaction/`、`tui/src/components/Composer.tsx`
- Transcript：`tui/src/transcript/`
- 测试：`tests/unit/protocol/`、`tests/e2e/test_stdio_product.py`、
  `tui/tests/protocol/`、`tui/tests/structural/`、`tui/tests/e2e/`
