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
                 +---- claim runtime_probe only
                 +---- heartbeat lease
                 +---- LangGraph sync checkpoint
                 +---- fenced projection update
```

Each Worker process executes at most one Run. The current graph is the
diagnostic `initialize -> checkpoint_probe -> finalize` flow and never reads or
modifies repository content. A crashed Worker leaves its checkpoint and lease;
after lease expiry, a replacement Worker claims with a new fencing token and
resumes from the checkpoint. Unsupported graph versions enter
`recovery_required`. Coding Runs are deliberately ineligible until the real
model/tool graph exists.

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

The Leader is the only agent present initially. It decides between solo and team
mode, owns the task tree, manages Teammates, integrates accepted work, and makes
the final completion decision.

Teammates own durable responsibilities and may communicate through an auditable
mailbox. Each Teammate may independently create up to three Subagents.
Subagents do not participate in team conversation and report only to their
creator.

The Verifier is a specialized Teammate created whenever team mode starts.
Teammate output reaches the Leader only after verification passes or after
rejected work is revised and re-verified.

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
The Worker connects provider-neutral model turns to solo read-only and solo
modifying Coding graphs when a model provider is configured.

## Read-Only Coding Loop

Workers with a configured model provider also advertise the versioned
`coding + read_only + solo-readonly@1` route. The graph contains explicit
`execute_tools -> model_turn` and `feedback -> model_turn` back edges, so tool
selection and iteration count are model-driven rather than a fixed workflow.
Only evidence-backed final answers terminate successfully.

See [Read-only agent loop](docs/design-docs/read-only-agent-loop.md) for the
complete node, loop, budget, tool, failure, and recovery contract.

## Modifying Coding Loop

Workers with a configured model provider also advertise the versioned
`coding + modifying + solo-modifying@1` route. The graph loops through model
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

See [Durable execution](docs/design-docs/durable-execution.md) for the complete
target contract.

## Security Boundary

Docker is the default command execution boundary. CLI users may explicitly opt
into trusted local execution. FastAPI runs cannot use trusted-local mode.
Writing Teammates use isolated Git worktrees.

Approval is scoped to one exact canonical tool invocation. Repository
validation configuration and inferred project commands are untrusted input;
only strongly evidenced check-only commands may run automatically.
In `solo-modifying@1`, ambiguous shell execution creates a durable
`approvals` row, checkpoints with LangGraph `interrupt(value)`, releases the
worker lease as `paused + waiting`, and resumes with `Command(resume=...)`
after API/CLI decision. Resume revalidates the canonical arguments hash, tool
version, workspace fingerprint, and requested capabilities before execution.
Unsafe shell commands are denied without approval.

## Detailed Designs

See [docs/design-docs/index.md](docs/design-docs/index.md).

Repository engineering rules are under
[docs/engineering](docs/engineering/engineering-harness.md).
