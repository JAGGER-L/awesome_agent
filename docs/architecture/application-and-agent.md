# Application and Agent

Awesome separates product lifecycle from reasoning execution. Application
answers “may this work start, and how does it become a durable product fact?”
Agent answers “given an admitted Turn, what model/tool transition happens
next?”

Combining these roles would make protocol concerns leak into graph state and
make graph routing a second product scheduler. Keeping them separate provides
one cancellation point, one operation authority, and one checkpoint owner.

## Ownership matrix

| Concern | Application | Agent |
| --- | --- | --- |
| workspace trust and activation | owns | does not inspect |
| bootstrap phase and pre-ready admission | owns | does not inspect |
| selected Thread and effective config | owns | receives frozen values |
| Turn creation and terminal status | owns | returns graph result |
| foreground admission and cancellation | owns | cooperates with cancellation |
| interaction presentation and resolution | owns | tool call waits through injected executor |
| graph topology and routes | invokes | owns |
| provider message/tool-chain validity | observes result | owns |
| model/tool/compression budgets | supplies config | owns counters and routing |
| graph checkpoints | reconciles lifecycle | LangGraph writes graph channels |
| transcript and bounded activity | owns product records | produces facts to persist |
| concrete providers/storage/extensions | composes | depends on neutral contracts |

## Application boundary

`LocalApplication` implements `ApplicationFacade`, the only surface-facing
product API. It converts expected `ApplicationFailure` exceptions into typed
`ApplicationResult` values. Concrete composition remains behind its backend;
the protocol cannot reach repositories, providers, or tools directly.

```text
Protocol dispatcher
  -> ApplicationFacade
  -> LocalApplication
  -> composed backend
       -> lifecycle/command service
       -> Conversation or Storage port
       -> compiled Agent graph
```

Application responsibilities are deliberately split into focused modules:

- `facade.py`: stable surface contract and expected failure envelope;
- `bootstrap.py`: bootstrap phase transitions and pre-ready admission;
- `composition.py`: activation and concrete dependency wiring;
- `foreground.py` and `operations.py`: atomic foreground ownership;
- `turns.py`: Turn execution, finalization, cancellation, and recovery;
- `dispatcher.py` plus command services: deterministic slash commands;
- `interactions.py`: typed decisions and authority bindings;
- `context.py`: per-Turn context capture and frozen manifest projection;
- `events.py`: product facts projected to the surface.

`composition.py` may be large because it owns wiring and startup sequencing. It
must not become a home for command semantics, graph routes, arbitrary result
construction, or presentation formatting.

### Bootstrap phase ownership

`LocalApplication` owns one concrete `ApplicationBootstrap`; no Protocol or UI
component may mutate `BootstrapPhase`. The coordinator begins uninitialized.
An initialize invocation moves it to initializing before asynchronous work
starts, then consumes the typed `ApplicationResult[InitializeResult]`: ready,
trust-required, and state-reset-required results select the corresponding
phase, while failure or cancellation restores the previous phase. Repeating
initialize after ready remains ready throughout, so a surface retry cannot
close an already active Application.

The coordinator also binds a bootstrap interaction to its exact identity. A
trust response reaches ready only after the typed interaction result confirms
acceptance and backend activation has returned successfully. A stale, denied,
failed, or cancelled response cannot advance the phase. Accepting state reset
keeps Application non-ready until a later initialize completes.

Surfaces ask Application for a surface-neutral admission decision before
dispatch. The stdio Host maps a rejection to the existing Protocol v5
`-32002` diagnostics, but never keeps a second state machine or parses a
serialized request/result payload to infer readiness. Cancellation and
shutdown remain admitted in every phase. This is an internal ownership change:
Protocol v5 wire shapes, status values, and error semantics remain Application-owned.

### Workspace runtime snapshot

Successful trusted activation publishes one immutable `WorkspaceRuntime`.
The snapshot contains resolved configuration plus the stable workspace service
graph: Conversation, Turn coordination, command services, Tool Registry, Model
Catalog, context and extensions, memory and MCP, Change Journal services, and
one `RuntimeResources` owner.
Mutable lifecycle coordination such as foreground ownership, pending
interactions, permission grants, recovery delivery, and process shutdown stays
on the Application backend.

A top-level request binds `_runtime` once in an Application-owned request
context. Awaited callbacks and foreground-owned child tasks inherit that
immutable snapshot instead of reassembling dependencies from independent
backend fields. Detached tasks do not acquire an independent runtime reader
lease and are not a supported ownership boundary. Publication follows
one sequence: build the complete candidate from local values, validate it,
reconcile startup state when this is an activation, confirm there is no
foreground Operation, and assign the runtime pointer once. Recovery notification
then runs against the published candidate; notification failure is reported but
does not restore the old runtime.

Requests admitted before publication continue to see the old runtime while new
requests see the new one. `RuntimeResources` gives each candidate an independent
generation identity and reader count; retirement drains that generation before
its `AsyncExitStack` closes, so publication never closes a resource beneath a
paused reader. The stack owns reusable provider clients, an internally created
Mem0 client, and MCP. Registration order makes shutdown run exactly once in the
reverse order MCP, Mem0, provider. Injected gateway and Mem0 objects are borrowed
and are never closed by Awesome. Candidate construction failure or cancellation
closes the whole candidate stack while leaving the old runtime and request
authority unchanged. Candidate construction clears the caller's runtime
binding, while retirement and close tasks start in a clean context, so
long-lived resources do not retain the previous generation.
Provider and credential mutations build a
complete candidate from the committed snapshot without repeating startup
reconciliation, carry the selected Thread into that candidate without firing a
selection callback, publish it atomically, and retire the old runtime only after
the bound mutation request exits. Cleanup failures are reported without hiding
the candidate's primary failure or skipping process cleanup. The checkpoint
saver, one process-owned `ApplicationSQLite` worker, and state leases remain
owned by a separate process-lifetime Application `AsyncExitStack` across
runtime generations. The database worker serializes Application-facing
repository calls without occupying the event-loop thread.

### Application invocation diagnostics

The diagnostic sink is owned by the process/session Application lifecycle, not
by `WorkspaceRuntime`. Runtime publication therefore cannot split an invocation
across log owners, and replacing a workspace runtime does not close the writer.
The writer uses a bounded queue and performs file I/O away from the caller. A
full queue or logging failure is fail-open: it may lose a diagnostic record but
cannot delay, fail, or change the Application invocation.

`ObservationalMiddleware` records one allowlisted JSON object for a completed
facade invocation. Its fields are `version`, `timestamp`, `session_id`,
`correlation_id`, `operation`, `outcome`, and `duration_ms`, with optional
`error_code` and bounded `usage`. It does not serialize arbitrary arguments,
results, exceptions, or events. In particular, prompts, model and Tool bodies,
queries, URLs, paths, secrets, and arbitrary payloads never enter this log.

The invocation outcome describes only the facade call observed by the
middleware. Some calls admit asynchronous Agent work and return before the
Turn reaches a terminal state. A successful invocation is therefore not an
Agent Turn success record; Turn lifecycle events and durable Conversation state
remain the authorities for that outcome.

## Foreground serialization

`ForegroundArbiter` has three lease kinds: Operation, exclusive, and resolving
interaction. It records the owning `asyncio.Task`, rejects any second owner,
and refuses all new leases after closing begins.

`OperationController.reserve()` acquires the lease synchronously before the
Turn coordinator can persist a new Turn. `start_reserved()` emits
`operation.started`, starts one task, and owns terminal event delivery and
lease release in `finally`.

Admission is not a check performed before that lease. After acquiring it,
`reserve()` synchronously revalidates the current pending interaction before it
publishes an active Operation ID. Ordinary Turns and Direct commands require no
pending interaction. Recovery resume is the only exception: it carries an
internal continuation bound to the current recovery interaction ID,
interaction generation, Thread, and Turn. A stale or partially matching token
cannot degrade into a generic bypass.

This in-process arbiter is different from storage leases:

- the foreground lease serializes semantic work inside one Core session;
- the state lease coordinates state replacement across Core processes;
- workspace path and entity leases prevent two sessions from treating the same
  workspace generation as independent recovery domains.

None is an OS sandbox.

## Agent graph

`agent/graph.py` is the only module that imports and builds a LangGraph
`StateGraph`. The compiled topology is intentionally small:

```text
START
  -> prepare_context
       | enough context
       +-----------------> call_model
       | compression needed
       +-> compress_context -> call_model | finalize

call_model
  | tool calls -> execute_one_tool --+
  | compression ----------------------|-> compress_context
  | answer or terminal budget --------+-> finalize -> END

execute_one_tool
  | more pending calls -> execute_one_tool
  + next model step ----> call_model
```

The graph operates on `AgentState`, a strict checkpoint contract containing:

- Thread, Turn, workspace, provider, model, and Thinking identity;
- context manifest, token estimate, effective limit, and compression request;
- provider-neutral messages and continuation state;
- pending tool calls, next-call index, and results;
- model/tool/Web/retry/compression/active-time counters;
- usage, recovery issue, final answer, and termination reason.

`tool_results` stores each complete serialized `ToolResult`, including its
tuple of minimal Core `Citation` values. After each result, Agent derives the
ordered Turn snapshot in `AgentState.citations`; both that snapshot and the
`web_requests` counter survive checkpoint recovery. Finalization validates
`[[S1]]` markers, Conversation persists the same sources with the assistant
entry, and Protocol v5 projects them to the TUI and headless surfaces.

Adding a channel changes checkpoint compatibility and recovery validation. It
is not a convenient place for arbitrary UI or product state.

## Model/tool loop invariants

The Agent must preserve these properties for every route:

1. Context is prepared before the first model request.
2. Provider messages use only `awesome_agent.modeling` contracts.
3. Tool calls from one assistant message are observed in order.
4. Every emitted tool call receives exactly one observation before the next
   assistant request.
5. A budget-skipped call receives a deterministic non-executed error
   observation; it is not silently dropped.
6. Expected tool failures are observations; invariant failures stop the Turn.
7. Compression preserves the active assistant/tool tail exactly once.
8. Finalization reserves a model call when ordinary loop progress is exhausted.

The one-tool-at-a-time node is a correctness choice. Parallel execution could
reduce latency but would require defining ordering, approval concurrency,
ChangeSet conflicts, cancellation fan-out, and deterministic replay. Awesome
does not claim those semantics today.

## Budgets

`TurnBudget` defaults and hard ceilings are enforced in Agent code:

| Budget | Default | Maximum |
| --- | ---: | ---: |
| model calls | 32 | 256 |
| tool calls | 64 | 512 |
| active execution | 1,800 seconds | 21,600 seconds |
| provider retries | 2 | 6 |
| compressions | 2 | 10 |

Active execution time is charged around model, tool, and compression segments;
it is not wall-clock age while the user considers an approval. The last model
capacity can be reserved with tools disabled so a bounded final response is
still possible.

Budget counters are checkpointed. Recovery validates them against the product
Turn and rejects impossible or open message chains rather than restarting from
an inferred state.

## Context capture and graph invocation

Application captures explicit path snapshots and enabled local memory before
graph execution, then Agent's `prepare_context` node calls the injected context
service. The prepared manifest is recorded as a product projection and in graph
state. Subsequent compression can rebuild bounded base context while preserving
the active tool tail.

```text
Application accepts Turn
  -> capture natural input / explicit paths / local memory
  -> create initial AgentState
  -> graph.ainvoke(..., thread_id=turn.id)
  -> Agent prepare_context asks injected service
  -> manifest + messages enter checkpoint
```

The Application service owns access to Conversation and workspace snapshots;
Agent remains unaware of concrete SQLite repositories or filesystem discovery.

## Post-answer finalizer port

Agent owns one provider-neutral `PostAnswerFinalizer` port and its strict,
immutable `PostAnswerFinalizationRequest` and `PostAnswerFinalizationResult`
values. The request contains the user text, already-generated answer, selected
model, workspace identity, remaining model/retry budgets, and the ordered
citations collected from `tool_results`. Collection preserves first appearance,
collapses an identical repeated ID, and treats the same ID with different
values as an invariant failure. More than 128 unique Turn citations is likewise
an aggregation invariant failure before the finalizer runs. The request carries
that bounded tuple without adding an `AgentState` channel.

The result contains an answer whose stripped value is nonblank, zero or one
primary model call, bounded `ModelUsage`, and at most 32 generic
`PostAnswerDiagnostic` values. Nonzero usage is valid only with one reported
model call. Agent strictly rebuilds the returned value and rejects it if
provider retries exceed the remaining retry budget or if the primary call plus
retries exceed the remaining model-call budget. Active-time exhaustion forces
the request's remaining model calls to zero, but the finalizer is still invoked
so a no-model implementation can complete. A valid result replaces the answer,
charges one primary call plus its provider retries, merges its usage, and
projects each diagnostic's exact code and message.

`DisabledPostAnswerFinalizer` returns the existing answer unchanged.
Application may instead inject `memory.Mem0PostAnswerFinalizer`; Agent neither
imports Memory nor knows Mem0 identities, adapters, statuses, or diagnostics.
If the injected finalizer raises, returns an invalid value, or exceeds its
budget, Agent emits `answer_finalization_failed` and keeps the already-generated
answer and its existing usage. Cancellation does not project a warning from
Agent: it preserves the prior checkpointed answer and immediately re-raises the
caller's original cancellation to the Application lifecycle. It is not normal
completion. Optional finalization cannot turn the answer into an empty or
partially updated result.

## Completion and cancellation

On normal graph completion, Application validates the returned state, appends
the assistant answer, records bounded usage, and completes the Turn. It then
attempts to seal the ChangeSet and remove the checkpoint. On failure or
cancellation it records a stable terminal product fact and performs the same
durable finalization. Local transcript, activity, ChangeSet, and checkpoint
work remains owned until it reaches a known result. Cleanup exceptions are
deliberately suppressed after the primary terminal fact; startup reconciliation
retries stale terminal checkpoints rather than changing the
completed/cancelled/failed outcome.

The Operation phase makes the cancellation boundary explicit. Before the
commit point, a matching cancel changes `running` to `cancelling` and the sole
terminal outcome is cancelled. Durable finalization changes `running` to
`committing` before it asks the Application SQLite worker to persist the
completed or failed Turn. A matching `operation.cancel` then returns false and
shutdown waits instead of issuing another cancellation. If the request task is
cancelled while a durable write is already admitted, Core waits until the
worker reports a known COMMIT or ROLLBACK result before re-raising the first
caller cancellation. Bounded shielded publication preserves the committed Turn
and Operation terminal events even if the event sink fails. There is no
interval in which both cancellation and completion can win.

Cancellation may arrive while a model stream, tool, or finalization step is
active. Local durable fact preservation is shielded until it reaches a known
result, and the foreground lease remains owned for that interval. Only external
process cleanup and best-effort event delivery use bounded deadlines. Neither
path may swallow the original cancellation.

## Recovery relationship

Application does not deserialize a checkpoint and continue blindly. It
validates identity, budgets, message roles, context anchors, active tool tail,
and termination state. Valid graph state can be finalized or resumed; invalid
state fails with a stable recovery code. Uncertain external operations require
an explicit decision.

The separate Application and checkpoint databases create a commit window.
Recovery converges it with strict lineage and compare-and-swap, not with a
second graph implementation. Details are in
[Storage and recovery](storage-and-recovery.md).

## Dependency rules

Agent may import only Agent, Core, and Modeling packages. It cannot import
Application, Memory, Storage, Protocol, providers, or the TUI. Application is
the top Python composition layer and may depend on current adapters.
`tests/structural/test_dependency_architecture.py` and
`tests/structural/test_product_architecture.py` enforce these directions and
the single `StateGraph` owner.

## Tradeoffs

- **One graph, more Application coordination:** lifecycle code is explicit,
  but there is no ambiguous second runtime.
- **One tool at a time, deterministic recovery:** lower concurrency, simpler
  observations and ChangeSet ownership.
- **Separate databases, clear owners:** no cross-database transaction; strict
  recovery is required.
- **Typed events, more contract work:** a new fact must update Python,
  fixtures, TypeScript schemas, and presentation instead of falling through a
  generic renderer.

## Source and test map

- Facade and composition: `application/facade.py`, `application/composition.py`
- Invocation diagnostics: `application/middleware.py`,
  `application/diagnostics.py`
- Admission: `application/foreground.py`, `application/operations.py`
- Turns and recovery: `application/turns.py`
- Graph, state, and finalizer port: `agent/graph.py`, `agent/state.py`,
  `agent/nodes.py`, `agent/finalization.py`
- Budgets: `agent/budgets.py`
- Unit tests: `tests/unit/application/`, `tests/unit/agent/`
- Integration: `tests/integration/test_agent_turn.py`,
  `tests/integration/test_agent_recovery.py`
- Structural: `tests/structural/test_application_architecture.py`,
  `tests/structural/test_agent_architecture.py`
