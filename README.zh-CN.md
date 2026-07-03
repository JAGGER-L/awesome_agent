# awesome_agent

[English](README.md) | [简体中文](README.zh-CN.md)

`awesome_agent` 是一个 local-first coding-agent runtime，用于执行可持久化、可观测、受权限约束的本地代码任务。

## 项目是什么

`awesome_agent` 通过 Typer CLI、本地 FastAPI API、PostgreSQL 持久化状态和 Worker 进程，在本地 Git 仓库中运行 coding task。它支持 solo read-only、solo modifying，以及带独立 Verifier 的 Leader/Teammate/Subagent team runtime。

这个项目首先是 runtime kernel：在更高层产品 UI 之前，优先解决崩溃恢复、可审计副作用、有边界的 model/tool loop、本地仓库安全和检查入口。

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

Provider secrets 放在 `.env`。默认模型 provider 设置包括 `AWESOME_AGENT_DEEPSEEK_API_KEY`、`AWESOME_AGENT_DEEPSEEK_BASE_URL`、`AWESOME_AGENT_DEEPSEEK_PRO_MODEL` 和 `AWESOME_AGENT_DEEPSEEK_FLASH_MODEL`。

Extension source 配置放在 `awesome-agent.yaml`。Project skills 会从 `skills/` 发现。不要把 secrets 放进 `awesome-agent.yaml`。

### 选择启动模式

Makefile 命令是主要 API 启动契约。Docker API 使用 `make docker-init` 和 `make docker-start`；本地 API 开发使用 `make check`、`make install`、`make setup-sandbox` 和 `make dev`。本地交互式 CLI/TUI 使用 `awesome`，默认使用 embedded local runtime，不需要先启动 API server。

| Mode | Best for | Command | Status |
| --- | --- | --- | --- |
| Local CLI | Interactive local coding-agent entrypoint | `awesome`, `awesome commands` | Primary |
| Local API | API + Worker inspection from host Python | `make check`, `make install`, `make setup-sandbox`, `make dev` | Primary |
| Docker API/Web | Browser/API inspection against containerized API | `make docker-init`, `make docker-start` | Primary |
| Local CLI fallback | First local run and development | `.\scripts\quickstart.ps1` | Fallback |
| Docker CLI | Containerized runtime with CLI-driven inspection | `.\scripts\docker-quickstart.ps1` | Fallback |

当前 “Web” surface 是本地 FastAPI inspection surface 和生成的 API docs at `/docs`，还不是 hosted multi-user web application。

### Local CLI

首次启动本地 CLI：

```powershell
awesome init
cd E:\my-project
awesome
```

`awesome init` 会创建 `~/.awesome-agent/config.yaml`。这个文件只保存模型名称和环境变量名，不保存真实 API key。模型调用前，请把 `AWESOME_AGENT_DEEPSEEK_API_KEY` 设置在 shell、操作系统环境变量、密码管理器或本地 `.env` 文件中。

在你希望 agent 工作的项目目录中启动：

```powershell
cd E:\my-project
awesome
awesome commands
```

启动目录会成为当前 thread 的默认上下文。Plain user messages are the only product execution creation path. A user message turn creates an internal conversation Run with a Leader Agent and executes through the embedded local runtime. 只有当你明确希望 TUI 连接到某个 API server 时，才使用 `awesome --api-url http://127.0.0.1:8000`。

本地 TUI 是 chat-first 的：启动时显示欢迎面板，之后主界面保留 transcript 和输入框。运行细节通过 `/status`、`/tools`、`/mcp`、`/usage`、`/config` 等 slash commands 按需查看。

| Command | Purpose |
| --- | --- |
| `/new` | 开启新的本地 thread。 |
| `/threads` | 切换 conversation。 |
| `/status` | 查看当前 thread、run、runtime 状态。 |
| `/model` | 选择当前 conversation 的模型。 |
| `/thinking` | 选择 thinking mode。 |
| `/skills` | 浏览 skills。 |
| `/tools` | 查看 built-in、MCP 和 sandbox tools。 |
| `/mcp` | 查看 MCP server 状态。 |
| `/memory` | 查看 memory 状态。 |
| `/details` | 切换详细活动日志。 |
| `/usage` | 查看 token 使用和上下文。 |
| `/config` | 查看配置路径和覆盖项。 |
| `/help` | 查看帮助。 |
| `/quit` | 退出 TUI。 |

### 自动启动

```powershell
.\scripts\quickstart.ps1
```

这个脚本会启动本地依赖、执行 migrations、启动 API + Worker、创建被忽略的 sample repository、用 diagnostic probe 验证 runtime，并打印检查步骤。除非传入 `-RunReadOnly`，否则不需要模型 key。

Docker lane:

```powershell
.\scripts\docker-quickstart.ps1
```

### 手动启动

```powershell
.\scripts\bootstrap.ps1
docker compose up -d postgres
.\scripts\migrate.ps1
.\.venv\Scripts\awesome-agent.exe doctor --profile api
.\.venv\Scripts\awesome-agent.exe start
```

API 默认绑定到 `http://127.0.0.1:8000`。`/health` 用于进程 liveness，`/ready?profile=api` 和 `/ready?profile=runtime` 用于依赖 readiness。

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

### 第一条 Model-Backed User Message

在 `.env` 中设置 `AWESOME_AGENT_DEEPSEEK_API_KEY`，重启本地交互 runtime，在项目目录中打开 `awesome`，然后发送一条 plain user message：

```text
Build a single-file HTML timer in this folder.
```

提交的默认配置会让 Leader 使用 `deepseek-v4-pro`，让 Teammate、Verifier 和 Subagent 使用 `deepseek-v4-flash`。可以通过 `AWESOME_AGENT_LEADER_MODEL`、`AWESOME_AGENT_TEAMMATE_MODEL`、`AWESOME_AGENT_VERIFIER_MODEL` 和 `AWESOME_AGENT_SUBAGENT_MODEL` 覆盖。

Distributed Leader、Teammate 和 Verifier 仍是 runtime 能力；chat-first product entry 和 team controls 会按 roadmap 继续完善。

## 第一次运行

最快且安全的第一次运行是自动 quickstart：

```powershell
.\scripts\quickstart.ps1
```

它使用 diagnostic probe 作为必要成功检查。只有在配置 provider key 并明确想运行脚本的可选 model-backed validation path 时，才添加 `-RunReadOnly`。

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

`awesome-agent start` 是 fallback/debug supervisor，用于同时监看 API 和 Worker 进程。正常本地 API 开发请优先使用 `make dev`；如果需要由外部进程管理器分别管理它们，可以使用 `awesome-agent serve` 和 `awesome-agent worker`。本地 API 未内置认证，默认只绑定 loopback；绑定到非 loopback 地址需要显式 unsafe consent。

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

它还不是 hosted multi-user service。生产部署、dashboards 和 hosted product workflows 仍是 roadmap 中的后续工作。

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

Thread workspaces persist under `~/.awesome-agent/threads/<thread_id>/workspace/`. Generated files are shown as workspace changes in the TUI. Internal run evidence can persist under `~/.awesome-agent/runs/<run_id>/artifacts/`, but users normally interact with files in the launch workspace/project. LocalSandbox is trusted-local only.
