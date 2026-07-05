# Agent Loop

AgentLoop is the provider/tool conversation boundary used by runtime routes.
It turns runtime context into model input, receives tool requests and assistant
messages, and returns normalized events to the kernel.

## Responsibilities

- build model input from thread context and compacted evidence
- apply middleware for tool visibility and model-call policy
- normalize provider responses into assistant messages, tool requests, usage,
  and stream events
- hand tool requests back to the runtime executor
- preserve deterministic replay inputs through persisted events and checkpoints

## Non-Responsibilities

AgentLoop does not own surface rendering, direct filesystem edits, provider
secret loading, workspace cleanup, or roadmap policy. Those are owned by CLI/API
surfaces, tools, provider configuration, operations, and governance docs.

## Loop Shape

The common loop is model turn, optional tool execution, observation, and another
model turn until a final answer, cancellation, approval wait, or failure state
is reached. Tool execution remains outside provider child processes so the
runtime can enforce capability and approval policy.

## Related Documents

- [Runtime kernel](runtime-kernel.md)
- [Tool capabilities](tool-capabilities.md)
- [Providers and streaming](providers-streaming.md)
