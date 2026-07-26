# 权限与安全

本页面向需要决定 Awesome 在受信项目中应要求多少确认的用户。它解释每项控制保护什么、权限何时过期，以及哪些风险仍在产品边界之外。

## 从威胁出发，而不是从开关出发

四项控制分别回答四个问题：

```text
Workspace 信任    是否允许加载由项目控制的内容？
权限模式           这个受信操作是否必须询问用户？
硬拒绝             这个已识别操作是否始终过于危险？
外部隔离           进程究竟可以访问宿主机上的哪些内容？
```

Awesome 实现前三项，目前不提供第四项。因此，权限提示和命令分析用于防止失误与意外授权，而不是针对恶意代码的遏制边界。

## Workspace 信任

Awesome 会先请求信任，之后才启用 Workspace 配置、根指令、Skills、MCP 声明或普通工具。请确认显示的启动路径。Core 会规范化该路径，并以规范 Workspace 键存储信任，因此解析到同一规范目录的两个别名共享同一条持久信任记录。选择拒绝会退出且不会保存拒绝结果，所以下次启动仍会询问。

实时进程会另外记录物理根身份、取得路径/实体 lease，并检查根目录是否被替换。该身份属于 Session 安全状态，不是持久信任键。发生替换会使活动 Session 失效，但不会自动产生新的持久信任决定。信任一个规范 Workspace 不代表信任另一个，也不会让内置文件工具访问其外部路径。

## 权限模式

运行 `/permissions` 检查或选择活动模式。三种模式是：

- **Request approval**：允许读取；内置创建、编辑、删除和 shell 执行前询问。这是默认模式。
- **Accept edits**：还允许普通内置文件创建和修改；删除和 shell 命令仍会询问。
- **Full access**：明确确认警告后，允许已知的内置本地写入、删除和 shell 执行。

MCP 和未知扩展能力在所有模式中都逐次询问。内置 Memory 操作遵循自己的显式启用和 mutation 策略。权威能力矩阵见[权限模式](../reference/permission-modes.zh-CN.md)。

## 选择模式

探索陌生代码库、审查风险较高的变更或了解任务需要哪些工具时，使用 Request approval。普通实现工作中，如果不希望确认每次编辑，但仍希望检查每次删除和命令，请使用 Accept edits。仅当你充分了解某个 Thread 的目标和宿主环境，能够接受无需提示的本地执行时，才使用 Full access。

渐进示例：

```text
/permissions request_approval
```

检查计划和第一个拟议变更，然后允许普通编辑：

```text
/permissions accept_edits
```

开始无关工作前回到保守基线：

```text
/permissions request_approval
```

切换模式会清除临时 capability grant，并推进 permission generation。选择其他 Thread 也会把权限 Session 重置为 Request approval。

## 审批语义

写入审批可能提供：

- **Yes**：仅允许本次调用；
- **Yes, allow all edits during this session**：允许选中 Thread/Session 中之后的 `workspace.write` 调用；
- **No**：拒绝本次调用。

临时写入 grant 绝不包含删除或 shell 执行。删除、shell、MCP 和未知能力只提供单次决策。Escape 会拒绝当前审批。

每个工具审批都与其 Thread、Turn、Operation 和 interaction 身份绑定。响应最多接受一次，并且只在这些事实仍是当前值时接受。过期的 UI 响应无法授权替代它的新 Turn。

## Full Access 确认

输入 `/permissions full_access` 不会立即切换模式。必须已经选中 Thread，不能有其他活动 Operation 或 interaction，而且警告提示默认选择 **Keep current permission mode**。

确认会绑定选中的 Thread 和当前 permission generation。切换 Thread 或模式会使旧提示失效。Full access 是 Session 权限，不是永久仓库规则，并且仍然无法绕过：

- 内置文件工具的敏感路径、Workspace 逃逸、link、reparse、hard link 和身份检查；
- 已识别的命令硬拒绝；
- MCP 和未知扩展审批；
- 工具参数验证和资源限制。

## 直接 `!` 命令

`! command` 表示用户已经显式选择该命令，因此 Core 会使用 direct authority 运行 `execute` 工具，而不是请求 shell 审批。该行为与所选权限模式无关。命令仍会在本应审批之前和进程启动前经过相同的硬拒绝评估，并应用相同的环境过滤、超时、进程清理、脱敏和 Journal observation。

direct command 只应用于你能够自行审查的命令：

```text
! git status --short
! uv run pytest tests/unit/example.py -q
```

不要把 `!` 当作逃避理解生成命令的方式。如果命令影响不清楚，请先要求 Awesome 解释。

## 硬拒绝

命令 circuit breaker 会在所有模式和 direct command 中拒绝可识别的灾难性操作。其范围包括提权、关机/重启、磁盘格式化或块设备覆写、fork bomb，以及递归删除文件系统根或 Workspace 根。它会在有界范围内解析 CMD、POSIX shell 和 PowerShell 形式，包括部分 wrapper、复合命令、encoded PowerShell 和字面量 Python 命令调用。

无法安全检查的输入会被拒绝。这一策略有意采取保守行为，但它不是通用恶意代码检测器：足够间接的程序可能执行命令字符串中不可见的影响。

## 权限不能保护什么

宿主进程一旦获准运行，就可能使用当前用户拥有的网络、凭据和文件系统权限。环境过滤会从 `execute` 中删除以常见 secret 后缀结尾的变量，但程序可能通过其他宿主渠道发现数据。进程树清理会限制孤儿进程，但不是 containment。连接超时后，外部服务仍可能完成操作。

仓库、命令、依赖或 MCP server 不受信时，请使用 VM、容器、受限 OS 账户、mount boundary 或托管沙箱。只向该环境提供任务所必需的凭据和路径。

## 提示看起来不正确时

选择 No 或按 Escape，然后检查 `/status`、`/tools` 和原始请求。待处理 interaction 会阻止新 mutation，直到得到解决。如果目标或 capability 出乎预期，应把它视为任务不匹配，而不是提高权限模式。

接着阅读[工具与 shell 执行](tools-and-shell.zh-CN.md)和聚焦的[安全架构](../architecture/README.zh-CN.md)。精确策略结果见[权限参考](../reference/permission-modes.zh-CN.md)。
