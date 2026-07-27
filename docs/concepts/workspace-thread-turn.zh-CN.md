# Workspace、Thread、Turn 与 Operation

本页面向需要理解身份、并发、取消和持久化的用户与贡献者。核心规则是：项目、会话、请求和实时执行是四种不同的事物。

## Workspace：项目边界

启动 `awesome` 时所在的目录就是 Workspace。Core 将其解析为规范路径，并记录文件系统身份。信任与该规范 Workspace 关联；同时，实时 Session 还持有基于路径和身份的 lease，防止两个 Awesome 进程通过不同别名同时恢复或修改同一个项目。

只有先完成信任，才会启用项目控制的输入。持久信任键与实时身份/lease 检查有意彼此分离：

```text
解析候选项 -> 身份/状态预检 -> 请求信任
                               |
                  否：退出 <---+
                               |
                  是：取得路径/实体 lease
                               |
                               v
                      重新验证身份 -> 启用受信输入
```

受信 Workspace 可以影响上下文和声明扩展。它不会让内置文件工具访问任意宿主机路径、禁用权限检查或创建 OS 沙箱。如果绑定的根目录在 Awesome 运行时被替换，后续身份检查会失败，而不是悄悄跟随新目录。

## Session：运行时权限边界

一个 Session 由一个 TUI 及其私有 Python Core 进程组成。它拥有：

- 活动 Workspace 和运行时 lease；
- 一份不可变的根 `AGENTS.md` 快照；
- 选中的 Thread；
- 前台 arbiter 和任何待处理 interaction；
- 当前权限模式和临时 capability grant；
- TUI 中最多容纳三个后续输入、且仅当前 Session 有效的队列。

关闭 Awesome 会终止这些内存中的权限。持久会话和 Journal 数据仍可供之后的 Session 使用。

## Thread：会话边界

Thread 是绑定到一个 Workspace 的持久会话。其中包含有序的用户、助手和 direct-command 条目，Turn、用量、摘要以及 Tool activity。它还保存影响未来 Turn 的模型、Thinking 模式和 Skill 模式等选项。

可使用：

```text
/new
/resume
/resume <thread_id>
/fork [turn_id]
/retry [turn_id]
/rename Dependency graph review
```

`/new` 创建一个干净的 Thread，但不会删除之前的 Thread。`/resume` 只提供当前 Workspace 中的 Thread。选择或切换 Thread 会清除临时权限 grant，并把权限 Session 重置为 Request approval。旧的 Full access 确认不能应用到新选中的 Thread。

新 Thread 接受第一条自然语言请求时，会获得自动生成且长度受限的标题。`/rename` 会把标题标记为用户选择。标题用于整理会话历史，不影响 Workspace 身份或模型上下文。

### 物化的 Thread 分支

Fork 与 retry 会从当前 Thread 的一个终态 Turn 创建独立 Thread record。`/fork` 包含目标
Turn；`/retry` 包含目标之前的前缀，再为同一请求创建全新的 user entry 和进行中 Turn。
省略 ID 时选择最近的终态 Turn；进行中的 Turn 不能作为物化目标。

这是使用全新 Thread、entry、Turn、checkpoint-key 和 client-message identity 的物理前缀
复制。目标只保存直接父级 lineage——`fork` 或 `retry`、来源 Thread ID 与来源 Turn ID——
不会与来源共享历史 DAG。Summary、checkpoint、Tool activity 和 ChangeSet record 均不复制。
Retry 会把目标 Turn 的 Provider、模型、Thinking、Skill 与预算冻结到新 Turn，并经过普通路径
执行；旧工具调用不会重放，先前副作用也不会被撤销。

## Turn：持久请求边界

Turn 表示一个自然语言请求。模型执行前，Core 会持久化用户条目，并冻结解释或恢复该请求所需的事实：Provider/模型、Thinking 与 Skill 选项、预算、上下文 manifest 和 checkpoint 身份。

每个 Turn 恰好有一个终态：

- `completed`：存在一条持久的助手条目；
- `failed`：错误码说明工作停止的原因；
- `cancelled`：用户在正常完成前取消。

`in_progress` 仅在执行仍存活或等待启动恢复时有效。直接执行的 `! command` 刻意不属于 Turn：它在显式用户权限下调用普通 Tool Executor，并以 direct command 加 Tool activity 的形式存储。

## Operation：实时并发边界

Operation 是自然语言 Turn 或 direct command 的独占 lease。会改变状态的 slash command 和凭据变更使用相关的独占 mutation lease。Core 最多授予一个可变前台所有者。

```text
请求 A ---- 取得 lease ---- 执行 ---- 释放
请求 B ---------X operation_busy
```

反方向也遵循同一顺序：缓慢的状态变更命令会阻止新 Turn，活动 Turn 会阻止状态变更命令。单一所有者规则防止两个操作对选中的 Thread、权限 generation、ChangeSet 或数据库生命周期发生竞争。

在 Core 命令边界，普通 Operation 占有前台时只接受以下无副作用快照：

```text
/context  /workspace  /tools  /mcp  /mcp status [id]
/status   /usage      /config
```

`/diff` 不在其中，因为它可能在 ChangeSet 仍在写入时读取它。`/doctor` 不在其中，因为它可能联系 Provider。这是面向受信协议调用方的 Core 并发契约，不是当前 TUI 的提交行为：Ink 会把包括快照命令在内的所有后续输入排队，直到 Operation 完成。精确命令契约见[命令](../reference/commands.zh-CN.md)。

## Interaction：暂停，但并非没有所有者

信任、工具审批、Full access 确认、状态重置和 Turn 恢复都属于 interaction。待处理 interaction 会阻止新的 Operation 和状态变更，直到用户解决或取消它。正常命令处理启用后，Core 可以豁免只读快照；启动信任和状态重置提示发生在该工作流之前，而当前 TUI 在两种情况下都会将后续输入排队。

工具审批携带创建它的 Thread、Turn 和 Operation。解决审批时会重新检查这些事实。Full access 确认还会单独绑定 Thread 和权限 generation。这样，过期响应会成为错误，而不是赋予新会话权限。

## 排队与取消

任务运行时，TUI 最多可以排队三个消息、slash command 或直接 shell 命令，其中也包括 Core 自身本可并发接受的快照命令。前台所有者释放 lease 后，它们会按提交顺序启动。队列只是界面便利功能，不是持久工作流状态；退出会丢失待处理输入。

按 Ctrl+C 请求取消。Core 会限制清理时间，尽可能终止受管理的进程树，发出一个终态结果，然后释放 lease。取消不能证明外部系统没有执行任何操作。如果 shell 或 MCP 调用可能已经产生影响，审计和恢复路径会保留这种不确定性，而不会自动重放。

## 选择正确的边界

| 目标 | 使用方式 |
| --- | --- |
| 带着历史继续解决同一问题 | 同一 Thread，新 Turn |
| 在同一项目中开始无关任务 | `/new` |
| 回到之前的工作 | `/resume` |
| 从已完成节点探索且不修改原历史 | `/fork [turn_id]` |
| 使用原始设置再次运行一个终态请求 | `/retry [turn_id]` |
| 运行已经由你选定的命令 | `! command` |
| 从当前 TUI 检查当前状态 | 等待或取消，然后使用快照命令 |
| 更改权限或配置 | 等待 Operation/interaction 完成 |

## 失败与恢复

如果看到 `operation_busy`，请让当前操作完成或将其取消；并发重试不会提高吞吐量。如果看到 `interaction_busy`，请找到可见提示并作出决定。崩溃后请遵循启动恢复提示，不要创建第二个进程或手动删除状态。

接着阅读[上下文与指令](context-and-instructions.zh-CN.md)和[变更与恢复](changes-and-recovery.zh-CN.md)。
