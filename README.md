# awesome_agent

[English](README.md) | [简体中文](README.zh-CN.md)

`awesome_agent` is a local-first coding-agent runtime for durable, observable,
permissioned coding runs.

## What It Is

`awesome_agent` runs coding tasks against local Git repositories through a Typer
CLI, a local FastAPI API, PostgreSQL-backed durable state, and Worker processes.
It supports solo read-only runs, solo modifying runs, and an explicit
Leader/Teammate/Subagent team runtime with an independent Verifier.

The project is a runtime kernel first: it focuses on crash recovery, auditable
side effects, bounded model/tool loops, local repository safety, and inspection
surfaces before higher-level product UI.

## Why It Exists

Most coding-agent prototypes are easy to start and hard to trust after
something goes wrong. This project optimizes for the other half of the problem:
recoverability, least-privilege tool access, operator visibility, and local
control.

The runtime is designed so a Run can be inspected after a crash, approval wait,
validation failure, cancellation, or team rework without relying on hidden
process memory.

## Core Capabilities

- Durable Run intake, dispatch leases, Worker heartbeats, retries, cancellation,
  and checkpoint resume through PostgreSQL and LangGraph checkpointing.
- Repository-aware execution with allowed roots, registered repositories, clean
  base commits, and managed per-Run worktrees.
- Solo read-only and modifying AgentLoop routes with bounded repository tools,
  Docker-backed shell execution for mutation, approval interrupts, validation
  gates, and rework.
- Distributed team mode with model-planned Teammates, assignment-scoped tools,
  Teammate-owned read-only Subagents, independent Verifier review, and targeted
  rework.
- Token and active-time budget ledgers. The runtime intentionally does not
  enforce money-based limits.
- Durable observability through query-table spans, model-call summaries,
  metrics, diagnostics, recovery metrics, trace IDs, and redacted API/CLI
  inspection.
- Extension catalog foundations for project `skills/`, `awesome-agent.yaml`,
  MCP sources, and community tool packages, all gated by capability resolution.

## Quick Start

For the full manual path, see
[docs/getting-started/quickstart.md](docs/getting-started/quickstart.md).
Task 54 will add an automated `scripts\quickstart.ps1` path; until then, use
the commands below.

Prerequisites:

- Python 3.12
- `uv`
- Git
- Docker Desktop or a compatible Docker engine
- Windows PowerShell for the current helper scripts

Start the local runtime:

```powershell
.\scripts\bootstrap.ps1
Copy-Item .env.example .env
docker compose up -d postgres
.\scripts\migrate.ps1
.\.venv\Scripts\awesome-agent.exe doctor --profile api
.\.venv\Scripts\awesome-agent.exe start
```

The API binds to `http://127.0.0.1:8000` by default. Use `/health` for process
liveness and `/ready?profile=api` or `/ready?profile=runtime` for dependency
readiness.

## First Run

Authorize a parent directory and register a clean Git checkout:

```powershell
.\.venv\Scripts\awesome-agent.exe config root add <parent-directory>
.\.venv\Scripts\awesome-agent.exe repo add <repository-path>
```

Verify the durable runtime without a model key:

```powershell
.\.venv\Scripts\awesome-agent.exe probe --repo <repository-path>
.\.venv\Scripts\awesome-agent.exe diagnostics <run-id>
```

Set `AWESOME_AGENT_DEEPSEEK_API_KEY` in `.env`, restart the runtime, then run a
read-only coding task:

```powershell
.\.venv\Scripts\awesome-agent.exe run "Inspect this repository" --repo <repository-path> --read-only
```

Committed defaults route Leader work to `deepseek-v4-pro` and Teammate,
Verifier, and Subagent work to `deepseek-v4-flash`. Override them with
`AWESOME_AGENT_LEADER_MODEL`, `AWESOME_AGENT_TEAMMATE_MODEL`,
`AWESOME_AGENT_VERIFIER_MODEL`, and `AWESOME_AGENT_SUBAGENT_MODEL`.

Use `--team` when you want the distributed Leader, Teammate, and Verifier
runtime.

## Extensions

Project extension configuration lives in `awesome-agent.yaml`. It is for
extension sources such as project skill roots and MCP sources, not for secrets.
Keep provider keys and runtime settings in `.env` or environment variables.

Project skills live under `skills/`; each skill package contains a `SKILL.md`.
Skills can request instructions, context, and tool capabilities, but they do
not grant execution authority by themselves. MCP and community tools enter
through the extension catalog and still pass through exposure, capability,
approval, budget, execution, and observability boundaries.

## Operations

Useful local operations:

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

`awesome-agent start` supervises API and Worker processes together. Use
`awesome-agent serve` and `awesome-agent worker` separately when another
process manager should own them. The local API is unauthenticated and binds to
loopback by default; non-loopback binding requires explicit unsafe consent.

## Architecture At A Glance

The target architecture is a small durable kernel surrounded by policy and
extension layers:

- API and CLI own intake, inspection, approval, cancellation, and operator
  commands.
- Worker and dispatch own claims, leases, heartbeats, retries, and execution
  ownership.
- Graph modules own durable state transitions, checkpoints, interrupts,
  resume, child-run coordination, and terminal projections.
- AgentLoop owns one bounded model-to-tool loop for one agent role.
- Middleware and hooks own context assembly, observability, budget checks,
  permission checks, tool exposure, retries, error classification, validation,
  and artifact offload.
- Capability resolution is the authority for tool exposure and execution.

See [ARCHITECTURE.md](ARCHITECTURE.md) and
[docs/design-docs/index.md](docs/design-docs/index.md) for the detailed
contracts.

## Current Maturity

The project is suitable for local development and runtime-kernel iteration. It
has real durable execution, repository registration, Worker recovery, solo and
team runtime paths, diagnostics, budgets, and extension catalog foundations.

It is not a hosted multi-user service. Production deployment, dashboards, and
hosted product workflows remain future work tracked in the roadmap.

## Documentation

- [Documentation map](docs/README.md)
- [Quickstart](docs/getting-started/quickstart.md)
- [User guide](docs/user-guide/README.md)
- [Operations guide](docs/operations/README.md)
- [Architecture](ARCHITECTURE.md)
- [Design documents](docs/design-docs/index.md)
- [Security](docs/SECURITY.md)
- [Reliability](docs/RELIABILITY.md)
- [Runtime roadmap](docs/project-governance/runtime-roadmap.md)
- [Technical debt tracker](docs/project-governance/tech-debt-tracker.md)

## Security Note

Keep secrets out of committed files. Use `.env` for local provider keys and
machine-specific runtime settings. Run untrusted code through Docker-backed
sandboxing; host execution requires explicit trusted-local consent.
