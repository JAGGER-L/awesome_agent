# 扩展 Awesome

本节面向希望让 Awesome 记住稳定事实、遵循可复用工作流，或调用外部工具服务器的用户和
维护者。在启用由 Workspace 控制的扩展之前，请先阅读[核心概念](../concepts/README.zh-CN.md)
并信任该 Workspace。

Awesome 有三类扩展。它们解决的问题不同，权限、持久化和隐私边界也不同；其中任何一类都
不是通用插件系统。

## 选择满足需求的最小扩展

| 需求 | 使用 | 进入 Turn 的内容 | 存放位置 | 网络 | 对权限的影响 |
| --- | --- | --- | --- | --- | --- |
| 复用稳定偏好或项目事实 | [Memory](memory.zh-CN.md) | 有界且不受信任的参考上下文 | 用户拥有的 Markdown，可选 Mem0 Cloud | 本地：否；Mem0：是 | 不能授予工具权限 |
| 复用指令和辅助文本文件 | [Skills](skills.zh-CN.md) | 一个被选中的指令正文，或按需读取的资源 | Bundled、User 或受信任 Workspace 中的包 | 否，除非 Skill 随后要求 Agent 使用联网工具 | 不能授予工具权限；`allowed-tools` 仅作说明 |
| 添加由另一个进程实现的工具 | [MCP](mcp.zh-CN.md) | 经过验证的工具 schema 和有界结果 | User 或受信任 Workspace 的配置 | 由服务器决定 | 即使在 Full access 下，每次调用也都询问 |

Memory 用于事实，而不是流程。Skill 用于流程，而不是持久状态。只有当能力必须跨越 Core
进程边界时才使用 MCP。为一项简单约定同时组合三者，只会增加上下文、故障模式和信任面，
不会带来额外价值。

## 所有扩展共同遵守的不变量

只有在 Workspace 获得信任后，项目控制的内容才能影响模型；模型影响力永远不等于权限。
工具调用仍须经过与内置工具相同的 registry、参数验证、权限策略、超时、审计和事件管线。

```text
user config / trusted workspace config / package files
                         |
                         v
          configuration load, Skill discovery, or catalog compile
                         |
                         v
              context source or ToolSpec
                         |
                         v
                    Agent Loop
                         |
                         v
       Tool Executor -> permission -> backend-specific limits
                         |
                         v
              normalized result + audit
```

这一设计刻意将四个问题分开：

1. **信任：** 可以把该 Workspace 中的文件作为配置或指令读取吗？
2. **有效性：** 可以在产品资源限制内解析这些内容吗？
3. **相关性：** 这些内容应该进入当前 Turn 吗？
4. **权限：** 请求的工具操作可以执行吗？

通过前一个问题绝不意味着同时通过后一个问题。例如，受信任 Workspace 中的 MCP 声明仍然
需要明确启用、有效的 catalog，以及逐次调用审批。

## 配置与检查

- User 声明位于 `<AWESOME_HOME>` 下，适用于所有 Workspace。
- Workspace 声明位于 `<workspace>/.awesome/` 下，在信任被接受之前会被忽略。
- `/memory`、`/skills` 和 `/mcp` 分别显示对应的运行时状态。
- `/tools` 显示有效工具 catalog，以及当前权限模式是否会询问。
- `/doctor` 报告配置和 Provider 就绪情况；特定扩展的诊断仍会显示在其各自的命令输出中。

完整的 YAML 契约见[配置参考](../reference/configuration.zh-CN.md)。工具与权限的精确行为见
[内置工具](../reference/built-in-tools.zh-CN.md)和[权限模式](../reference/permission-modes.zh-CN.md)。

## 故障隔离

对于 Agent Loop，扩展都是可选项：

- 一个无效 Skill 会产生一条诊断，但不会隐藏有效 Skill；
- 已启用的**本地** Memory 若无效或不可读，会使 Turn 上下文准备失败，而不是静默遗漏持久
  事实；coordinator 会终结已创建的 Turn 并尝试删除其 checkpoint；如果清理失败，启动
  reconciliation 会重试删除遗留的 checkpoint；
- **Mem0 Cloud** recall 不可用时，会省略该云端来源并报告有界诊断；
- 无效的 MCP catalog、失败的发布或失败的 MCP 传输会使该服务器的 Manager 快照与
  Registry namespace 失效，但不会替换 Core 状态或其他服务器的工具。

这些边界并不完全一致。尤其是，当前 Workspace 配置读取器虽受信任门控制，却还没有采用
`AGENTS.md` 和 Workspace Skills 所使用的有界、禁止跟随链接、打开后身份检查。对于 MCP，
一个 server lock 会保护候选项编译与发布：Registry 先替换完整 namespace，随后 Manager
在中间没有 `await` 的情况下发布同一 generation 和 `CONNECTED`。共享 Registry 限制可以
整体拒绝候选项，而不影响其他 namespace。见
[配置](../reference/configuration.zh-CN.md)与 [MCP](mcp.zh-CN.md)。

可选不等于不可见。降级的扩展可能改变模型可获得的证据，因此应当诊断它，而不能假定一个
Turn 使用了与更早 Turn 相同的上下文和工具。

## 推荐阅读路径

- 个人偏好或仓库约定：[Memory](memory.zh-CN.md) ->
  [上下文与指令](../concepts/context-and-instructions.zh-CN.md)。
- 可重复的审查、调试或测试流程：[Skills](skills.zh-CN.md) ->
  [工具与 shell](../user-guide/tools-and-shell.zh-CN.md)。
- 外部工具服务器：[MCP](mcp.zh-CN.md) ->
  [权限](../user-guide/permissions.zh-CN.md) ->
  [Protocol v5](../reference/protocol.zh-CN.md)。
