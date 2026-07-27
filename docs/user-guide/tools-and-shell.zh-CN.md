# 工具与 shell 执行

本页面向希望理解从模型工具请求到本地影响之间发生什么的用户。内容涵盖内置工具、验证、审批、shell 行为、超时、输出和审计边界。

## 一条执行路径

模型绝不会直接调用文件系统、shell、Memory 或 MCP。每个工具名都通过同一个 registry 和 executor 解析：

```text
模型或直接请求
        |
        v
 registry 查找 -> 参数验证 -> 路径/命令硬检查
        |
        v
 capability 策略 -> 可选审批 -> 有界 handler
        |
        v
 终态事件 + ToolActivity + 可选 ChangeSet
```

正因为共用这条路径，扩展无法仅靠公开一个新名称来绕过审批。`/tools` 会显示有效 registry、只读状态，以及每个工具当前是否需要审批。

## 内置工具类别

Awesome 初始注册：

- 发现：`ls`、`glob` 和 `grep`；
- 读取：`read_file`；
- 写入：`write_file` 和 `edit_file`；
- 删除：`delete`；
- 宿主执行：`execute`。

启用并正确配置 Web 后，同一 registry 还会包含 `web_search`。它对模型保持 provider-neutral，
经过相同的严格校验/policy/审批/审计路径，并标记为 non-replayable。

Memory 和 MCP 可以扩充整个 catalog。带 namespace 的扩展工具仍使用同一个 executor。精确参数 schema、限制和结果字段见[内置工具](../reference/built-in-tools.zh-CN.md)。

### 使用 Web search

设置 `TAVILY_API_KEY`，运行 `/web on`，随后请求当前信息。第一次 `network.read` 调用即使在
Full access 下也会 ASK；可选择默认 deny、allow once 或 allow for this Thread。
`/web status` 显示 readiness 与披露，`/web revoke` 清除当前 Thread grant，`/web off`
会从重建后的 runtime 中移除该工具。

Awesome 会依据 Tavily 的[隐私政策](https://www.tavily.com/privacy)与
[平台条款](https://www.tavily.com/terms)发送 query。它为严格 HTTPS result 分配 `S1...`
citation，并将其保留到最终回答、transcript、headless JSON 与 checkpoint。默认每 Turn
最多八次请求。精确参数见[内置工具契约](../reference/built-in-tools.zh-CN.md)。

## 模型驱动与 direct shell

希望模型选择命令时使用自然语言：

```text
运行覆盖 parser 变更的最小测试。
```

已经选定精确命令时使用 direct execution：

```text
! git diff --stat
```

两条路径都使用相同的 `execute` handler、命令策略、净化环境、process runner、超时、输出限制、脱敏和 Journal observation。direct execution 提供显式用户权限，因此不显示普通 shell 审批提示，但它不会绕过硬拒绝。

## Workspace 路径

内置文件工具接受相对于 Workspace 的路径，而不是任意绝对路径。Core 在跟随任何组件前验证平台特有语法，然后通过不跟随链接的操作绑定 Workspace 根、父目录和目标身份。

检查会拒绝 Workspace 逃逸、link 或 reparse 遍历、敏感 secret/key 路径、含糊的 Windows 拼写，以及存在多个 hard link 的普通文件。读取有大小限制，并重新检查内容和 metadata 是否稳定。支持时，写入通过同级原子替换完成。递归删除会先构建并验证完整 inventory，在删除任何内容前拒绝嵌套 symlink、junction 或 reparse 目录。

在受支持的 POSIX 路径上，可以删除最终 symlink 节点本身，因为 Core 只删除链接而不跟随目标。链接父目录、嵌套链接或 Windows 目录 reparse 目标仍会被拒绝。

这些控制防止内置路径解析沿别名访问外部目标。它们无法在每一种可移植边缘情况中阻止另一个同权限宿主进程竞争普通文件系统操作。面对恶意并发进程，请使用 OS 隔离或 mount boundary。

## 命令检查

审批前，Core 会评估命令文本、宿主 shell dialect、Workspace，以及请求的、位于规范 Workspace 根下的词法 working directory。随后 handler 会验证该目录存在、仍位于 Workspace 内、不是链接且身份稳定。紧邻进程启动前，同一纯策略会使用经过验证和解析的目录再次运行。评估器是共享的，但两个阶段刻意使用不同的路径证据；第二次检查保证解析或策略不安全时 runner 不会启动。

最终 OS spawn 仍接收 working-directory 路径名，而不是固定的目录 handle。同权限进程可以在第二次检查后、spawn 前替换它。这也是宿主执行仍不属于文件工具更强的身份固定边界的原因之一。

有界 parser 能够处理已知 CMD、POSIX shell 和 PowerShell wrapper；复合命令、pipeline 和换行；切换目录的 segment；encoded PowerShell；提权别名；以及选定的字面量 Python `-c` 进程/文件系统调用。它会规范化可执行路径、大小写和常见 Windows 可执行后缀。无法安全解析的形式会被拒绝。

parser 是识别意外操作的 circuit breaker，而不是程序行为证明。例如，它可以区分无害的 `python -c "print('rm -rf /')"` 与字面量破坏调用，却无法仅从任意下载程序的文件名理解其行为。

## 工作目录与环境

`execute` 工具默认使用 Workspace 根，也可以选择已存在的 Workspace 相对目录。symlink 工作目录或 Workspace 外部路径会被拒绝。

子进程继承环境前，会删除名称以 `_API_KEY`、`_TOKEN`、`_SECRET`、`PASSWORD` 等常见 secret 后缀结尾的变量。这可以减少意外凭据暴露，但并非完整的 secret scanner，也不会撤销可通过文件、agent 或 OS store 获得的凭据。

## 超时、取消与清理

`execute` 请求可选择一个不超过 600 秒的正数命令超时，默认值为 60 秒。Core 会额外给予工具一段有界清理预算，用于终止进程树和排空输出 pipe。其他工具使用普通外层工具 deadline。

超时时，process runner 会先尝试优雅清理，再强制清理进程树。Windows 上每个命令由 Job Object 管理；POSIX 上使用进程组。有意逃离受管理组的后代仍可能存活。继承输出 pipe 的后代无法让调用永久挂起；输出可能会改为标记为 truncated。

Ctrl+C 会在有界清理后保留取消作为终态。超时、取消、spawn 失败和 backend 失败各自产生一个终态 Tool event 和一个 ToolActivity。由于命令在观察到失败前可能已经执行，Change Journal 会在 runner 启动前记录脱敏的 execute 尝试。无效参数、权限拒绝和硬拒绝不会记录一次执行尝试。

## 输出与脱敏

标准输出与标准错误分别受到限制和脱敏，随后连同 exit code、duration、timeout 和 truncation metadata 一起呈现。非零 exit code 表示进程已经完成，但在展示层标记为失败；runner 失败或超时则是 Tool error。当前 Session 中可以用 Ctrl+O 展开细节，而恢复的 Thread 只保留持久且有界的摘要，不保留原始工具流。

不要把终端脱敏当成 secret 的唯一保护。首先就不要打印 secret，并且只通过遮罩的 `/auth` 或 `/model` 流程输入 Provider 凭据。

## 实用示例

只读并解释，不做更改：

```text
检查 @src/parser.py 并解释 invalid token 是如何报告的。不要编辑文件或运行命令。
```

允许普通编辑但仍要求审批命令：

```text
/permissions accept_edits
为空 token 场景添加回归测试，并实现最小修复。
```

直接运行已审查的命令：

```text
! git status --short
```

审查捕获到的文件影响：

```text
/diff
```

## 工具失败时

使用 `/tools` 确认名称和审批状态。路径失败通常意味着目标是绝对路径、位于 Workspace 外、敏感、经过别名，或在检查期间发生变化。命令拒绝表示有界策略无法确认形式可接受；请把它简化成显式且可检查的步骤。超时表示命令结果可能不完整，因此重试前应检查 Workspace 或外部目标。

按症状恢复见[故障排查](troubleshooting.zh-CN.md)，权限模型见[权限与安全](permissions.zh-CN.md)。
