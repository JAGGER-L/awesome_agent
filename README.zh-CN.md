# awesome_agent

[English](README.md) | [简体中文](README.zh-CN.md)

`awesome_agent` 是一个本地优先、可观测的 Python Coding Agent，核心采用
Agent Team 架构：

- 启动时只存在一个 Leader
- Leader 仅在复杂任务中动态创建 Teammates
- Teammate 可以创建上下文隔离且数量受限的 Subagents
- Team 模式必须包含独立的 Verifier
- 对话、工具、任务、产物、模型分配和验收过程均可追溯

## 当前状态

首版框架已经可以在本地运行，包含编排基础设施、PostgreSQL checkpoint
和 API 投影、沙箱后端、Team/Subagent/Verifier 生命周期、记忆适配器、
可追溯事件、产物、CLI、FastAPI 查询接口、仓库身份注册、允许根目录策略，
以及可在崩溃后恢复的具名 Git worktree Run intake。

## 技术栈

- Python 3.12 和 `uv`
- LangGraph，以及项目自有的编排和 provider 接口
- 默认使用 DeepSeek Chat Completions
- PostgreSQL 和 LangGraph PostgreSQL checkpoint
- Typer CLI 和本地 FastAPI API
- 默认 Docker 沙箱，CLI 可显式启用 trusted-local
- OpenTelemetry，不使用 LangSmith
- 可选的内置记忆和 Mem0 Platform 集成

## 模型配置

仓库默认配置如下：

| 角色 | 模型 |
| --- | --- |
| Leader | `deepseek-v4-pro` |
| Teammate | `deepseek-v4-flash` |
| Verifier | `deepseek-v4-flash` |
| Subagent | `deepseek-v4-flash` |

可以通过 `AWESOME_AGENT_LEADER_MODEL`、`AWESOME_AGENT_TEAMMATE_MODEL`、
`AWESOME_AGENT_VERIFIER_MODEL`、`AWESOME_AGENT_SUBAGENT_MODEL` 修改默认值，
也可以通过 `AWESOME_AGENT_ROLE_MODEL_OVERRIDES` 的 JSON 配置覆盖具体
profile。

## 本地启动

```powershell
.\scripts\bootstrap.ps1
Copy-Item .env.example .env
docker compose up -d postgres
.\scripts\migrate.ps1
.\scripts\check.ps1
.\scripts\system-test.ps1
.\.venv\Scripts\awesome-agent.exe doctor
.\.venv\Scripts\awesome-agent.exe serve
```

创建 Run 前，需要先授权本地父目录，并注册一个干净的主 Git checkout：

```powershell
.\.venv\Scripts\awesome-agent.exe config root add E:\projects
.\.venv\Scripts\awesome-agent.exe repo add E:\projects\example
.\.venv\Scripts\awesome-agent.exe run "检查 parser" --repo E:\projects\example --read-only
```

只有仓库已经位于 allowed root 下时，`run --repo` 才能隐式注册或刷新仓库。
CLI 只向 FastAPI 发送 repository UUID，API 不接受文件系统路径。read-only
和 modifying Run 都要求原 checkout 干净，并基于捕获的 base commit 创建稳定
worktree。Task 02 仅完成持久化的 `created + queued` intake；worker 执行属于
后续路线图任务。

真实模型调用前，需要在被 Git 忽略的本地 `.env` 中配置
`AWESOME_AGENT_DEEPSEEK_API_KEY`。仓库配置默认关闭内置记忆和 Mem0；
本地可设置 `AWESOME_AGENT_BUILTIN_MEMORY_ENABLED=true` 和
`AWESOME_AGENT_MEM0_ENABLED=true` 开启，Mem0 还需要
`AWESOME_AGENT_MEM0_API_KEY`。

## 前端演示

静态演示页面不连接后端：

```powershell
.\.venv\Scripts\python.exe -m http.server 4173 -d demo
```

打开 `http://127.0.0.1:4173`。页面包含模拟的 Agent 拓扑、Todo、事件追踪、
Agent 上下文、命令审批、产物和移动端导航。

本地 PostgreSQL 默认配置：

```text
database: awesome_agent
username: awesome_agent
password: awesome_agent
host port: 54329
container port: 5432
```

## 文档

- [Agent 指令](AGENTS.md)
- [架构](ARCHITECTURE.md)
- [设计文档](docs/design-docs/index.md)
- [工程 Harness](docs/engineering/engineering-harness.md)
- [运行时 Agent Harness](docs/design-docs/runtime-agent-harness.md)
- [前端演示规范](docs/FRONTEND.md)
- [项目治理](docs/project-governance/README.md)
- [产品规格](docs/product-specs/local-coding-agent.md)
- [质量](docs/QUALITY_SCORE.md)
- [可靠性](docs/RELIABILITY.md)
- [安全](docs/SECURITY.md)

英文与中文 README 必须同步维护。
