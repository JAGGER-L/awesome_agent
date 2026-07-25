# Application and LangGraph

`awesome_agent.application` provides the product lifecycle around a directly
invoked LangGraph Agent. It is a composition boundary, not a second graph
engine.

## Application owns

- workspace trust and resolved configuration;
- Thread/Turn creation and terminal lifecycle state;
- one foreground operation, cancellation, and interaction responses;
- slash-command dispatch and direct-input routing;
- concrete composition of providers, tools, Memory, Skills, MCP, and Storage;
- typed event projection to the surface;
- reconciliation between product Turns and checkpoints at startup.

## Command authority

Core-owned slash commands have one execution path:

```text
Ink command controller
  -> Protocol v3 command.execute
  -> LocalApplication facade
  -> complete CommandDispatcher
  -> one focused command service
  -> CommandOutcome
  -> exhaustive TUI Presenter
  -> current transcript path
```

`CommandDispatcher` owns the complete immutable inventory. Focused services own
conversation, context, provider configuration, changes, extensions,
diagnostics, and permission semantics. `composition.py` injects those services
but does not branch on command names or construct outcomes. Slash commands are
deterministic product operations and never submit hidden model prompts.
Ink-owned presentation commands never enter Core RPC; natural-language input
is the only path that starts an Agent Turn.

Conversation commands also own Thread naming semantics. A new Thread begins
with automatic title provenance. Accepting its first natural-language message
atomically commits the normalized automatic title, user Entry, and Turn.
`/rename <title>` persists manual provenance through a typed deterministic
result; `/new` rejects arguments and has no hidden title path.

`LocalApplication` is the only surface-facing Application API. Command progress
belongs to the Surface pending lifecycle and is not persisted as another
operation state machine.

Application serializes exactly one foreground Core Operation. Ink may retain a
bounded, session-only input queue for terminal convenience, but it cannot start
a second Operation, persist queued input, or pre-bind queued input to a Thread.
Each item re-enters the ordinary parser and controller only after the foreground
Operation and any exclusive interaction have ended.

`InteractionResolved` acknowledges that Core accepted the user's decision; it
does not claim that later workspace activation has completed. Security-upgrade
decisions fail closed: Core delivers the resolution while holding the resolving
lease, then applies Workspace Trust or Full access. If delivery fails, neither
trust nor the permission mode is elevated, and the consumed interaction ID
cannot be replayed.

Synchronous command progress uses one replaceable transcript block. For
example, `/compact` creates a pending presentation before its RPC and replaces
that same block identity on success or failure; neither Core nor LangGraph gains
a second progress protocol. Change commands keep filesystem semantics in
`ChangeCommandService`: Diff returns typed identity/content facts, Undo/Redo
return exact paths and lifecycle, and known domain failures keep distinct error
codes. Ink owns only folding and terminal layout through the global Ctrl+O
detail mode.

## LangGraph owns

- graph routing and execution;
- `AgentState` and node transitions;
- per-Turn checkpoint writes;
- continuation through model, tool, compression, and finalization nodes.

`awesome_agent.storage.checkpoints` adapts LangGraph's SQLite saver and validates
the latest `AgentState`. Application SQLite stores product lifecycle and
transcript records; it does not copy internal graph channels.

For an unfinished Turn, the latest structurally and semantically validated
checkpoint is the recovery fact source for its frozen context; the manifest in
Application SQLite is a product projection and later a terminal fact. Because
the two SQLite databases cannot share one transaction, recovery validates the
checkpoint's identity, budgets, message hashes, token accounting, tool tail,
and immutable-source lineage before reconciling a projection mismatch with an
expected-manifest compare-and-swap. A concurrent projection change, changed
mandatory anchor, or invalid checkpoint fails closed rather than being
overwritten. If no durable checkpoint exists, there is no graph position to
resume and the Turn is failed with a stable recovery code.

Recovery reconciles one local product Turn with its checkpoint. A finished
state is finalized, an unfinished state can resume, missing or corrupt state
fails with a stable code, and uncertain external side effects require a user
decision.

Thread Usage is derived from persisted Turn Usage and means the cumulative
usage of the current Thread. Current Context means the latest meaningful
Context manifest and is not a cumulative token total. Cancellation and failure
preserve reliably observed terminal facts before checkpoint cleanup.

Glob enumerates path metadata without reading file contents; Grep applies path
filters before bounded text reads. Both use the shared safe enumerator, default
pruning, worker-thread execution, and cooperative cancellation so a scan cannot
block the Application event loop.
