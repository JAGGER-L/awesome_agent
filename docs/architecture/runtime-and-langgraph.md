# Application and LangGraph

The retained runtime is not a separate package or platform. It is the thin
lifecycle behavior in `awesome_agent.application` around a directly invoked
LangGraph Agent.

## Application owns

- workspace trust and resolved configuration;
- Thread/Turn creation and terminal lifecycle state;
- one active foreground operation, cancellation, and interaction responses;
- slash-command dispatch and direct-input routing;
- concrete composition of providers, tools, memory, Skills, MCP, and storage;
- typed event projection to the surface.

## LangGraph owns

- graph routing and execution;
- AgentState and node transitions;
- per-Turn checkpoint writes and resumable graph progress;
- continuation after model/tool nodes according to graph state.

`awesome_agent.storage.checkpoints` only adapts LangGraph's SQLite saver and
validates the latest `AgentState`; it does not define a second state machine.
Application SQLite stores product lifecycle and transcript records, not a copy
of internal graph channels.

There is no queue, Worker, lease, heartbeat, recovery scheduler, durable run
resource, or custom event source of truth. Recovery means reconciling a
product Turn with its LangGraph checkpoint and continuing locally.
