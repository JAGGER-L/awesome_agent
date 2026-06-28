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
- managed execution workspace retention: `workspace list` and explicit
  dry-run-first `workspace cleanup` inspect and safely remove owned inactive
  worktrees and integration branches.
- dependency-aware readiness: `/health` remains cheap process liveness, while
  `/ready?profile=api`, `/ready?profile=runtime`, and `doctor --profile`
  verify PostgreSQL, Alembic migrations, LangGraph checkpointing, workspace
  writability, provider configuration, model routes, API bind policy, and fresh
  Worker heartbeat evidence.

### Coding execution (implemented)

- **Read-only Coding Runs** execute through the checkpointed `solo-readonly`
  Agent loop with bounded repository tools (`repo.status`, `repo.list`,
  `repo.search`, `repo.read`, `repo.instructions`), up to four concurrent
  read-only calls with deterministic order, model-driven tool/feedback back
  edges, convergence feedback, no-progress detection, and evidence-gated
  completion. A deterministic PostgreSQL + fake-provider E2E test covers the
  full loop.
- **Modifying Coding Runs** route to `solo-modifying`, add `repo.apply_patch`,
  `repo.diff`, Docker-backed `shell.execute`, and `artifact.read`, execute
  writes sequentially, offload oversized tool output to artifact storage, and
  persist side-effecting tool invocations with idempotency metadata. Completion
  requires at least one applied patch, a `repo.diff` after the last write, and
  passing required validation gates from `.agents/validation.toml` or
  conservative project detection. Failed required gates feed a bounded rework
  loop; exhausted or non-reworkable validation failure marks the Run failed.
- **Explicit Team Coding Runs** are selected with CLI `--team` or API
  `mode: "team"`. Two team runtimes exist today: scoped `team-coding-scoped` keeps
  one Run and one checkpoint thread while creating internal durable role
  records; distributed `team-coding` creates Teammate, Subagent, and Verifier
  child Runs that independent Workers can claim through PostgreSQL dispatch.
  The distributed path persists lineage, assignments, mailbox messages, child
  results, cancellation propagation, and inspection APIs/CLI. Its first E2E is
  deterministic and does not yet perform model-driven team planning or
  side-effecting team tools.

### Approval (implemented for solo modifying runs)

Durable approval interrupt and resume is wired into `solo-modifying` for one
exact invocation at a time. Ambiguous shell commands create an `approvals`
record, checkpoint the graph, release the worker lease as `paused + waiting`,
and resume through `Command(resume=...)` after the API or CLI approves or
denies the request. Unsafe shell commands are denied without creating an
approval.

### Multi-agent (implemented as scoped and distributed runtimes)

The durable team runtime is explicit. Intake starts with only the Leader. When
`--team` or API `mode: "team"` is selected, current intake routes to
distributed `team-coding`. The Leader creates Teammate child Runs; Teammates
may create bounded Subagent child Runs; and the Leader creates an independent
Verifier child Run before finalization. Subagents have isolated context and
return evidence only to their owning Teammate. The Verifier must pass the work
before the Leader can complete the root Run.

The older scoped `team-coding-scoped` runtime remains documented and tested, but the
new distributed path is the forward architecture. Rich model-driven
specialization and team tool execution remain later work. Distributed team
assignments now support deferred tool exposure, root-aware token/active-time
budget checks, and artifact-backed compaction for large handoff, child-result,
and verifier evidence payloads.

### Observability (implemented)

Runtime observability now records durable query-table evidence for
run/graph/model/tool/sandbox spans, model-call summaries, and metrics such as
run, model, and tool latency. Runtime events receive a stable Run-scoped
`trace_id`, and FastAPI exposes `GET /runs/{run_id}/trace`,
`GET /runs/{run_id}/metrics`, and `GET /runs/{run_id}/model-calls` for frontend
inspection. The current production Worker path uses project-owned durable
records rather than full OpenTelemetry span instrumentation; full OTel coverage,
cost budgeting, and dashboards remain later work.

### Context and budget management (implemented)

Solo read-only and modifying graphs now bound prompt/checkpoint growth with a
deterministic context manager. When the soft context limit is crossed, older
messages and oversized tool observations are preserved as artifacts, the
checkpoint keeps a compact summary plus recent evidence, and runtime events
record `context.compacted`. Hard context pressure forces a bounded final
no-tool answer. Per-Run token ledgers track input, output, reasoning tokens,
model-call count, and active Worker execution seconds. FastAPI exposes
`GET /runs/{run_id}/budget` and
`GET /runs/{run_id}/context-compactions`; the CLI mirrors them with
`awesome-agent budget <run-id>` and
`awesome-agent context-compactions <run-id>`.

Distributed Team Runs add root-aware budget checks across the Leader,
Teammates, Verifier, and Subagents. Large team handoff, child-result, and
verifier evidence payloads are offloaded to artifacts and recorded through
`context_compactions`. Money cost budgeting is still deferred.

## Stack

- Python 3.12 and `uv`
- LangGraph with project-owned orchestration and provider interfaces
- DeepSeek Chat Completions as the default model provider (OpenAI Responses also mapped)
- PostgreSQL and LangGraph PostgreSQL checkpointing
- Typer CLI and local FastAPI API
- Docker sandbox with explicit trusted-local opt-in
- durable query-table observability without LangSmith
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
.\.venv\Scripts\awesome-agent.exe doctor --profile api
.\.venv\Scripts\awesome-agent.exe start
```

Before creating a Run, authorize a local parent directory and register a clean
primary Git checkout:

```powershell
.\.venv\Scripts\awesome-agent.exe config root add E:\projects
.\.venv\Scripts\awesome-agent.exe repo add E:\projects\example
.\.venv\Scripts\awesome-agent.exe run "Inspect the parser" --repo E:\projects\example --read-only
.\.venv\Scripts\awesome-agent.exe run "Implement the feature with a team" --repo E:\projects\example --team
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
explicit `--unsafe-bind-public` flag. The same bind policy is also checked by
the API settings path, so direct ASGI hosting must set
`AWESOME_AGENT_API_HOST` and `AWESOME_AGENT_UNSAFE_BIND_PUBLIC=true` before
binding publicly.

Health and readiness endpoints are split deliberately:

```text
GET /health                  # process liveness, 200 when the API responds
GET /ready?profile=api       # API dependency readiness
GET /ready?profile=runtime   # API readiness plus provider/model/Worker checks
```

`healthy` and `degraded` readiness return HTTP 200. `unhealthy` readiness
returns HTTP 503. CLI diagnostics use the same checks:

```powershell
.\.venv\Scripts\awesome-agent.exe doctor --profile api --no-docker
.\.venv\Scripts\awesome-agent.exe doctor --profile runtime
```

`doctor` exits 0 for `healthy` and `degraded`, and exits 1 for `unhealthy`.

Dispatch state is available at `GET /runs/{run_id}/dispatch`. Queued,
retry-scheduled, waiting-approval, claimed, and executing solo Runs accept
durable cancellation. Active cancellation is recorded as a request, observed by
the owning Worker, and committed as `cancelled + terminal` once the graph and
subprocess boundary stops cleanly.

Managed execution workspaces can be inspected and cleaned explicitly:

```powershell
.\.venv\Scripts\awesome-agent.exe workspace list
.\.venv\Scripts\awesome-agent.exe workspace cleanup --run-id <run-id>
.\.venv\Scripts\awesome-agent.exe workspace cleanup --run-id <run-id> --apply
.\.venv\Scripts\awesome-agent.exe workspace cleanup --older-than 14d --apply
```

Cleanup defaults to preview. Normal cleanup removes only clean managed
workspaces for terminal completed or cancelled Runs. Failed or dirty workspaces
require `--force --reason`; `recovery_required` workspaces are retained as
recovery evidence.

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

- model-driven distributed team planning, team tool execution, and verifier
  rework on the `team-coding` path.
- richer model-driven distributed team planning, team tool use, and mailbox
  collaboration policy.

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
