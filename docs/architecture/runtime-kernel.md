# Runtime Kernel

The runtime kernel owns execution authority. Product surfaces describe intent;
they do not execute graphs, call providers directly, bypass approvals, or own
durable state transitions.

## Primary Turn Path

The product route is:

```text
user message turn -> conversation run -> initial leader agent
```

Plain user messages create conversation turns. The runtime creates the
conversation run, resolves context, starts the initial leader agent, and routes
work through shared execution services.

Surfaces do not execute graphs. They submit user intent, render projections,
and expose controls such as cancellation, retry, model selection, and status.

## Authority Boundaries

The kernel owns:

- run intake and state transitions
- graph route selection
- AgentLoop handoff
- tool execution and approval checks
- model-call deadlines and usage accounting
- checkpoint, event, and result persistence
- cancellation, retry, and recovery semantics

The kernel does not own user-interface rendering, product copy, local execution
plans under `.codex/`, or long-term roadmap sequencing.

## Runtime Budgets

Runtime limits are token, reasoning-token, active-time, model-call, retry, and
rework boundaries. Monetary amount budgeting is intentionally outside the
runtime kernel.

## Related Documents

- [Agent loop](agent-loop.md)
- [Product surfaces](product-surfaces.md)
- [Persistence and recovery](persistence-recovery.md)
- [Security model](security-model.md)
