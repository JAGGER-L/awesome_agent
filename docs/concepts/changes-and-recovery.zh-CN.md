# 变更与恢复

本页说明 Awesome 如何让文件变更可审查，以及它如何避免在取消或崩溃后猜测。任何需要判断重试、撤销或放弃某个 Turn 是否安全的人都应阅读本页。

## 三种不同记录

Awesome 不会用一条数据库记录来证明所有事实：

| 记录 | 能证明什么 | 不能证明什么 |
| --- | --- | --- |
| Thread 与 Turn | 请求、终态、回答和用量 | 精确的文件系统状态 |
| LangGraph checkpoint | 可恢复 Agent 循环停在何处 | 待处理的副作用工具是否已经执行 |
| Change Journal | 观测到的内置文件前后状态和 shell 尝试 | 调用幂等性，以及 shell 或 MCP 造成的任意影响 |

将这些记录分开，能够防止把会话中的“completed”标记误当成文件系统事务。

## ChangeSet 生命周期

一个修改型 Operation 拥有一个 ChangeSet。内置 `write_file`、`edit_file` 和 `delete` 会记录变更前后的节点类型、内容身份和恢复数据。`execute` 会在 process runner 启动前记录一条保守且脱敏的 observation。

```text
             /undo
open --> applied ------> undone
          ^                 |
          |------ /redo ----|
```

所有者结束时，开放的 ChangeSet 会密封为 `applied`。仅包含文件的集合可以完全可逆。同时包含内置文件修改和 shell 执行的集合只能部分可逆。仅包含 execute 的集合不可逆，因为 Awesome 不会为任意命令影响创建快照。

因此，`/undo` 并不表示“撤销整个 Agent”。它只恢复已记录的 Workspace 节点。

## 恢复前审查

使用：

```text
/diff
/diff <change_set_id>
/undo
/undo <change_set_id>
/redo
/redo <change_set_id>
```

不提供 ID 时，命令会选择该 Workspace 最近的 ChangeSet。`/diff` 为 UTF-8 文本显示有界 unified diff，为二进制、目录或 symlink 变更显示摘要。`/undo` 和 `/redo` 会先验证全部受影响路径；只要当前 Workspace 与记录的预期状态存在冲突，就不会开始恢复。

详细用户流程见[审查、撤销与重做](../user-guide/changes.zh-CN.md)。

## 原子性采取保守定义

恢复前，Core 会绑定每个目标，检查完整集合有无冲突，并写入待处理的恢复 intent。随后，它通过同一个有界 Workspace tree 应用变更，且只有所有路径都符合预期结果后才提交生命周期。

如果某条路径在提交前失败，Core 会回滚已经恢复的路径。如果无法验证回滚结果，它会保留待处理证据供启动恢复使用，而不是删除记录。这些证据比虚假的干净状态更有价值。

## Turn 恢复

意外退出可能留下一个 `in_progress` Turn。下次启动时，Awesome 会将持久 Turn 与其 checkpoint 和冻结的上下文事实进行比较。

```text
未完成 Turn
      |
      +-- 已验证且待处理工作可重放 -----> Retry 是安全默认值
      |
      +-- 不可重放工具可能已经执行 ----> Abort 是安全默认值
      |
      +-- checkpoint/上下文无效 -------> 以诊断失败
```

对于已验证的本地 checkpoint，Retry 会从该 checkpoint 继续，而不会用当前文件重新构建
不同的上下文。只有待处理工具的注册信息能够证明重复调用安全时，恢复才会自动继续。
内置文件修改工具属于 non-replayable，因为崩溃后用户或其它进程可能修改同一路径。对于
结果不确定的文件修改、shell、MCP 或 Web 调用，Awesome 绝不会透明重放或假定失败。
用户必须在“让这种不确定性保持可见的情况下重试剩余 Turn”和“中止 Turn”之间选择。

Abort 会把未完成 Turn 标记为失败，不再继续。它不会回滚文件系统或外部系统，也不会擦除
Change Journal 证据。

## 启动状态兼容性

产品升级可能导致嵌入式会话/checkpoint schema 不兼容。当产品能够安全识别可重置的旧状态时，启动界面会提供 **Reset local state and continue**，并列出将删除的内容。重置会删除会话、信任记录、checkpoint 和 undo 历史，但保留 API Key、用户配置、Skills 以及 Local/Cloud Memory 设置。

较旧的二进制不会重置由较新 Awesome 版本创建的状态。未知、损坏、不可读、锁定或正被并发使用的状态会产生诊断。请升级、关闭其他 Session 或调查错误；不要仅为绕过检查而删除数据目录。

## 取消与外部影响

Ctrl+C 会请求有界清理，并保留原始取消结果。进程树终止会减少孤儿子进程，却不能撤回网络请求、逃逸进程组的 daemon，或已经提交外部变更的命令。MCP 超时和连接丢失同样会报告不确定结果，因为服务器可能在连接失败前已经执行。

安全规则是：纯读取可自由重试；恢复前检查文件变更；外部写入超时后，应将其视为可能成功，直到目标系统提供反证。

## 恢复检查清单

1. 阅读精确提示，判断不确定性属于本地、文件系统、shell、MCP 还是 Web。
2. 在可用时检查 `/status`、`/diff`、受影响文件和外部目标。
3. 只有重放剩余逻辑工作安全时才选择 Retry。
4. 当重复副作用比未完成 Turn 更糟时，选择 Abort。
5. 开始一个新 Turn，说明任何已经手动验证的状态。

按症状和错误采取的步骤见[故障排查](../user-guide/troubleshooting.zh-CN.md)。存储细节和文件位置见[文件与状态](../reference/files-and-state.zh-CN.md)。
