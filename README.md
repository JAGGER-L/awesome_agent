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

For the full guide, see
[docs/getting-started/quickstart.md](docs/getting-started/quickstart.md).
The target startup profile contract is defined in
[docs/design-docs/runtime-profiles-and-startup.md](docs/design-docs/runtime-profiles-and-startup.md).

### Prerequisites

- Python 3.12
- `uv`
- Git
- Docker Desktop or a compatible Docker engine
- Windows PowerShell for the current helper scripts

### Configure

```powershell
Copy-Item .env.example .env
```

Put provider secrets in `.env`. The default model provider settings are
`AWESOME_AGENT_DEEPSEEK_API_KEY`, `AWESOME_AGENT_DEEPSEEK_BASE_URL`,
`AWESOME_AGENT_DEEPSEEK_PRO_MODEL`, and
`AWESOME_AGENT_DEEPSEEK_FLASH_MODEL`.

Keep extension source configuration in `awesome-agent.yaml`. Project skills
are discovered from `skills/`. Do not put secrets in `awesome-agent.yaml`.

### Choose A Run Mode

The Makefile commands are the primary startup contract. Docker API mode uses
`make docker-init` and `make docker-start`; local API development uses
`make check`, `make install`, `make setup-sandbox`, and `make dev`; local
interactive CLI uses `awesome` after Task 60. The existing PowerShell scripts
remain Windows fallback entrypoints.

| Mode | Best for | Command | Status |
| --- | --- | --- | --- |
| Local CLI | Interactive local coding-agent entrypoint | `awesome`, `awesome commands` | Primary |
| Local API | API + Worker inspection from host Python | `make check`, `make install`, `make setup-sandbox`, `make dev` | Primary |
| Docker API/Web | Browser/API inspection against containerized API | `make docker-init`, `make docker-start` | Primary |
| Local CLI fallback | First local run and development | `.\scripts\quickstart.ps1` | Fallback |
| Docker CLI | Containerized runtime with CLI-driven inspection | `.\scripts\docker-quickstart.ps1` | Fallback |

The current "Web" surface is the local FastAPI inspection surface and
generated API docs at `/docs`. It is not yet a hosted multi-user web
application.

### Run Local API

```powershell
make check
make install
make setup-sandbox
make dev
```

This checks host dependencies, installs Python dependencies, builds the AIO
sandbox service image, runs migrations, starts API + Worker, and prints
readiness URLs.

### Run Docker API

```powershell
make docker-init
make docker-start
```

Docker mode does not start the CLI. It starts PostgreSQL, the AIO sandbox
service, API, and Worker.

### PowerShell Fallback

```powershell
.\scripts\bootstrap.ps1
docker compose up -d postgres
.\scripts\migrate.ps1
.\.venv\Scripts\awesome-agent.exe doctor --profile api
.\.venv\Scripts\awesome-agent.exe start
```

The PowerShell quickstart remains available:

```powershell
.\scripts\quickstart.ps1
.\scripts\docker-quickstart.ps1
```

The API binds to `http://127.0.0.1:8000` by default. Use `/health` for process
liveness and `/ready?profile=api` or `/ready?profile=runtime` for dependency
readiness.

### Run Local CLI

For a first local CLI launch:

```powershell
awesome init
cd E:\my-project
awesome
```

`awesome init` creates `~/.awesome-agent/config.yaml`. The file stores model
names and environment-variable names only. Set `AWESOME_AGENT_DEEPSEEK_API_KEY`
in your shell, operating-system environment, password manager, or local `.env`
file before model-backed use.

```powershell
cd E:\my-project
awesome
awesome commands
```

Run `awesome` from the project directory you want the agent to work on. The
launch directory becomes the default thread context. If it is a Git checkout,
Runs inherit that repository. If it is not a Git checkout, the CLI uses
workspace-only mode and still accepts ordinary chat messages.

`awesome` is the default chat-first local CLI/TUI. Use `awesome-agent`
subcommands for direct operations, diagnostics, and scripting.

The local TUI is intentionally chat-first. It shows a welcome panel at launch,
then keeps the main screen focused on the transcript and input prompt. Runtime
details are available through slash commands such as `/status`, `/tools`,
`/mcp`, `/artifacts`, `/usage`, and `/config`.

| Command | Purpose |
| --- | --- |
| `/new` | Start a new durable local conversation/thread. |
| `/threads` | List known threads. |
| `/switch` | Alias for `/threads`. |
| `/resume` | Resume a thread by id or title when supported. |
| `/status` | Show current thread/run/runtime status. |
| `/model` | Alias for `/models`. |
| `/models` | List configured model profiles. |
| `/skills` | Browse enabled and available skills. |
| `/tools` | Show built-in, MCP, and sandbox tools. |
| `/mcp` | Show MCP server status. |
| `/memory` | Show memory configuration and current memory summary. |
| `/uploads` | Show uploaded files for this thread. |
| `/artifacts` | Show generated artifacts. |
| `/details` | Toggle verbose activity rendering. |
| `/usage` | Show token usage and context. |
| `/config` | Show resolved config paths and overrides. |
| `/help` | Show help. |
| `/quit` | Exit the TUI. |

Slash commands are CLI/TUI interaction syntax. API routes expose semantic
resources such as threads, runs, models, memory, readiness, and approvals
rather than slash-command route names.

### Verify

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

### First Read-Only Run

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

## First Run

The fastest safe first run is the automated quickstart:

```powershell
.\scripts\quickstart.ps1
```

It uses a diagnostic probe for the required success check. Add `-RunReadOnly`
only after configuring a provider key and deciding to create a model-backed
read-only Run.

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

Open the local TUI operator console:

```powershell
.\.venv\Scripts\awesome-agent.exe tui
.\.venv\Scripts\awesome-agent.exe tui --run-id <run-id>
```

The TUI is a local API-backed inspection and control surface for Runs,
diagnostics, events, and approvals. It is not a hosted web dashboard.

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
`aio-docker` sandboxing for API-created Runs. LocalSandbox is reserved for the
local CLI/TUI profile or explicit trusted local operation.

Thread workspaces persist under
`~/.awesome-agent/threads/<thread_id>/workspace/`. Run artifacts persist under
`~/.awesome-agent/runs/<run_id>/artifacts/`.
