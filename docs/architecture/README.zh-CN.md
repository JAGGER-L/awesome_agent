# 架构指南

本指南说明：当用户请求依次经过终端 UI、私有协议、应用生命周期、
LangGraph Agent、模型提供商、工具以及持久化状态时，Awesome 如何始终维持唯一的
执行权威。

仓库根目录的[架构概览](../../ARCHITECTURE.zh-CN.md)是拓扑和职责归属的权威说明。
本目录中的页面回答范围更窄的问题，并指向相关源代码和测试。若某个专题页面与根目录
概览或当前代码冲突，应以根目录概览和代码为准，并在同一次变更中更新该专题页面。

## 从不变量开始

核心不变量并不是“项目使用 LangGraph”或“产品有 TUI”，而是：

> 恰好由一个 Python Application host 管理产品生命周期，并由一个 Agent graph
> 管理模型/工具循环的执行。所有界面和扩展都必须穿过这两层边界，不能建立第二条
> 执行路径。

有了这一不变量，取消、审批、持久化、恢复和事件顺序才能围绕同一个操作进行推理。
大多数架构规则都由此而来：

- Ink 负责捕获意图并渲染类型化事实；它不运行模型或工具。
- `ApplicationFacade` 是面向界面的产品 API。
- `application/` 在创建持久化 Turn 之前准入并协调前台工作。
- `agent/graph.py` 是唯一的 `StateGraph` 编译器。
- 所有工具都经过同一条 Registry -> Policy -> Executor 路径；
- 产品记录与图 checkpoint 分属不同的所有者和数据库；
- Skills、Memory、工作区指令和 MCP 结果都是上下文，而不是执行权威；
- Full access 会改变审批行为，但不会建立操作系统沙箱。

## 选择阅读路径

| 如果你需要了解…… | 阅读 |
| --- | --- |
| 启动、Turn、直接命令、交互、取消和关闭 | [请求生命周期](request-lifecycles.zh-CN.md) |
| Application 与 Agent 为什么分离，以及 LangGraph 应位于何处 | [Application 与 Agent](application-and-agent.zh-CN.md) |
| 提示词来源、预算、模型适配器、Skills、Memory 和 MCP | [上下文、模型与扩展](context-model-and-extensions.zh-CN.md) |
| 工具注册、审批、Shell 执行和可逆变更 | [工具与变更](tools-and-changes.zh-CN.md) |
| SQLite 职责、checkpoint、lease、重置和崩溃收敛 | [存储与恢复](storage-and-recovery.zh-CN.md) |
| Protocol v3、stdio 边界、Ink 状态、输入模式和状态校正 | [协议与 TUI](protocol-and-tui.zh-CN.md) |
| 威胁模型、信任、隔离边界和强制依赖方向 | [安全与依赖](security-and-dependencies.zh-CN.md) |

如果希望从代码入手，请按以下顺序阅读：

1. `src/awesome_agent/application/facade.py`
2. `src/awesome_agent/application/composition.py`
3. `src/awesome_agent/application/turns.py`
4. `src/awesome_agent/agent/graph.py`
5. `src/awesome_agent/agent/nodes.py`
6. `src/awesome_agent/context/builder.py`
7. `src/awesome_agent/core/tools/executor.py`
8. `src/awesome_agent/protocol/stdio.py`
9. `tui/src/app/App.tsx`

## 边界概览

```text
terminal input
    |
    v
Ink surface -- Protocol v3 --> LocalApplication
                                     |
                              foreground arbiter
                                     |
                 +-------------------+-------------------+
                 |                                       |
                 v                                       v
           TurnCoordinator                         command service
                 |
                 v
         compiled Agent graph
                 |
        +--------+--------+
        |                 |
        v                 v
    ModelGateway       ToolExecutor
        |                 |
        v                 v
 provider adapter    built-in / MCP adapter
                          |
                          v
                    workspace / host
```

箭头表示调用关系，而非所有权关系。Application 负责组装具体对象，但下层包仍然持有
自身的不变量。例如，Application 可以取消 Turn；但不能绕开 Agent 节点去修复提供商
消息链。

## 如何理解一条架构陈述

每个页面都区分五类陈述：

- **职责（responsibility）**：某个包被允许执行的工作；
- **不变量（invariant）**：在成功、失败和竞态下都必须成立的属性；
- **契约（contract）**：跨边界消费的类型化或持久化形态；
- **机制（mechanism）**：当前用于落实不变量的实现；
- **边界（limit）**：该机制不保证的行为。

这种区分很重要。“文件路径在词法上被包含”是一种机制；“工具不得沿工作区链接访问
外部目标”是一项不变量；而“Awesome 是操作系统沙箱”则会是错误的保证。

## 架构变更检查清单

变更边界之前：

1. 找出当前决策的所有者以及其持久化状态。
2. 追踪所有调用方、事件、恢复路径和取消路径。
3. 判断变更是否修改公共、协议、存储或扩展契约。
4. 先在能够证明该不变量的最低层加入失败测试，再为跨越的边界补充集成覆盖。
5. 如果包的职责或依赖方向发生变化，更新根目录架构概览。
6. 当相关契约变化时，同步更新专题页面、用户文档、生成的 Protocol v3 fixtures
   以及 TUI schema/presenter。

贡献者工作流详见[契约与文档](../development/contracts-and-documentation.zh-CN.md)。

## 源代码与测试索引

| 关注点 | 主要源代码 | 契约测试 |
| --- | --- | --- |
| 包与框架职责 | `src/awesome_agent/*` | `tests/structural/test_dependency_architecture.py` |
| Application facade 与命令 | `application/facade.py`、`dispatcher.py` | `tests/structural/test_application_architecture.py` |
| Agent graph 与 checkpoint 状态 | `agent/graph.py`、`agent/state.py` | `tests/structural/test_agent_architecture.py` |
| 上下文组装 | `context/`、`application/context.py` | `tests/structural/test_context_architecture.py` |
| 工具与 Change Journal | `core/tools/`、`core/changes/` | `tests/structural/test_tool_architecture.py` |
| 嵌入式状态 | `storage/` | `tests/structural/test_storage_architecture.py` |
| 扩展 | `extensions/`、`memory/` | `tests/structural/test_extension_architecture.py` |
| 协议与 TUI | `protocol/`、`tui/src/` | Python 与 TUI 契约/结构测试套件 |
