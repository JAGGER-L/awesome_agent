# Runtime Kernel

The target runtime is a thin, in-process Python application boundary around
LangGraph. Product surfaces submit typed intent to `ApplicationFacade` and
render typed events; they do not execute graphs, call providers, access SQLite,
or run tools directly.

## Primary Turn Path

```text
surface intent
  -> ApplicationFacade
  -> single foreground Operation
  -> ConversationService begins a Turn
  -> LangGraph agent graph
  -> provider-neutral ModelGateway
  -> ToolExecutor when requested
  -> LangGraph SQLite checkpoint
  -> durable Turn result in application SQLite
  -> live typed Event notifications
```

LangGraph owns graph execution, graph state, node recovery, and checkpoints.
The application layer owns workspace trust, Thread/Turn lifecycle, one active
foreground operation, configuration snapshots, cancellation, command dispatch,
change capture, and projection of live Events. There is no second custom state
machine, durable Run resource, Worker, dispatch queue, lease, heartbeat, or
EventStore in the target Host.

## Recovery

Application state is reconstructed from SQLite, the LangGraph SQLite
checkpointer, workspace files, and the Change Journal. Events are live
notifications and are never replayed as authoritative state. On restart, a
completed checkpoint can finalize its Turn; an unfinished safe checkpoint can
resume; uncertain shell/MCP side effects require an explicit interaction.

## Tool and Interaction Boundary

Every model and direct tool call enters the same `ToolExecutor`. Workspace
trust enables ordinary in-workspace development operations. Exceptional shell
boundaries use a short-lived interaction; they are not generalized approval
resources. Direct `!command` execution creates a foreground Operation and a
bounded `direct_command` Thread entry, but no model Turn or checkpoint.

## Surface Boundary

Protocol version 1 is JSON-RPC 2.0 over UTF-8 NDJSON stdio. One client owns one
Host. Responses and live Event notifications share one serialized writer;
stdout contains protocol frames only and stderr contains diagnostics. HTTP,
WebSocket, LangGraph Server, authentication, and multi-client sessions are not
part of this runtime.

`awesome` starts one `awesome-core` child with piped stdin, stdout, and
stderr. Core stdout is consumed privately as protocol and never copied to the
terminal; Ink alone owns terminal stdout. Graceful exit requests protocol
shutdown, waits up to five seconds, then terminates once. Unexpected exit keeps
the bounded final 20 non-empty stderr lines only in fatal UI state.

The public `awesome` entry is the Ink implementation. The superseded Textual,
Python CLI, HTTP client, and duplicate surface packages have been removed.

## Runtime Budgets

The graph enforces model calls, tool calls, provider retries, compression
attempts, active execution time, and context-token limits. Middleware may
observe correlation, tracing, redacted logs, latency/counts, and Usage, but it
cannot retry, short-circuit, change inputs, or alter results.

## Related Documents

- [Local-first target architecture](local-first-target.md)
- [Agent loop](agent-loop.md)
- [Persistence and recovery](persistence-recovery.md)
- [Security model](security-model.md)
