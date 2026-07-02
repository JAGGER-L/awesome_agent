# Durable Execution

## Purpose

This document defines the target contracts for the durable local coding
runtime. It is an implementation boundary for the roadmap tasks that add
repository intake, dispatch, workers, model/tool loops, approvals,
cancellation, and validation.

The contracts are target behavior. Features not yet implemented must not be
presented as current runtime capability.

## State Authorities

The runtime deliberately uses multiple durable representations with separate
responsibilities.

| State | Authority | Purpose |
| --- | --- | --- |
| graph checkpoint | LangGraph PostgreSQL saver | next executable graph position and resumable agent context |
| business projection | project PostgreSQL tables | user-visible Run, Agent, Todo, ToolCall, Approval, and Verification state |
| dispatch state | project PostgreSQL tables | queue eligibility, lease, heartbeat, attempt, and fencing token |
| event history | ordered runtime events | bounded audit and frontend history |
| observability query tables | project PostgreSQL tables | trace, metric, and model-call inspection by Run |
| large evidence | artifact storage | raw or oversized safe outputs, patches, reports, and logs |

Runtime events are not a replay-complete event-sourcing system. Reconstructing a
Run uses domain projections and checkpoints; events explain how it reached the
current state. Observability query tables are also not execution authorities:
they make run/model/tool/sandbox behavior inspectable, while checkpoints,
dispatch rows, projection rows, and durable side-effect records remain the
execution source of truth.

Visible Run, Agent, and Todo lifecycle changes are committed through
transaction-scoped projection helpers. A status transition updates the
projection row, maintains `updated_at`, increments Agent/Todo `revision` when a
visible field changes, and appends the matching runtime event in the same
transaction. Worker heartbeat updates `heartbeat_at`; it does not by itself
change the product `updated_at`.

## Product and Dispatch State

`RunStatus` and `DispatchStatus` are independent.

Target `RunStatus` values:

```text
created
running
paused
completed
failed
cancelled
recovery_required
```

Target `DispatchStatus` values:

```text
queued
claimed
executing
waiting
retry_scheduled
terminal
```

Examples:

| Situation | RunStatus | DispatchStatus |
| --- | --- | --- |
| API accepted the run | `created` | `queued` |
| worker owns execution | `running` | `executing` |
| approval interrupt persisted | `paused` | `waiting` |
| valid approval re-enqueued the run | `running` | `queued` |
| completed successfully | `completed` | `terminal` |
| stores cannot be reconciled safely | `recovery_required` | `terminal` |

The API exposes both values. Product UI may present a combined label but cannot
discard either state.

Current implementation note: the Worker executes `runtime_probe`, distributed
team graphs `team-coding`, `team-role`, and `team-verifier`. When a model
provider is configured, it also executes `solo-readonly`,
`solo-modifying`, and scoped `team-coding-scoped`. Modifying solo Runs can pause
for durable exact-invocation approval and resume from the LangGraph checkpoint.
Distributed team parent Runs release to child-wait states; child Runs commit
terminal assignment state and requeue the waiting parent.

## Dispatch, Lease, and Fencing

V1 uses PostgreSQL as the queue.

1. A worker claims an eligible row transactionally with
   `FOR UPDATE SKIP LOCKED`.
2. Claiming records worker identity, attempt, lease timestamps, and a
   monotonically increasing fencing token.
3. Heartbeats extend the lease before a configured safety margin.
4. A worker must include its fencing token in every protected state
   transition.
5. After lease loss, the stale worker may terminate local work but cannot
   commit projection changes, events, or external side effects.
6. An expired lease makes non-terminal work eligible for recovery.

Lease safety must not depend on exactly synchronized clocks. PostgreSQL time is
the authority for lease acquisition and expiry decisions.

Defaults are a 60-second lease, 15-second heartbeat interval, and three maximum
claims. Heartbeat is a direct conditional Worker-to-PostgreSQL update; FastAPI
and Agents do not relay it. Expired ownership cannot be revived.

Claim and heartbeat remain internal and have no public API.
Queued, retry-scheduled, and waiting Runs cancel atomically. Claimed and
executing Runs store `cancel_requested_at`, `cancel_requested_by`, and
`cancel_reason`; the owning Worker observes the request through the dispatcher,
cancels the graph task, and commits `cancelled + terminal` only after execution
reaches a safe boundary.

## Current Probe Graph

Task 04 persists `execution_kind` and `runtime_route`. The Worker claims only
`runtime-probe` and executes:

```text
initialize -> checkpoint_probe -> finalize
```

The graph uses `AsyncPostgresSaver` with `durability="sync"` and a stable
Run-scoped thread ID. A replacement process resumes an incomplete checkpoint.
If the graph checkpoint is complete but the terminal projection is missing,
the Worker reconciles the fenced projection without replaying graph nodes.
Unsupported runtime route or corrupt runtime state enters
`recovery_required`; `/resume` does not bypass that state.

## Transition Identity and Reconciliation

Every replay-sensitive graph transition has a stable transition ID scoped to a
Run and graph execution.

Projection writes are idempotent by transition ID. Side-effecting tool calls
have stable invocation and idempotency IDs.

Recovery cases:

### Checkpoint and Projection Agree

Resume from the checkpoint normally.

### Checkpoint Ahead

The graph transition checkpoint exists but its projection is missing. Replay
the idempotent projection update and event append, then continue.

### Projection Ahead

The projection records a completed transition but the checkpoint did not
advance. Load the recorded transition result into graph state and skip its
external side effect.

### Irreconcilable

If transition identity, side-effect state, or workspace state cannot establish
a safe result, set `RunStatus.recovery_required`, stop automatic execution, and
preserve the workspace and evidence.

Task implementations must introduce deterministic fault points around
checkpoint and projection commits so all cases can be tested.

## Repository Identity and Authorization

Repository identity and filesystem authorization are separate controls.

PostgreSQL registry records include:

- stable repository ID;
- resolved repository root;
- display name and Git metadata;
- enabled/disabled state;
- last observed revision and timestamps.

Local configuration contains allowed roots. Registration and every Run start
must:

1. resolve the candidate repository and allowed root;
2. verify containment after symlink and junction resolution;
3. require a Git repository for modifying runs;
4. record the selected base commit.

The CLI requires `--repo PATH` and resolves or registers it only after local
allowed-root validation. API run creation accepts `repository_id`, never an
arbitrary filesystem path. Registration, listing, disabling, and explicit
relocation are local CLI operations; FastAPI exposes repository list/get only.

Task 02 creates a private durable intake reservation before Git side effects.
The Run, Leader, initial events, and reservation publication share one
PostgreSQL transaction after workspace readiness. Startup reconciliation rolls
back incomplete owned worktrees. A half-provisioned intake is never exposed as
a public Run.

## Workspace Contract

- Every new Run starts only from a clean primary Git checkout in V1, including
  untracked files and in-progress Git operations.
- Both read-only and modifying Runs receive a dedicated integration branch and
  worktree from the captured full base commit.
- The original checkout is never modified automatically.
- LocalSandbox selects a trusted local command backend for local CLI/TUI use; it
  does not permit direct edits to the original checkout.
- Intent controls later tool capabilities; read-only does not bypass workspace
  isolation.
- Team worktrees later branch from the Run integration branch.
- V1 retains Run worktrees until explicit workspace cleanup.

Workspace cleanup is dry-run-first. It resolves and verifies the target,
confirms runtime ownership, rejects active leases, checks branch identity, and
preserves unexported diffs unless the caller uses force with a reason. Normal
cleanup deletes only clean managed workspaces for terminal completed or
cancelled Runs. Failed or dirty workspaces require force; `recovery_required`
workspaces are retained as recovery evidence. Background automatic retention
deletion and snapshots of uncommitted user changes are deferred.

## Local Process Topology

Target commands:

```text
awesome-agent serve   # API only
awesome-agent worker  # one durable worker
awesome-agent start   # local supervisor for API plus one worker
```

API request handling never owns the lifetime of a coding run. The supervisor is
a convenience process, not a different execution architecture. If either child
exits, the supervisor stops its sibling. During Worker shutdown, claiming stops
while the active Run retains heartbeat for a bounded grace period.

## Structured Model and Tool Turns

The internal protocol represents:

- system, user, assistant, and tool messages;
- provider-neutral assistant reasoning continuation;
- structured tool calls with stable IDs;
- JSON-schema-validated arguments;
- structured tool results and errors;
- stop reason and continuation state;
- token usage and provider response identity;
- retry classification.

Provider adapters translate SDK-specific responses. DeepSeek thinking
continuation data is preserved without exposing DeepSeek SDK objects to
orchestration.

Prompt context is bounded. Durable call records store safe metadata, usage,
status, summaries, and artifact references rather than unrestricted prompts or
responses.

Task 16 implements concrete context and budget controls for solo coding graphs.
Before each solo provider call, the runtime estimates prompt tokens. Crossing
the soft context threshold triggers artifact-backed compaction: removed
messages and oversized tool observations are persisted as artifacts, while the
checkpoint keeps a deterministic summary and recent evidence. Crossing the hard
context threshold disables further tools and forces a bounded final response.
The raw evidence remains inspectable through artifact refs and
`context_compactions`.

Each Run has a durable token ledger for input, output, reasoning tokens,
model-call count, threshold status, and active Worker execution seconds. Active
time is counted only while a Worker is inside graph execution; queued, paused,
and approval-wait time is excluded. Task 18 extends the same ledger model to
distributed team roots by aggregating the root Run and descendants at team graph
boundaries. Large team handoff, child-result, verifier evidence, and mailbox
payloads are compacted into artifacts and recorded through
`context_compactions`. Runtime budget enforcement remains token-based; monetary
spend limits are not a runtime budget concept.

Task 34 replaces heuristic-only prompt estimates with provider/model-aware
token accounting. `TokenAccountant` selects a model profile, counts message
content, tool calls, and tool definitions, and records whether the estimate is
exact or calibrated with an error margin. Pre-call budget checks and context
compaction use these estimates; post-call budget ledgers continue to use
provider-reported `ModelUsage` as the durable source of truth. Unknown models
fall back to a conservative character heuristic, so budget safety remains
bounded even without a provider tokenizer.

Current implementation note: Task 05 implements this provider-neutral protocol
and streaming adapters. Visible reasoning and private continuation are separate:
displayable reasoning may later reach the frontend, while continuation is
checkpoint-only and omitted from public serialization. The Coding Worker does
not consume model turns until Task 06.

Task 06 adds `solo-readonly`, a checkpointed model-tool-model loop with
explicit tool and feedback back edges. Read-only tool failures return to the
model as structured observations; retryable infrastructure failures release
the Worker for durable retry; normal permanent execution failures become
`failed`, while corrupt state remains `recovery_required`.

## Approval Contract

V1 approvals apply to one exact invocation only.

An approval binds:

- run, agent, and optional task lineage;
- tool-call ID and tool version;
- canonical argument hash;
- workspace identity and expected base state;
- requested capabilities and risk;
- expiry.

The default expiry is 60 minutes and the configurable maximum is 24 hours.

The approval request, graph checkpoint, and pending state must be durable before
the worker releases its lease. Decisions use compare-and-set semantics and are
idempotent. Resume revalidates every binding before executing the same
invocation.

For `solo-modifying`, ambiguous shell commands now follow this path:

1. `ToolExecutor` raises `ApprovalRequired`.
2. The graph upserts one `approvals` row, emits `approval.requested`, and calls
   LangGraph `interrupt(value)`.
3. After the synchronous checkpoint is durable, the worker releases its lease as
   `RunStatus.PAUSED + DispatchStatus.WAITING`.
4. API or CLI approval updates the durable row and requeues the Run.
5. Worker reclaim resumes with `Command(resume=...)`; the graph revalidates
   arguments hash, tool version, workspace fingerprint, and capabilities before
   executing.

Denial or expiry becomes a structured tool result. V1 does not support
run-scoped or persistent command-class approvals.

## Validation Contract

The target repository owner may commit `.agents/validation.toml` to define
ordered validation gates. Configuration is versioned and treated as untrusted
repository input.

Without configuration, conservative detection may infer only check-only
commands supported by strong evidence, such as:

- pytest configuration in `pyproject.toml`;
- Ruff or mypy configuration in `pyproject.toml`;
- an explicit `lint` or `test` script in `package.json`.

Detection must not automatically run dependency installation, migrations,
deployment, publication, network operations, unknown Make targets, Docker
Compose, or write-capable formatters.

Clearly read-only detected checks may execute automatically in Docker. Custom,
ambiguous, networked, installation, migration, deployment, or write-capable
commands require approval.

Task 10 implements this contract for `solo-modifying`. The graph resolves a
configured or conservatively detected validation plan after the final diff,
executes gates through the central shell/tool boundary, persists validation
reports and gate results, and only finalizes after required gates pass. If no
validation gates exist, the Run fails with `no_validation_gates`.

Required gate command failures are reworkable and feed bounded evidence back
to the model. Approval denial or expiry, policy denial, timeout, command not
found, Docker unavailable, invalid configuration, no gates, cancellation, or
corrupt state are terminal validation failures rather than rework prompts.

Formatting checks and formatting fixes are separate operations. A Verifier may
run check-only validation but cannot modify implementation.

Solo completion requires:

- no pending approval or active tool call;
- an accepted diff from at least one applied patch;
- every required gate passed;
- no unrecorded uncertainty that invalidates the result.

## Team Runtime Contract

Team execution is explicit. CLI `--team` or API `mode: "team"` routes a coding
Run to distributed `team-coding`; default read-only and modifying Runs remain
solo.

Task 13 implements a scoped v1 team graph:

- intake creates only the Leader;
- the graph creates durable Teammate, Verifier, and Subagent records inside the
  same Run;
- Leader assignments contain `allowed_tools`, `allowed_skills`, write,
  delegation, and acceptance constraints;
- Teammates may use only the tools granted by the assignment, and repository
  tools still pass through the central `ToolExecutor`;
- Subagents have isolated context, do not join team conversation, and return
  evidence only to their owning Teammate;
- model calls, tool invocations, Todo transitions, verification reports,
  runtime events, and observability records are durable and queryable.

Verifier rejection caused by model output quality can trigger bounded rework by
the responsible Teammate. Task 13 defaults to 10 verification-rejection reworks.
Verifier execution or external failures use a separate retry budget and default
to one retry. These defaults are intentionally conservative and tracked as
policy-tuning debt.

Task 17 adds distributed `team-coding`; Task 22A begins replacing deterministic
root coordination with model-driven planning:

- the root Leader Run calls the Leader model for a structured `TeamPlan`;
- the plan is validated, retried once on invalid output, and recorded through
  `team.plan_created` / `team.plan_rejected` events;
- the accepted plan creates 1-3 durable Teammate child Runs;
- the Leader cannot include Verifier assignments, `subagent_goals`,
  `delegation_guidance`, or Subagent task direction in the plan;
- Teammates with delegation permission can create dynamic read-only Subagent
  child Runs through `team.create_subagent`;
- the root Leader creates a Verifier child Run before finalization;
- child Runs are independently claimable through the normal PostgreSQL
  dispatch queue;
- `team_assignments` stores role, runtime route, permissions, status, and
  handoff context, including deferred and promoted tool exposure;
- `team_mailbox_messages` stores route-restricted communication, read/respond
  lifecycle, and response links. The root Run mailbox view is the Leader audit
  view for all team messages;
- `team_child_results` stores summaries, patch artifact references, changed
  files, aggregation status, and failure classification;
- child terminal state requeues waiting parents;
- parent cancellation recursively propagates to nonterminal descendants.

Task 18 adds root-aware team budget checks, deferred assignment tool filtering,
and artifact-backed compaction for large team payloads. Task 22B adds
assignment-scoped model/tool loops for Teammate child Runs. The model receives
only effective assignment tools, execution rechecks authorization, read-only
roles require repository evidence before finalization, writing roles must call
`repo.diff` after the last write, and patch artifacts are generated from the
child workspace diff. Task 22C adds dynamic Teammate-owned Subagent creation
with depth, authorization, and active-count limits. Task 22D adds structured
model-driven Verifier decisions and persists them through child results and
mailbox messages. Task 22E adds durable targeted replacement rework while
preserving original attempts.

## Retry, Cancellation, and Failure

- Retries are bounded and classified.
- Model calls may retry only before a committed tool side effect.
- Side-effecting tools require idempotency or an explicit non-retry policy.
- Cancellation is a durable request checked by the Worker during active graph
  execution. `asyncio.CancelledError` is preserved across model and tool
  boundaries instead of being classified as retryable failure.
- Active subprocesses receive bounded termination. Docker-backed shell tools
  use managed container names and attempt forced container removal on timeout
  or cancellation.
- Projection changes and their event append share one transaction when they
  describe the same domain transition. Run status events include previous and
  next product/dispatch status; Agent and Todo status events include revision
  and updated timestamp.
- Events contain bounded summaries; large output is stored as an artifact.
- Terminal failure preserves workspace, diff, evidence, and recovery reason.

## Test and Fault Matrix

Later roadmap tasks must cover:

| Area | Required evidence |
| --- | --- |
| repository access | allowed-root containment, symlink and Windows junction escape |
| dispatch | two-worker claim race, heartbeat, expiry, takeover |
| fencing | stale worker transition and event rejection |
| reconciliation | checkpoint-ahead, projection-ahead, irreconcilable state |
| model protocol | native tool calls, malformed arguments, continuation |
| tool safety | traversal, patch conflict, output offload, idempotency |
| approval | approve/deny/expire races, changed hash, duplicate decision |
| cancellation | model, subprocess, Docker, and approval wait |
| validation | detection, configured gates, order, short-circuit, restart |
| workspace | clean-base requirement, retained diff, explicit safe cleanup |
