# ADR 0001: Python, LangGraph, and a Thin Runtime

- Status: Accepted
- Date: 2026-07-10
- Scope: Agent Core and runtime

## Context

The current repository places a custom durable runtime, dispatch and worker
machinery around LangGraph. That architecture is appropriate for a service or
general agent platform, but the target product is a single-user local coding
agent with one primary interactive execution path.

## Decision

Python is the only language for Agent Core, runtime, providers, tools,
persistence, configuration, skills, MCP, and memory.

LangGraph owns graph execution, graph state, checkpointing, streaming,
interrupts, and graph recovery. Awesome Agent retains a thin runtime that owns
only session and turn lifecycle, single-turn serialization, cancellation,
application command dispatch, and event forwarding.

The target has no independent Worker, lease, heartbeat, dispatch queue, custom
durable run state machine, or recovery scheduler.

## Consequences

- Local turns execute directly in the application process.
- Runtime state cannot duplicate LangGraph graph state.
- Reliability work focuses on checkpoint correctness, cancellation, bounded
  retry, and clear local recovery instead of distributed coordination.
- Service-scale execution would require a new, evidence-backed decision rather
  than remaining latent in the local core.

## Rejected Alternatives

- Keep the current durable runtime unchanged: excessive product and operations
  cost for the target use case.
- Remove LangGraph and build a custom loop runtime: duplicates mature graph,
  checkpoint, stream, and interrupt behavior.
- Rewrite the core in TypeScript: conflicts with the project's required Python
  core and creates an unnecessary cross-language migration.
