# Runtime Agent Harness

## Scope

This harness defines how the `awesome_agent` product behaves when it executes a
coding task in a user's project. It does not govern Codex while Codex modifies
this repository.

Runtime configuration may be supplied from `.agents/` in the target project.
Generated state belongs in PostgreSQL and the local `.awesome-agent/` data
directory.

## Runtime Loop

```text
load user task and project policy
-> inspect repository and environment
-> Leader creates a dynamic task tree
-> choose solo mode or create Teammates plus Verifier
-> execute tools through approval and sandbox policy
-> persist events, lineage, artifacts, and task revisions
-> independently verify team output
-> Leader decides whether the run is complete
```

## Enforced Boundaries

- Only the Leader exists initially.
- Only the Leader creates or deletes Teammates.
- Team mode always includes exactly one primary Verifier.
- Each Teammate may create at most three depth-one Subagents.
- Subagents have isolated context, no team mailbox access, and no descendants.
- Tool execution passes through the centralized registry and approval policy.
- The registry is tool inventory only. Effective tool exposure and execution
  authorization are capability-resolver decisions, represented as an
  `EffectiveToolPolicy` and enforced again at the executor boundary.
- Untrusted commands use Docker; trusted-local requires explicit CLI consent.
- PostgreSQL stores durable run projections and LangGraph checkpoints.
- Run business state and worker dispatch state remain separate.
- A worker may commit protected transitions only with the current fencing
  token.
- Workers heartbeat directly to PostgreSQL. API availability does not control
  lease validity.
- Expired leases requeue until the maximum claim count, then enter
  `recovery_required`.
- Every Run uses an isolated integration worktree from a clean captured base;
  read-only intent controls tools rather than bypassing isolation, and
  trusted-local does not permit direct edits to the user's checkout.
- API runs select a registered repository identity rather than an arbitrary
  filesystem path.
- Approval applies to one exact canonical tool invocation and expires.
- Full conversations, source, secrets, and raw tool output are excluded from
  memory.
- The Leader may finish team work only after independent verification passes.

## Durable Solo Execution

The target local process topology is:

```text
awesome-agent serve
awesome-agent worker
awesome-agent start
```

`serve` hosts the API, `worker` claims durable work, and `start` supervises one
of each for normal local use. API request handling never owns the lifetime of a
coding Run.

The Worker claims diagnostic `runtime_probe` Runs and read-only Coding Runs
routed to `solo-readonly`. Modifying Coding Runs remain queued. The read-only
graph must loop through the centralized tool registry and may finish only after
successful repository inspection.

LangGraph checkpoints own the resumable graph position. Project PostgreSQL
tables own user-visible projections and dispatch leases. Stable transition IDs,
tool invocation IDs, and fencing tokens prevent stale or replayed work from
silently duplicating side effects.

An irreconcilable checkpoint/projection mismatch places the Run in
`recovery_required` and preserves its workspace and evidence.

## Repository and Validation Policy

All Runs require a clean primary Git base and execute in a retained Run
worktree. V1 rejects linked-worktree registration, does not modify the current
checkout, and does not automatically delete accepted Run worktrees.

The target repository may define ordered checks in
`.agents/validation.toml`. Without configuration, the runtime may infer only
strongly evidenced check-only commands. Ambiguous, networked, installation,
migration, deployment, or write-capable commands require approval.

Solo completion requires no pending approval or tool call, an accepted diff or
explicit no-change result, and all required validation accounted for.

## Runtime Plans

Runtime plans are product data, represented by the Leader plan, Todo tree,
revisions, events, and checkpoints. They are not Markdown files under
`docs/` or `.codex/`.

Future export features may write user-facing reports under
`.awesome-agent/exports/`, but PostgreSQL remains the authoritative runtime
state.

## AgentLoop Middleware Context

AgentLoop middleware receives a `MiddlewareContext` at loop boundaries. Stable
runtime facts are carried in focused typed envelopes:

- trace context for Run, parent Run, trace, span, and runtime route identity;
- capability context for the subject and effective tool-policy surface;
- assignment context for team assignment identity, Leader/root ownership, role,
  and objective;
- token budget context for token limits and usage only;
- handoff context for source, target, and reason;
- error-classification context for category, retryability, and origin.

Raw metadata remains available for route-specific annotations and legacy
observability dimensions, but new cross-cutting behavior should use the typed
envelopes. The graph remains responsible for durable coordination; middleware
uses this context for observability, permission checks, budgets, retries,
approval waits, validation policy, and other cross-cutting concerns without
becoming a monolithic mutable runtime object.

## Capability Policy Boundary

Tool access is a shared effective-policy decision. Runtime routes, API
inspection, validation helpers, and execution helpers should ask the capability
resolver for the applicable effective tool policy instead of deriving
permissions from prompt text, route-local lists, or registry membership alone.

The tool registry provides descriptors, schemas, risk, and capability
requirements. The effective policy decides which inventory items are visible to
the model and which invocations may execute for the current subject, route,
assignment, and grant set. The executor rejects invocations that are outside a
provided effective policy even when the invocation's raw capability set would
otherwise satisfy the tool descriptor.

## Provider Routing Boundary

Provider routing is a model-call concern, not graph business logic. A
`ModelRouter` resolves a route request into an ordered `ModelRouteDecision`.
The model-call executor attempts those candidates in order and falls back only
for provider errors classified as retryable before any external tool side
effect. Authentication, invalid request, context-length, unsupported provider,
capability, approval, validation, and token-budget failures are not silently
retried as provider fallback.

Each routing attempt carries provider, model, route id, attempt number,
outcome, and fallback reason for observability. Token budget checks happen
before each attempt, and token usage recording happens after each completed
turn. Routing data structures intentionally contain no monetary fields.

Production Worker graph construction injects route-aware provider resolvers for
`solo-readonly`, `solo-modifying`, `team-coding-scoped`, `team-coding`,
`team-role`, and `team-verifier`. Graphs still receive a provider-resolver
boundary and do not own provider ordering or fallback policy.

## Runtime Documentation Discipline

When the product modifies a user's project, it should detect the target
project's documentation conventions and include documentation work in its Todo
tree when the implementation changes documented behavior. This repository's
Codex rules are not automatically imposed on unrelated user repositories.
