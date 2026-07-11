# ADR 0001: Python, LangGraph, and a thin runtime

- Status: Accepted and implemented
- Date: 2026-07-10

## Decision

Python owns all Agent and product behavior. TypeScript is limited to the Ink
surface. LangGraph owns graph execution, graph state, node routing, checkpoint
progress, and resume. Thin lifecycle behavior in `awesome_agent.application`
owns local Thread/Turn lifecycle, one foreground operation, cancellation,
commands, interactions, concrete composition, and event forwarding.

There is no independent runtime platform, Worker, dispatch queue, lease,
heartbeat, custom durable state machine, or recovery scheduler.

## Consequences

Local Turns call the compiled graph directly. Product records cannot duplicate
LangGraph channel state. Reliability work targets checkpoints, cancellation,
bounded retries, and understandable local recovery. A service-scale runtime
would require a new evidence-backed decision.

## Rejected

Keeping a distributed runtime is needless operational cost. Replacing
LangGraph with a custom loop duplicates mature execution behavior. Moving Core
to TypeScript violates the required language boundary.
