# Structured Model Protocol

## Purpose

The model boundary translates provider-specific APIs into one internal protocol
that orchestration can checkpoint, test, and later connect to the durable
Coding graph. Task 05 defines this boundary but does not execute Coding Runs.

## Request Model

A request contains an ordered list of discriminated messages:

```text
system
user
assistant(text + native tool calls)
tool(call ID + bounded result)
```

Tool definitions use JSON Schema. Tool arguments remain raw JSON text until
orchestration validates them against the registered tool schema. This preserves
malformed model output as evidence and allows the next model turn to receive a
structured correction.

Tool choice supports `auto`, `none`, `required`, or one named tool. The protocol
allows multiple calls in one assistant turn; execution order is a later graph
policy.

## Streaming Model

Providers expose an asynchronous stream of:

```text
reasoning.started
reasoning.delta
text.delta
tool_call.started
tool_arguments.delta
turn.completed
turn.failed
```

`complete()` is a convenience collector over the same stream. Provider
adapters do not maintain a separate non-streaming semantic path.

The future frontend presents a generic `thinking` state while reasoning deltas
arrive and collapses the reasoning block after completion. It does not display
provider-source labels.

## Reasoning and Continuation

Visible reasoning and private continuation are separate data paths.

### Visible Reasoning

`ReasoningTrace` contains displayable segments returned by the provider:

- DeepSeek `reasoning_content`;
- OpenAI reasoning summaries;
- no fabricated text when a provider exposes neither.

Visible reasoning may later be redacted, stored as bounded model-call evidence
or an artifact, and streamed to the frontend. It is not written to memory.
Different provider outputs are not claimed to have identical semantics even
though the UI uses one generic presentation.

### Private Continuation

`ContinuationState` contains JSON-serializable provider data needed to continue
the same reasoning/tool sequence:

- DeepSeek reasoning content that must accompany a prior assistant tool call;
- OpenAI reasoning output items, including encrypted continuation content.

Continuation is opaque outside the matching provider adapter. It is:

- allowed in LangGraph checkpoints;
- excluded from normal model/request dumps;
- excluded from API responses, frontend events, logs, runtime events, and
  memory;
- never represented by provider SDK objects.

## Turn Result

A completed turn contains:

- assistant text and native tool calls;
- normalized stop reason;
- model, provider, and response identity;
- optional visible reasoning;
- optional private continuation;
- token usage with missing values represented as `None`.

Normalized stop reasons are `completed`, `tool_calls`, `max_tokens`,
`content_filter`, and `unknown`.

## Failure Contract

Adapters classify failures as:

```text
authentication
rate_limit
transient
invalid_request
context_length
provider_protocol
```

Classification records whether a later runtime may retry. Adapters never retry
internally because only orchestration knows whether a side effect has already
committed.

## Provider Mapping

DeepSeek uses streaming Chat Completions and preserves native tool calls,
reasoning content, completion usage, and reasoning continuation.

OpenAI uses the streaming Responses API and preserves function calls, reasoning
summaries, encrypted reasoning items, response identity, and detailed usage.

Provider-specific request and response objects terminate inside
`providers/`. Orchestration, memory, checkpoints, and tests depend only on the
models under `modeling/`.

## Deferred Work

- Task 06 connects complete turns to the read-only Coding graph and validates
  tool arguments.
- Later runtime work persists bounded model-call evidence and exposes streaming
  events through PostgreSQL/SSE.
- Cancellation, retry policy, budgets, context compression, and artifact
  offload remain separate runtime concerns.
