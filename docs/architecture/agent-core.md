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
- Each pending tool call is handled once in order. Executable calls pass through
  `ToolExecutor`; calls skipped after a loop budget is exhausted receive a
  deterministic non-executed error observation without invoking the tool.
- Before another assistant message is requested, every tool call in the prior
  assistant batch has exactly one ordered tool observation. Budget exhaustion
  cannot leave a provider-invalid open tool chain.
- Expected tool failures become bounded observations; unexpected failures stop
  the Turn instead of being hidden.
- Cancellation propagates through model/tool work and leaves recoverable
  checkpoint/product state.
- Compression, message repair, retry accounting, and budget exhaustion are
  Agent Loop invariants, not optional middleware.

Compression rebuilds only the bounded base context. It reserves capacity for
and then appends the complete active-Turn assistant/tool tail exactly once;
pending calls, the next-call index, accumulated results, and their token cost
remain part of the graph state. If the mandatory base plus that tail does not
fit, the Turn ends with `context_unrecoverable` instead of dropping or replaying
an observation.

## Budgets

The default context budget is 262,144 tokens. Per-Turn defaults are 32 model
calls, 64 tool calls, 1,800 active seconds, 2 Provider retries, and 2
compressions. Hard configuration ceilings are 256, 512, 21,600 seconds, 6,
and 10 respectively. Finalization reserves capacity so an exhausted loop can
still return a bounded result.

Application middleware may observe timing, usage, and event metadata. It may
not reroute graph execution, repair messages, or implement fallback state.
