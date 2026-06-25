# awesome_agent

[English](README.md) | [简体中文](README.zh-CN.md)

`awesome_agent` is a local-first Python coding agent built around an observable
Agent Team:

- one Leader exists at startup
- the Leader creates Teammates only for complex work
- Teammates may create isolated, bounded Subagents
- Team mode always includes an independent Verifier
- conversations, tools, tasks, artifacts, model assignments, and verification
  remain traceable

## Current Status

The initial framework is locally runnable. It includes orchestration primitives,
PostgreSQL checkpoints and API projections, sandbox backends,
Team/Subagent/Verifier lifecycle, memory adapters, traceable events, artifacts,
CLI, FastAPI inspection APIs, registered repository identities, allowed-root
policy, and crash-recoverable Run intake into named Git worktrees.
The PostgreSQL queue supports transactional claims, leases, heartbeats,
fencing tokens, delayed retry, and expired-lease recovery. A durable Worker now
executes checkpointed `runtime_probe` Runs and resumes them after process
failure. Normal Coding Runs remain queued until the model/tool loop is added.

## Stack

- Python 3.12 and `uv`
- LangGraph with project-owned orchestration and provider interfaces
- DeepSeek Chat Completions as the default model provider
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
`AWESOME_AGENT_ROLE_MODEL_OVERRIDES`.

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
`run` commands create Coding Runs that remain queued in the current phase.

Use a diagnostic probe to verify the Worker, lease, LangGraph checkpoint, and
cross-process event path without executing a coding goal:

```powershell
.\.venv\Scripts\awesome-agent.exe probe --repo E:\projects\example
```

`awesome-agent start` supervises independent API and Worker child processes.
`serve` and `worker` remain available when the processes should be managed
separately.

Dispatch state is available at `GET /runs/{run_id}/dispatch`. Queued and
retry-scheduled Runs can be cancelled immediately. Claimed or executing Runs
return `409` until durable cancellation propagation is implemented.

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
mobile navigation.

Local PostgreSQL defaults:

```text
database: awesome_agent
username: awesome_agent
password: awesome_agent
host port: 54329
container port: 5432
```

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

English and Chinese READMEs are maintained together.
