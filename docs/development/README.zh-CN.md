# 为 Awesome 贡献

Awesome 分为 Python Core 和 Ink + React TUI。只有当贡献能够维持产品行为、package
所有权、恢复语义和跨语言契约时才会被接受；仅仅让某一个复现通过还不够。

本节是贡献者路径。产品安装与日常使用属于 getting-started 和 user guide。

## 第一次贡献的路径

1. 阅读仓库根目录 `AGENTS.md` 和[架构概览](../../ARCHITECTURE.zh-CN.md)。
2. 按照[开发环境设置](setup.zh-CN.md)完成配置，并至少启动一次当前 checkout。
3. 在[架构指南](../architecture/README.zh-CN.md)中找到当前行为的所有者。
4. 添加或找到一个能够证明当前行为与目标不变量的测试。
5. 做最小且完整的变更；避免无关重构。
6. 运行[测试](testing.zh-CN.md)中与风险匹配的 gate。
7. 按[契约与文档](contracts-and-documentation.zh-CN.md)更新契约和文档。
8. 检查 diff 与 status，再提交一个经过验证的逻辑变更。

新增 provider、built-in tool、command、Skill 行为、MCP 行为或 protocol feature 时，
编辑前先阅读[扩展 Awesome](extending-awesome.zh-CN.md)。准备 artifact 的维护者应阅读
[发布](release.zh-CN.md)。

## 贡献者仓库索引

| 区域 | 职责 | 从这里开始 |
| --- | --- | --- |
| `src/awesome_agent/application/` | 生命周期、命令、前台工作、interaction、组装 | `facade.py`、`composition.py`、`turns.py` |
| `src/awesome_agent/agent/` | 唯一 LangGraph 与模型/工具循环 | `graph.py`、`nodes.py`、`state.py` |
| `src/awesome_agent/context/` | 提示词来源、预算、路径、压缩 | `builder.py`、`compression.py` |
| `src/awesome_agent/modeling/` | 提供商中立契约与 gateway | `provider.py`、`gateway.py` |
| `src/awesome_agent/providers/` | DeepSeek 与 Kimi adapter | `deepseek.py`、`kimi.py` |
| `src/awesome_agent/core/tools/` | registry、policy、executor、built-in、进程 | `executor.py`、`registry.py` |
| `src/awesome_agent/core/changes/` | Change Journal 与 undo/redo | `journal.py`、`operations.py` |
| `src/awesome_agent/storage/` | 嵌入式状态与 checkpoint | `database.py`、`conversations.py` |
| `src/awesome_agent/extensions/` | Skills 与 MCP | `skills/`、`mcp/` |
| `src/awesome_agent/memory/` | 本地与 Mem0 memory | `service.py`、`mem0_cloud.py` |
| `src/awesome_agent/protocol/` | Protocol v5 与 stdio Host | `jsonrpc.py`、`stdio.py` |
| `tui/src/` | 终端展示与 Core adapter | `app/App.tsx`、`protocol/`、`state/` |
| `tests/` 与 `tui/tests/` | 行为、集成、结构、打包 | 最近的 package suite |

## 贡献不变量

- 仓库文件和测试是事实来源。
- 在共享 branch 或 worktree 中保留无关的用户与 agent 工作。
- 保持唯一产品权威：Ink 负责展示，Application 负责协调，Agent 负责推理，Tool Executor
  负责产生作用。
- 不要为了让变更通过而新增 compatibility adapter、skip、expected failure、type ignore
  或更宽松的断言。
- 预期 failure、cancellation、timeout、race 和 recovery 行为是 feature 的组成部分，
  不是可选的润色。
- 公共行为、配置、命令、协议、存储和架构变更必须在同一次变更中更新文档。
- 绝不提交凭据、私有路径、本地状态、生成 cache、debug output 或原始 tool/provider
  payload。

## 变更工作流

### 1. 确定范围

说明用户可见目标，以及保证它正确的不变量。沿界面追踪请求，经过其所有者到达存储或
外部作用。跨边界变更应在被忽略的 active plan 目录中写一份简短执行计划。

### 2. 证明缺陷或契约

优先使用确定性失败测试。覆盖原始情况、等价变体和正常负例。并发或取消测试应使用
barrier/event 和有界 fake backend，而不是 sleep 与 live service。

### 3. 在所有者处实现

复用当前 error type、event 和 module boundary。如果修复看似需要第二执行路径、通用
兼容层或新生产依赖，应在编码前重新审视设计。

### 4. 渐进验证

先运行 format/lint 和聚焦测试。较低 gate 失败后应停止，直到问题被修复或证明确属无关。
只有当变更跨越相应边界时，才增加 integration、structural、packaging、TUI 和 E2E 覆盖。

### 5. 交付证据

记录确切命令和结果、推迟的平台/live 检查以及残余风险。聚焦 commit 或 pull request 前，
检查 `git diff --check`、完整 diff 和 `git status`。

## 应在哪里提出设计问题

用职责归属来构造问题：

- “这个状态是否应在重启后保留？”从 Conversation/Storage 开始。
- “谁可以开始这项工作？”从 Application foreground admission 开始。
- “下一次模型/工具 transition 是什么？”从 Agent 开始。
- “这个作用能否运行？”从 Tool Policy 与 Executor 开始。
- “如何展示它？”从 Protocol fact 与 TUI Presenter 开始。
- “能否安全恢复？”从 Turn/checkpoint 与 external-effect evidence 开始。

这样可以避免一个框架或组件偏好意外演变成架构决策。

## Pull request 要求

一份可审查的 pull request 应说明：

- 用户问题和受保护的不变量；
- 选定的所有者，以及该边界为何正确；
- 行为与公共契约变更；
- 视情况为 success、negative、failure、race、cancellation 或 recovery 路径新增的测试；
- 已执行的确切验证；
- 未执行的平台、凭据、网络或 release 检查；
- 残余风险和刻意留在范围外的后续工作。

当检查被跳过、不可用或仅从其他环境推断时，不要将其描述为通过。
