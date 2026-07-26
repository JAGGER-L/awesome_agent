# 审查、撤销与重做

本页说明如何审查 Awesome 记录的文件变更并恢复它们，同时避免覆盖后续工作。它解释 Change Journal 的覆盖范围及保证边界。

## 审查最近的 ChangeSet

修改型 Turn 或 direct command 完成后运行：

```text
/diff
```

Awesome 会选择当前 Workspace 中最近的 ChangeSet。如需检查较早的已知记录：

```text
/diff <change_set_id>
```

文本文件显示为有界 unified diff。二进制文件显示变更前后的字节数；目录和 symlink 记录显示节点类型变化。空结果表示所选记录中没有文件 delta，而不代表任意 shell 命令没有产生影响。

## 什么可以恢复

内置 `write_file`、`edit_file` 和 `delete` 会保存恢复所需的前后状态。Journal 还区分文件、目录和 symlink，因此可以准确表达一种节点类型被另一种类型替换的情况。

shell 和 MCP 影响不是文件系统快照：

- 仅含文件的 ChangeSet 完全可逆；
- 同时包含内置文件变更和 shell 的 ChangeSet 部分可逆；
- 仅包含 execute 的 ChangeSet 不可逆。

`/undo` 只恢复记录的内置文件节点。部分可逆集合上的警告意味着未恢复不受管理的 shell 影响。

## 安全撤销

先检查 `/diff`，再运行：

```text
/undo
```

或指定特定记录：

```text
/undo <change_set_id>
```

Core 会在更改任何路径前预检所有路径。每个当前节点都必须与 ChangeSet 预期的“after”状态匹配。如果你或另一个进程在 Turn 之后编辑了某个文件，`/undo` 会返回 Workspace 冲突，且不会更改集合中的任何项目。

冲突确实存在时，不要强制执行旧恢复。使用 `/diff` 比较当前文件，并在新 Turn 中手动整合所需部分。

## 重做已撤销的集合

成功撤销后：

```text
/redo
```

或：

```text
/redo <change_set_id>
```

Redo 会执行对称预检：每个当前节点必须与记录的“before”状态匹配。随后，它恢复记录的 applied 状态，并将生命周期移回 `applied`。

## 为什么恢复分为多个阶段

如果没有持久 intent 而逐个恢复多个路径，进程停止时会留下无法解释的半撤销状态。因此，Awesome 遵循：

```text
绑定所有目标 -> 验证所有状态 -> 持久化待处理 intent
             -> 应用所有路径 -> 验证结果 -> 提交生命周期
```

如果提交前发生错误，Core 会尝试回滚已经恢复的路径。如果验证结果含糊，它会保留待处理证据。启动恢复随后会验证已提交结果，或回滚未提交的部分操作；它不会根据时间戳或文件名猜测。

## 常见结果

| 结果 | 含义 | 下一步 |
| --- | --- | --- |
| Empty diff | 所选记录中没有文件 delta | 检查 `/status`，确认工作是否只使用了 shell |
| `change_set_not_found` | 当前 Workspace 中不存在该 ID | 使用最近的集合或复制正确 ID |
| `workspace_conflict` | 当前路径与预期状态不同 | 保留当前工作并手动整合 |
| `change_not_reversible` | 没有可恢复的内置文件状态 | 手动检查 shell/MCP 目标 |
| `invalid_change_lifecycle` | 请求操作与 applied/undone 状态不匹配 | 检查集合，按需使用相反操作 |

## 安全审查流程

1. 第一次实现时保持 Request approval 或 Accept edits。
2. 阅读助手摘要和 `/diff`。
3. 运行或请求相关验证。
4. 只有整个已记录文件集合应该一起移动时才撤销。
5. 发生超时或取消后，先检查外部影响再重放。

底层持久性模型见[变更与恢复](../concepts/changes-and-recovery.zh-CN.md)。文件位置和存储所有权见[文件与状态](../reference/files-and-state.zh-CN.md)。
