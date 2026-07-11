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
