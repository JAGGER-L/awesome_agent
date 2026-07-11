# Architecture

Awesome is a single-user Local-first coding agent. The public `awesome`
launcher starts one Ink process and one private Python Core process.

## Topology

```text
Ink -> stdio JSON-RPC Host -> Application -> LangGraph Agent
                                      -> ModelGateway -> DeepSeek/Kimi
                                      -> ToolExecutor -> workspace/process
                                      -> SQLite/Change Journal/memory
                                      -> Events -> Ink
```

## Turn flow

```text
user input
  -> Ink intent
  -> versioned JSON-RPC over NDJSON/stdin/stdout
  -> Application trust/config/foreground-operation checks
  -> Thread and Turn lifecycle
  -> LangGraph context -> model -> tools -> observation loop
  -> LangGraph SQLite checkpoint
  -> Application SQLite completion and Change Journal seal
  -> typed event notifications
  -> Ink transcript rendering
```

Application commands enter the Application boundary without entering model
reasoning. Every model-requested tool enters the same Tool Executor.

## Package ownership

- `awesome_agent.agent`: graph state, nodes, routing, budgets, compression, and
  finalization.
- `awesome_agent.application`: composition, Thread/Turn lifecycle, commands,
  foreground operation serialization, interactions, cancellation, and events.
- `awesome_agent.modeling` and `awesome_agent.providers`: Provider-neutral
  contracts plus DeepSeek/Kimi adapters.
- `awesome_agent.core`: workspace identity, tools, execution policy, typed
  events, and Change Journal.
- `awesome_agent.context`, `awesome_agent.memory`, and
  `awesome_agent.extensions`: prompt context, two optional memory layers,
  Skills, and MCP.
- `awesome_agent.conversation` and `awesome_agent.storage`: product records and
  embedded storage adapters.
- `awesome_agent.protocol`: private stdio Host and JSON-RPC boundary.
- `tui/`: Ink input, rendering, keyboard behavior, and local presentation
  preferences only.

## Dependency direction

Ink depends on protocol schemas, never Python implementation modules. Protocol
calls the Application facade. Application is the outer composition root and
may wire Agent, providers, tools, extensions, and storage. Agent depends on
Provider-neutral and tool contracts, not concrete surfaces or storage
implementations. Inner packages never import `tui/`.

## Non-goals

There is no hosted service, HTTP product path, distributed scheduler, Worker,
custom durable runtime, general approval resource, artifact resource, event
store, multi-user database, or Docker execution backend. A later API, IDE
adapter, or Docker tool backend requires demonstrated demand and must reuse the
same Application contracts.

See the focused [architecture guide](docs/architecture/README.md) and
[accepted decisions](docs/architecture/decisions/0001-python-langgraph-thin-runtime.md).
