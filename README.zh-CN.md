# awesome_agent

[English](README.md) | [简体中文](README.zh-CN.md)

`awesome_agent` 是一个本地优先的 Python Coding Agent 运行时，目标有三个：
**可持久运行**（能在进程崩溃和审批等待中存活）、**可观测**、以及由
Leader/Teammate/Subagent 加独立 Verifier 组成的**多 Agent**组织。

本 README 如实说明当前哪些能力已落地、哪些仍是原型或规划中，避免夸大项目实际能力。

## 当前状态

### 持久化运行时（已实现）

持久化执行地基是真实的，并有针对 PostgreSQL 的集成测试：

- 仓库感知 Run intake：CLI `--repo PATH` 解析为已注册的仓库 UUID、允许根目录策略、
  每个 Run 基于干净 base commit 创建具名 Git worktree、崩溃可恢复的 intake 预留；
- PostgreSQL 调度队列，支持 `FOR UPDATE SKIP LOCKED` 抢占、lease、heartbeat、单调
  递增的 fencing token、延迟 retry、过期 lease 恢复为 `recovery_required`；
- 持久化 Worker 单次最多执行一个 Run，用 `AsyncPostgresSaver` 做 checkpoint，
  心跳续约，并在进程崩溃后从 checkpoint 恢复（有真实子进程崩溃恢复测试佐证）；
- Provider 中性的结构化 model turn 协议（消息、原生 tool call、流式
  reasoning/text delta、stop reason、usage、仅用于 checkpoint 的私有 continuation），
  已为 DeepSeek Chat Completions 和 OpenAI Responses 做映射；Provider SDK 对象不跨
  适配器边界；
- 跨进程 SSE 基于有序 PostgreSQL 事件轮询，而非进程本地状态。

### Coding 执行（已实现，仅 solo）

- **Read-only Coding Run** 通过带 checkpoint 的 `solo-readonly@1` Agent loop 执行，
  提供受限仓库工具（`repo.status`、`repo.list`、`repo.search`、`repo.read`、
  `repo.instructions`），最多 4 路并发只读调用且结果顺序确定，模型驱动的
  tool/feedback 回边，收敛反馈，无进展检测，以及证据门控的完成。有确定性的
  PostgreSQL + fake provider E2E 测试覆盖完整循环。
- **Modifying Coding Run** 路由到 `solo-modifying@1`，新增 `repo.apply_patch`、
  `repo.diff`、Docker 后端的 `shell.execute` 和 `artifact.read`，顺序执行写操作，
  将超大工具输出卸载到 artifact 存储，并用幂等元数据持久化有副作用的工具调用。
  完成要求至少应用一个 patch 且最后一次写之后调用 `repo.diff`，完成状态记为
  `modifying_unvalidated`——确定性验证和返工属于 Task 10 规划，当前不声称已实现。

### 审批（已在 solo modifying run 中实现）

`solo-modifying@1` 已接入持久化审批中断与恢复，V1 只批准一个确切工具调用。
有歧义的 shell 命令会创建 `approvals` 记录、写入 graph checkpoint、将 worker
lease 释放为 `paused + waiting`，并在 API 或 CLI 批准/拒绝后通过
`Command(resume=...)` 恢复。危险 shell 命令会直接拒绝，不创建审批。

### 多 Agent（原型，尚未持久化）

Leader/Teammate/Subagent/Verifier 组织、mailbox、任务板和验收协调器作为内存数据
结构存在于 `src/awesome_agent/orchestration/`，但**未接入**持久化 Worker 执行路径。
目前没有 team 模式图；Worker 只会领取 `runtime_probe`、`solo-readonly@1`、
`solo-modifying@1`。Leader 可以报告某任务"需要 team 模式"，但 team 执行尚未端到端
跑通。真实 team 运行时执行属于 Task 13 规划。下方模型分配表反映的是预期的 team 角色，
而非正在运行的 teammates。

### 可观测性（脚手架，尚未真实）

OpenTelemetry 已配置（console exporter），不可变运行时事件、SSE 和 REST 查询接口
已存在，但尚未发出任何 span 或 metric，`trace_id`/`span_id` 未填充，cost 和 latency
未跟踪，`/health` 返回静态存活响应。完整可观测性属于 Task 12 规划。

## 技术栈

- Python 3.12 和 `uv`
- LangGraph，以及项目自有的编排和 provider 接口
- 默认使用 DeepSeek Chat Completions（同时映射了 OpenAI Responses）
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
profile。未配置 DeepSeek API key 时禁用 Coding 抢占。

## 本地启动

```powershell
.\scripts\bootstrap.ps1
Copy-Item .env.example .env
docker compose up -d postgres
.\scripts\migrate.ps1
.\scripts\check.ps1
.\scripts\system-test.ps1
.\.venv\Scripts\awesome-agent.exe doctor
.\.venv\Scripts\awesome-agent.exe start
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
worktree。当前普通 `run` 命令会创建 modifying Coding Run；使用 `--read-only`
可禁用修改工具。Modifying 完成不等于验证通过，确定性检查和 rework 仍属于 Task 10。

可以创建诊断 Probe 来验证 Worker、lease、LangGraph checkpoint 和跨进程事件
链路，而不会执行 Coding 目标：

```powershell
.\.venv\Scripts\awesome-agent.exe probe --repo E:\projects\example
```

`awesome-agent start` 会监督相互独立的 API 和 Worker 子进程。需要分别管理
进程时仍可使用 `serve` 和 `worker`。本地 FastAPI API 没有认证，默认只绑定
`127.0.0.1`。如果要将 `serve` 或 `start` 绑定到非 loopback host，必须显式传入
`--unsafe-bind-public`。

可以通过 `GET /runs/{run_id}/dispatch` 查看调度状态。queued、retry-scheduled、
waiting-approval、claimed 和 executing 的 solo Run 都支持持久化取消。active
取消会先记录为 durable request，由持有 lease 的 Worker 观察，在 graph 和 subprocess
边界干净停止后提交为 `cancelled + terminal`。

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
Agent 上下文、命令审批、产物和移动端导航。这是一个 UI 原型，不是正在运行的
多 Agent 系统。

本地 PostgreSQL 默认配置：

```text
database: awesome_agent
username: awesome_agent
password: awesome_agent
host port: 54329
container port: 5432
```

## 路线图

持久化运行时工作记录在
[docs/project-governance/runtime-roadmap.md](docs/project-governance/runtime-roadmap.md)。
尚未实现的重点项：

- 变更输出的确定性验证与返工（Task 10）；
- 生命周期投影一致性（Task 11）；
- 真实的 run/model/tool/sandbox span、metrics、cost 和 latency（Task 12）；
- 真实 team 运行时端到端执行（Task 13）；
- worktree 和 branch 保留与清理（Task 14）；
- 依赖感知的 `/health` 和 `doctor`（Task 15）。

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
- [运行时路线图](docs/project-governance/runtime-roadmap.md)
- [技术债跟踪](docs/project-governance/tech-debt-tracker.md)

英文与中文 README 必须同步维护。
