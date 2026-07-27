# Workspace, Thread, Turn, and Operation

This page is for users and contributors who need to understand identity,
concurrency, cancellation, and persistence. The central rule is that a project,
a conversation, a request, and live execution are four different things.

## Workspace: the Project Boundary

The directory from which `awesome` starts is the Workspace. Core resolves it
to a canonical path and records a filesystem identity. Trust is associated
with that canonical Workspace, while the live session also holds path- and
identity-based leases so two Awesome processes cannot recover or mutate the
same project through different aliases at the same time.

Trust happens before project-controlled inputs are activated. The persistent
trust key and the live identity/lease checks are deliberately separate:

```text
resolve candidate -> identity/state precheck -> ask for trust
                                                |
                       no: exit <---------------+
                                                |
                       yes: acquire path/entity leases
                                      |
                                      v
                         revalidate identity -> activate trusted inputs
```

A trusted Workspace may influence context and declare extensions. It does not
give arbitrary host paths to built-in file tools, disable permission checks, or
create an OS sandbox. If the bound root is replaced while Awesome runs, later
identity checks fail instead of silently following the new directory.

## Session: the Running Authority Boundary

A Session is one TUI plus its private Python Core process. It owns:

- the active Workspace and runtime leases;
- one immutable root `AGENTS.md` snapshot;
- the selected Thread;
- the foreground arbiter and any pending interaction;
- the current permission mode and temporary capability grants;
- a session-only queue of up to three later inputs in the TUI.

Closing Awesome ends these in-memory authorities. Durable conversation and
journal data remain available for a later session.

## Thread: the Conversation Boundary

A Thread is a durable conversation tied to one Workspace. It contains ordered
user, assistant, and direct-command entries; Turns; usage; summaries; and Tool
activity. It also stores future-Turn choices such as model, Thinking mode, and
Skill mode.

Use:

```text
/new
/resume
/resume <thread_id>
/fork [turn_id]
/retry [turn_id]
/rename Dependency graph review
```

`/new` creates a clean Thread; it does not delete the previous one. `/resume`
only offers Threads from the current Workspace. Selecting or changing Threads
clears temporary permission grants and returns the permission session to
Request approval. An old Full access confirmation cannot apply to the newly
selected Thread.

The first accepted natural-language request gives a new Thread an automatic,
bounded title. `/rename` makes the title user-selected. Titles organize
conversation history; they do not affect Workspace identity or model context.

### Materialized Thread branches

Fork and retry create independent Thread records from a terminal Turn in the
selected Thread. `/fork` includes the target Turn; `/retry` includes the prefix
before it, then creates a fresh user entry and in-progress Turn for the same
request. Omitting the ID selects the latest terminal Turn. An in-progress Turn
cannot be a materialization target.

This is a physical prefix copy with new Thread, entry, Turn, checkpoint-key,
and client-message identities. The destination stores immediate-parent
lineage—`fork` or `retry`, source Thread ID, and source Turn ID—but does not
share a history DAG with its source. Summary, checkpoint, Tool activity, and
ChangeSet records are not copied. A retry freezes the target Turn's Provider,
model, Thinking, Skill, and budgets into its fresh Turn. It executes normally;
old tool calls are not replayed and their prior side effects are not undone.

## Turn: the Durable Request Boundary

A Turn represents one natural-language request. Before model execution, Core
persists the user entry and freezes the facts needed to explain or resume the
request: Provider/model, Thinking and Skill choices, budgets, context manifest,
and checkpoint identity.

A Turn has exactly one terminal state:

- `completed`: a durable assistant entry exists;
- `failed`: an error code explains why work stopped;
- `cancelled`: the user cancelled before normal completion.

`in_progress` is only valid while execution is live or awaiting startup
recovery. A direct `! command` is deliberately not a Turn: it invokes the
normal Tool Executor under explicit user authority and is stored as a direct
command plus Tool activity.

## Operation: the Live Concurrency Boundary

An Operation is the exclusive lease for a natural-language Turn or direct
command. State-changing slash commands and credential changes use a related
exclusive mutation lease. Core grants at most one mutable foreground owner.

```text
request A ---- acquire lease ---- execute ---- release
request B ---------X operation_busy
```

The same ordering holds in the opposite direction: a slow state-changing
command blocks a new Turn, and a live Turn blocks state-changing commands. This
single-owner rule prevents two actions from racing over the selected Thread,
permission generation, ChangeSet, or database lifecycle.

At the Core command boundary, only these side-effect-free snapshots are
admitted while a normal Operation owns the foreground:

```text
/context  /workspace  /tools  /mcp  /mcp status [id]
/status   /usage      /config
```

`/diff` is not in this set because it can read a ChangeSet while it is being
written. `/doctor` is not in it because it can contact Providers. This is a
Core concurrency contract for trusted protocol callers, not the current TUI
submission behavior: Ink queues every later input, including a snapshot
command, until the Operation finishes. The exact command contract is listed in
[Commands](../reference/commands.md).

## Interaction: Paused, Not Unowned

Trust, tool approval, Full access confirmation, state reset, and Turn recovery
are interactions. A pending interaction blocks new Operations and state
changes until the user resolves or cancels it. Core can exempt read-only
snapshots after normal command handling is active; startup trust and state-reset
prompts occur before that workflow, and the current TUI queues later input in
either case.

A tool approval carries the Thread, Turn, and Operation that created it.
Resolution rechecks those facts. Full access confirmation separately binds the
Thread and permission generation. This makes a stale response an error instead
of authority for a newer conversation.

## Queueing and Cancellation

While a task runs, the TUI can queue up to three messages, slash commands, or
direct shell commands. This includes the snapshot commands that Core itself
could admit concurrently. They start in submission order after the foreground
owner releases its lease. The queue is an interface convenience, not durable
workflow state; exiting loses pending inputs.

Press Ctrl+C to request cancellation. Core bounds cleanup, terminates managed
process trees when possible, emits one terminal outcome, and then releases the
lease. Cancellation cannot prove that an external system did nothing. If a
shell or MCP call may already have acted, the audit and recovery paths preserve
that uncertainty rather than replaying automatically.

## Choosing the Right Boundary

| Goal | Use |
| --- | --- |
| Continue the same problem with its history | Same Thread, new Turn |
| Start an unrelated task in the same project | `/new` |
| Return to earlier work | `/resume` |
| Explore from a completed point without changing its history | `/fork [turn_id]` |
| Run a terminal request again with its original settings | `/retry [turn_id]` |
| Run a command you already chose | `! command` |
| Inspect current state from the current TUI | Wait or cancel, then use a snapshot command |
| Change permissions or configuration | Wait for the Operation/interaction to finish |

## Failure and Recovery

If you see `operation_busy`, let the current item finish or cancel it; retrying
concurrently does not increase throughput. If you see `interaction_busy`, find
the visible prompt and resolve it. After a crash, follow the startup recovery
prompt rather than creating a second process or deleting state manually.

Continue with [Context and instructions](context-and-instructions.md) and
[Changes and recovery](changes-and-recovery.md).
