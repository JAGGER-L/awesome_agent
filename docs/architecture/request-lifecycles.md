# Request lifecycles

Awesome has one product surface but several kinds of requests. They share
identity, admission, event, cancellation, and shutdown rules; they do not all
become Agent Turns.

## Lifecycle vocabulary

| Term | Meaning | Durable? |
| --- | --- | --- |
| Workspace | canonical project root plus a stable workspace key | trust and history are durable |
| Thread | selected conversation and its effective settings | yes |
| Turn | one accepted natural-language request and its result | yes |
| Operation | one foreground Turn or direct shell execution | session identity; lifecycle events are live |
| Exclusive action | state-changing command, credential mutation, interaction resolution, or shutdown | depends on the action |
| Interaction | typed user decision required before progress | pending state is session-local |
| ChangeSet | file mutations and conservative shell observations associated with work | yes |

The foreground arbiter admits only one Operation, exclusive action, or
interaction resolution at a time. Admission must happen before a Turn is
persisted; otherwise a losing race would leave a ghost Turn that never ran.
The decisive pending-interaction check runs inside the acquired Operation
lease, after any asynchronous preflight. Only recovery resume may present a
continuation, and it must exactly match the current interaction ID, generation,
Thread, and Turn.

## Inputs, outputs, and invariants

| Request | Authoritative input | Success output | Durable effect |
| --- | --- | --- | --- |
| initialize | workspace and compatible client identity | ready state or typed bootstrap interaction | trust/state setup when accepted |
| Turn | Thread, natural input, client message ID | Operation ID, events, final transcript | Turn, entries, usage, ChangeSet |
| direct command | Thread and command | Operation ID and bounded transcript entry | direct entry, audit, ChangeSet |
| slash command | typed `CommandIntent` | discriminated outcome | command-specific |
| interaction | interaction ID and allowed decision | accepted/rejected/stale status | decision-specific |
| cancellation | active Operation ID | cancellation acknowledgement | terminal facts and known changes |
| shutdown | empty request | stopped acknowledgement | clean resource closure |

Across every lifecycle, identity is explicit, no second foreground mutation is
admitted, expected busy/failure states are typed, cancellation is propagated,
and an uncertain side-effecting action is never replayed without a user
decision.

## Startup and trust

```text
awesome [workspace]
  -> Ink starts one private awesome-core
  -> initialize(protocol=4, client identity)
  -> resolve candidate workspace identity
  -> shared-lease read-only state preflight
  -> if migration_required:
       exclusive lease -> recheck -> SQLite backup -> one transaction
       -> downgrade to shared lease -> initialize repositories
  -> trust lookup
  -> trust decision, if required
  -> after acceptance/already trusted: acquire path + entity leases
  -> recheck workspace root identity under those leases
  -> load project-controlled configuration and extensions
  -> snapshot root AGENTS.md
  -> reconcile unfinished Turns
  -> select/create Thread
  -> ready ApplicationState
```

The order is security-sensitive. State compatibility can be inspected before
trust because it does not read project instructions. Workspace configuration,
workspace Skills, MCP declarations, `AGENTS.md`, and tools are activated only
after trust. An untrusted candidate does not hold a workspace lease while the
user decides. Acceptance (or an existing trust record) is followed by acquiring
both leases and rechecking identity before activation. A rejected trust
decision exits and is not persisted as a denial.

The production migration floor is 7 and the current schema is 8. The one
registered 7→8 step adds nullable Thread lineage while preserving existing
Threads with `lineage = null`. Schemas 1–6 therefore produce a typed
reset-or-exit interaction. Migration keeps
`application.db.pre-migration.bak` for manual recovery and never triggers an
automatic reset or restore. Newer, unknown, corrupt, unreadable, or locked
state stops safely and is never silently deleted. A confirmed reset runs under
the bootstrap lock, a foreground interaction-resolution lease, and an
exclusive cross-process state lease.

Failure before `ready` restores or leaves the Application-owned
`ApplicationBootstrap` in a non-ready phase. The protocol handshake is only an
admission projection of that fact and remains closed. See
[Storage and recovery](storage-and-recovery.md) for state classification and
[Protocol and TUI](protocol-and-tui.md) for bootstrap admission.

## Natural-language Turn

```text
turn.submit(thread_id, content, client_message_id)
  -> validate input and selected Thread
  -> reject a pending interaction
  -> validate configured provider
  -> reserve foreground Operation and revalidate pending interaction atomically
  -> begin Turn + append user entry in Application SQLite
  -> prepare immutable per-Turn inputs
  -> start Operation task and emit operation.started
  -> invoke compiled Agent graph with thread_id == Turn ID
       -> prepare context
       -> call model
       -> execute zero or more tools
       -> finalize answer
  -> complete/cancel/fail product Turn
  -> attempt to seal its ChangeSet
  -> attempt to remove terminal checkpoint
  -> emit exactly one Operation terminal event
  -> release foreground lease
```

Local durable cleanup after the primary terminal fact remains owned until its
result is known. Failure does not rewrite a completed, cancelled, or failed
Turn; startup reconciliation can retry leftover checkpoint cleanup. External
process cleanup and best-effort event publication retain bounded deadlines.

The worker-owned durable Turn transition and Operation phase form one commit
point. Before it, cancellation wins; after it, cancellation is rejected and
bounded terminal publication preserves the already committed completed or
failed outcome. Shutdown observes the same phase and waits rather than issuing
a second cancellation.

The TUI's `client_message_id` correlates an optimistic message with the
authoritative accepted Turn. `operation_id`, `thread_id`, and `turn_id` bind
events to the same execution. A Thread generation on the TUI side prevents late
events from a previous selection from mutating the new surface.

The first accepted message updates an automatic title, appends the user entry,
and creates the Turn in one Application transaction. A later model failure does
not erase an accepted user message. It terminates that Turn visibly.

### Tool approval is a continuation

When policy returns `ASK`, the Tool Executor creates an interaction bound to
the active Thread, Turn, Operation, and permission generation. The Operation
remains the owner while it awaits the response:

```text
Agent tool call
  -> policy asks
  -> interaction.required
  -> TUI returns interaction.respond
  -> verify interaction + Thread + Turn + Operation identities
  -> allow once / grant thread writes / deny
  -> continue the same Tool call
```

Tool approval deliberately bypasses ordinary exclusive admission. Acquiring a
second foreground lease would deadlock the Operation waiting for its own
decision. Every other interaction resolution uses a resolving lease.

## Direct shell command

Input beginning with `!` does not create an Agent Turn and is never hidden in a
model prompt. It still creates a foreground Operation and an open ChangeSet:

```text
! command
  -> direct.execute
  -> validate Thread and pending interaction
  -> reserve Operation
  -> ToolExecutor(execute, origin=direct)
       -> strict-validate registered execute arguments
       -> registered hard admission
       -> typed description exactly once
       -> emit tool.started
       -> Direct capability policy
       -> deadline + Process Runner + bounded process cleanup
  -> bounded transcript entry + ChangeSet observation
  -> seal ChangeSet and emit terminal Operation event
```

Direct execution uses the same registered admission, command policy, process
runner, redaction, deadline, cancellation, and audit path as an Agent `execute`
call. It does not become reversible merely because it has a ChangeSet;
arbitrary shell effects remain unmanaged.

It is independent of the selected Thread permission mode. The user's exact
`! command` is the authorization, so Application supplies that Direct Operation
with its own Full-access permission session and does not open the ordinary shell
approval interaction. Hard denial and every execution/cleanup boundary above
remain active.

Transcript persistence and ChangeSet sealing are primary-outcome-preserving
finalizers. If execution has already failed or been cancelled, a later
persistence or sealing failure is reported without replacing that original
exception. A cancelled Direct Operation therefore emits `operation.cancelled`,
even when both finalizers fail; focused regressions cover successful, failed,
and cancelled paths.

## Slash command

Slash commands are deterministic product operations. They never submit a
natural-language Turn:

```text
/command
  -> Ink parser and owner catalog
  -> Ink-owned command: local presentation action
     or
  -> command.execute
  -> immutable Core CommandDispatcher
  -> focused command service
  -> discriminated CommandOutcome
  -> optional authoritative state effect
  -> exhaustive Presenter
```

Fork and retry use the same command boundary and exclusive foreground
admission. The source is re-read and fingerprinted before one SQLite
transaction materializes a prefix with new identities. Fork publishes a
`thread_transition` only after the independent Thread exists. Retry first
creates the independent prefix and fresh in-progress Turn, then starts the
ordinary Turn path and returns one combined `thread_retry` payload containing
both the transition and Operation acceptance. No checkpoint, ToolActivity, or
ChangeSet is copied, and no old tool call is replayed.

Retry Events may reach stdio before the command response. Ink therefore gates
them before sequence reduction, applies the authoritative retry transition,
binds the accepted Operation to the new Thread generation, and only then
replays the buffered Events in sequence. Identity mismatch fails the protocol
instead of projecting an Event into the old Thread.

During an active Operation, only the following side-effect-free observations
may cross the Core gate:

- `/context`
- `/workspace`
- `/tools`
- `/mcp` and `/mcp status [id]`
- `/status`
- `/usage`
- `/config`

`/diff` is excluded because it reads a ChangeSet being mutated; `/doctor` is
excluded because diagnostics may contact a provider. A pending interaction
allows the same observations but blocks state changes. This whitelist is a
Core/private-protocol capability. The current Ink composer does not dispatch
even whitelisted input immediately while an Operation is active; it queues all
submitted input for later promotion. That queue is presentation-only and
creates no second Core scheduler.

## Permission change

Request approval and Accept edits can be applied as an exclusive command.
Selecting Full access first creates a safe-default confirmation bound to the
selected Thread and current permission generation:

```text
/permissions full_access
  -> exclusive command lease
  -> create Full access confirmation
     default: keep current mode
  -> release command lease
  -> interaction.respond
  -> resolving lease
  -> recheck selected Thread + permission generation
  -> emit resolved event
  -> apply Full access only for a matching confirmation
```

Changing Thread or mode invalidates the old confirmation and clears temporary
capability grants. MCP and unknown extension capabilities remain one-call
approval even in Full access.

## Cancellation

`operation.cancel` addresses one Operation ID. Cancellation propagates through
the Operation task into the model stream or Tool Executor. For a Turn,
Application then records cancellation facts and retains foreground ownership
until local ToolActivity, transcript, ChangeSet sealing, and checkpoint deletion
reach known results. It then emits `operation.cancelled` and releases the lease.
Cleanup failure does not replace the cancelled terminal fact and can be retried
during startup reconciliation. For a shell process, the Process Runner performs
bounded process-tree and pipe cleanup and then re-raises the original
`CancelledError`. Direct transcript and ChangeSet finalizers preserve that
primary cancellation as it crosses the Application boundary. Event delivery is
bounded best-effort and never shortens local durable ownership.

A true cancellation acknowledgement means the matching Operation had not yet
crossed its commit point; this includes the observable window after
`operation.started` but before the acceptance response. In that starting
window, cancellation prevents the factory from running. Once completion or
failure is committed, cancellation returns false and cannot replace its
terminal event.

Cancellation is not rollback. File changes already captured by the journal
remain visible and can later be inspected or undone. External shell or MCP
effects may already have occurred.

## Resume and crash recovery

Startup reconciles each non-terminal product Turn with its checkpoint:

```text
unfinished Turn + checkpoint
  |-- completed valid graph state -> finalize product records
  |-- resumable + currently registered replayable tool -> resume
  |-- non-replayable, missing, or unknown metadata -> interaction: Abort | explicit Retry
  |-- missing, corrupt, or conflicting state -> fail Turn with stable code
```

The checkpoint's frozen manifest may repair an empty or lineage-matching
Application projection only with compare-and-swap. A self-consistent but
unrelated checkpoint is not accepted as authority. Recovery processes other
Turns even if one fails.

For an interrupted tool call, recovery looks up the same name in the current
Runtime Registry and uses that registration's replay-safety metadata; it does
not branch on concrete tool names. A proven local built-in may be replayable.
File mutation, MCP, and Web tools are non-replayable, and missing or unknown
metadata fails closed. Those cases never retry automatically: the interaction
defaults to Abort, while an explicit Retry may continue the old checkpoint and
repeat the pending call.
Changing a same-named tool contract must therefore account for checkpoint
compatibility.

This recovery Retry is distinct from `/retry [turn_id]`. Recovery may continue
one unfinished checkpoint after explicit approval; the slash command requires
a terminal Turn, creates a fresh Thread and Turn, and never reuses or copies the
source checkpoint.

## Shutdown

Shutdown closes foreground admission first. It cancels an active Operation only
while that Operation is still running, waits for any committing Operation and a
separate exclusive owner, and then waits until the arbiter is idle. It retires
the workspace runtime in reverse resource order, closes the checkpoint saver,
drains and closes the process-owned Application SQLite worker, and finally
releases workspace and state leases under the bootstrap lock.

```text
shutdown request
  -> foreground.begin_closing()
  -> cancel RUNNING Operation / await COMMITTING Operation
  -> cancel exclusive owner if external
  -> wait_idle()
  -> retire runtime (MCP, Mem0, providers)
  -> close checkpoint saver
  -> drain and close ApplicationSQLite
  -> release workspace and state leases
  -> mark Application closed
```

This order prevents a new mutation from starting after database teardown has
begun.

## Failure semantics

- malformed input fails before admission or persistence;
- a busy foreground returns a typed, retryable busy result;
- expected tool errors become bounded Agent observations;
- unexpected graph/tool errors terminate the Operation visibly;
- one Operation produces one terminal lifecycle event;
- uncertain side-effecting outcomes are never replayed automatically;
- event-delivery failure after durable completion is logged rather than
  changing the already-completed product fact.

## Design tradeoffs

- Serial foreground work limits throughput but gives approvals, ChangeSets,
  cancellation, and recovery one unambiguous owner.
- Session-local interactions avoid persisting stale prompts, but a restarted
  session must reconstruct recovery decisions from durable facts.
- TUI input queuing improves flow without promising Core-side parallelism.
- Explicit uncertain-outcome decisions add friction in exchange for avoiding
  duplicate side effects.

## Source and test map

- Admission: `application/foreground.py`, `application/operations.py`
- Turn lifecycle: `application/turns.py`, `conversation/service.py`
- Direct execution: `application/direct.py`
- Commands: `application/commands.py`, `application/dispatcher.py`
- Interactions: `application/interactions.py`, `application/composition.py`
- Recovery: `application/turns.py`, `storage/checkpoints.py`
- Tests: `tests/unit/application/`, `tests/integration/test_agent_recovery.py`,
  `tests/integration/test_recovery_interactions.py`,
  `tests/integration/test_state_reset_concurrency.py`
