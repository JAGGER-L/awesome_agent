# Agent Core

`src/awesome_agent/agent/` is the reasoning authority. `graph.py` is the only
package that constructs a LangGraph `StateGraph`; `state.py` defines the graph
state; `nodes.py` owns context preparation, model calls, compression, one-tool
execution, and finalization.

## Invariants

- One foreground Turn is serialized by Application.
- Context is assembled deterministically before a model call.
- Provider messages, tool calls, usage, and errors use
  `awesome_agent.modeling` contracts.
- Each pending tool call is executed once in order through `ToolExecutor`.
- Expected tool failures become bounded observations; unexpected failures stop
  the Turn instead of being hidden.
- Cancellation propagates through model/tool work and leaves recoverable
  checkpoint/product state.
- Compression, message repair, retry accounting, and budget exhaustion are
  Agent Loop invariants, not optional middleware.

## Budgets

The default context budget is 262,144 tokens. Per-Turn defaults are 32 model
calls, 64 tool calls, 1,800 active seconds, 2 Provider retries, and 2
compressions. Hard configuration ceilings are 256, 512, 21,600 seconds, 6,
and 10 respectively. Finalization reserves capacity so an exhausted loop can
still return a bounded result.

Application middleware may observe timing, usage, and event metadata. It may
not reroute graph execution, repair messages, or implement fallback state.
