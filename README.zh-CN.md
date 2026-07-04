# awesome_agent

[English](README.md) | [简体中文](README.zh-CN.md)

`awesome_agent` 是一个 local-first coding-agent runtime，用于执行可持久化、可观测、受权限约束的本地代码任务。

## 项目是什么

`awesome_agent` 通过 Typer CLI、本地 FastAPI API、PostgreSQL 持久化状态和 Worker 进程，在本地 Git 仓库中运行 coding task。它支持 solo read-only、solo modifying，以及带独立 Verifier 的 Leader/Teammate/Subagent team runtime。

这个项目首先是 runtime kernel：在更高层产品 UI 之前，优先解决崩溃恢复、可审计副作用、有边界的 model/tool loop、本地仓库安全和检查入口。

## 为什么存在

很多 coding-agent 原型容易启动，但出错后很难信任。这个项目关注另一半问题：可恢复性、最小权限工具访问、运维可见性和本地控制。

Runtime 的设计目标是：即使 Run 经历进程崩溃、审批等待、验证失败、取消或 team rework，之后仍然可以被检查，而不是依赖隐藏的进程内存。

## 核心能力

- 通过 PostgreSQL 和 LangGraph checkpointing 实现持久化 Run intake、dispatch lease、Worker heartbeat、retry、cancellation 和 checkpoint resume。
- Repository-aware execution：allowed roots、registered repositories、clean base commit，以及每个 Run 的 managed worktree。
- Solo read-only 和 modifying AgentLoop route：受限 repository tools、Docker-backed shell execution、approval interrupt、validation gates 和 rework。
- Distributed team mode：模型规划 Teammates、assignment-scoped tools、Teammate-owned read-only Subagents、独立 Verifier review 和 targeted rework。
- Token 和 active-time budget ledger。Runtime 明确不做金额限制。
- 通过 query-table spans、model-call summaries、metrics、diagnostics、recovery metrics、trace IDs 和脱敏 API/CLI inspection 实现持久化观测。
- Project `skills/`、`awesome-agent.yaml`、MCP sources 和 community tool packages 的 extension catalog 基础，统一经过 capability resolution。

## 快速开始

完整说明见 [docs/getting-started/quickstart.md](docs/getting-started/quickstart.md)。目标启动 profile 契约见 [docs/design-docs/runtime-profiles-and-startup.md](docs/design-docs/runtime-profiles-and-startup.md)。

### 前置依赖

- Python 3.12
- `uv`
- Git
- Docker Desktop 或兼容 Docker engine
- 当前 helper scripts 使用 Windows PowerShell

### 配置

```powershell
Copy-Item .env.example .env
```

Provider secrets 放在 `.env`。当前产品构建只支持官方 DeepSeek provider 的 conversation turns。配置 `AWESOME_AGENT_DEEPSEEK_API_KEY`；产品 runtime 不支持自定义 DeepSeek-compatible base URL。

Extension source 配置放在 `awesome-agent.yaml`。Project skills 会从 `skills/` 发现。不要把 secrets 放进 `awesome-agent.yaml`。

### 选择运行模式

Makefile 命令是主要 API 启动契约。Docker API 使用 `make docker-init` 和 `make docker-start`；本地 API 开发使用 `make check`、`make install`、`make setup-sandbox` 和 `make dev`。本地交互式 CLI/TUI 使用 `awesome`，默认使用 embedded local runtime，不需要先启动 API server。

| 模式 | 适用场景 | 命令 | 状态 |
| --- | --- | --- | --- |
| Local CLI | 交互式本地 coding-agent 入口 | `awesome`, `awesome commands` | 主要入口 |
| Local API | 从宿主 Python 检查 API + Worker | `make check`, `make install`, `make setup-sandbox`, `make dev` | 主要入口 |
| Docker API/Web | 通过容器化 API 使用浏览器/API inspection | `make docker-init`, `make docker-start` | 主要入口 |
| Local CLI fallback | 首次本地运行和开发 | `.\scripts\quickstart.ps1` | 备用 |
| Docker CLI | 面向容器化 runtime 的 CLI inspection | `.\scripts\docker-quickstart.ps1` | 备用 |

当前 “Web” surface 是本地 FastAPI inspection surface 和生成的 API docs at `/docs`，还不是托管式多用户 Web 应用。

### Local CLI

首次启动本地 CLI：

```powershell
awesome init
cd E:\my-project
awesome
```

`awesome init` 会创建 `~/.awesome-agent/config.yaml`。这个文件只保存模型名称和环境变量名，不保存真实 API key。模型调用前，请把 `AWESOME_AGENT_DEEPSEEK_API_KEY` 设置在 shell、操作系统环境变量、密码管理器或本地 `.env` 文件中。

从项目目录运行 `awesome`：

```powershell
cd E:\my-project
awesome
awesome commands
```

启动目录会成为默认 thread context。如果目录是 Git checkout，Runs 会继承该 repository；如果不是 Git checkout，CLI 会使用 workspace-only mode，并且仍接受用户消息 turn。

`awesome` 是默认的 chat-first 本地 CLI/TUI。`awesome-agent` 子命令用于直接操作、diagnostics 和脚本化检查。

普通用户消息是唯一的产品执行创建路径。用户消息 turn 会创建内部 conversation Run 和 Leader Agent，然后通过 embedded local runtime 执行。只有当你明确希望 TUI 连接到某个本地、Docker 或远程 API server 时，才使用 `awesome --api-url http://127.0.0.1:8000`。

本地 TUI 是 chat-first 的：启动时显示欢迎面板，之后主界面聚焦 transcript 和输入框。运行细节可通过 `/status`、`/tools`、`/mcp`、`/usage`、`/config` 等 slash commands 按需查看。

| 命令 | 作用 |
| --- | --- |
| `/new` | 开启新的本地 conversation/thread。 |
| `/threads` | 切换 conversation。 |
| `/status` | 查看当前 thread、run 和 runtime 状态。 |
| `/model` | 先选择 provider，再选择当前 conversation 的模型。 |
| `/thinking` | 选择 thinking mode。 |
| `/skills` | 浏览 skills。 |
| `/tools` | 查看 built-in、MCP 和 sandbox tools。 |
| `/mcp` | 查看 MCP server 状态。 |
| `/memory` | 查看 memory 配置和当前 memory 摘要。 |
| `/details` | 切换详细活动渲染。 |
| `/usage` | 查看 token 使用和上下文。 |
| `/config` | 查看解析后的配置路径和覆盖项。 |
| `/help` | 查看帮助。 |
| `/quit` | 退出 TUI。 |

Slash commands 是 CLI/TUI 交互语法。API routes 暴露 threads、runs、models、memory、readiness 和 approvals 等语义资源，而不是按 slash command 命名的路由。

### 验证

先授权父目录并注册一个干净的 Git checkout：

```powershell
.\.venv\Scripts\awesome-agent.exe config root add <parent-directory>
.\.venv\Scripts\awesome-agent.exe repo add <repository-path>
```

不需要模型 key 也可以先验证 durable runtime：

```powershell
.\.venv\Scripts\awesome-agent.exe probe --repo <repository-path>
.\.venv\Scripts\awesome-agent.exe diagnostics <run-id>
```

`awesome-agent probe` 是 diagnostic 入口，用来验证 runtime 路径；它不是产品执行创建入口。

### 第一条模型驱动用户消息

在 `.env` 中设置 `AWESOME_AGENT_DEEPSEEK_API_KEY`，重启本地交互 runtime，在项目目录中打开 `awesome`，然后发送一条普通用户消息：

```text
Build a single-file HTML timer in this folder.
```

提交的默认配置会让 Leader 使用 `deepseek-v4-pro`，让 Teammate、Verifier 和 Subagent 使用 `deepseek-v4-flash`。可以通过 `AWESOME_AGENT_LEADER_MODEL`、`AWESOME_AGENT_TEAMMATE_MODEL`、`AWESOME_AGENT_VERIFIER_MODEL` 和 `AWESOME_AGENT_SUBAGENT_MODEL` 覆盖。

Distributed Leader、Teammate 和 Verifier 仍是 runtime 能力；聊天优先的产品入口和团队控制会按路线图继续完善，不伪装为当前 CLI 入口。

## 第一次运行

最快且安全的第一次运行是自动 quickstart：

```powershell
.\scripts\quickstart.ps1
```

它使用 diagnostic probe 作为必要成功检查。只有在配置 provider key 并明确想运行脚本的可选模型驱动验证路径时，才添加 `-RunReadOnly`。

Docker 路径：

```powershell
.\scripts\docker-quickstart.ps1
```

## 手动启动

```powershell
.\scripts\bootstrap.ps1
docker compose up -d postgres
.\scripts\migrate.ps1
.\.venv\Scripts\awesome-agent.exe doctor --profile api
.\.venv\Scripts\awesome-agent.exe start
```

API 默认绑定到 `http://127.0.0.1:8000`。`/health` 用于进程 liveness，`/ready?profile=api` 和 `/ready?profile=runtime` 用于依赖 readiness。

## 扩展

项目级 extension 配置放在 `awesome-agent.yaml`。它用于配置 project skill roots、MCP sources 等 extension sources，不用于保存 secrets。Provider keys 和 runtime settings 应放在 `.env` 或环境变量中。

Project skills 位于 `skills/`；每个 skill package 包含一个 `SKILL.md`。Skills 可以请求 instructions、context 和 tool capabilities，但它们本身不授予执行权限。MCP 和 community tools 会进入 extension catalog，并继续经过 exposure、capability、approval、budget、execution 和 observability 边界。

## 运维

常用本地运维命令：

```powershell
.\.venv\Scripts\awesome-agent.exe doctor --profile api --no-docker
.\.venv\Scripts\awesome-agent.exe doctor --profile runtime
.\.venv\Scripts\awesome-agent.exe diagnostics <run-id>
.\.venv\Scripts\awesome-agent.exe recovery-metrics <run-id>
.\.venv\Scripts\awesome-agent.exe budget <run-id>
.\.venv\Scripts\awesome-agent.exe context-compactions <run-id>
.\.venv\Scripts\awesome-agent.exe workspace list
.\.venv\Scripts\awesome-agent.exe workspace cleanup --run-id <run-id>
```

打开本地 TUI 操作控制台：

```powershell
.\.venv\Scripts\awesome-agent.exe tui
.\.venv\Scripts\awesome-agent.exe tui --run-id <run-id>
```

TUI 是基于本地 API 的 Run、诊断、事件和审批检查/控制界面，不是托管式 Web dashboard。

`awesome-agent start` 是备用/调试 supervisor，用于同时监看 API 和 Worker 进程。正常本地 API 开发请优先使用 `make dev`；如果需要由外部进程管理器分别管理它们，可以使用 `awesome-agent serve` 和 `awesome-agent worker`。本地 API 未内置认证，默认只绑定 loopback；绑定到非 loopback 地址需要显式 unsafe consent。

## 架构概览

目标架构是一个小而稳定的 durable kernel，外面围绕 policy 和 extension layers：

- API 和 CLI 负责 intake、inspection、approval、cancellation 和 operator commands。
- Worker 和 dispatch 负责 claim、lease、heartbeat、retry 和 execution ownership。
- Graph modules 负责 durable state transitions、checkpoints、interrupts、resume、child-run coordination 和 terminal projections。
- AgentLoop 负责一个 agent role 的 bounded model-to-tool loop。
- Middleware 和 hooks 负责 context assembly、observability、budget checks、permission checks、tool exposure、retries、error classification、validation 和 artifact offload。
- Capability resolution 是 tool exposure 和 execution 的权限来源。

详细契约见 [ARCHITECTURE.md](ARCHITECTURE.md) 和 [docs/design-docs/index.md](docs/design-docs/index.md)。

## 当前成熟度

项目适合本地开发和 runtime-kernel 迭代。它已经有真实的 durable execution、repository registration、Worker recovery、solo/team runtime paths、diagnostics、budgets 和 extension catalog foundations。

它还不是托管式多用户服务。生产部署、dashboards 和托管式产品工作流仍是路线图中的后续工作。

## 文档

- [文档地图](docs/README.md)
- [快速开始](docs/getting-started/quickstart.md)
- [用户指南](docs/user-guide/README.md)
- [运维指南](docs/operations/README.md)
- [架构](ARCHITECTURE.md)
- [设计文档](docs/design-docs/index.md)
- [安全](docs/SECURITY.md)
- [可靠性](docs/RELIABILITY.md)
- [Runtime 路线图](docs/project-governance/runtime-roadmap.md)
- [技术债跟踪](docs/project-governance/tech-debt-tracker.md)

## 安全提示

不要把 secrets 提交进仓库。Provider keys 和本机 runtime settings 放在 `.env`。API profile 的命令执行默认使用 `aio-docker` sandbox；LocalSandbox 只用于本地 CLI/TUI 或显式可信本地执行。

Thread 工作区会保存在 `~/.awesome-agent/threads/<thread_id>/workspace/`。生成文件会在 TUI 中显示为 workspace changes。内部 run evidence 可以保存在 `~/.awesome-agent/runs/<run_id>/artifacts/`，但用户通常直接使用 launch workspace/project 中的文件。LocalSandbox 只适用于 trusted-local 场景。
