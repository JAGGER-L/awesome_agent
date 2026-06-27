# awesome_agent

[English](README.md) | [简体中文](README.zh-CN.md)

`awesome_agent` is a local-first Python coding agent runtime being built toward
three goals: **durable** execution that survives process failure and approval
waits, **observable** runs, and a **multi-agent** Leader/Teammate/Subagent
organization with an independent Verifier.

This README describes what works today and what is still prototype or planned,
so the project's actual capability is not overstated.

## Current Status

### Durable runtime (implemented)

The durable execution foundation is real and integration-tested against
PostgreSQL:

- repository-aware Run intake: CLI `--repo PATH` resolves to a registered
  repository UUID, allowed-root policy, clean-base named Git worktree per Run,
  crash-recoverable intake reservations;
- PostgreSQL dispatch queue with `FOR UPDATE SKIP LOCKED` claims, leases,
  heartbeats, monotonically increasing fencing tokens, delayed retry, and
  expired-lease recovery to `recovery_required`;
- a durable Worker that executes at most one Run at a time, checkpoints with
  `AsyncPostgresSaver`, heartbeats the lease, and resumes from the checkpoint
  after a process crash (verified by a real subprocess-crash recovery test);
- a provider-neutral structured model turn protocol (messages, native tool
  calls, streamed reasoning/text deltas, stop reasons, usage, private
  checkpoint-only continuation) mapped for DeepSeek Chat Completions and OpenAI
  Responses; provider SDK objects never cross adapter boundaries;
- cross-process SSE backed by ordered PostgreSQL event polling, not process-local
  state.
- frontend-ready Run, Agent, and Todo lifecycle projections: visible status
  transitions update projection rows, Agent/Todo revisions, timestamps, and
  matching runtime events together.

### Coding execution (implemented, solo only)

- **Read-only Coding Runs** execute through the checkpointed `solo-readonly@1`
  Agent loop with bounded repository tools (`repo.status`, `repo.list`,
  `repo.search`, `repo.read`, `repo.instructions`), up to four concurrent
  read-only calls with deterministic order, model-driven tool/feedback back
  edges, convergence feedback, no-progress detection, and evidence-gated
  completion. A deterministic PostgreSQL + fake-provider E2E test covers the
  full loop.
- **Modifying Coding Runs** route to `solo-modifying@1`, add `repo.apply_patch`,
  `repo.diff`, Docker-backed `shell.execute`, and `artifact.read`, execute
  writes sequentially, offload oversized tool output to artifact storage, and
  persist side-effecting tool invocations with idempotency metadata. Completion
  requires at least one applied patch, a `repo.diff` after the last write, and
  passing required validation gates from `.agents/validation.toml` or
  conservative project detection. Failed required gates feed a bounded rework
  loop; exhausted or non-reworkable validation failure marks the Run failed.

### Approval (implemented for solo modifying runs)

Durable approval interrupt and resume is wired into `solo-modifying@1` for one
exact invocation at a time. Ambiguous shell commands create an `approvals`
record, checkpoint the graph, release the worker lease as `paused + waiting`,
and resume through `Command(resume=...)` after the API or CLI approves or
denies the request. Unsafe shell commands are denied without creating an
approval.

### Multi-agent (prototype, not yet durable)

The Leader/Teammate/Subagent/Verifier organization, mailbox, task board, and
verification coordinator exist as in-memory data structures in
`src/awesome_agent/orchestration/`, but are **not** wired into the durable
Worker execution path. There is no team-mode graph; the Worker only claims
`runtime_probe`, `solo-readonly@1`, and `solo-modifying@1` runs. A Leader may
report that a task "requires team mode," but team execution does not run
end-to-end yet. Real team runtime execution is planned (Task 13). The model
assignment table below reflects the intended team roles, not running teammates.

### Observability (implemented for solo runtime)

Solo runtime observability now records durable query-table evidence for
run/graph/model/tool/sandbox spans, model-call summaries, and metrics such as
run, model, and tool latency. Runtime events receive a stable Run-scoped
`trace_id`, OpenTelemetry console export is failure-isolated, and FastAPI
exposes `GET /runs/{run_id}/trace`, `GET /runs/{run_id}/metrics`, and
`GET /runs/{run_id}/model-calls` for frontend inspection. Full cost budgeting,
dashboards, and dependency-aware `/health` remain later work.

## Stack

- Python 3.12 and `uv`
- LangGraph with project-owned orchestration and provider interfaces
- DeepSeek Chat Completions as the default model provider (OpenAI Responses also mapped)
- PostgreSQL and LangGraph PostgreSQL checkpointing
- Typer CLI and local FastAPI API
- Docker sandbox with explicit trusted-local opt-in
- OpenTelemetry without LangSmith
- optional built-in memory and optional Mem0 Platform integration

## Model Configuration

The committed defaults are:

| Role | Model |
| --- | --- |
| Leader | `deepseek-v4-pro` |
| Teammate | `deepseek-v4-flash` |
| Verifier | `deepseek-v4-flash` |
| Subagent | `deepseek-v4-flash` |

Assignments can be changed with `AWESOME_AGENT_LEADER_MODEL`,
`AWESOME_AGENT_TEAMMATE_MODEL`, `AWESOME_AGENT_VERIFIER_MODEL`,
`AWESOME_AGENT_SUBAGENT_MODEL`, or profile-specific JSON in
`AWESOME_AGENT_ROLE_MODEL_OVERRIDES`. Coding claims are disabled when no
DeepSeek API key is configured.

## Local Setup

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

Before creating a Run, authorize a local parent directory and register a clean
primary Git checkout:

```powershell
.\.venv\Scripts\awesome-agent.exe config root add E:\projects
.\.venv\Scripts\awesome-agent.exe repo add E:\projects\example
.\.venv\Scripts\awesome-agent.exe run "Inspect the parser" --repo E:\projects\example --read-only
```

`run --repo` may register or refresh the repository only when it is already
under an allowed root. The CLI sends a repository UUID to FastAPI; the API does
not accept filesystem paths. Both read-only and modifying Runs require a clean
checkout and receive a stable worktree at the captured base commit. Normal
`run` commands create modifying Coding Runs; use `--read-only` to deny mutation
tools. Modifying Runs complete only after required validation gates pass.

Use a diagnostic probe to verify the Worker, lease, LangGraph checkpoint, and
cross-process event path without executing a coding goal:

```powershell
.\.venv\Scripts\awesome-agent.exe probe --repo E:\projects\example
```

`awesome-agent start` supervises independent API and Worker child processes.
`serve` and `worker` remain available when the processes should be managed
separately. The local FastAPI API is unauthenticated and binds to `127.0.0.1`
by default. Binding `serve` or `start` to a non-loopback host requires the
explicit `--unsafe-bind-public` flag.

Dispatch state is available at `GET /runs/{run_id}/dispatch`. Queued,
retry-scheduled, waiting-approval, claimed, and executing solo Runs accept
durable cancellation. Active cancellation is recorded as a request, observed by
the owning Worker, and committed as `cancelled + terminal` once the graph and
subprocess boundary stops cleanly.

Set `AWESOME_AGENT_DEEPSEEK_API_KEY` in the ignored local `.env` before real
model calls. Built-in memory and Mem0 are disabled in committed defaults. Enable
them locally with `AWESOME_AGENT_BUILTIN_MEMORY_ENABLED=true` and
`AWESOME_AGENT_MEM0_ENABLED=true`; Mem0 also requires
`AWESOME_AGENT_MEM0_API_KEY`.

## Frontend Demo

The standalone interface demonstration does not connect to the backend:

```powershell
.\.venv\Scripts\python.exe -m http.server 4173 -d demo
```

Open `http://127.0.0.1:4173`. The demo includes mock Agent topology, Todos,
event trace, per-Agent context, command approval, artifacts, and responsive
mobile navigation. It is a UI mockup, not a running multi-agent system.

Local PostgreSQL defaults:

```text
database: awesome_agent
username: awesome_agent
password: awesome_agent
host port: 54329
container port: 5432
```

## Roadmap

Durable runtime work is tracked in
[docs/project-governance/runtime-roadmap.md](docs/project-governance/runtime-roadmap.md).
Highlights of what is planned but not yet implemented:

- real team-runtime end-to-end execution (Task 13);
- worktree and branch retention and cleanup (Task 14);
- dependency-aware `/health` and `doctor` (Task 15).
- full token-window, wall-clock, and cost budget management (Task 16).

## Documentation

- [Agent instructions](AGENTS.md)
- [Architecture](ARCHITECTURE.md)
- [Design documents](docs/design-docs/index.md)
- [Engineering harness](docs/engineering/engineering-harness.md)
- [Runtime agent harness](docs/design-docs/runtime-agent-harness.md)
- [Frontend demo specification](docs/FRONTEND.md)
- [Project governance](docs/project-governance/README.md)
- [Product specification](docs/product-specs/local-coding-agent.md)
- [Quality](docs/QUALITY_SCORE.md)
- [Reliability](docs/RELIABILITY.md)
- [Security](docs/SECURITY.md)
- [Runtime roadmap](docs/project-governance/runtime-roadmap.md)
- [Technical debt tracker](docs/project-governance/tech-debt-tracker.md)

English and Chinese READMEs are maintained together.
