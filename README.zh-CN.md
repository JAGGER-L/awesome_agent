# awesome_agent

[English](README.md) | [简体中文](README.zh-CN.md)

`awesome_agent` 是一个 local-first 的 Python coding agent runtime，目标是同时具备三类能力：可在进程崩溃和审批等待中恢复的持久化执行、可追溯的运行观测，以及带独立 Verifier 的 Leader/Teammate/Subagent 多 Agent 组织。

本文档说明当前已经实现的能力和仍在路线图中的能力，避免夸大项目现状。

## 当前状态

### 持久化运行时（已实现）

持久化执行基础已经落地，并通过 PostgreSQL 集成测试覆盖：

- repository-aware Run intake：CLI `--repo PATH` 会解析为已注册 repository UUID，经过 allowed-root 策略、干净 base commit、每个 Run 独立命名 Git worktree，以及可崩溃恢复的 intake reservation。
- PostgreSQL dispatch queue 支持 `FOR UPDATE SKIP LOCKED` claim、lease、heartbeat、单调递增 fencing token、delayed retry，以及 expired lease 到 `recovery_required` 的恢复。
- Worker 每次最多执行一个 Run，使用 `AsyncPostgresSaver` checkpoint，持有 lease heartbeat，并能在进程崩溃后从 checkpoint 恢复。
- provider-neutral structured model turn protocol 已映射到 DeepSeek Chat Completions 和 OpenAI Responses；provider SDK 对象不会跨越 adapter 边界。
- 跨进程 SSE 基于有序 PostgreSQL event polling，而不是进程本地状态。
- 面向前端的 Run、Agent、Todo 生命周期投影会同步维护 status、revision、timestamp 和匹配的 runtime event。
- managed execution workspace retention：`workspace list` 和默认 dry-run 的 `workspace cleanup` 可以检查并安全删除 awesome_agent 拥有的 inactive worktree 和 integration branch。
- dependency-aware readiness：`/health` 保持轻量进程 liveness，`/ready?profile=api`、`/ready?profile=runtime` 和 `doctor --profile` 会检查 PostgreSQL、Alembic migration、LangGraph checkpoint、workspace 可写性、provider 配置、model routes、API bind policy 和新鲜 Worker heartbeat。

### Coding 执行（已实现）

- **Read-only Coding Run** 通过带 checkpoint 的 `solo-readonly@1` Agent loop 执行，支持受限仓库工具、最多 4 路并发只读工具、模型驱动的 tool/feedback 回边、收敛反馈、无进展检测，以及证据门控的完成。
- **Modifying Coding Run** 路由到 `solo-modifying@1`，支持 `repo.apply_patch`、`repo.diff`、Docker-backed `shell.execute` 和 `artifact.read`。写操作顺序执行，有副作用工具调用持久化，超大工具输出卸载到 artifact storage。完成需要至少一个 patch、最后一次写后调用 `repo.diff`，并通过 `.agents/validation.toml` 或保守项目检测得到的 required validation gates。
- **显式 Team Coding Run** 只有在 CLI 使用 `--team` 或 API 使用 `mode: "team"` 时才路由到 `team-coding@1`。Task 13 v1 保持一个 Run、一个 Worker、一个 checkpoint thread，并在图内部创建持久化的 Leader、Teammate、Verifier 和 Subagent 记录。Verifier 拒绝后可触发 rework，真实 E2E 覆盖 Worker、PostgreSQL、checkpoint、provider protocol、repository tools、validation records 和 observability evidence。

### 审批（已在 solo modifying run 中实现）

`solo-modifying@1` 已接入持久化 approval interrupt/resume。模糊 shell 命令会创建 `approvals` 记录，checkpoint graph，释放 worker lease 为 `paused + waiting`，并在 API 或 CLI approve/deny 后通过 `Command(resume=...)` 恢复。危险 shell 命令会直接拒绝，不创建 approval。

### 多 Agent（已实现受限 v1）

持久化 team runtime 是显式且受限的。Intake 初始只创建 Leader。选择 `--team` 或 API `mode: "team"` 后，`team-coding@1` 图会在同一个 Run 内创建 Teammate、一个 Verifier 和有界 Subagent。Subagent 具有独立上下文，只向自己的 Teammate 返回证据。Verifier 必须验收通过后，Leader 才能完成 Run。

这个 v1 还不是未来的分布式 team 架构；未来 Leader 会创建 Teammate child Runs，并由独立 Worker 分别 claim。

### 可观测性（已实现）

Runtime 会记录持久化 query-table 证据，包括 run/graph/model/tool/sandbox span、model-call 摘要，以及 run/model/tool latency 等 metrics。Runtime event 会写入稳定的 Run 级 `trace_id`，OpenTelemetry console exporter 已做失败隔离，FastAPI 提供 `GET /runs/{run_id}/trace`、`GET /runs/{run_id}/metrics` 和 `GET /runs/{run_id}/model-calls` 供前端检查。完整 cost budget 和 dashboard 仍属于后续工作。

## 技术栈

- Python 3.12 和 `uv`
- LangGraph，加项目自有 orchestration 和 provider interfaces
- 默认模型 provider 为 DeepSeek Chat Completions，也映射 OpenAI Responses
- PostgreSQL 和 LangGraph PostgreSQL checkpointing
- Typer CLI 和本地 FastAPI API
- 默认 Docker sandbox，CLI 可显式启用 trusted-local
- OpenTelemetry，不使用 LangSmith
- 可选内置记忆和可选 Mem0 Platform 集成

## 模型配置

默认模型配置如下：

| 角色 | 模型 |
| --- | --- |
| Leader | `deepseek-v4-pro` |
| Teammate | `deepseek-v4-flash` |
| Verifier | `deepseek-v4-flash` |
| Subagent | `deepseek-v4-flash` |

可通过 `AWESOME_AGENT_LEADER_MODEL`、`AWESOME_AGENT_TEAMMATE_MODEL`、`AWESOME_AGENT_VERIFIER_MODEL`、`AWESOME_AGENT_SUBAGENT_MODEL` 修改默认值，也可用 `AWESOME_AGENT_ROLE_MODEL_OVERRIDES` 的 JSON 覆盖具体 profile。未配置 DeepSeek API key 时，Coding claim 会被禁用。

## 本地启动

```powershell
.\scripts\bootstrap.ps1
Copy-Item .env.example .env
docker compose up -d postgres
.\scripts\migrate.ps1
.\scripts\check.ps1
.\scripts\system-test.ps1
.\.venv\Scripts\awesome-agent.exe doctor --profile api
.\.venv\Scripts\awesome-agent.exe start
```

创建 Run 前，先授权本地父目录并注册干净的主 Git checkout：

```powershell
.\.venv\Scripts\awesome-agent.exe config root add E:\projects
.\.venv\Scripts\awesome-agent.exe repo add E:\projects\example
.\.venv\Scripts\awesome-agent.exe run "检查 parser" --repo E:\projects\example --read-only
.\.venv\Scripts\awesome-agent.exe run "用 team 实现这个功能" --repo E:\projects\example --team
```

`run --repo` 只会在仓库位于 allowed root 下时注册或刷新仓库。CLI 向 FastAPI 发送 repository UUID；API 不接受任意 filesystem path。read-only 和 modifying Run 都要求原 checkout 干净，并基于捕获的 base commit 创建稳定 worktree。普通 `run` 命令会创建 modifying Coding Run；使用 `--read-only` 可禁用修改工具。Modifying Run 只有在 required validation gates 通过后才会完成。

可用 diagnostic probe 验证 Worker、lease、LangGraph checkpoint 和跨进程事件链路，而不执行 coding goal：

```powershell
.\.venv\Scripts\awesome-agent.exe probe --repo E:\projects\example
```

`awesome-agent start` 会监督相互独立的 API 和 Worker 子进程。需要分别管理进程时仍可使用 `serve` 和 `worker`。本地 FastAPI API 默认无认证且只绑定 `127.0.0.1`。绑定到非 loopback host 时必须显式传入 `--unsafe-bind-public`。同样的 bind policy 也会在 API settings 路径检查，因此直接 ASGI hosting 如果要公开绑定，需要设置 `AWESOME_AGENT_API_HOST` 和 `AWESOME_AGENT_UNSAFE_BIND_PUBLIC=true`。

Health 和 readiness 被明确拆分：

```text
GET /health                  # API 进程 liveness；进程能响应即 200
GET /ready?profile=api       # API 依赖 readiness
GET /ready?profile=runtime   # API readiness + provider/model/Worker 检查
```

`healthy` 和 `degraded` readiness 返回 HTTP 200；`unhealthy` 返回 HTTP 503。CLI 诊断使用同一套检查：

```powershell
.\.venv\Scripts\awesome-agent.exe doctor --profile api --no-docker
.\.venv\Scripts\awesome-agent.exe doctor --profile runtime
```

`doctor` 在 `healthy` 和 `degraded` 时退出码为 0，在 `unhealthy` 时退出码为 1。

可通过 `GET /runs/{run_id}/dispatch` 查看 dispatch state。queued、retry-scheduled、waiting-approval、claimed 和 executing 的 solo Run 都支持持久化取消。active cancellation 会先记录 durable request，由持有 lease 的 Worker 观察，并在 graph 和 subprocess 边界干净停止后提交为 `cancelled + terminal`。

可以显式检查和清理 managed execution workspace：

```powershell
.\.venv\Scripts\awesome-agent.exe workspace list
.\.venv\Scripts\awesome-agent.exe workspace cleanup --run-id <run-id>
.\.venv\Scripts\awesome-agent.exe workspace cleanup --run-id <run-id> --apply
.\.venv\Scripts\awesome-agent.exe workspace cleanup --older-than 14d --apply
```

cleanup 默认只是 preview。普通 cleanup 只会删除 terminal completed 或 cancelled Run 的干净 managed workspace。failed 或 dirty workspace 需要 `--force --reason`；`recovery_required` workspace 会作为恢复证据保留。

真实模型调用前，需要在被 Git 忽略的本地 `.env` 中配置 `AWESOME_AGENT_DEEPSEEK_API_KEY`。内置记忆和 Mem0 在提交默认配置中关闭；本地可设置 `AWESOME_AGENT_BUILTIN_MEMORY_ENABLED=true` 和 `AWESOME_AGENT_MEM0_ENABLED=true` 开启，Mem0 还需要 `AWESOME_AGENT_MEM0_API_KEY`。

## 前端演示

静态演示页面不连接后端：

```powershell
.\.venv\Scripts\python.exe -m http.server 4173 -d demo
```

打开 `http://127.0.0.1:4173`。页面包含 mock Agent topology、Todo、event trace、per-Agent context、command approval、artifact 和移动端导航。这是 UI 原型，不是正在运行的多 Agent 系统。

本地 PostgreSQL 默认配置：

```text
database: awesome_agent
username: awesome_agent
password: awesome_agent
host port: 54329
container port: 5432
```

## 路线图

持久化 runtime 工作记录在 [docs/project-governance/runtime-roadmap.md](docs/project-governance/runtime-roadmap.md)。尚未实现的重点项：

- 完整 token-window、wall-clock 和 cost budget 管理（Task 16）；
- 由独立 Worker claim 的分布式 Teammate child Runs（Task 17）。

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
- [Runtime 路线图](docs/project-governance/runtime-roadmap.md)
- [技术债跟踪](docs/project-governance/tech-debt-tracker.md)

英文和中文 README 必须同步维护。
