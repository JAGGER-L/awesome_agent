# 核心概念

本节解释 Awesome 背后的产品模型。当你希望能够预测应用行为，而不只是记住命令时，请阅读这里。

## 架构要解决的问题

编码 Agent 必须结合三种绝不能混为一谈的事物：

1. 对话意图：用户想要什么；
2. 模型推理：什么操作可能满足该意图；
3. 本地权限：进程实际上获准做什么。

如果这些关注点共享一份隐式状态，取消、重试、审批和崩溃恢复都会变得含糊。因此，Awesome 为每项关注点指定了明确的所有者和生命周期。

## 产品模型

```text
Workspace
  `-- Session（一个运行中的 Core、一份不可变指令快照）
       `-- Thread（持久会话与未来 Turn 的选项）
            |-- Turn（一个用户请求和一个终态结果）
            |    `-- Operation（独占的实时执行 lease）
            |         `-- 模型/工具循环 + interaction
            `-- Direct command（没有模型 Turn 的 Operation）
```

这些术语分别回答不同问题：

| 概念 | 回答的问题 | 典型生命周期 |
| --- | --- | --- |
| Workspace | 当前使用哪个项目和文件系统边界？ | 跨 Session |
| Session | 当前由哪个运行中的 Core 拥有此 Workspace？ | 一次进程运行 |
| Thread | 我们在延续哪段持久会话？ | 跨 Session |
| Turn | 一次自然语言请求发生了什么？ | 持久记录 |
| Operation | 当前是谁拥有可变前台执行权？ | 直到成功、失败或取消 |
| Interaction | 为了安全继续，需要用户做出什么决定？ | 直到解决或取消 |
| ChangeSet | 哪些观测到的文件影响可以一起审查或恢复？ | 持久 Journal 记录 |

## 一个请求的端到端流程

```text
提交消息
    |
    v
原子取得 Operation lease
    |
    v
持久化 Turn + 冻结配置/上下文事实
    |
    v
准备有界上下文 -> 调用模型
             -> 请求工具
             -> 验证 + 策略 + 审批
             -> 执行 + 记录结果/变更
             -> 再次调用模型
    |
    v
持久化恰好一个 Turn 终态
```

lease 必须在持久化 Turn 之前取得。这样，并发竞争失败的请求不会留下空 Turn 或永远处于进行中的 Turn。工具审批是同一个 Operation 的延续，而不是第二个 Operation，因此审批流程不会与自身发生死锁。

## 四个独立的安全层

Awesome 有意区分：

- **Workspace 信任**：决定是否可以加载由项目控制的内容；
- **权限模式**：决定受信操作是否需要审批；
- **硬安全检查**：在任何权限模式下都拒绝特定的危险路径或命令；
- **宿主机隔离**：Awesome 目前不提供。

这种分层避免产生一个误导性的“安全/不安全”开关。Full access 会减少部分已知内置本地能力的提示，但不会禁用硬拒绝、自动批准 MCP，也不会创建沙箱。参见[权限与安全](../user-guide/permissions.zh-CN.md)。

## 持久状态与 Session 状态

Thread 消息、Turn、摘要、checkpoint、信任记录、扩展启用状态和 Change Journal 记录可以跨重启保留。前台 lease、输入队列、当前权限模式、临时写入 grant、待处理 UI 状态和根目录 `AGENTS.md` 快照属于当前 Session。

这种区分构成恢复边界：Awesome 会持久化解释工作或安全恢复工作所需的事实，但不会假装一项内存中的权限授予在进程或 Thread 切换后仍然有效。

## 设计取舍

- 单一前台变更所有者牺牲了一个 Session 内的并行编辑，以换取确定的顺序和恢复行为。
- 不可变的根指令快照牺牲了热重载，以换取稳定的 Turn 契约。
- 宿主机执行保留了与常规开发工具的兼容性，但面对恶意代码时需要外部隔离。
- 保守恢复可能会询问用户或拒绝恢复，而不会在外部影响含糊时猜测。
- 有界上下文可能会总结较早的历史；manifest 使这一选择可检查。

## 阅读顺序

1. [Workspace、Thread、Turn 与 Operation](workspace-thread-turn.zh-CN.md)
2. [上下文与指令](context-and-instructions.zh-CN.md)
3. [变更与恢复](changes-and-recovery.zh-CN.md)
4. [日常工作流](../user-guide/README.zh-CN.md)
5. 了解实现所有权时阅读[架构](../architecture/README.zh-CN.md)

精确的字段值和限制请查阅[参考手册](../reference/README.zh-CN.md)。
