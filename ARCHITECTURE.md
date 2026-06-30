# Architecture

## System Intent

The system is a local-first coding agent runtime. It separates orchestration,
side effects, persistence, observation, and model providers so each can evolve
without coupling the whole application to one vendor.

## Component Topology

```text
                         ┌──────────────────┐
                         │  Browser / User  │
                         └────────┬─────────┘
                                  │ HTTP / SSE
                                  ▼
┌──────────────────┐     ┌──────────────────┐
│    Typer CLI     │────►│     FastAPI      │
└────────┬─────────┘     │ Inspection API   │
         │               └────────┬─────────┘
         │ local command          │ runtime commands / queries
         └──────────────┬─────────┘
                        ▼
                 ┌──────────────────┐
                 │ Runtime Service  │
                 │ runs/events/API  │
                 └────────┬─────────┘
                          │
                          ▼
                 ┌──────────────────┐
                 │ Leader Runtime   │
                 │ LangGraph plan   │
                 └───┬──────┬───────┘
                     │      │
          team tasks │      │ model requests
                     ▼      ▼
          ┌──────────────┐  ┌──────────────────┐
          │ Team Runtime │  │ Provider Adapter │
          │ + Verifier   │  │ DeepSeek default │
          └──────┬───────┘  └────────┬─────────┘
                 │                   │ HTTPS
                 │ tools             ▼
                 ▼          ┌──────────────────┐
          ┌──────────────┐  │  Model Provider  │
          │ Tool Registry│  └──────────────────┘
          │ + Approval   │
          └──────┬───────┘
                 │ approved execution
                 ▼
          ┌──────────────┐
          │ Sandbox      │
          │ Docker/local │
          └──────┬───────┘
                 │
                 ▼
          ┌──────────────┐
          │ User Project │
          │ + worktrees  │
          └──────────────┘

        ┌────────────────────────────────────────────────────┐
        │                 State and Evidence                 │
        │                                                    │
        │  ┌──────────────┐  ┌──────────────┐  ┌──────────┐ │
        │  │ PostgreSQL   │  │ Artifacts    │  │ OTel /   │ │
        │  │ runs/events/ │  │ filesystem   │  │ events   │ │
        │  │ checkpoints  │  └──────────────┘  └──────────┘ │
        │  └──────────────┘                                 │
        │                                                    │
        │  ┌──────────────┐  ┌──────────────┐               │
        │  │ USER.md /    │  │ Mem0         │               │
        │  │ MEMORY.md    │  │ Platform     │               │
        │  └──────────────┘  └──────────────┘               │
        └────────────────────────────────────────────────────┘
```

The CLI is the primary local execution surface. FastAPI exposes durable runs,
agents, Todos, event history, approvals, artifacts, and live SSE updates for a
future frontend. Both surfaces call application services rather than accessing
provider, database, or sandbox implementations directly.

### Health and Readiness

```text
CLI doctor --profile api/runtime
        |
        v
shared readiness collector
        |
        +--> Python/Git/Docker checks
        +--> PostgreSQL database check
        +--> Alembic migration head check
        +--> LangGraph checkpoint store check
        +--> workspace-root write probe
        +--> provider key and model-route checks
        +--> API bind policy check
        +--> worker_heartbeats table (runtime profile)

FastAPI
  GET /health                -> process liveness only
  GET /ready?profile=api     -> API dependency readiness
  GET /ready?profile=runtime -> API readiness plus Worker/provider readiness
```

`/health` is intentionally cheap and returns 200 when the API process can
respond. `/ready` and `doctor` share the structured readiness model with
`healthy`, `degraded`, and `unhealthy` statuses. `healthy` and `degraded`
readiness return HTTP 200; `unhealthy` returns HTTP 503. CLI `doctor` exits 0
for `healthy` and `degraded`, and exits 1 for `unhealthy`.

Worker liveness is not inferred from active Run leases. Workers upsert a
process-scoped row in `worker_heartbeats` with worker id, worker name,
supported runtime routes, status, start time, and heartbeat time. The runtime
readiness profile requires a fresh online heartbeat that covers the required
runtime routes.

### Repository-Aware Run Intake

```text
┌──────────────────┐      local path       ┌────────────────────┐
│    Typer CLI     │──────────────────────►│ Allowed-root policy│
└────────┬─────────┘                       └─────────┬──────────┘
         │ repository UUID                           │ validated path
         ▼                                           ▼
┌──────────────────┐      repository ID    ┌────────────────────┐
│     FastAPI      │──────────────────────►│ Repository registry│
│ POST /runs       │                       │ PostgreSQL         │
└────────┬─────────┘                       └─────────┬──────────┘
         │                                           │ clean Git identity
         ▼                                           ▼
┌──────────────────┐      reserve first    ┌────────────────────┐
│  Intake Service  │──────────────────────►│ Intake reservation │
└────────┬─────────┘                       │ PostgreSQL         │
         │ exact base commit               └────────────────────┘
         ▼
┌──────────────────┐      named branch     ┌────────────────────┐
│ Managed worktree │──────────────────────►│ User Git repository│
│ per Run          │                       │ original unchanged │
└────────┬─────────┘                       └────────────────────┘
         │ ready
         ▼
┌───────────────────────────────────────────────────────────────┐
│ One transaction: Run(created/queued) + Leader + initial       │
│ events + reservation(published)                              │
└───────────────────────────────────────────────────────────────┘
```

Filesystem paths enter only through the local CLI. FastAPI accepts a registered
repository UUID and exposes repository list/get for a future frontend. Both
read-only and modifying intents use a stable worktree; intent later controls
tool capabilities. Task 02 queues the Run but does not claim or execute it.

### PostgreSQL Dispatch Protocol

```text
queued / retry_scheduled
          |
          | FOR UPDATE SKIP LOCKED
          v
       claimed ----- heartbeat -----> PostgreSQL lease extension
          |                                  |
          | fenced transition                | lease expires
          v                                  v
   retry / release / terminal        queued or recovery_required
```

The current lease lives on the Run row. A claim records a process-scoped
worker UUID, diagnostic name, attempt, expiry, and monotonically increasing
fencing token. PostgreSQL time decides lease validity. State changes and their
dispatch events share one transaction.

### Durable Worker and Probe Graph

```text
awesome-agent start
        |
        +---- API process ---- PostgreSQL events ---- SSE polling
        |
        +---- Worker process
                 |
                 +---- claim supported runtime routes
                 +---- heartbeat lease
                 +---- LangGraph sync checkpoint
                 +---- fenced projection update
```

Each Worker process executes at most one Run. Workers always claim the
diagnostic `runtime-probe` route and, when model providers are configured, also
claim `solo-readonly`, `solo-modifying`, and explicit `team-coding-scoped`
Runs. Workers also publish process heartbeat rows for readiness; Run lease
heartbeat remains a separate fencing mechanism. A crashed Worker leaves its
checkpoint and lease; after lease expiry, a replacement Worker claims with a
new fencing token and resumes from the checkpoint. Unsupported runtime routes
enter `recovery_required`.

## Agent Orchestration Topology

```text
                         ┌──────────────────┐
                         │       User       │
                         └────────┬─────────┘
                                  │ task / approval
                                  ▼
                         ┌──────────────────┐
                         │      Leader      │
                         │ plan + final say │
                         └───┬──────────┬───┘
                             │ creates  │ observes all
                   ┌─────────┘          └─────────┐
                   ▼                              ▼
          ┌──────────────────┐           ┌──────────────────┐
          │    Teammate A    │◄─────────►│    Teammate B    │
          │ durable context  │  mailbox  │ durable context  │
          └───────┬──────────┘           └───────┬──────────┘
                  │ creates without approval      │ creates without approval
          ┌───────┴────────┐              ┌───────┴────────┐
          ▼                ▼              ▼                ▼
 ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
 │  Subagent A1 │ │  Subagent A2 │ │  Subagent B1 │ │  Subagent B2 │
 │ isolated ctx │ │ isolated ctx │ │ isolated ctx │ │ isolated ctx │
 └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘

                         ┌──────────────────┐
                         │     Verifier     │
                         │ independent gate │
                         └────────┬─────────┘
                                  │ pass / reject evidence
                                  ▼
                         ┌──────────────────┐
                         │      Leader      │
                         │ completion choice│
                         └──────────────────┘
```

The Leader is the only agent present initially. Team mode is explicit through
CLI `--team` or API `mode: "team"`; automatic solo/team routing remains future
work. The Leader owns the task tree, manages Teammates, integrates accepted
work, and makes the final completion decision.

Teammates own durable responsibilities and may communicate through an auditable
mailbox. Each Teammate may independently create up to three Subagents.
Subagents do not participate in team conversation and report only to their
creator.

The Verifier is a specialized Teammate created whenever team mode starts.
Teammate output reaches the Leader only after verification passes or after
rejected work is revised and re-verified.

Scoped `team-coding-scoped` uses one Run, one Worker claim, and one LangGraph
checkpoint thread. The graph creates durable internal agent sessions for two
Teammates, one Verifier, and bounded Subagents, and it records model calls,
tool invocations, Todo transitions, validation reports, events, and
observability spans.

Distributed `team-coding` is the forward architecture. The root Leader Run
creates child Runs with durable lineage. The Leader calls the model through
`TeamAgentLoop` middleware for a validated structured `TeamPlan` and creates
Teammate child Runs from that plan; it does not create or direct Subagents.
Teammate child Runs execute assignment-scoped model/tool loops through the
same team AgentLoop boundary using only effective Leader-granted tools. When
the Leader grants `can_delegate` and Subagent slots, a Teammate may call
`team.create_subagent` to create read-only Subagent child Runs with depth and
concurrency limits. Independent Workers can claim Teammate, Subagent, and
Verifier child Runs through the same PostgreSQL dispatch protocol. Verifier
child Runs produce structured model decisions through
`TeamVerificationMiddleware`; the graph persists those decisions as child
results and mailbox messages. Rework decisions create replacement Teammate
child Runs instead of reopening original attempts. Parent Runs release their
lease while waiting for child work and are requeued when child assignments
become terminal.

```text
                         +----------------------+
                         | Root Run             |
                         | Leader team-coding |
                         +----------+-----------+
                                    |
                                    | model TeamPlan -> Teammate child Runs
                                    v
                         +----------------------+
                         | Child Run            |
                         | Teammate team-role |
                         +----------+-----------+
                                    |
                         terminal result wakes Leader
                                    |
                                    v
                         +----------------------+
                         | Child Run            |
                         | Verifier team-verifier |
                         +----------+-----------+
                                    |
                         pass/fail mailbox message wakes Leader
                                    |
                                    v
                         +----------------------+
                         | Root Run finalizes   |
                         +----------------------+

Durable state:
  runs(parent_run_id, root_run_id, depth, child_role)
  team_assignments(kind, runtime_route, permissions, status, handoff_context)
  team_mailbox_messages(route, subject, status)
  team_child_results(summary, patch_artifact_id, patch_aggregated)
```

## Harness and State Boundaries

```text
tracked repository governance
  AGENTS.md
  docs/engineering/
  scripts/ and structural tests

ignored development-agent state
  .codex/exec-plans/

tracked product runtime configuration
  .agents/

ignored or durable product runtime state
  .awesome-agent/
  PostgreSQL
```

The four locations are intentionally distinct. Development-agent plans never
become product runtime plans. Runtime Leader plans and Todos are domain data,
not repository-maintenance Markdown.

## Source Layout

```text
src/
`-- awesome_agent/
    |-- agents/
    |-- modeling/
    |-- orchestration/
    |-- domain/
    |-- providers/
    |-- tools/
    |-- sandbox/
    |-- memory/
    |-- persistence/
    |-- observability/
    |-- artifacts/
    |-- repositories/
    |-- runtime/
    |-- api/
    `-- cli/
```

The `src` layout is intentional. `src` is the import root and `awesome_agent` is
the package. Tests must import the installed package rather than accidentally
loading repository files.

## Dependency Direction

```text
api / cli
    -> orchestration
        -> modeling
        -> domain

providers / tools / sandbox / memory / persistence / observability / artifacts
    implement ports owned by domain or orchestration
```

Rules:

- `domain` does not import infrastructure or framework modules.
- `orchestration` owns workflows but not concrete storage or provider details.
- provider-specific message types do not cross provider boundaries.
- `modeling` owns provider-neutral messages, tools, turns, reasoning,
  continuation, usage, streaming events, and failure categories.
- every Agent records its resolved model for API and event traceability.
- tool execution always passes through approval and sandbox policies.
- runtime events are immutable and all state changes emit an event.
- implementation agents cannot approve their own team-mode work.

These rules must be enforced with structural tests once source modules exist.

## Structured Model Boundary

```text
orchestration / memory
        |
        | ModelRequest(messages, tools, continuation)
        v
provider-neutral modeling protocol
        |
        +---- DeepSeek adapter ---- Chat Completions stream
        |
        +---- OpenAI adapter ------ Responses stream
        |
        v
reasoning/text/tool deltas -> completed ModelTurn
```

Visible reasoning is a frontend-capable trace. Private continuation is a
separate opaque JSON value used only by the matching adapter and LangGraph
checkpoint. SDK objects, encrypted continuation data, and provider-specific
message types never enter orchestration, events, logs, memory, or public APIs.
The Worker connects provider-neutral model turns to solo read-only, solo
modifying, and explicit team Coding graphs when a model provider is configured.

## Read-Only Coding Loop

Workers with a configured model provider also advertise the
`coding + read_only + solo-readonly` route. The graph contains explicit
`execute_tools -> model_turn` and `feedback -> model_turn` back edges, so tool
selection and iteration count are model-driven rather than a fixed workflow.
Only evidence-backed final answers terminate successfully.

See [Read-only agent loop](docs/design-docs/read-only-agent-loop.md) for the
complete node, loop, budget, tool, failure, and recovery contract.

## Modifying Coding Loop

Workers with a configured model provider also advertise the
`coding + modifying + solo-modifying` route. The graph loops through model
turns and sequential tool execution. It exposes read tools, `repo.apply_patch`,
`repo.diff`, `artifact.read`, and Docker-backed `shell.execute`.

Side-effecting modifying tools are recorded in PostgreSQL with stable
idempotency keys before execution. Completed tool results are reused after
checkpoint replay; ambiguous patch state and unknown shell completion enter
`recovery_required` rather than replaying an unsafe side effect.

Successful completion requires at least one applied patch, a `repo.diff` after
the last write, and passing required validation gates from configuration or
conservative project detection. Failed required check commands feed bounded
evidence back to the model for rework; exhausted or non-reworkable validation
failure marks the Run failed.

## Team Coding Loop

Workers with a configured model provider also advertise explicit
`coding + modifying + team-coding-scoped` and distributed `team-coding` routes.
The caller must request team mode; default modifying Runs stay on the solo
modifying graph.

`team-coding-scoped` is a real but bounded team runtime path. Intake creates only
the Leader. The graph then creates role assignments with `allowed_tools` and
`allowed_skills`, a backend Teammate, a repository-explorer Teammate, one
Verifier, and a backend-owned read-only Subagent. Repository tools still execute
through the central `ToolExecutor`; tools not granted by the Leader assignment
are rejected before execution.

Verifier rejection caused by model or quality output can trigger bounded
same-Teammate rework. Verifier execution or external failures have a separate
small retry budget. Completion is recorded as `team_validated` only after
Verifier pass and Leader finalization. Task 13 E2E covers Worker claim,
PostgreSQL checkpointing, fake provider calls, repository tool execution,
patch/rework, durable validation records, tool invocation records, events, and
observability query tables.

## Persistence

PostgreSQL is authoritative for LangGraph checkpoints and project-owned runtime
records. Checkpoint semantics remain owned by LangGraph. The API reads runs,
agents, tasks, and event history through a runtime repository instead of
process-local dictionaries. The in-memory repository is an explicit test
adapter only. SSE reads ordered events from PostgreSQL so API and Worker
processes share one durable event history. `EventStream` remains a local
notification adapter, not durable state.

Project tables store runs, agents, tasks, messages, tool calls, artifacts,
approvals, verification, and memory audit data. Agent records include the
resolved model assignment. Repository identities and private intake
reservations are also PostgreSQL records. Existing prototype Runs are preserved
as legacy rows; unsafe non-terminal legacy rows become `recovery_required`.

Local `~/.awesome-agent/config.toml` stores allowed roots and the managed
worktree root. This local authorization is intentionally separate from the
PostgreSQL repository registry.

Large outputs live in external artifact storage. PostgreSQL stores metadata,
hashes, ownership, and paths.

Distributed `team-coding`, `team-role`, and `team-verifier` are the forward
team routes. Their graph modules own durable child-run coordination, child
wait/requeue behavior, patch aggregation, result persistence, mailbox messages,
and terminal mapping. Leader planning, Teammate/Subagent model/tool execution,
delegation tool calls, Verifier decisions, and team observability run through
`TeamAgentLoop` middleware.

AgentLoop middleware receives a typed `MiddlewareContext` rather than relying
on route-specific metadata for stable runtime facts. The context exposes
focused envelopes for trace, capability subject, assignment, token budget,
handoff, and error classification. Metadata remains a compatibility and
annotation channel; new cross-cutting policy should consume the typed
envelopes and leave durable state transitions to the graph.

## Observability

Runtime observability has three layers:

- durable evidence in PostgreSQL query tables: `observability_spans`,
  `observability_metrics`, and `model_calls`;
- ordered runtime events with a stable Run-scoped `trace_id`;
- best-effort OpenTelemetry export and structured logs.

The Worker records outer `run.execute` and `graph.execute` spans without
letting observability writes or exporter failures affect Run execution.
Migrated solo and forward distributed team AgentLoop stages record `agent.run`,
`model.call`, and `tool.call` spans through `ObservabilityMiddleware`; scoped
team compatibility routes keep event-projection observability until migrated.
Model-call records store provider, model, status, stop reason, token usage,
latency, and trace/span IDs. FastAPI exposes `GET /runs/{run_id}/trace`,
`GET /runs/{run_id}/metrics`, and `GET /runs/{run_id}/model-calls` for the
future frontend.

Cost budgeting, dashboards, and dependency-aware health checks remain separate
roadmap work.

## Durable Execution Target

The durable coding roadmap separates execution concerns instead of treating one
status field or one store as authoritative for everything.

```text
CLI / API
   |
   | create Run(repository_id, base commit, policy)
   v
PostgreSQL dispatch state
   | queued -> claimed -> executing -> waiting / retry -> terminal
   | lease + heartbeat + fencing token
   v
Worker
   |
   | start/resume stable LangGraph thread
   v
LangGraph checkpoint ----------------------+
   | next graph position and agent context |
   |                                       |
   +--> model/tool/approval/validation -----+
                  |
                  | fenced projection transition
                  v
PostgreSQL domain projections + ordered events
                  |
                  +--> API / SSE / future frontend

Large output, patches, and evidence -> artifact storage
```

State ownership:

- LangGraph checkpoints own the next executable position and resumable agent
  context.
- PostgreSQL domain tables own user-visible business projections.
- Run, Agent, and Todo visible lifecycle transitions use a transaction-scoped
  projection helper so projection rows, `updated_at`, Agent/Todo revisions, and
  matching runtime events are committed together.
- Separate `DispatchStatus` owns queue and worker scheduling state.
- PostgreSQL row locking and `SKIP LOCKED` serialize claims without a separate
  broker.
- Runtime events are ordered audit records, not a replay-complete event store.
- Stable transition IDs reconcile checkpoint-ahead and projection-ahead
  partial failures.
- An ambiguous mismatch enters `recovery_required`; it is never guessed
  through automatically.
- Active cancellation is a durable PostgreSQL request. The API records it, and
  only the owning fenced Worker commits active `cancelled + terminal` after the
  graph and subprocess boundary stops cleanly.

Repository access also has two layers:

- PostgreSQL stores stable registered repository identities.
- local configuration stores allowed filesystem roots.

Every modifying Run uses a dedicated integration worktree from a clean base
commit. The user's checkout is never modified automatically, including when
trusted-local command execution is selected.

Managed execution workspaces remain explicit runtime evidence until the user
requests cleanup. `workspace list` and the workspace cleanup API evaluate
PostgreSQL Run state, ownership markers, managed-root containment, Git worktree
state, branch identity, and dirty status before deletion. Cleanup defaults to
preview; apply removes only owned inactive workspaces and matching
`awesome-agent/run/<run_id>` branches. Failed or dirty workspaces require force
with a reason, while `recovery_required` workspaces are retained.

See [Durable execution](docs/design-docs/durable-execution.md) for the complete
target contract.

## Context And Budget Boundaries

Task 16 adds a shared context manager and per-Run budget ledger. Solo
`solo-readonly` and `solo-modifying` model turns compact context before
provider calls when the soft context limit is crossed. Removed messages and
oversized tool observations are written to artifact storage; checkpoints retain
a deterministic rolling summary plus recent evidence. Compactions are visible
through `context.compacted` events, `context_compactions` rows,
`GET /runs/{run_id}/context-compactions`, and the matching CLI command.

The budget ledger records input, output, reasoning tokens, model-call count,
threshold status, and active Worker execution seconds. Worker active time is
opened only while graph work is executing and is closed before approval wait,
pause, retry, completion, or failure is projected. Distributed team boundaries
use root-aware budget checks, deferred tool exposure, and artifact-backed
handoff/result/verifier payload compaction. Monetary amount budgeting is
intentionally outside the runtime kernel.

## Security Boundary

Docker is the default command execution boundary. CLI users may explicitly opt
into trusted local execution. FastAPI runs cannot use trusted-local mode.
Writing Teammates use isolated Git worktrees.

Approval is scoped to one exact canonical tool invocation. Repository
validation configuration and inferred project commands are untrusted input;
only strongly evidenced check-only commands may run automatically.
In `solo-modifying`, ambiguous shell execution creates a durable
`approvals` row, checkpoints with LangGraph `interrupt(value)`, releases the
worker lease as `paused + waiting`, and resumes with `Command(resume=...)`
after API/CLI decision. Resume revalidates the canonical arguments hash, tool
version, workspace fingerprint, and requested capabilities before execution.
Unsafe shell commands are denied without approval.

## Detailed Designs

See [docs/design-docs/index.md](docs/design-docs/index.md).

Repository engineering rules are under
[docs/engineering](docs/engineering/engineering-harness.md).
