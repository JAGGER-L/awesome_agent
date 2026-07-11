# Architecture

Awesome is a single-user, local-first coding agent. The public `awesome`
command starts an Ink interface and one private Python Core child process.

## Product Topology

```text
User
  -> Ink + React
  -> JSON-RPC 2.0 over stdio
  -> Python Application
  -> LangGraph Agent graph
  -> ModelGateway
  -> ToolExecutor
  -> Workspace

Python Application
  -> typed Events -> Ink rendering
  -> application SQLite
  -> LangGraph SQLite checkpoints
```

## Turn Flow

1. Ink submits typed intent through the stdio protocol.
2. Application validates workspace trust, configuration, and foreground
   operation ownership.
3. Application creates the Thread/Turn lifecycle and invokes the graph compiled
   by `awesome_agent.agent.graph`.
4. LangGraph owns graph execution, Agent state, routing, checkpoints, and
   resumable graph progress.
5. `ModelGateway` normalizes the selected DeepSeek or Kimi provider.
6. Every model-requested action enters the shared Core `ToolExecutor`.
7. Tool observations return to Agent state before the next model call.
8. Application persists durable conversation state and projects typed live
   Events to Ink.

## Module Ownership

- `awesome_agent.agent`: graph, state, nodes, routing, budgets, compression,
  retries, message repair, and finalization.
- `awesome_agent.application`: local lifecycle, trust coordination, foreground
  operations, commands, cancellation, composition, and event projection.
- `awesome_agent.modeling` and `awesome_agent.providers`: provider-neutral
  contracts and DeepSeek/Kimi adapters.
- `awesome_agent.core.tools`: built-in and extension tool registry, policy,
  execution, results, and errors.
- `awesome_agent.context`, `awesome_agent.memory`, and
  `awesome_agent.extensions`: context assembly, dual-layer memory, skills, and
  MCP integration.
- `awesome_agent.conversation` and `awesome_agent.storage`: Thread/Turn models
  and embedded state adapters.
- `awesome_agent.protocol`: private stdio host boundary.
- `tui/`: presentation and terminal interaction only.

## Dependency Direction

Ink depends on protocol contracts, not Python implementation packages. Protocol
depends on Application contracts. Application composes the Agent, providers,
tools, extensions, and storage. Agent depends on provider-neutral model, tool,
context, event, and checkpoint contracts. Inner packages never import a user
surface.

## Runtime Boundary

Application lifecycle calls the LangGraph Agent directly. LangGraph is the
execution and checkpoint authority; Application does not duplicate graph
routing or checkpoint recovery. Events are ordered live projections and are
not an independent source of truth.

For focused design decisions, see [the architecture guide](docs/architecture/README.md).
