# ThinGraph, AgentLoop, and Middleware Contracts

Task 19 defines the target runtime shape. It does not migrate the existing
solo or team graphs yet.

## Graph Identity

Runtime graph identity is `graph_name` only. The current graph names are:

- `runtime-probe`
- `solo-readonly`
- `solo-modifying`
- `team-coding-scoped`
- `team-coding`
- `team-role`
- `team-verifier`

Do not add `graph_version` back. If future compatibility needs appear, use an
explicit concept that names the actual compatibility boundary:

- `runtime_contract_version` for AgentLoop input/output contract changes.
- `state_schema_version` for checkpoint state migrations.
- `middleware_stack_id` for deliberate behavior-stack selection.

## ThinGraph

ThinGraph owns durable control flow only:

- claim-time graph routing by `graph_name`;
- checkpoint thread identity;
- interrupt, resume, retry, cancellation, and recovery transitions;
- durable terminal state projection;
- lease-safe event emission around graph boundaries.

ThinGraph does not own model/tool iteration, memory, sandbox policy, approval
policy, tool filtering, validation, budget decisions, team delegation, or
subagent behavior. Those belong to AgentLoop and middleware.

## AgentLoop

AgentLoop owns one model-tool loop:

1. Build a model request from current messages and activated context.
2. Call the model.
3. If the model returns tool calls, execute allowed tools and append tool
   results.
4. Loop until no tool calls remain, a middleware terminates, or a budget/error
   boundary stops execution.

AgentLoop returns a structured result to ThinGraph:

- `status`: completed, failed, waiting, cancelled, or recovery_required.
- `messages`: checkpoint-safe messages or summarized/offloaded references.
- `final_answer`: optional user-facing completion text.
- `events`: durable event payloads to emit through the graph boundary.
- `artifacts`: durable artifact references created during the loop.

## Middleware Stages

Middleware ordering is explicit. Stages run in this order:

1. `before_agent`: initialize run-scoped context, uploads, thread data,
   memory reads, team policy, and budget guards.
2. `before_model`: prepare request context, summaries, image references, tool
   exposure, and prompt budget.
3. `wrap_model_call`: surround the model call with input sanitization, system
   message coalescing, dangling tool-call repair, LLM error handling, token
   budget checks, deferred tool filtering, and loop detection.
4. `after_model`: record token usage, title/model metadata, finish-reason
   safety, Todo updates, and routing hints.
5. `wrap_tool_call`: surround each tool call with central tool execution,
   approval, sandbox, guardrails, output budget, audit, tool error handling,
   and deferred tool promotion.
6. `after_agent`: persist memories, close budgets, summarize final context,
   clean temporary state, and emit terminal observations.

Within a stage, middleware order is fixed by registration order. Middleware
that changes request shape must run before middleware that measures or persists
that shape.

## Durable Boundary

ThinGraph checkpoints at durable boundaries:

- before starting a run;
- before an interrupt or approval wait;
- after a successful AgentLoop turn batch that changes durable state;
- before terminal projection;
- after recovery decisions.

AgentLoop may iterate model-to-tools several times inside one ThinGraph node.
It should not force a graph checkpoint after every internal model/tool step
unless a middleware produces an interrupt, durable side effect, or recovery
boundary that must survive a crash.

## Middleware Routing

Middleware may influence routing without becoming graph nodes:

- return a terminal AgentLoop result;
- request an interrupt or approval wait;
- remove or defer tool calls from a model response;
- request another model turn;
- raise a classified error that ThinGraph maps to retry, failure, cancellation,
  or recovery_required.

Graph nodes remain stable. Behavior changes should be implemented by changing
middleware composition or middleware configuration, not by proliferating graph
versions.
