# 用户指南

本指南适用于第一次成功会话之后的日常使用。内容围绕用户需要做出的决策组织：如何表达任务、何时授予权限、如何检查工作，以及 Operation 停止时如何恢复。

## 一套可靠的工作循环

```text
定位 -> 请求 -> 观察 -> 必要时审批 -> 审查 -> 验证 -> 继续
```

### 1. 定位

从项目根目录启动，并检查当前 Session：

```text
/workspace
/status
/permissions
/tools
```

任务依赖之前的对话时使用 `/resume`。如果新问题会继承无关历史，则使用 `/new`。

### 2. 让请求可测试

说明预期结果、约束和完成证据。例如：

```text
修复支付 worker 中重复重试的问题。保持公共 API 不变，为并发失败添加回归测试，
并且只运行受影响的测试。
```

当某个范围较窄的文件是权威入口时，用 `@path` 引用它。除非具体步骤本身就是需求，否则不要规定每一个实现步骤；Agent 需要空间检查当前架构。

### 3. 观察并审慎审批

Tool activity 会显示 Awesome 正在读取、更改或运行什么。在 Request approval 模式下，将每个提示中的操作和目标与你声明的目标进行比较。“Allow all edits during this session”仅适用于普通 Workspace 写入，不包含删除、shell 命令、MCP 或未来 Session。

如果你已经选定精确的 shell 命令，`! command` 会通过 Core 直接运行它，而不要求模型规划。该直接形式代表显式用户权限：它会跳过普通 shell 审批，但命令硬拒绝、超时、清理、脱敏和审计仍然适用。

### 4. 审查产物

发生文件变更后：

```text
/diff
/status
```

阅读助手的验证说明，区分实际运行的测试和仅被建议的测试。`/diff` 覆盖已记录的内置文件变更；它无法总结 shell 命令或外部 MCP 服务产生的任意影响。

### 5. 继续或恢复

后续问题依赖当前上下文时，在同一 Thread 中提出。无关工作请新建 Thread。只有在阅读 ChangeSet 并理解其可逆性后才使用 `/undo`。外部操作结果不确定时，先检查目标系统再选择 Retry。

## 常用工作流

### 探索仓库

```text
从 CLI 到持久化梳理请求路径。引用主要模块，不要修改文件。
```

受信 Workspace 中的内置读取会自动获准；MCP 和未知扩展能力仍会询问。`/context` 显示响应所使用的来源。

### 实现聚焦的变更

```text
为导入请求增加超时。保留现有错误类型，先添加一个失败的回归测试，
并在修复后运行该测试。
```

如果希望普通创建和修改无需重复提示，但仍要求审批删除和 shell 执行，请使用 Accept edits。

### 运行已知命令

```text
! git status --short
```

对已经审查过的命令使用 direct command。当你希望模型判断什么检查最相关时，使用自然语言。

### 比较或恢复变更

```text
/diff
/undo
/redo
```

冲突检查会阻止旧 ChangeSet 覆盖后续编辑。参见[审查、撤销与重做](changes.zh-CN.md)。

## 选择正确的页面

- [命令与交互](commands.zh-CN.md)：启动形式、slash command、键盘行为、排队和 direct shell。
- [权限与安全](permissions.zh-CN.md)：信任、审批模式、硬拒绝，以及不提供 OS 沙箱这一边界。
- [工具与 shell 执行](tools-and-shell.zh-CN.md)：工具如何选择、验证、执行、超时和呈现。
- [审查、撤销与重做](changes.zh-CN.md)：ChangeSet、冲突、可逆性和崩溃恢复。
- [配置与凭据](configuration.zh-CN.md)：模型设置、凭据来源、用户设置和受信 Workspace 限制。
- [故障排查](troubleshooting.zh-CN.md)：按症状诊断并安全恢复。

扩展有各自的指南：[Memory](../extensions/memory.zh-CN.md)、[Skills](../extensions/skills.zh-CN.md) 和 [MCP](../extensions/mcp.zh-CN.md)。精确语法、schema 和限制见[参考手册](../reference/README.zh-CN.md)。
