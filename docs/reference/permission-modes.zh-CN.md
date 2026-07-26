# 权限模式参考

权限模式只回答一个狭窄问题：当经过验证的工具请求一项 capability 时，Core 可以自动继续，
还是必须询问用户？它不能代替 Workspace 信任、路径验证、shell circuit breaker、Change
Journal 捕获、超时或操作系统隔离。

## 精确矩阵

| 模式 | Workspace 读取 | 创建/修改 | 删除 | Shell | MCP/未知扩展 |
| --- | --- | --- | --- | --- | --- |
| Request approval | Allow | Ask | Ask | Ask | Ask |
| Accept edits | Allow | Allow | Ask | Ask | Ask |
| Full access | Allow | Allow | Allow | Allow | Ask |

该矩阵适用于已选中 Thread 权限会话中的 Agent 工具调用。直接 `! command` 是对该精确命令的
显式授权，并使用独立的 Direct Full-access 会话，因此不显示普通 shell prompt；所有 schema、
hard-deny、runner、审计、超时和取消控制仍然适用。本地 Memory 是下面说明的另一个策略例外。

对应的模式值是 `request_approval`、`accept_edits` 和 `full_access`。`/permissions` 打开模式
picker；`/permissions <value>` 直接请求一种模式。

读取权限假定 Workspace 已经通过启动信任边界。Workspace 信任决定是否能加载由仓库控制的
配置、指令和扩展；权限模式决定激活后的一项 capability。

## 为什么有三种模式

**Request approval** 最大化可见性。每项内置修改和 shell 执行都会暂停并等待决定。它是会话
默认模式，也是切换 Thread 时恢复的模式。

**Accept edits** 免去普通创建和精确文件修改的重复 prompt，这是 coding agent 最常见的工作
负载。它刻意将删除与 shell 分开：覆盖已知文件可以通过 Change Journal 恢复，而递归删除或
任意进程具有更广的故障面。

**Full access** 免除内置本地 capability 的逐次调用 prompt。它适合受信任仓库和有人监督的
长任务，但并不等于“允许一切”。MCP 和未来未知扩展 capability 仍然逐次询问，因为 Core
无法推断它们的外部权限或幂等性。

这种非对称设计遵循最小权限原则：只有在 Awesome 知道 capability 语义的地方才授予便利。

## 评估顺序

工具安全分布在准入与具体 handler 中。请求审批前，Core 会执行：

1. registry lookup 和已注册的 Pydantic/schema 验证；
2. 内置路径工具的词法路径语法检查；
3. 针对请求的词法 working directory 执行 shell circuit breaker；
4. capability policy：先检查有效 temporary grant，再检查当前模式矩阵；
5. 结果为 `ask` 时，创建一个与 Tool call 绑定的 interaction。

准入后、产生效果前，handler 会应用特定后端的检查。Filesystem handler 解析包含关系、
link/reparse 状态、对象身份、敏感名称和受保护删除目标。`execute` 解析其实际 working
directory，并在 spawn 前再次运行相同命令策略。Memory 修改检查 workspace/Agent/Turn
权限、内容策略和 compare-and-swap 状态。MCP 参数已经过 schema 验证，并且远程 I/O 前
仍会执行 generation 检查。

审批不会改变任何 handler 检查。Full access 可以绕过 prompt；它不能让格式错误的请求、
受保护 filesystem 操作、陈旧 generation 或 circuit-breaker denial 变为有效。

```text
Capability request
       |
       v
  lexical hard deny? - yes --------> DENY
       |
       no
       v
known built-in? ----- no ---------> ASK ONCE
       |
       yes
       v
valid temporary grant? -- yes ----> ALLOW
       |
       no
       v
apply mode matrix ----------------> ALLOW or ASK ONCE
       |
       v
handler safety checks ------------> EFFECT or DENY
```

## 审批选择与 temporary grant

普通审批会绑定到 pending Tool call 的 Thread、Turn、operation 和 interaction generation。
可选项是：

- **Yes**（`allow_once`）：只授权本次调用；
- **Yes, allow all edits during this session**（`allow_thread_writes`）：只对
  `workspace.write` 显示；授权当前所选 Thread 后续的创建/修改；
- **No**（`deny`）：拒绝调用。

“all edits”标签不包括 delete、shell、MCP 或未知 capability。Core 会把任何尝试将该决定
用于非 write capability 的行为作为不变量违规而拒绝。

Temporary capability grant 是内存中的会话状态。每次模式变化——包括通过一次新转换，从一种
模式回到概念上相同的权限——都会清除 grant 集合，并递增 permission generation。选择、创建
或恢复另一个 Thread 会将模式重置为 Request approval 并清除 grant。任何内容都不会作为
永久规则持久化。

## Full access 确认

Full access 是两步提权：

1. `/permissions full_access` 要求已经选择 Thread，并创建绑定到
   `thread_id + permission generation` 的 pending confirmation；
2. 单独的 interaction 首先提供 **Keep current permission mode**，其次提供
   **Enable Full access for this thread**。

安全的“keep”选择位于第一项/默认项。确认 pending 期间，新 Turn、直接命令、`/new`、
`/resume`、其他状态修改或另一个外部 Operation 都不能穿过前台 gate。私有 Core 边界上的
Snapshot 命令与取消除外。当前 Ink TUI 不会通过活动 interaction 提交新输入的 snapshot；
它会把后续输入排队，直到 interaction/Operation 得到解决。

Thread 选择或任何权限模式转换都会使旧确认失效。响应时，Core 会在持有 resolving foreground
lease 的同时重新检查 interaction ID、选中的 Thread 和 permission generation；只有这之后
才会发出 resolution 并应用模式。因此，来自旧 Thread 的延迟响应无法提升新 Thread 的权限。

Full access 只在当前 Core 会话内对选中的 Thread 持续有效。它不会写入 User 配置或对话状态。

## 不可关闭的边界

权限模式不能授权：

- 逃逸 Workspace 或跟随 symlink/reparse point 的结构化 filesystem-tool 路径（不跟随、
  只删除最终链接节点是这个规则的狭窄例外）；
- 内置 filesystem-tool 对受保护秘密路径的访问；
- filesystem-tool 删除 Workspace root、filesystem root、`.git` 或受保护敏感内容；
- 被有界 command circuit breaker 拒绝的 shell 命令，包括可识别的 root/workspace-root
  破坏性删除、提权、shutdown、disk formatting、裸 block-device 写入或 fork bomb；
- 无效的 MCP schema/catalog 或陈旧的 MCP catalog generation；
- 陈旧的 interaction、operation、Thread、Turn 或 permission generation。

这些是产品不变量，而不是用户偏好。Shell policy 明确只是防止常见灾难性事故的 circuit
breaker，不是完整恶意代码分类器。Full access 仍然使用 Awesome 进程的 host account 运行
命令。需要更强 containment 时，请使用 VM、container、操作系统沙箱或一次性 checkout。

Filesystem-tool 的 secret/path 边界不会 sandbox `execute`。被允许的 shell 可以用 host-account
权限点名敏感文件或 Workspace 外文件；经过净化的子进程环境不是 filesystem access control。

## MCP 与未知扩展

MCP 工具使用内置 `workspace.read`/`workspace.write`/`workspace.delete`/`shell.execute`
集合之外的 capability。三种模式下，策略都为每次这类调用返回 `ask`。审批只授权一次调用；
不存在 Thread 级 MCP grant。

即使工具宣称只读，这一点也很重要。MCP 服务器是外部进程，它的 description 不是可强制
执行的无副作用证明。Dispatch 后发生传输中断时，Awesome 会返回 `uncertain_outcome`，
使 catalog 失效，并且不重放调用。

本地 Memory capability 是另一个有意的例外。`memory.read` 和 `memory.write` 在 Workspace
矩阵之外被允许，并且不会 prompt。修改型工具 description 对模型施加“当前用户明确请求”规则；
运行时本身不做语义意图分类，而是强制检查活动 Agent Turn、匹配的 Workspace、内容、作用域、
脱敏和 compare-and-swap。见 [Memory](../extensions/memory.zh-CN.md)。

## 并发与生命周期

权限变化是前台状态修改。存在活动 Operation 或无关 interaction pending 时，它们会被拒绝。
Tool approval 是现有 Operation 的 continuation，因此只有在所有权限字段匹配后，才会绕过
普通 exclusive gate；否则 Operation 会在等待自己时死锁。

Shutdown 会停止新 lease、取消并等待活动 Operation/mutation，然后关闭 MCP、database 和
进程资源。它不会为下次启动持久化更宽松的模式。
