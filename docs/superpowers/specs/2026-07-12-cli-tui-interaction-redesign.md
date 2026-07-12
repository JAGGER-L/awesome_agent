# CLI/TUI 统一交互架构设计

## 1. 目的

本设计修正 Awesome 当前 CLI/TUI 中视觉层级、消息时序、键盘焦点、Slash Command、认证、审批、权限、Workspace Trust、模型身份和 Agent 过度执行等问题。

目标不是继续为每个界面增加局部状态和按键监听，而是建立一套单一、清晰、可恢复的终端交互体系：

- Python Core 是产品行为、权限和会话状态的唯一权威。
- Ink + React TUI 只负责输入意图、即时视觉反馈和事件渲染。
- 任意时刻只有一个输入所有者。
- 所有长操作都有开始、进展、结束或失败反馈。
- 新设计替代旧设计后，删除旧状态、旧事件和旧组件分支，不保留兼容层。

## 2. 范围与非目标

### 2.1 本次范围

- Welcome、主题、消息、Markdown、输入区和工具状态视觉层级。
- 用户消息即时显示与服务端持久化后的确定性对账。
- Slash Command 菜单、补全、执行反馈和 `/new` 切换。
- `/auth` 的 Provider、密钥输入、更新与删除交互。
- Provider、模型、运行环境和 fallback 身份一致性。
- Thinking、工具调用、工具结果和整轮耗时展示。
- Workspace Trust 启动门禁。
- Request approval / Full access 权限模式。
- Approval 的焦点、语义、响应和恢复。
- Run 取消后的可继续对话能力。
- Agent 最小行动和停止条件。

### 2.2 非目标

- 不改变 Python Core + Ink/React TUI 的语言边界。
- 不引入 LangGraph Server、Web API 或额外后台服务。
- 不引入 Docker sandbox；Docker 仍是未来可选 Tool Executor backend。
- 不设计复杂的规则编辑器、命令白名单 DSL 或持久化权限模板。
- 不保留旧协议字段、旧输入分支或兼容适配器。
- 不在本设计阶段编写实现代码。

## 3. 当前问题分类与复现

| 分类 | 复现 | 当前表现 | 应有行为 |
| --- | --- | --- | --- |
| 视觉层级 | 启动并发送一轮消息 | Welcome、历史、输入和状态缺少层级 | Mint 品牌视觉、稳定输入区、明确角色和状态 |
| 消息时序 | 提交普通消息 | 用户消息等待服务端回合后才出现 | 提交接受后立即进入历史，并在服务端确认后对账 |
| Markdown | 模型输出标题或列表 | 原始 `#`、`*` 直接显示 | 流式稳定、完成后完整的终端 Markdown |
| 命令输入 | 输入 `/` 后按方向键、Tab、Enter、Esc | 菜单只展示文本，不能完整操作 | 候选、选择、补全、执行和关闭形成闭环 |
| 命令反馈 | 执行 `/usage`、`/help`、`/new` | 静默、阻塞或残留旧消息 | 每个命令有历史反馈；新会话清空旧投影 |
| Auth | `/auth` 选择 Provider | 已配置无反馈；未配置没有可用密钥输入 | 专用掩码输入、验证、保存、取消与焦点恢复 |
| Approval | 工具请求审批后按 Enter | Enter 可能被其他监听者消费，Run 卡住 | Approval 独占输入并把确定结果送回 Core |
| 模型身份 | 询问模型身份 | 模型自行猜测为 Claude | Runtime 注入真实 Provider/模型/fallback 信息 |
| 工具状态 | 执行文件或 Shell 工具 | 通用完成文案，层级和耗时不清楚 | 操作语义、结果摘要、工具耗时清晰可见 |
| Agent 行为 | 只要求创建文件 | 写入后继续执行不必要命令 | 满足目标后停止；验证必须有任务依据 |
| Trust | 首次进入新目录 | 信息弱、Esc 无效、与权限语义混淆 | Trust 独占启动页；明确路径、影响和退出方式 |

## 4. 共同根因

### 4.1 交互状态分散

`tui/src/app/App.tsx` 同时持有 Picker、Credential、Help、Status、Notice 和 Composer 状态，并通过条件渲染隐式决定谁可以输入。`TrustPrompt` 又位于 CLI 根组件，启动阶段与运行期交互使用不同控制方式。

结果是 UI 的“当前模式”没有一个可验证的单一来源。

### 4.2 多个组件直接监听键盘

`Composer`、`Picker` 和 App 全局键分别调用 Ink 输入监听。是否激活依赖多个布尔条件和局部 ref，而不是统一路由结果。新增 Modal 时容易出现 Enter、Esc、方向键被多个监听者同时处理。

### 4.3 命令菜单只有展示能力

`CommandMenu` 根据 Composer 文本显示候选，但自身没有选择状态，也没有和 Composer 共享 Tab、方向键和 Enter 的语义，因此不是一个完整的命令交互状态。

### 4.4 Runtime Interaction 语义过窄

Core 当前 Interaction 主要覆盖 Workspace Trust 和 Shell 边界，decision 只有 trust、allow_once、deny。自由文本 prompt 和 choice 让 TUI 无法稳定渲染真实操作、危险等级、可授权范围和成功/失败状态。

### 4.5 UI 事件缺少展示所需事实

工具事件只提供工具名和通用 summary；live 工具耗时固定为零；用户提交与服务端 Turn 建立之间缺少客户端关联标识；模型身份和 fallback 没有形成单一快照。这迫使 TUI 猜测或等待最终持久化结果。

### 4.6 行为策略没有形成一个权限决策点

文件边界、Shell 策略、Interaction 和 Tool Executor 各自承担部分安全责任，但缺少统一的 capability policy 结果。若直接在 TUI 或各工具里增加“完全访问”判断，会产生新的旁路。

## 5. 设计原则

1. **单一权威**：会话、模型、权限、工具和持久化状态由 Python Core 决定。
2. **单一输入所有者**：任意时刻只有一个交互模式接收键盘事件。
3. **意图与事实分离**：TUI 发送用户意图；Core 返回结构化事实；TUI 不推断权限或工具结果。
4. **先本地反馈，后权威对账**：用户输入立即出现，但最终由 Thread/Turn 事实对账。
5. **默认最小权限**：首次进入可信项目仍采用 Request approval。
6. **硬安全不可关闭**：Full access 只把 `ASK` 转为 `ALLOW`，不能把 `DENY` 转为允许。
7. **最小行动**：Agent 只执行完成当前目标所必需的动作。
8. **替换而非叠加**：新协议和状态机落地后删除旧字段、旧分支和旧测试，不做双轨兼容。
9. **可恢复**：取消、拒绝、失败、Thread 切换后都能回到唯一明确的输入状态。

## 6. Target Architecture

```text
┌──────────────────────────────────────────────────────────────┐
│ Ink + React TUI                                             │
│                                                              │
│  Transcript  Status  Composer  Overlay Renderer              │
│       ▲          ▲        ▲            ▲                      │
│       └──────────┴────────┴────────────┘                      │
│                    UI State Machine                           │
│                           ▲                                   │
│                    Keyboard Router                            │
└───────────────────────────┼──────────────────────────────────┘
                            │ intent / typed events
                            ▼
┌──────────────────────────────────────────────────────────────┐
│ Python Application                                          │
│                                                              │
│  Session/Thread  Command  Interaction  Permission Snapshot   │
│        │           │          │                 │             │
│        └───────────┴──────────┴─────────────────┘             │
│                         Agent Core                            │
│                            │                                  │
│                 Capability Policy Engine                     │
│                 ALLOW │ ASK │ DENY                            │
│                            │                                  │
│                      Tool Executor                            │
└────────────────────────────┼─────────────────────────────────┘
                             ▼
                  Files / Host Shell / MCP
```

### 6.1 Ink TUI

负责：

- 渲染 Welcome、Transcript、状态、输入区和单一活动 Overlay。
- 把键盘输入路由给当前输入所有者。
- 对已接受的用户提交生成临时视觉投影。
- 消费结构化事件并与 Thread 读取结果对账。
- 保存纯展示偏好，例如主题和折叠状态。

不负责：

- 判断工具是否允许。
- 保存权限模式或会话授权。
- 推断当前模型、fallback 或工具操作语义。
- 直接执行命令、工具或 Graph。

### 6.2 Python Application

负责：

- Workspace Trust 门禁。
- Thread/Turn 生命周期和当前 Thread 切换。
- Slash Command 的产品语义。
- Auth、模型选择和模型身份快照。
- Permission Mode 和临时授权生命周期。
- Interaction 创建、响应、超时、取消和失败。
- 向所有 Surface 提供相同的结构化状态和事件。

### 6.3 Agent Core

负责：

- 真实 Provider/模型信息注入。
- reasoning、model call、tool call 和停止条件。
- 把工具结果完整反馈到下一轮模型输入。
- 遵守最小行动规则，不在目标完成后固定追加验证。

### 6.4 Capability Policy Engine

它是所有工具权限的唯一决策点。输入至少包括：

- capability；
- 规范化操作目标；
- workspace；
- 当前 Thread；
- Permission Mode；
- 当前 Thread 临时授权；
- 硬安全规则。

输出只有：

- `ALLOW`：直接执行；
- `ASK`：创建结构化 Interaction；
- `DENY`：直接拒绝并返回原因。

工具、Agent Prompt 和 TUI 都不能绕过这个结果。

## 7. 统一 UI 状态模型

TUI 状态分为三类，不再混用：

### 7.1 Product Projection

来自 Core 的权威状态：

- connection；
- workspace/thread；
- operation/turn；
- model identity；
- permission snapshot；
- pending interaction；
- transcript/events；
- usage/change/fatal。

### 7.2 Presentation State

只存在于 TUI：

- composer buffer 和历史；
-滚动位置；
- Markdown 流式渲染缓存；
-工具输出展开状态；
-主题；
-本地通知。

### 7.3 Interaction Mode

必须使用互斥联合状态表达，不允许多个布尔字段组合：

```text
startup_trust
composer
command_menu
command_picker
auth_secret
approval
permission_confirmation
```

`/help` 改为历史消息，不再占用 Overlay；Status 和普通命令反馈同样进入历史投影。

每次状态转换必须定义：

- 进入条件；
- 唯一输入所有者；
- Enter/Tab/Esc/方向键行为；
- 成功目标状态；
- 失败目标状态；
- 是否恢复 Composer 焦点。

### 7.4 稳定布局

- 已完成的 Transcript 只向上增长，不在 Composer 下方插入内容。
- Active Turn 位于历史与 Composer 之间，完成后原位转为历史块。
- Composer 固定为当前视口底部的输入区域；消息增加时移动的是上方内容，不是输入语义位置。
- 状态行位于 Composer 下方，只显示短状态，不承载命令结果或错误正文。
- Welcome 只属于空 Thread 的历史起点；开始对话后不重复渲染。

## 8. 键盘事件优先级

输入路由只有一个入口，优先级固定为：

1. Workspace Trust；
2. Auth Secret Input；
3. Approval；
4. Permission Confirmation；
5. Command/Model/Theme Picker；
6. Slash Command Menu；
7. Composer；
8. Global Lifecycle Keys。

规则：

- 当前模式先消费按键；只有未消费的按键才允许进入生命周期控制。
- Ctrl+C 在活动 Run 中请求取消；不同时触发退出或清空。
- Esc 关闭当前可关闭交互；Trust 中 Esc 退出；Approval 中 Esc 按拒绝处理。
- Enter 只提交当前输入所有者。
- 方向键优先操作可见菜单；没有菜单时才操作 Composer 历史或光标。
- Tab 只在 Slash Command Menu 中补全当前选项，不能提交命令。
- Interaction 正在提交时禁用重复响应，并显示提交状态。

## 9. Workspace Trust

### 9.1 语义

Workspace Trust 只回答：Awesome 是否可以打开并受该项目内容影响。

它不等于 Full access，也不替代 Tool Permission。Core 进程可以为握手而启动，但 Trust 之前不得读取项目配置、AGENTS.md、Skills、MCP 声明或项目代码，也不得运行工具。

### 9.2 UI

```text
  Trust this workspace?

  E:\test2

  Is this a project you created or trust?

  Awesome can read files in this workspace. File changes and
  shell commands follow your current permission mode.

❯ 1. Yes, I trust this folder
  2. No, exit

  ↑/↓ Select · Enter Confirm · Esc Exit
```

- 标题和当前选项使用 Mint。
- 路径单独高亮。
- 不渲染 Welcome、Transcript 或主输入框。
- 上下键与数字键选择；Enter 确认；Esc 退出。
- 保存失败时显示明确错误并留在 Trust 状态，不进入 Agent。

### 9.3 持久化

- Core 规范化绝对路径并生成 workspace identity。
- Trust 保存到用户级 SQLite，不写入项目。
- 只信任精确 workspace，不从父目录继承。
- 同一路径后续跳过确认；路径变化重新询问。
- No 不持久化并退出。

## 10. Permission Mode 与 Approval

### 10.1 模式

仅提供两种显式模式：

| 模式 | 默认 | 行为 |
| --- | --- | --- |
| Request approval | 是 | 读取通常允许；文件修改、删除和 Shell 按策略询问 |
| Full access | 否 | 将普通 `ASK` 转为 `ALLOW`，不显示交互审批 |

`/permissions` 打开模式选择器。切换为 Full access 时显示一次确认：

```text
Awesome will edit files and run shell commands without approval.
Commands execute directly on your host.

❯ Enable for this thread
  Cancel
```

Full access：

- 只对当前 Thread 有效，不持久化；
- `/new`、`/resume` 和退出后恢复 Request approval；
- 不能在 Run 活动中或 Interaction 待处理时切换；
- 仍显示全部工具调用和结果；
- 不绕过 workspace 边界、敏感路径、schema、预算、超时和取消；
- 不绕过提权、关机、磁盘操作和根目录破坏等 `DENY`。

### 10.2 Capability 分类

权限按能力分类，不按硬编码工具数量分类：

- `workspace.read`：`ls`、`read_file`、`glob`、`grep`；
- `workspace.write`：`write_file`、`edit_file`；
- `workspace.delete`：`delete`；
- `shell.execute`：`execute`；
- MCP/User Tool：必须声明 read/mutate/execute capability，未知能力默认 `ASK` 或 `DENY`。

默认基础工具从这些工具开始，但架构不限制只能存在这些工具。

### 10.3 Approval 选择

普通文件创建或编辑：

```text
Do you want to create circle_area.py?

❯ 1. Yes
  2. Yes, allow ordinary file edits for this thread
  3. No

  ↑/↓ Select · Enter Confirm · Esc Cancel
```

- Yes：仅本次。
- Allow ordinary file edits：只覆盖当前 Thread 的 `workspace.write`。
- No/Esc：拒绝当前操作。
- 删除和 Shell 不被“允许普通编辑”覆盖。
- Request approval 下 Shell 每次询问；第一阶段不提供“允许全部 Shell”。
- Prompt 使用结构化操作事实生成，不展示抽象工具名。

Interaction 响应失败时，Approval 保留并显示错误；成功后由 Core 的 resolved 事件关闭，恢复 Composer 或继续 Agent Run。

## 11. 消息、Markdown 与流式输出

### 11.1 用户消息即时显示

提交流程：

```text
Composer Submit
→ 本地生成 pending user block
→ 调用 turn.submit
→ Core 返回 operation/thread/turn identity
→ 流式事件更新当前 Turn
→ Thread 读取结果与 pending block 对账
```

提交 RPC 失败时，用户文本保留并标记失败，允许恢复到 Composer 重试；不能静默消失。`/new` 必须清除旧 Thread 的历史投影、pending blocks、live turn 和会话级授权，然后订阅新 Thread。

### 11.2 角色样式

- 删除 `You` 和 `Assistant` 标签。
- 用户消息使用 Mint/品牌色前导符和清晰缩进。
- Assistant 使用中性高对比前导符与正文。
- 状态、Thinking、工具和错误使用独立层级，不伪装成对话正文。

### 11.3 Markdown

- Assistant 最终消息按终端 Markdown 渲染标题、列表、强调、引用、代码块和链接。
- 流式阶段保持稳定块边界，避免每个 token 触发整篇重新排版。
- 未闭合 Markdown 结构在流式阶段使用安全的纯文本/增量策略；完成事件后执行最终渲染。
- 超长内容限制的是终端展示和折叠，不是模型 reasoning token 上限。

## 12. Thinking、工具和耗时

一轮展示顺序固定为：

```text
用户输入
→ Thinking 状态
→ 工具调用
→ 工具结果
→ 后续 Thinking
→ 最终回复
→ 本轮耗时
```

规则：

- 只显示本地实际测量的状态持续时间，不声称是 Provider 内部推理时间。
- Thinking duration 是本地处于等待模型/推理输出状态的持续时间。
- Tool duration 由 Core Tool Executor 提供。
- Turn duration 从 Turn started 到 terminal event。
- 工具事件提供结构化 display verb、target、outcome、summary、duration 和可折叠详情。
- UI 不从自然语言 summary 猜测“创建、编辑或执行”。
- 长工具输出默认折叠；展开状态只属于 TUI。
- 空闲 Composer 模式下使用 `Ctrl+O` 统一展开或折叠长工具详情；不为每个 Tool Block 创建独立键盘监听。

示例：

```text
● Write circle_area.py
  └ Created · 21 lines · 18ms

  Thought for 1.8s

● 已创建 `circle_area.py`。

✻ Worked for 2.4s
```

## 13. Slash Command

### 13.1 菜单行为

- `/` 打开候选。
- 输入继续筛选。
- 上下键循环选择。
- Tab 把选中命令补全到 Composer，不执行。
- Enter 执行选中项；无显式选择时执行精确解析结果。
- Esc 关闭菜单并保留 Composer 文本。
- 所有命令必须产生历史反馈、选择器、专用输入或明确错误，禁止静默成功。

### 13.2 关键命令

- `/help`：输出普通历史块，不再阻塞输入。
- `/usage`：输出当前上下文/usage 快照；没有数据也显示明确说明。
- `/new`：创建新 Thread，清空旧界面投影，重置 Thinking、pending interaction、临时权限和取消状态。
- `/auth`：管理 Provider 凭据。
- `/model`：选择 Provider/模型；缺少凭据时引导进入 Auth 专用输入。
- `/permissions`：切换 Request approval / Full access。
- `/status`：显示 Thread ID、Workspace、Provider、具体模型、fallback、Thinking、Memory、Permissions 和唯一版本号。
- 不恢复 `/editor` 或 `/details`。

## 14. Auth

`/auth` 是凭据管理入口，`/model` 是模型选择入口；两者职责不混合，但 `/model` 可在缺失凭据时引导 Auth。

流程：

```text
/auth
→ Provider Picker（显示 configured/missing）
→ Add / Replace / Delete
→ Secret Input（掩码）或 Delete Confirmation
→ 空值校验
→ Provider 验证
→ 保存或显示错误
→ 刷新 Application State
→ 写入普通历史反馈
→ 恢复 Composer
```

- API Key 只通过专用协议字段传输。
- 不进入 Transcript、事件、日志、错误详情或 Composer history。
- 保存到用户级 Awesome 配置，不写入 workspace。
- 环境变量优先于用户级配置；环境变量来源只能显示状态，不能由 Awesome 删除。
- 对环境变量来源执行 Replace/Delete 时必须明确提示其不可由应用修改。

## 15. 模型身份

Core 生成唯一 `ModelIdentitySnapshot`，至少包含：

- provider；
- configured model；
- effective model；
- runtime name；
- fallback 状态和来源。

Welcome、`/model`、`/status`、请求路由和系统上下文都使用该快照。Agent 的系统上下文明确当前真实身份，禁止让模型自行猜测。发生 fallback 后，先更新快照和事件，再渲染 UI 和后续自述。

## 16. Agent 最小行动原则

Agent Loop 的完成判断属于 Core，不属于 Middleware 或 TUI。

规则：

- 工具结果满足用户明确目标后，应进入最终回复而不是固定追加命令。
- 只有用户要求验证、验收标准要求验证，或没有验证就无法判断任务是否完成时，才运行额外命令。
- 写文件工具成功不自动触发 Shell。
- 每次工具结果必须完整反馈回 Agent Loop；缺失结果视为运行时错误，不能通过继续调用工具掩盖。
- iteration/tool/model budgets 是硬上限，不是继续执行的目标。

## 17. 模块职责调整

| 模块 | 目标职责 | 应删除的旧职责 |
| --- | --- | --- |
| `tui/src/app/` | 组合渲染、读取单一 UI 模式 | App 内多个互相独立的 modal state/ref 和输入阻塞判断 |
| `tui/src/state/` | Product projection + UI mode reducer | 用隐式条件组合推导焦点 |
| `tui/src/composer/` | 文本编辑和当前模式下的 Composer 行为 | 自行处理 Slash 菜单已拥有的方向键/Enter |
| `tui/src/components/` | 无业务权威的纯渲染组件 | 每个面板各自定义输入优先级和提交生命周期 |
| `tui/src/commands/` | 解析、菜单投影、命令意图发送 | 静态只读 CommandMenu 与另一套 Picker 状态并存 |
| `tui/src/lifecycle/` | 取消、退出、交互响应生命周期 | 与组件局部 responding ref 重复管理 Interaction |
| `tui/src/protocol/` | 新的严格结构化协议 | 自由文本 choice 和缺少展示事实的旧事件 schema |
| `application/` | Trust、Thread、命令、权限、Interaction 权威 | 在多个 command/tool path 中重复判断模式 |
| `core/tools/` | capability policy、执行、硬安全、审计 | 每个工具自行决定是否弹审批 |
| `agent/` | reasoning/tool loop、身份注入、停止条件 | 固定或无依据的额外验证行为 |

## 18. 无兼容迁移原则

这是开发阶段的架构修正，不保留旧用户数据或协议兼容性：

- 直接升级协议版本或同时更新 Python/TypeScript schema 与 fixtures。
- 不接受旧 interaction kind、旧 decision、旧 permission 字段或旧事件 payload。
- 不建立 v1/v2 双分支、字段 alias、fallback parser 或临时 adapter。
- 新 reducer 生效后删除旧 modal state、`commandInputBlocked` 类旁路和重复 `useInput`。
- 新命令菜单生效后删除只读菜单旧实现。
- 新 Interaction 生效后删除自由文本 choice 到 UI 的直接映射。
- 新 transcript 对账生效后删除依赖“最终 Thread 读取才显示用户消息”的逻辑。
- 删除绑定旧实现的测试，用新行为、协议和边界测试替代。

每个实施 PR 都必须列出“被替代并删除的旧逻辑”，不允许只增加新路径。

## 19. 实施顺序

### Phase 1：状态与协议基础

目标：建立单一 UI mode、键盘路由、结构化 Interaction/Permission/Identity/Event 契约。

原因：后续所有 UI 都依赖这些边界；先做样式会继续放大旧状态问题。

风险：Python/TypeScript 协议必须原子升级，旧 fixture 和旧字段必须同 PR 删除。

### Phase 2：Trust、Approval 与 Permission

目标：修复最危险的焦点和权限路径，完成 Workspace Trust、`/permissions`、Approval 和 Thread 级授权。

原因：这些状态必须优先于普通输入，并由 Core 权威决定。

风险：Run 可能因 Interaction 未 resolved 而卡住；需要协议和端到端验证。

### Phase 3：Composer、Slash Command、Auth 与 Thread 切换

目标：完成菜单键盘闭环、Auth 专用输入、命令反馈和 `/new` 清理。

原因：它们共享同一输入路由，应该在交互基础稳定后统一替换。

风险：Thread 切换和异步回调可能把旧结果写入新 Thread。

### Phase 4：Transcript、Markdown、工具时间线与身份

目标：即时用户消息、稳定 Markdown、Thinking/Tool/Turn 层级和模型身份一致性。

原因：需要新的结构化事件和稳定状态机作为前提。

风险：流式增量与持久化结果可能重复；必须通过 identity 对账。

### Phase 5：Agent 行为与视觉收口

目标：最小行动停止条件、Mint Welcome、统一主题、间距、折叠和完整恢复验证。

原因：先保证交互正确，再收口视觉和 Agent 行为。

风险：不能通过 Prompt 文案掩盖工具结果或停止条件的运行时错误。

## 20. 测试与验收

### 20.1 状态与键盘

- 每种 UI mode 只有一个输入监听者生效。
- 覆盖 Enter、Tab、Esc、上下方向键、Ctrl+C、Ctrl+D。
- Modal 提交中重复 Enter 不会发送两次响应。
- Overlay 关闭后 Composer 恢复输入。

### 20.2 Trust

- Trust 前不读取项目级配置、指令、Skills、MCP 或代码。
- Yes 持久化并进入 Welcome；No/Esc 退出。
- 同一路径跳过；不同路径重新询问。
- Trust 不会开启 Full access。
- 持久化失败不会进入 Agent。

### 20.3 Permission/Approval

- Request approval 下读取直接执行，write/edit/delete/execute 按策略询问。
- Thread 编辑授权只覆盖普通 write/edit。
- Full access 不产生 `ASK` Interaction，但硬 `DENY` 仍生效。
- `/new`、`/resume`、退出清除临时权限。
- Approval 响应传到 Core，resolved 后 Run 继续并恢复输入。

### 20.4 Slash/Auth

- `/`、筛选、方向键、Tab、Enter、Esc 完整工作。
- `/usage`、`/help` 和错误命令均有历史反馈。
- `/new` 不显示旧 Thread 消息，也不接收旧 Thread 异步结果。
- Auth 覆盖 add/replace/delete、掩码、空值、验证失败、取消和焦点恢复。
- Secret 不出现在事件、日志、Transcript 和测试快照。

### 20.5 Transcript/Streaming

- 用户消息在 RPC 返回前后均不会消失或重复。
- Assistant 流式输出稳定，完成后 Markdown 正确。
- Tool started/completed/failed/cancelled 顺序和耗时正确。
- Thinking duration 只来自本地测量。
- 取消 Run 后 terminal 状态完整，下一条消息可以正常提交。

### 20.6 Agent

- “只创建一个文件”在成功写入后停止，不自动执行 Shell。
- 明确要求测试时才执行相应验证。
- 工具结果缺失或不匹配时显式失败，不无限继续。

### 20.7 验证层级

每个阶段执行：

1. TypeScript format/lint/typecheck；
2. Python format/lint/typecheck；
3. 当前 reducer/policy/controller 的单元测试；
4. Python/TypeScript 协议 fixture 合约测试；
5. 受影响的 TUI 组件和交互测试；
6. stdio 产品流集成测试；
7. 针对 Trust、Approval、Auth、`/new`、取消恢复的 E2E。

重构阶段不要求修复与本次新架构无关的旧全量测试；被替代的旧测试直接删除。最终收口阶段再运行当前新架构的完整测试体系。

## 21. 完成定义

只有同时满足以下条件，重构才算完成：

- 目标状态机和协议是唯一运行路径。
- 没有多个组件竞争输入。
- Core 是 Trust、Permission、Approval、模型身份和 Thread 的唯一权威。
- 所有用户动作都有成功、失败、取消或进行中反馈。
- 新 Thread、取消、拒绝和错误后都能继续对话。
- 被替代的旧状态、协议、组件分支和测试已删除。
- 仓库中不存在兼容适配器、临时 Patch 或新旧实现并存。
