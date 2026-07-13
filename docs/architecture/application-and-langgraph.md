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
  -> Protocol v2 command.execute
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

`LocalApplication` is the only surface-facing Application API. Command progress
belongs to the Surface pending lifecycle and is not persisted as another
operation state machine.

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

Recovery reconciles one local product Turn with its checkpoint. A finished
state is finalized, an unfinished state can resume, missing or corrupt state
fails with a stable code, and uncertain external side effects require a user
decision.
