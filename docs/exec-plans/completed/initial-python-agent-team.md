# Initial Python Agent Team Runtime

Status: proposed

Owner: project owner

Execution authority: implementation starts only after explicit user approval

Last updated: 2026-06-24

## 1. Objective

Build the first locally runnable version of `awesome_agent`: an observable,
traceable, harness-driven Python coding agent with a Leader, dynamically created
Teammates, isolated Subagents, mandatory team verification, Docker-backed tool
execution, PostgreSQL persistence, optional built-in memory, and optional Mem0
Platform memory.

The first release targets local development only. Server deployment,
multi-tenant authentication, and a production frontend are explicitly deferred.

## 2. Confirmed Decisions

- Python 3.12.
- `uv` for dependency and virtual environment management.
- Typer CLI.
- FastAPI local API.
- LangGraph as the orchestration runtime.
- No LangSmith integration or compatibility layer.
- OpenAI is the first model provider behind a project-owned provider interface.
- PostgreSQL is used for LangGraph checkpointing and application persistence.
- The project owns its API, worker loop, event model, and observability stack.
- FastAPI-triggered work uses Docker sandboxes.
- CLI work uses Docker by default and requires an explicit `--trusted-local`
  option to run commands on the host.
- The initial runtime contains only the Leader.
- The Leader may dynamically create and remove Teammates for complex tasks.
- The Leader may create only Teammates, never Subagents.
- Each Teammate may independently create up to three Subagents without Leader
  approval.
- Subagents have isolated context, do not participate in team conversations,
  report only to their owning Teammate, and cannot delegate further.
- Team mode supports at most six Teammates, including one mandatory Verifier.
- A Teammate may use up to three concurrent Subagents.
- Delegation depth is fixed at one.
- Maximum model concurrency is eight.
- Maximum tool concurrency is twelve.
- Maximum sandbox concurrency is six.
- Teammates communicate through an auditable mailbox visible to the Leader.
- Writing Teammates receive separate Git worktrees.
- Read-only agents receive a read-only repository snapshot.
- Subagents may produce patches in the owning Teammate's worktree. The
  Teammate decides whether to accept them.
- Team results must pass the Verifier before being reported to the Leader.
- The Verifier may create up to three verification Subagents.
- The Verifier returns failed work directly to the responsible Teammate while
  emitting an event visible to the Leader.
- The Leader alone decides whether the overall task is complete.
- Built-in `USER.md` and `MEMORY.md` memory is disabled by default and writes
  automatically only when enabled.
- Mem0 Platform memory is disabled by default and reads/writes automatically
  only when enabled.
- Mem0 stores preferences, experience, and summaries, never full source code,
  full conversations, secrets, or raw tool output.
- All agent conversations, model calls, tool calls, progress, artifacts,
  approvals, state transitions, verification reports, and memory operations
  must be queryable for a future frontend.
- Run artifacts are stored outside target repositories.
- The Leader owns a dynamic task tree for frontend visualization.
- Teammates may add child tasks only beneath work assigned to them.
- Each task has one primary owner and may have multiple collaborators.
- Team-mode tasks require `VERIFIED` before the Leader can mark them `DONE`.
- Every task-plan revision is persisted and exported as a `plan.json` artifact.

## 3. Python Source Layout Decision

Keep the following standard `src` layout:

```text
src/
`-- awesome_agent/
```

`src` is the import root and `awesome_agent` is the Python package. This layout
prevents tests and scripts from accidentally importing source files directly
from the repository root. It also makes editable installs, wheels, test
environments, and production imports behave consistently.

Moving `awesome_agent/` to the repository root is technically possible, but is
not selected because it weakens import isolation and makes packaging mistakes
easier. The `src` directory will contain one top-level package by design; that
is normal rather than redundant.

## 4. Existing File Disposition

### `AGENTS.md`

Rewrite rather than preserve.

Keep:

- repository-first knowledge
- baseline checks before implementation
- WIP limit
- evidence-based completion
- clean handoff requirements

Remove:

- references to nonexistent `claude-progress.md`
- references to nonexistent `feature_list.json`
- references to nonexistent `init.sh`
- Claude-specific naming
- detailed material that belongs in topic documents

The replacement must remain a short navigation and enforcement entry point.

### `PROGRESS.md`

Delete the root file after migrating its useful intent.

It currently contains only empty session templates. Active status will instead
live in:

- `docs/exec-plans/active/`
- the persisted task tree
- verification evidence
- Git history

A short compatibility note may be retained only if external tools prove to
depend on `PROGRESS.md`.

### `README.md`

Rewrite as the human project entry point:

- project purpose
- local quickstart
- architecture summary
- documentation map
- current maturity and limitations

### `session-handoff.md`

Delete the root file after migrating the template into the execution-plan
system. A handoff section belongs in each active execution plan. A reusable
template may be added under `docs/exec-plans/` only if it remains useful after
the first implementation cycle.

### `HARNESS_ENGINEERING.md`

Preserve its intent, but move detailed long-term guidance to:

```text
docs/design-docs/harness-engineering.md
```

Convert descriptive guidance into executable repository conventions, scripts,
schemas, tests, and CI checks. Avoid duplicating the full text across documents.

## 5. Target Repository Structure

```text
.
|-- AGENTS.md
|-- ARCHITECTURE.md
|-- README.md
|-- USER.md.example
|-- MEMORY.md.example
|-- pyproject.toml
|-- uv.lock
|-- docker-compose.yml
|-- alembic.ini
|-- .env.example
|-- docs/
|   |-- design-docs/
|   |   |-- index.md
|   |   |-- core-beliefs.md
|   |   |-- harness-engineering.md
|   |   |-- agent-team-and-subagents.md
|   |   |-- task-and-verification-model.md
|   |   |-- memory-architecture.md
|   |   `-- observability.md
|   |-- exec-plans/
|   |   |-- active/
|   |   |   `-- initial-python-agent-team.md
|   |   |-- completed/
|   |   `-- tech-debt-tracker.md
|   |-- generated/
|   |   `-- db-schema.md
|   |-- product-specs/
|   |   |-- index.md
|   |   `-- local-coding-agent.md
|   |-- references/
|   |   |-- harness-engineering-source.md
|   |   |-- uv-llms.txt
|   |   `-- postgres-llms.txt
|   |-- DESIGN.md
|   |-- FRONTEND.md
|   |-- PLANS.md
|   |-- PRODUCT_SENSE.md
|   |-- QUALITY_SCORE.md
|   |-- RELIABILITY.md
|   `-- SECURITY.md
|-- src/
|   `-- awesome_agent/
|       |-- __init__.py
|       |-- agents/
|       |-- orchestration/
|       |-- domain/
|       |-- providers/
|       |-- tools/
|       |-- sandbox/
|       |-- memory/
|       |-- persistence/
|       |-- observability/
|       |-- artifacts/
|       |-- api/
|       |-- cli/
|       `-- settings.py
|-- tests/
|   |-- unit/
|   |-- structural/
|   |-- integration/
|   `-- e2e/
|-- scripts/
|   |-- bootstrap.ps1
|   |-- check.ps1
|   |-- test.ps1
|   `-- generate_db_docs.py
`-- migrations/
```

Empty package directories will not be created without at least an interface,
model, implementation, or test that justifies them.

## 6. Harness Engineering Contract

The repository harness will contain five enforceable layers.

### 6.1 Instructions

- Keep root `AGENTS.md` concise.
- Store detailed rules in topic documents.
- Treat repository documents and persisted runtime state as the source of
  truth.
- Require assumptions to be explicit when repository evidence is absent.
- Add nested `AGENTS.md` files only when a subtree needs materially different
  rules.

### 6.2 Environment

- Pin Python to 3.12.
- Commit `uv.lock`.
- Provide one bootstrap command and one comprehensive check command.
- Use Docker Compose for PostgreSQL and supporting local services.
- Add a health command that verifies Python, dependencies, database,
  migrations, Docker, and sandbox availability.
- Fail before feature work when baseline health checks fail.

### 6.3 State and Scope

- WIP defaults to one execution-plan milestone per implementation session.
- Each milestone records scope, exclusions, files, validation, and evidence.
- The persisted dynamic task tree is the machine-readable work source.
- Execution plans are the human-readable reasoning and handoff source.
- Scope changes require an explicit task-plan revision event.

### 6.4 Feedback and Verification

- Static gate: formatting, lint, type checks, import boundaries.
- Behavioral gate: unit and integration tests.
- System gate: local application startup and end-to-end scenarios.
- Team-mode work requires independent Verifier approval.
- Validation commands and results are persisted as evidence.
- A task cannot reach `VERIFIED` or `DONE` without required evidence.

### 6.5 Harness Governance

- Encode recurring review findings as checks.
- Test architecture dependency rules.
- Track harness failures and technical debt.
- Review instructions and scripts for duplication and staleness.
- Keep the harness smaller than the product logic it protects.

## 7. Domain and State Model

Implement typed domain models for:

- `Run`
- `Team`
- `Agent`
- `AgentProfile`
- `AgentCapability`
- `AgentLifecycle`
- `SessionLineage`
- `MailboxMessage`
- `TodoItem`
- `TodoDependency`
- `TodoAssignment`
- `TodoRevision`
- `ConversationMessage`
- `ModelCall`
- `ToolCall`
- `ToolProgress`
- `ApprovalRequest`
- `Artifact`
- `VerificationReport`
- `MemoryCandidate`
- `MemoryOperation`
- `RuntimeEvent`

Task states:

```text
TODO
READY
IN_PROGRESS
BLOCKED
SUBMITTED
VERIFYING
REJECTED
VERIFIED
DONE
CANCELLED
```

Agent lifecycle states:

```text
CREATED
READY
RUNNING
WAITING
PAUSED
COMPLETED
FAILED
CANCELLED
DELETED
```

Every state change must produce an immutable runtime event.

## 8. Leader, Team, and Subagent Runtime

### 8.1 Leader

The Leader:

- starts as the only agent
- analyzes task complexity
- creates and owns the dynamic task tree
- chooses solo or team mode
- creates, pauses, resumes, and deletes Teammates
- assigns work and capabilities
- observes all mailbox traffic and descendant activity
- integrates accepted Teammate output
- requests user approval for high-risk actions
- makes the final completion decision

### 8.2 Team Mode

Enter team mode when at least one of the following applies:

- multiple independent workstreams exist
- multiple engineering specialties are necessary
- work can benefit materially from parallelism
- responsibilities need persistent context across several stages
- the task exceeds a single reliable context boundary

Creating a team also creates exactly one primary Verifier. The Verifier counts
toward the six-Teammate limit.

### 8.3 Teammates

Teammates:

- have stable identities for the run
- receive isolated LangGraph state and checkpoints
- own a mailbox
- own an independent worktree when writing
- may communicate directly with other Teammates
- may create and delete their own Subagents
- may create child tasks only below assigned tasks
- submit structured results and evidence

### 8.4 Subagents

Subagents:

- are created only by a Teammate
- have independent context
- do not read or write the team mailbox
- do not interact with the user
- do not create descendants
- return results only to their owner
- may read a repository snapshot or create a temporary patch
- are destroyed after their bounded task completes

### 8.5 Verifier

The Verifier:

- is mandatory in team mode
- remains independent from implementing Teammates
- reads diffs, artifacts, task acceptance criteria, and test evidence
- may run tests and create up to three verification Subagents
- cannot repair implementation directly
- returns rejected work to the owning Teammate
- emits a structured `VerificationReport`
- reports verified team output to the Leader

## 9. Concurrency and Resource Controls

```yaml
team:
  max_teammates: 6
  require_verifier: true
  max_subagents_per_teammate: 3
  delegation_depth: 1

concurrency:
  max_model_calls: 8
  max_tool_calls: 12
  max_sandboxes: 6
```

Use process-wide asynchronous semaphores with fair queuing. Agent existence does
not imply active model execution. Waiting agents retain state without consuming
a model-call slot.

## 10. Persistence

### 10.1 PostgreSQL

Use PostgreSQL for:

- LangGraph checkpoints
- runs, teams, agents, and lineage
- dynamic task plans and revision history
- messages and mailbox traffic
- model/tool call metadata
- approvals
- verification reports
- artifact metadata
- memory operation audit records
- runtime event indexes

Use SQLAlchemy 2 async for project-owned tables and Alembic for migrations.
Use the supported LangGraph PostgreSQL checkpointer rather than reimplementing
checkpoint semantics.

### 10.2 Event Integrity

- Runtime events are append-only.
- Mutable read models may be rebuilt from events.
- Each run uses monotonic sequence numbers.
- Each child operation records parent IDs.
- Sensitive fields are redacted before persistence.

## 11. Dynamic Task Plan

The Leader creates the authoritative task tree after initial analysis.

- The Leader may add, remove, split, merge, and reorder tasks.
- Every revision is retained.
- Teammates may update assigned tasks and add child tasks.
- Subagents cannot edit the task tree.
- The Verifier moves submitted tasks to `VERIFYING`, `REJECTED`, or `VERIFIED`.
- Only the Leader moves team-mode tasks from `VERIFIED` to `DONE`.
- Each task has one primary owner and optional collaborators.
- Blockers, dependencies, acceptance criteria, and evidence are first-class.
- Every meaningful revision exports an updated `plan.json` artifact.

## 12. Provider Layer

Define a project-owned provider protocol for:

- model invocation
- streaming
- structured output
- tool-call messages
- usage and cost metadata
- cancellation
- model capability discovery

Implement OpenAI first. Keep provider-specific message formats outside domain
and orchestration modules. Do not add a second provider in the first milestone.

## 13. Tool Registry and Approval

Create a centralized `ToolRegistry`.

Each tool declares:

- name and version
- typed input and output
- risk level
- compatible agent profiles
- required capabilities
- sandbox requirements
- timeout
- progress event support
- approval policy
- redaction policy

Command decisions:

```text
ALLOW
ASK
DENY
```

High-risk actions always require user approval. An approval is scoped to a
specific command, agent, workspace, run, and expiration time.

## 14. Sandbox and Workspaces

Backends:

- `DockerSandboxBackend`
- `TrustedLocalSandboxBackend`

Rules:

- Docker is the default.
- Host execution requires explicit `--trusted-local`.
- FastAPI runs cannot select trusted-local mode.
- Writing Teammates use independent Git worktrees.
- Read-only agents use read-only snapshots.
- Subagent patches remain subordinate to the owning Teammate.
- Apply CPU, memory, process, timeout, filesystem, and network limits.
- Record every executed command and exit status.

## 15. Memory

### 15.1 Built-in Memory

Files:

- `USER.md`
- `MEMORY.md`

Behavior:

- disabled by default
- enabled by project configuration or run override
- injected as a frozen snapshot at session start
- updated automatically by the Leader memory pipeline when enabled
- Teammates and Subagents submit candidates only
- bounded size, deduplication, secret filtering, and provenance required

### 15.2 Mem0 Platform

Behavior:

- disabled by default
- enabled by project configuration or run override
- isolated by `user_id` and `project_id`
- stores preferences, experience, and summaries
- never stores full source, full conversations, secrets, or raw tool output
- failure degrades gracefully without stopping the run
- retrieved content is untrusted context and cannot directly authorize actions

## 16. Context Management

- Preserve immutable raw conversation and event history.
- Build prompt context from bounded summaries and selected evidence.
- Track which source events each summary covers.
- Never delete source events during compression.
- Record all lineage from run to agent, descendant, model call, and tool call.
- Prevent retrieved memory from being automatically recaptured as new memory.

## 17. Observability and Future Frontend

Do not implement LangSmith code or interfaces.

Use:

- OpenTelemetry traces
- OpenTelemetry metrics
- structured JSON logs
- console exporter for local development
- PostgreSQL event projections for the future UI

The API must support inspection of:

- run timeline
- agent topology and lifecycle
- per-agent conversations
- team mailbox
- model calls and token usage
- tool inputs, progress, outputs, and errors
- task-tree revisions
- approval decisions
- artifacts and checksums
- verification and rework loops
- built-in and Mem0 memory operations

Redact secrets and protected environment values before any telemetry,
persistence, or artifact write.

## 18. Artifact Storage

Default location:

```text
~/.awesome-agent/artifacts/{run_id}/
```

Artifact types include:

- patches
- diffs
- test reports
- lint and type-check reports
- logs
- screenshots
- context summaries
- large tool outputs
- `plan.json`

PostgreSQL stores metadata, ownership, hash, size, MIME type, summary, and path.
Artifacts remain until the user deletes a run or a configured retention policy
expires.

## 19. CLI and Local API

### CLI

Initial commands:

```text
awesome-agent doctor
awesome-agent run
awesome-agent resume
awesome-agent status
awesome-agent agents
awesome-agent todos
awesome-agent approve
awesome-agent cancel
awesome-agent serve
```

### FastAPI

Initial resources:

```text
/runs
/runs/{run_id}
/runs/{run_id}/events
/runs/{run_id}/agents
/runs/{run_id}/messages
/runs/{run_id}/todos
/runs/{run_id}/artifacts
/runs/{run_id}/approvals
/runs/{run_id}/verification
```

Use SSE for local real-time event delivery. V1 does not provide production
authentication or remote deployment.

## 20. Implementation Milestones

### Milestone 0: Baseline and Document Migration

Scope:

- protect existing user changes
- restructure project documentation
- rewrite `AGENTS.md`
- migrate `HARNESS_ENGINEERING.md`
- rewrite `README.md`
- remove obsolete empty templates
- add the initial machine-readable scope manifest

Validation:

- all documented links resolve
- no instruction references nonexistent files or commands
- repository status and migration choices are recorded

### Milestone 1: Python and Harness Bootstrap

Scope:

- initialize `pyproject.toml`
- create package and test roots
- pin dependencies
- add bootstrap, doctor, check, and test commands
- configure Ruff, mypy, pytest, and coverage

Validation:

- clean environment installs with `uv sync`
- package imports only after installation
- doctor command reports baseline health
- static checks and empty test suite pass

### Milestone 2: Domain, Events, and Persistence

Scope:

- implement domain models and state transitions
- configure PostgreSQL and migrations
- configure LangGraph PostgreSQL checkpointing
- implement append-only runtime events
- generate database documentation

Validation:

- migration upgrade/downgrade tests
- state transition tests
- event ordering and lineage tests
- checkpoint save/resume integration test

### Milestone 3: Provider and Solo Leader

Scope:

- implement provider protocol
- implement OpenAI provider
- implement Leader solo graph
- implement dynamic task-tree creation
- implement context and usage recording

Validation:

- provider contract tests
- mocked solo run
- interrupted run resumes from checkpoint
- task revisions and `plan.json` are persisted

### Milestone 4: Tools, Approvals, and Sandbox

Scope:

- implement centralized tool registry
- implement command approval
- implement tool progress callbacks
- implement Docker and trusted-local backends
- implement Git worktree management

Validation:

- allow/ask/deny policy tests
- Docker filesystem and resource-boundary tests
- trusted-local cannot be selected by FastAPI
- commands and results are fully traceable

### Milestone 5: Team and Subagent Runtime

Scope:

- implement complexity decision
- implement Teammate lifecycle
- implement mailbox
- implement Teammate-owned Subagents
- implement concurrency limits
- implement worktree ownership

Validation:

- simple task remains solo
- complex task creates a team
- maximum counts are enforced
- Subagents cannot access team conversation or delegate
- mailbox and lineage are auditable

### Milestone 6: Verification Loop

Scope:

- create mandatory Verifier in team mode
- implement submission and rejection flow
- implement verification Subagents
- implement structured verification reports
- enforce `VERIFIED` before `DONE`

Validation:

- rejected work returns to the original Teammate
- Verifier cannot repair implementation
- Leader cannot complete unverified team work
- verification evidence remains queryable

### Milestone 7: Memory and Compression

Scope:

- implement built-in memory switches
- implement Leader memory pipeline
- integrate Mem0 Platform behind its own adapter
- implement context compression and fencing

Validation:

- both memory systems default off
- project/run overrides work
- prohibited content is filtered
- Mem0 outage does not fail a run
- summaries retain source provenance

### Milestone 8: Observability, Artifacts, CLI, and API

Scope:

- instrument OpenTelemetry
- implement artifact storage
- complete Typer commands
- implement FastAPI inspection resources
- implement SSE event streaming

Validation:

- run is reconstructable from persisted events
- every agent conversation and tool result is queryable
- frontend-facing schemas are stable
- SSE reconnect resumes from an event cursor
- artifact hash and download checks pass

### Milestone 9: End-to-End Harness Validation

Scenarios:

- solo read-only task
- solo trusted-local task with explicit opt-in
- team frontend/backend task
- Teammate with parallel Subagents
- mandatory verification rejection and rework
- approval pause and resume
- process interruption and PostgreSQL resume
- memory enabled and disabled
- Mem0 unavailable
- sandbox timeout and cancellation

Completion requires static, behavioral, and system gates to pass.

## 21. Quality Gates

Run gates in this order:

1. formatting and lint
2. type checking
3. unit tests
4. structural architecture tests
5. integration tests
6. application startup
7. end-to-end scenarios

Do not advance after a failed lower gate. Record command, timestamp, exit code,
and artifact reference for every required gate.

## 22. Explicit Non-Goals for V1

- production deployment
- LangGraph Agent Server
- LangSmith
- production web frontend
- multi-user authentication and authorization
- billing
- multiple model providers
- remote cloud sandboxes
- automatic GitHub pull requests
- autonomous memory when memory switches are disabled
- unbounded recursive agent delegation

## 23. Risks

- Windows Docker and worktree path behavior may require targeted adapters.
- PostgreSQL checkpoint and application transaction boundaries must remain
  separate.
- High event volume may require payload offloading to artifacts.
- Team parallelism can create merge conflicts despite worktree isolation.
- Model concurrency may exceed account rate limits and must support backoff.
- Mem0 content must be treated as untrusted external context.
- Full traceability increases privacy and storage obligations.
- Harness rules can become excessive and must be periodically simplified.

## 24. Completion Criteria

This execution plan is complete only when:

- all milestones have recorded evidence
- all required quality gates pass
- solo and team end-to-end scenarios pass
- Team mode cannot bypass the Verifier
- task, agent, message, tool, artifact, approval, and memory history is queryable
- process interruption can resume through PostgreSQL checkpoints
- Docker remains the default execution boundary
- documentation matches actual commands and architecture
- no instruction references missing files
- the active plan is moved to `docs/exec-plans/completed/`
- unresolved risks are transferred to the technical debt tracker

## 25. Session Handoff

Update this section whenever implementation pauses.

- Current milestone: none; implementation plan completed
- Current scope: final documentation, clean status, commit, and handoff
- Completed:
  - Milestone 0 document migration
  - root `AGENTS.md`, `README.md`, and `ARCHITECTURE.md`
  - layered design, product, quality, reliability, and security documents
  - removal of obsolete `PROGRESS.md`, `session-handoff.md`, and old
    `docs/ARCHITECTURE.md`
  - machine-readable milestone state
  - Python 3.12.13 managed by `uv` 0.11.24
  - locked package environment and standard `src` layout
  - Typer `doctor` and version commands
  - strict Ruff, mypy, pytest, and coverage gates
  - typed run, agent, todo, and runtime event models
  - enforced todo state transitions and structural domain boundary test
  - PostgreSQL Compose service, SQLAlchemy async persistence, and Alembic
  - LangGraph PostgreSQL checkpoint integration
  - generated database schema documentation
  - project-owned provider protocol and OpenAI Responses adapter
  - Solo Leader LangGraph and versioned `plan.json`
  - centralized tools, approval policy, progress model, Docker/local sandbox
  - dynamic Teammates, mailbox, isolated Subagents, worktree provisioning
  - mandatory Verifier rejection/rework/completion gate
  - built-in memory, Mem0 Platform adapter, filtering and compression
  - OpenTelemetry, events, artifacts, SSE, FastAPI, and Typer CLI
- Validation executed:
  - parsed `initial-python-agent-team.status.json` with PowerShell
    `ConvertFrom-Json`
  - scanned all Markdown relative links; all resolved
  - searched active instructions for stale file/command references
  - inspected Git diff and status
  - `scripts/bootstrap.ps1`: Python, Git, and Docker passed
  - `scripts/check.ps1`: Ruff, mypy, and 10 tests passed
  - coverage: 91.67%, above the 80% gate
  - Alembic upgrade, downgrade, and re-upgrade passed
  - PostgreSQL event repository round-trip passed
  - LangGraph checkpoint write/read round-trip passed
  - 55 regular tests passed with 85.91% coverage
  - 7 system tests passed against PostgreSQL, Docker and real Uvicorn
  - source tree contains no LangSmith imports
- Evidence:
  - `docs/exec-plans/active/initial-python-agent-team.status.json`
  - command output recorded in the 2026-06-24 implementation session
- Blockers: none
- Unverified paths:
- Unverified paths:
  - real OpenAI network call, because no project API key was required or used
  - real Mem0 Platform network call, because no project API key was required
  - production deployment and production frontend, which are V1 non-goals
- Working-tree state: implementation is ready for final review and commit
- Next action: address TD-004 by rehydrating local API projections from
  PostgreSQL before server deployment work

## 26. Milestone Evidence

### Milestone 0

Status: completed

Scope:

- migrate useful content from existing root documents
- establish layered Harness Engineering documentation
- remove stale instructions and empty progress/handoff templates
- add machine-readable plan state

Exclusions:

- no Python package
- no dependency installation
- no database or Docker service changes

Validation:

- active plan state parsed successfully
- all local Markdown links resolved
- active instructions contain no required nonexistent commands or files
- Git changes were limited to documentation and plan state

### Milestone 1

Status: completed

Scope:

- install and pin Python 3.12 tooling
- initialize the Python package and locked environment
- implement health checks
- configure static, type, unit, structural, and coverage gates

Exclusions:

- no PostgreSQL schema
- no agent orchestration
- no provider calls

Validation:

- `scripts/bootstrap.ps1` passed with Python 3.12.13, Git, and Docker 29.5.3
- `scripts/check.ps1` passed
- Ruff formatting and lint passed
- strict mypy passed for 9 source/test files
- 10 tests passed
- branch coverage reached 91.67%

### Milestone 2

Status: completed

Scope:

- typed domain entities and state transitions
- append-only runtime event persistence
- PostgreSQL, SQLAlchemy async, Alembic, and LangGraph checkpointer
- generated database documentation

Exclusions:

- no Leader graph
- no model provider calls
- no tools or sandbox

Validation:

- static and strict type gates passed
- Alembic upgrade, downgrade, and re-upgrade passed against PostgreSQL 17
- event repository wrote and read a real PostgreSQL event
- LangGraph saver wrote and read a real checkpoint
- standard unit/structural suite passed at 91.61% coverage

### Milestones 3-8

Status: completed

Delivered:

- provider boundary, OpenAI adapter, Solo Leader graph, and task-plan snapshots
- tool registry, approval policy, progress callbacks, and execution backends
- Team/Teammate/Subagent lifecycle, mailbox, workspaces, and concurrency
- mandatory independent verification and rejection/rework flow
- built-in and Mem0 memory with context compression and source fencing
- OTel setup, event stream, artifacts, CLI, FastAPI, and SSE

Validation:

- strict Ruff and mypy gates passed after every milestone
- unit and structural coverage remained above 80%
- no source module imports LangSmith
- Docker remains the default and API cannot select trusted-local execution

### Milestone 9

Status: completed

Validation:

- `scripts/check.ps1`: 55 passed, 2 infrastructure tests skipped when their
  environment variables were absent, 85.91% coverage
- `scripts/system-test.ps1`: 7 passed
- PostgreSQL migrations and event round-trip passed
- LangGraph PostgreSQL checkpoint round-trip passed
- Docker and trusted-local timeout behavior passed
- Git worktree create/remove passed
- Team E2E covered two workers, per-worker Subagents, mandatory Verifier,
  rejection, rework, verification, and Leader completion
- real Uvicorn startup responded successfully at `/health`
