# awesome_agent

[English](README.md) | [简体中文](README.zh-CN.md)

`awesome_agent` 是一个 local-first 的 Python coding agent runtime，目标是同时具备三类能力：可在进程崩溃和审批等待中恢复的持久化执行、可追溯的运行观测，以及带独立 Verifier 的 Leader/Teammate/Subagent 多 Agent 组织。

本文档描述当前已经实现的能力和仍在路线图中的能力，避免夸大项目现状。

## 当前状态

### 持久化运行时（已实现）

- repository-aware Run intake：CLI `--repo PATH` 会解析为已注册 repository UUID，经过 allowed-root 策略、干净 base commit、每个 Run 独立命名 Git worktree，以及可崩溃恢复的 intake reservation。
- PostgreSQL dispatch queue 支持 `FOR UPDATE SKIP LOCKED` claim、lease、heartbeat、单调递增 fencing token、delayed retry，以及 expired lease 到 `recovery_required` 的恢复。
- Worker 每次最多执行一个 Run，使用 `AsyncPostgresSaver` checkpoint，持有 lease heartbeat，并能在进程崩溃后从 checkpoint 恢复。
- Provider-neutral structured model turn protocol 已映射到 DeepSeek Chat Completions 和 OpenAI Responses；provider SDK 对象不会跨越 adapter 边界。
- `/health` 是轻量 liveness；`/ready?profile=api`、`/ready?profile=runtime` 和 `doctor --profile` 会检查 PostgreSQL、Alembic migration、LangGraph checkpoint、workspace、provider、model routes、API bind policy 和 Worker heartbeat。

### Coding 执行（已实现）

- **Read-only Coding Run** 通过 `solo-readonly` 执行，支持受限仓库工具、最多 4 路并发只读工具、模型驱动 tool/feedback 回边、无进展检测和证据门控完成。
- **Modifying Coding Run** 通过 `solo-modifying` 执行，支持 `repo.apply_patch`、`repo.diff`、Docker-backed `shell.execute` 和 `artifact.read`。写操作顺序执行，副作用工具调用持久化，超大工具输出卸载到 artifact storage。完成需要至少一个 patch、最后一次写后调用 `repo.diff`，并通过 required validation gates。
- **显式 Team Coding Run** 通过 CLI `--team` 或 API `mode: "team"` 选择。当前同时保留两个 team runtime：scoped `team-coding-scoped` 在一个 Run 和一个 checkpoint thread 内创建内部角色记录；distributed `team-coding` 会创建可由独立 Worker claim 的 Teammate 和 Verifier child Runs。分布式 Leader 现在会调用模型生成并验证结构化 `TeamPlan`，再从中创建 1-3 个 Teammates。Teammate child Runs 现在会执行 assignment-scoped model/tool loop，并且只暴露 Leader 授权后的 effective tools；具备 delegation 权限的 Teammate 可以创建 read-only Subagent child Runs。Verifier child Run 现在使用模型结构化决策；targeted rework 属于后续 Task 22 阶段。

### 审批（已在 solo modifying run 中实现）

`solo-modifying` 已接入持久化 approval interrupt/resume。模糊 shell 命令会创建 `approvals` 记录，checkpoint graph，释放 worker lease 为 `paused + waiting`，并在 API 或 CLI approve/deny 后通过 `Command(resume=...)` 恢复。危险 shell 命令会直接拒绝，不创建 approval。

### 多 Agent（已实现 scoped 和 distributed runtime）

Intake 初始只创建 Leader。选择 `--team` 或 API `mode: "team"` 后，当前默认路由到 distributed `team-coding`：Leader 创建模型生成的结构化 `TeamPlan`，验证后从计划中创建 Teammate child Runs。Leader 不创建 Subagents，也不能描述 Subagent 任务方向，只能授予 `can_delegate` 和 `max_subagents`。Teammate 获得 delegation 权限后，可按需创建 read-only Subagent child Runs。Leader 在最终完成前创建独立 Verifier child Run。Verifier 必须验收通过后，Leader 才能完成 root Run。

旧的 scoped `team-coding-scoped` runtime 仍保留文档和测试，但新的 distributed path 是后续架构方向。模型驱动 Leader planning、assignment-scoped Teammate role loop、Teammate-owned Subagent delegation 和模型结构化 Verifier review 已经开始；targeted rework 仍在后续 Task 22 阶段。分布式 team assignment 支持 deferred tool exposure、跨 Leader/Teammate/Verifier/Subagent 的 root-aware token/active-time budget 检查，以及对大型 handoff、child result、verifier evidence payload 的 artifact-backed compaction。

### 可观测性（已实现）

Runtime 会记录持久化 query-table 证据，包括 run/graph/model/tool/sandbox span、model-call 摘要，以及 run/model/tool latency 等 metrics。Runtime event 会写入稳定的 Run 级 `trace_id`。FastAPI 提供 `GET /runs/{run_id}/trace`、`GET /runs/{run_id}/metrics` 和 `GET /runs/{run_id}/model-calls`。当前生产 Worker 路径使用项目自有的持久化记录，而不是完整 OpenTelemetry span instrumentation；完整 OTel 覆盖、cost budget 和 dashboard 仍是后续工作。

### 上下文与预算管理（已实现）

Solo read-only 和 modifying 图现在会限制 prompt/checkpoint 增长。超过 soft context limit 后，旧消息和超大工具观察会写入 artifact，checkpoint 中只保留确定性摘要和最近证据，并记录 `context.compacted` 事件。Hard context pressure 会强制进入 bounded final no-tool answer。

每个 Run 都有 token ledger，记录 input/output/reasoning tokens、model call count 和 Worker active execution seconds。FastAPI 提供 `GET /runs/{run_id}/budget` 与 `GET /runs/{run_id}/context-compactions`；CLI 提供 `awesome-agent budget <run-id>` 与 `awesome-agent context-compactions <run-id>`。

Distributed Team Run 会在 root 范围内聚合 Leader、Teammate、Verifier 和 Subagent 的预算账本，并在图边界执行 token/active-time budget 检查。大型 team handoff、child result 和 verifier evidence payload 会卸载到 artifact，并通过 `context_compactions` 记录。Money cost budget 仍延后实现。

## 技术栈

- Python 3.12 和 `uv`
- LangGraph，加项目自有 orchestration 和 provider interfaces
- 默认模型 provider 为 DeepSeek Chat Completions，也映射 OpenAI Responses
- PostgreSQL 和 LangGraph PostgreSQL checkpointing
- Typer CLI 和本地 FastAPI API
- 默认 Docker sandbox，CLI 可显式启用 trusted-local
- 持久化 query-table observability，不使用 LangSmith

## 模型配置

默认模型配置如下：

| 角色 | 模型 |
| --- | --- |
| Leader | `deepseek-v4-pro` |
| Teammate | `deepseek-v4-flash` |
| Verifier | `deepseek-v4-flash` |
| Subagent | `deepseek-v4-flash` |

真实模型调用前，需要在被 Git 忽略的本地 `.env` 中配置 `AWESOME_AGENT_DEEPSEEK_API_KEY`。内置记忆和 Mem0 默认关闭；可设置 `AWESOME_AGENT_BUILTIN_MEMORY_ENABLED=true` 和 `AWESOME_AGENT_MEM0_ENABLED=true` 开启，Mem0 还需要 `AWESOME_AGENT_MEM0_API_KEY`。

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

诊断和查询示例：

```powershell
.\.venv\Scripts\awesome-agent.exe probe --repo E:\projects\example
.\.venv\Scripts\awesome-agent.exe doctor --profile runtime
.\.venv\Scripts\awesome-agent.exe budget <run-id>
.\.venv\Scripts\awesome-agent.exe context-compactions <run-id>
```

本地 PostgreSQL 默认配置：

```text
database: awesome_agent
username: awesome_agent
password: awesome_agent
host port: 54329
container port: 5432
```

## 前端演示

静态演示页面不连接后端：

```powershell
.\.venv\Scripts\python.exe -m http.server 4173 -d demo
```

打开 `http://127.0.0.1:4173`。页面包含 mock Agent topology、Todo、event trace、per-Agent context、command approval、artifact 和移动端导航。这是 UI 原型，不是正在运行的多 Agent 系统。

## 路线图

持久化 runtime 工作记录在 [docs/project-governance/runtime-roadmap.md](docs/project-governance/runtime-roadmap.md)。尚未实现的重点项：

- targeted rework 和更丰富的 mailbox 协作策略。
- Money cost budget 和 dashboard。

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
