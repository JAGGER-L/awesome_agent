# Awesome 文档

[English](README.md) | [简体中文](README.zh-CN.md)

本目录是 Awesome 文档的规范来源。GitHub Pages 文档站由这些 Markdown
文件生成；本页刻意保留为面向仓库读者的文档地图，不会作为第二个网站首页发布。

Awesome 的文档按照读者要解决的问题组织，而不是照搬 Python 包目录：

```text
我能使用 Awesome 吗？       -> 从这里开始
我应该怎样与它协作？         -> 核心概念 + 用户指南
我怎样扩展它？              -> 扩展机制
精确契约是什么？            -> 参考手册
内部如何实现？              -> 系统架构
我怎样安全地修改它？         -> 开发指南
哪些已经存在，哪些仍在规划？  -> 路线图
```

## 选择阅读路径

| 目标 | 从这里开始 | 接着阅读 |
| --- | --- | --- |
| 安装并完成一个有用的 Turn | [快速开始](getting-started/quickstart.zh-CN.md) | [日常工作流](user-guide/README.zh-CN.md) |
| 安装前理解产品 | [从这里开始](getting-started/README.zh-CN.md) | [运行机制](concepts/README.zh-CN.md) |
| 在已有仓库中安全工作 | [权限](user-guide/permissions.zh-CN.md) | [工具与 Shell](user-guide/tools-and-shell.zh-CN.md)，然后阅读[变更](user-guide/changes.zh-CN.md) |
| 配置模型、预算或扩展 | [配置指南](user-guide/configuration.zh-CN.md) | [配置参考](reference/configuration.zh-CN.md) |
| 增加持久上下文或外部工具 | [扩展机制选择指南](extensions/README.zh-CN.md) | [Memory](extensions/memory.zh-CN.md)、[Skills](extensions/skills.zh-CN.md)或 [MCP](extensions/mcp.zh-CN.md) |
| 诊断故障 | [故障排查](user-guide/troubleshooting.zh-CN.md) | [文件与状态](reference/files-and-state.zh-CN.md) |
| 审计当前实现缺口 | [当前已知限制](roadmap.zh-CN.md#当前已知限制) | 继续阅读其中链接的契约页和架构页 |
| 审查实现 | [架构总览](../ARCHITECTURE.zh-CN.md) | [专题架构指南](architecture/README.zh-CN.md) |
| 贡献修改 | [开发指南](development/README.zh-CN.md) | [测试](development/testing.zh-CN.md)与[契约](development/contracts-and-documentation.zh-CN.md) |

## 文档地图

### 从这里开始

- [产品概览与学习路径](getting-started/README.zh-CN.md)
- [安装](getting-started/installation.zh-CN.md)
- [五步快速开始](getting-started/quickstart.zh-CN.md)

### 核心概念

- [Awesome 如何工作](concepts/README.zh-CN.md)
- [Workspace、Thread、Turn 与 Operation](concepts/workspace-thread-turn.zh-CN.md)
- [上下文与工作区指令](concepts/context-and-instructions.zh-CN.md)
- [变更、取消与恢复](concepts/changes-and-recovery.zh-CN.md)

### 使用 Awesome

- [日常工作流](user-guide/README.zh-CN.md)
- [命令与会话](user-guide/commands.zh-CN.md)
- [权限与审批](user-guide/permissions.zh-CN.md)
- [工具与 Shell 执行](user-guide/tools-and-shell.zh-CN.md)
- [审查、撤销与恢复变更](user-guide/changes.zh-CN.md)
- [配置与模型提供商](user-guide/configuration.zh-CN.md)
- [故障排查](user-guide/troubleshooting.zh-CN.md)

### 扩展 Awesome

- [选择扩展机制](extensions/README.zh-CN.md)
- [Memory](extensions/memory.zh-CN.md)
- [Skills](extensions/skills.zh-CN.md)
- [MCP](extensions/mcp.zh-CN.md)

### 参考手册

- [参考手册索引](reference/README.zh-CN.md)
- [CLI 与键盘操作](reference/cli.zh-CN.md)
- [斜杠命令](reference/commands.zh-CN.md)
- [配置 Schema](reference/configuration.zh-CN.md)
- [内置工具](reference/built-in-tools.zh-CN.md)
- [权限模式](reference/permission-modes.zh-CN.md)
- [文件与状态](reference/files-and-state.zh-CN.md)
- [Protocol v3](reference/protocol.zh-CN.md)

### 系统架构

- [架构阅读路径](architecture/README.zh-CN.md)
- [请求生命周期](architecture/request-lifecycles.zh-CN.md)
- [Application 与 Agent](architecture/application-and-agent.zh-CN.md)
- [上下文、模型与扩展](architecture/context-model-and-extensions.zh-CN.md)
- [工具执行与 Change Journal](architecture/tools-and-changes.zh-CN.md)
- [存储与恢复](architecture/storage-and-recovery.zh-CN.md)
- [Protocol 与 TUI](architecture/protocol-and-tui.zh-CN.md)
- [安全与依赖边界](architecture/security-and-dependencies.zh-CN.md)

### 参与贡献

- [贡献者概览](development/README.zh-CN.md)
- [环境搭建与代码库](development/setup.zh-CN.md)
- [测试与 CI](development/testing.zh-CN.md)
- [在代码中扩展 Awesome](development/extending-awesome.zh-CN.md)
- [协议与文档契约](development/contracts-and-documentation.zh-CN.md)
- [发布](development/release.zh-CN.md)
- [路线图](roadmap.zh-CN.md)

## 规范所有权

重复事实会发生漂移。为保持系统可维护，每类事实只有一个所有者：

| 事实 | 规范所有者 |
| --- | --- |
| 产品定位与最短首次运行路径 | 根 README 与快速开始 |
| 精确命令语法、配置字段、工具 Schema、限制和权限矩阵 | `docs/reference/` |
| 用户任务与恢复流程 | `docs/user-guide/` |
| 扩展配置与信任边界 | `docs/extensions/` |
| 系统拓扑与依赖方向 | 根 `ARCHITECTURE.md` / `ARCHITECTURE.zh-CN.md` 对页 |
| 子系统不变量与实现流程 | `docs/architecture/` |
| 构建、测试、CI、文档与发布流程 | `docs/development/` |
| 当前产品范围与未来方向 | `docs/roadmap.zh-CN.md` |

其他页面只总结并链接到规范所有者，不重复整张表或整段配置。

## 语言政策

英文和简体中文都是完整的一等文档集。每个英文 Markdown 源文件都必须在同一
目录中拥有唯一的 `name.zh-CN.md` 对页；权威架构总览使用根目录中的
`ARCHITECTURE.md` / `ARCHITECTURE.zh-CN.md` 对页。两个版本必须保留相同的
行为、安全边界、示例、图表和链接目的地。
首页通过配对的 `site/homepage-content.en.json` 与
`site/homepage-content.zh-CN.json` 遵守同一规则，并只使用一份共享 route map。

同步和导航检查会在任意一侧缺失或出现孤立译文时失败。站点不会合成语言回退页。
页面重命名或删除后会从规范路由集中直接移除，旧 URL 返回 404，不进入兼容重定向层。

`site/translation-lock.json` 会把每组经过审查的对页绑定到两个源的规范化 hash。更新并
审查两种语言后，运行 `npm --prefix site run translations:lock` 并检查 lock diff；没有审查
翻译就刷新 lock，不能作为完成证据。

## 维护契约

产品行为发生变化时：

1. 更新拥有该事实的规范页面；
2. 在同一次修改中同步更新另一语言对页；
3. 更新结果发生变化的任务指南；
4. 仅当所有权、流程或不变量改变时更新架构文档；
5. 把页面加入共享站点导航清单；
6. 两种语言都完成审查后刷新 translation lock；
7. 运行[开发指南](development/contracts-and-documentation.zh-CN.md)中说明的文档结构、站点类型、生产构建、路由和锚点检查。

文档示例就是产品契约。命令必须可运行，配置必须符合当前模型，声称可用的恢复路径
必须有源码或测试支持。未来行为只能写入[路线图](roadmap.zh-CN.md)。
