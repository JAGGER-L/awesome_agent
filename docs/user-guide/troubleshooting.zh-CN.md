# 故障排查

本页从可见症状出发，引导你采取范围最小的安全恢复操作。在诊断确认相关之前，不要删除状态、绕过校验和检查或提高权限。

## 首先：记录稳定事实

TUI 可用时，收集：

```text
/status
/config
/doctor
```

同时记录 `awesome --version`、主机/架构、Workspace 路径、失败的精确命令或 interaction，以及界面显示的诊断码。Tool 失败时用 Ctrl+O 展开有界细节。分享前请脱敏项目私有路径和输出；绝不要包含 API Key 或 secret 值。

使用这条决策路径：

```text
无法启动？ ----------> 安装/运行时
启动过早停止？ ------> 信任/配置/状态/协议
Turn 无法开始？ -----> 模型/凭据/busy interaction
Tool 无法执行？ -----> 参数/路径/权限/硬拒绝
操作停止？ ----------> 取消/超时/不确定结果
结果无法恢复？ ------> ChangeSet 冲突或不可逆
```

## 安装与启动

### 找不到 `awesome`

安装后请打开新终端。macOS 或 WSL2 上确认 `~/.local/bin` 位于 `PATH`；Windows 上确认 `%LOCALAPPDATA%\Programs\Awesome\bin` 位于用户 `PATH`。如果启动器不存在，请关闭所有 Awesome 进程并重新运行受支持的安装器。

不要从语言包管理器安装名称相近的随机 package。受支持发布版包含相互匹配的 TUI、Core、私有运行时和协议版本。

### 安装器拒绝主机

发布版安装器支持 Windows 11 x64、Apple Silicon macOS 和 WSL2 Ubuntu 24.04 x64。请确认架构；对于 WSL，还需同时确认 WSL2 和 Ubuntu 版本。安装器会在其他主机上失败关闭，因为经过验证的运行时制品是发布契约的一部分。参见[安装](../getting-started/installation.zh-CN.md)。

### 下载或校验和验证失败

不要绕过校验和。确认可以访问 GitHub release assets、releases.astral.sh 和 nodejs.org，检查企业代理是否改写下载，然后重试。持续不匹配可能是发布损坏或不完整，报告时应包含 asset 名称和精确错误。

### Awesome 要求交互式终端

Ink UI 需要 TTY 输入和输出。请直接在终端中启动，不要通过非交互式 pipe 或重定向的标准输出启动。自动化请使用普通 shell 工具；Awesome 当前不提供 TUI batch interface。

### Core 无法启动，或协议/版本握手失败

关闭所有 Awesome 进程并重新运行原始单行安装器。来自不同发布版的 TUI 与 Core 混用会被明确拒绝；相同的产品版本字符串不能代替私有协议版本。重新安装只会在暂存的发布版通过自身版本检查后替换应用。

## Workspace 启动

### 显示了错误的 Workspace

退出，切换到预期的现有项目目录，然后重新启动 `awesome`。当前目录就是 Workspace；已安装发布版没有用于更改它的公开启动 flag。接受信任前，请在信任提示中核对显示的启动路径。Core 会在内部规范化路径。

### Workspace 信任反复出现

选择 No 有意不保存拒绝结果。如果之前选择过 Yes，可能是规范目标发生变化，或本地信任状态被重置；解析到同一规范目录的别名共享信任记录。在活动 Session 中替换物理根目录，会使该 Session 的身份检查失效。请确认显示的路径和目标，不要仅因替代目标的文本看似相同就信任它。

### 启动提示 Workspace 正在使用

另一个 Awesome 进程可能通过某个别名持有规范路径或文件系统身份 lease。关闭其他 Session 后重试。只要所有者可能仍存活，就不要复制或删除 lock/state 文件。

### `AGENTS.md` 被忽略

请在 Welcome、状态栏或 `/doctor` 中阅读完整原因。根文件必须是稳定的普通 UTF-8 文件，不含 NUL 字节、link、junction、reparse 组件，也不能在读取期间被替换。它必须满足 32 KiB 字节限制，以及 8,192 Tokens 与有效输入预算 10% 中的较小值。

Awesome 会整份忽略无效文件。修复后启动新 Session；当前 Session 会保留不可变快照。缺少 `AGENTS.md` 是正常情况，不会产生警告。

## 配置与模型

### 配置无效

检查 `<AWESOME_HOME>/config.yaml`，以及完成信任后的 `<workspace>/.awesome/config.yaml`。每个文件都必须是带 `version: 1` 的 YAML mapping。重复 key、未知 key、不受支持的模型 ID、无效名称和超范围预算都会产生错误。

手动编辑时，可暂时把文档缩减为 `version: 1`，以定位无效的可选 section；但请先在仓库外保存备份。不要把凭据移入 Workspace 配置。手动修复后重启 Awesome 并运行 `/config`。

### 未配置模型

在设置提示上按 Enter，或运行 `/model`。选择 DeepSeek 或 Kimi，通过遮罩输入输入密钥，再选择模型。如果两个 Provider 都不可用且没有有效的模型默认值，Turn 无法启动，因为 Core 不会凭空推断 Provider。

### 凭据显示为 Unavailable

运行 `/auth <service>`。选中的 Environment 与 Awesome-managed 来源彼此独立。恢复所选来源，或显式选择另一个来源。当所选来源消失时，Awesome 不会静默 fallback。

Environment 值从启动 Awesome 的进程中捕获；请更改父 shell 并重启。Awesome-managed 值可以在 `/auth` 中添加、替换或删除。

### 密钥无效或未经验证

已知无效的 DeepSeek 或 Kimi 密钥不会保存。确认 Provider、区域、密钥状态和网络。网络/Provider 失败会提供显式 Save anyway 路径并把结果标记为 unverified；这不能证明密钥可用。之后运行 `/doctor` 进行按需验证。

删除本地密钥不会在 Provider 侧吊销它。凭据泄露后应在 Provider 自己的控制台中吊销。

### 有效预算低于配置值

所选模型的上下文限制可以限制上下文总量。受信 Workspace 也能降低但不能提高用户 Turn 预算。使用 `/context` 检查有效输入上限，使用 `/usage` 检查消耗，并同时比较用户配置和 Workspace 配置。

## Busy、Pending 与取消

### 输入显示为 pending

Awesome 每次只运行一个前台 Operation。TUI 最多排队三个后续消息、slash command 或 direct command，并按提交顺序执行。可以等待、按 Ctrl+C 取消活动 Operation，或者在 Composer 为空时按 Up，把最新待处理项召回 draft。

队列已满或 `/quit` 已在队列中时，新增文本会保留在 Composer，并显示未接受原因。队列仅当前 Session 有效，退出后不会恢复。

### `operation_busy`

某个 Turn、direct command、状态变更命令、interaction resolution 或 shutdown 已拥有可变前台。不要反复提交同一个 mutation。等待或取消可见所有者。普通 Operation 期间 Core 允许一小组快照命令，但当前 TUI 会把包括 `/status` 和 `/usage` 在内的所有后续输入排队，因此它们的结果只会在活动 Operation 完成后出现。

### `interaction_busy`

存在尚未解决的 Trust、Approval、Full access、state-reset 或 recovery prompt。回到提示并接受或拒绝。在该交互得到解决前，系统会有意禁止启动新 Thread 或更改权限。

### Ctrl+C 没有立即停止

取消包括有界的 handler 与进程树清理。等到 terminal cancelled 或 failed event 后再开始替代工作。命令或 MCP 服务可能已经执行；取消不能撤销外部副作用。

## 工具与 Shell

### 文件路径被拒绝

内置工具要求安全的 Workspace 相对路径。检查是否存在绝对路径、`..` 逃逸、symlink/junction/reparse 组件、敏感 secret/key 路径、多个 hard link、含糊的 Windows 语法，或检查期间发生变化的目标。请把预期工作移入普通 Workspace 路径，而不是要求 Full access；权限模式不会禁用路径安全。

### 删除在任何变更发生前失败

递归删除会先 inventory 整棵树，并拒绝任何嵌套 symlink、junction 或 reparse 目录。只有在验证目标后，才手动删除别名；也可以逐个删除普通子路径。预检失败的设计目标就是让 Workspace 和外部目标都保持不变。

### 命令被硬拒绝

把 wrapper、substitution 或 compound syntax 简化为显式、可检查的命令。硬拒绝在所有权限模式和 `!` 命令中都保持生效。Full access 不能覆盖已识别的提权、关机、磁盘、块设备、fork-bomb，或递归删除根/Workspace 根的规则。

如果安全命令确实无法表达为可检查形式，请自行在适当隔离的 shell 中运行。不要编码或混淆命令来绕过 circuit breaker。

### 命令超时

展开 Tool 细节，检查 exit/timeout/truncation metadata。Core 会尝试有界进程树清理，但 daemonized child 或外部副作用可能仍然存在。重试前检查 Workspace、进程列表和外部目标。只有已知命令正在取得进展且重复执行安全时，才适合使用更长超时。

### 输出被截断或脱敏

Tool 输出受到限制，以保护终端和上下文。保持 pipe 打开的后代也可能导致 drain truncation。在合适情况下，将大型非 secret 制品重定向到经过审查的 Workspace 文件，再读取相关部分。`[REDACTED]` 表示匹配了一个安全 pattern；它不保证检测到所有 secret 形式。

## 变更与恢复

### shell 命令之后 `/diff` 为空

Change Journal 为内置文件 mutation 创建快照。shell 执行会记录一条保守尝试，但不会推断任意文件系统 delta。请检查命令自身输出和版本控制状态等普通项目工具。

### `/undo` 报告 `workspace_conflict`

至少一个当前路径不再匹配 ChangeSet 预期的 applied 状态。不会恢复任何路径。保留当前文件，比较 `/diff`，并在新 Turn 中整合所需的反向变更。不要为了让旧 undo 通过而覆盖之后的用户或进程变更。

### ChangeSet 不可逆

仅包含 execute 的 ChangeSet 没有捕获文件前后状态。混合集合只恢复内置文件变更，并警告仍存在不受管理的 execute 影响。请手动验证 shell 和 MCP 目标。

### Awesome 询问是否恢复未完成 Turn

对于已验证的本地 checkpoint，Retry 排在第一位，并从冻结上下文继续。当 shell 或 MCP 调用可能已经执行时，Abort 排在第一位；Retry 可能重复剩余外部工作。决定前检查目标。Awesome 绝不会透明重放结果不确定的调用。

### Awesome 要求重置本地状态

检查确认面板。**Reset local state and continue** 会删除本地会话、Workspace 信任、checkpoint 和 undo 历史，同时保留 API Key、配置、Skills 以及 Local/Cloud Memory 设置。选择 Exit 或按 Escape 可在不更改状态的情况下离开。

如果另一个 Session 正在使用该状态，请关闭后重试。由更新版 Awesome 创建的状态要求升级，旧二进制绝不会重置它。未知、损坏、不可读或锁定的状态会产生诊断，而不会显示破坏性提示。不要手动删除数据目录。

## 扩展

### Skill 缺失或无效

运行 `/skills` 并检查来源和诊断。检查 package 名称、disabled list、有界 UTF-8 `SKILL.md`，并检查 Workspace Skills 中 `.awesome/skills/<package>` 的每个路径组件和 resource。修复 discovery-time 问题后重启。一个损坏 package 不应隐藏其他有效 Skills。

### Local Memory 被禁用、无效或发生冲突

运行 `/memory`，再检查所请求的 user 或 Workspace scope。Memory 默认关闭，显式 mutation 使用内容 hash 防止丢失更新。解决并发编辑后，针对新的 list 重试，而不要盲目替换文件。

如果已启用的 `USER.md` 或 `MEMORY.md` 无效或不可读，Turn 上下文准备会失败，而不是省略该 scope。当前的一个清理缺口可能在 Operation 报告失败后，让已经创建的 Turn 仍停留在 `in_progress`。修复 managed marker/UTF-8/size 问题或禁用 Local Memory，然后重启，让启动恢复协调 Turn。

### Mem0 Cloud 不可用

确认 `/auth mem0`、所选凭据可用性和 `/memory mem0 on`。网络或 SDK 失败会使 cloud Memory 降级，但不会禁用本地 Agent loop。精确诊断会区分初始化、搜索、写入和删除失败。

### MCP 断开或处于错误状态

运行：

```text
/mcp
/mcp status <id>
```

确认配置的命令、参数、allowlist 中的环境变量名、可执行文件可用性和 server schema。`/mcp restart <id>` 会先删除旧 namespace，再重新连接。Manager 只发布完整 catalog，Registry replacement 也只发布完整 namespace；它们是两个原子步骤，而不是一个事务。如果 Manager status 为 `connected`，但 `/tools` 或 Turn 准备失败，还应检查完整的 `mcp.<server>.<tool>` 名称是否超过 128 字符的 `/tools` 契约。当前 compiler 不会拒绝这一 downstream mismatch。

MCP 超时或连接丢失后，server 可能已经执行。Awesome 会使 catalog 失效并报告不可重试的不确定结果，而不会在同一 Turn 中重连和重放。

## 报告可复现问题

请包含：

- Awesome 版本和受支持的主机/架构；
- 失败发生在 Workspace 信任之前还是之后；
- 精确诊断码和经过脱敏的消息；
- 能够复现问题的最小命令或请求；
- 相关权限模式和扩展状态；
- 是否发生取消、超时或外部副作用；
- 问题能否在新 Thread 和新 Session 中复现。

不要包含 API Key、secret 文件、原始私有 prompt、完整 Tool 输出或整个状态数据库。更深入的行为见[核心概念](../concepts/README.zh-CN.md)和[参考手册](../reference/README.zh-CN.md)。
