# Runtime Roadmap

This roadmap is the durable product and architecture roadmap for the
`awesome_agent` runtime. It is not a session handoff and it is not a local
execution plan.

Local agent plans under `.codex/exec-plans/` may decompose work, record
evidence, and guide one implementation branch. They must not silently change
the durable roadmap, architecture direction, phase ordering, or exit
conditions in this file.

Historical task details that no longer guide current sequencing live in
[`runtime-roadmap-archive.md`](runtime-roadmap-archive.md).
Future roadmap updates must follow
[`roadmap-writing-rules.md`](roadmap-writing-rules.md).

## Project Positioning

`awesome_agent` is a local coding-agent runtime kernel. Its goal is not to
clone DeerFlow. DeerFlow remains a useful reference for role separation, team
orchestration, and workflow clarity, but this project optimizes for a different
center of gravity:

- durable local execution that survives worker crashes, process restarts, and
  resumptions;
- auditable side effects for repository mutation, shell execution, validation,
  approvals, cancellation, and recovery;
- least-privilege tool governance for solo agents, leaders, teammates,
  subagents, verifiers, and external extension points;
- bounded model-to-tool loops with explicit token, call-count, active-time,
  retry, and rework limits;
- a stable Graph, AgentLoop, middleware, hook, state, capability, and
  observability boundary that future provider, MCP, skills, and product
  surfaces must reuse.

The long-term product should feel like a reliable local agent runtime that can
scale from a solo coding run to a coordinated team run without losing audit
clarity or permission control.

## Long-Term Architecture Target

The architecture target is a small durable kernel surrounded by replaceable
policy and extension layers.

| Layer | Durable responsibility | Must not own |
| --- | --- | --- |
| API / CLI | Intake, inspection, operator commands, readiness, approval and cancellation surfaces. | Agent reasoning loops or route-specific policy. |
| Worker / Dispatch | Claiming Runs, leases, heartbeats, fencing, retry eligibility, and process-level execution ownership. | Tool permissions or model routing policy. |
| Graph | Checkpoint identity, durable state transitions, interrupts, resume, cancellation, child-run creation and waits, result persistence, patch aggregation, mailbox persistence, terminal projection. | Cross-cutting policy, prompt shaping, retry strategy, observability policy, or tool authorization logic. |
| AgentLoop | One bounded model-to-tool operation for one agent role. | Durable child-run orchestration or global roadmap policy. |
| Middleware / Hooks | Context assembly, observability, budget checks, permission checks, tool exposure, approval waits, validation policy, retry wrapping, error classification, artifact offload, and terminal cleanup. | Durable graph authority or hidden side effects outside audited boundaries. |
| Capability Resolver | Effective tool policy for exposure, execution, inspection, validation, and temporary grants. | Prompt-only permission conventions. |
| State / Repositories | Facts needed after crash, resume, audit, UI/API inspection, or idempotent recovery. | Per-turn scratch data, prompt scaffolding, transient retry state, or provider-local heuristics. |
| Provider Layer | Provider-neutral model calls, streaming, usage reporting, continuation, error classification, routing, and fallback. | Tool authority, graph state transitions, or commercial billing policy. |

Durable state is reserved for facts that must survive a crash or be inspected
later. Ephemeral state is preferred for prompt scaffolding, in-memory retries,
model-call shaping, and per-turn scratch policy unless a middleware crosses an
explicit durable boundary.

## Runtime Governance Invariants

These invariants override older task-level handoffs when they conflict:

- Graph modules own durable coordination only.
- AgentLoop owns model-to-tool iteration for one agent operation.
- Middleware and hooks own cross-cutting runtime policy.
- Leader authority is durable and explicit. The Leader assigns Teammates,
  grants capabilities, observes mailbox audit evidence, integrates results,
  decides bounded replanning, and creates the Verifier. The Leader does not
  directly create Subagents.
- Teammates own bounded execution and may create Subagents only when their
  durable assignment grants delegation.
- Subagents stay read-only, scoped, mailbox-less, non-delegating, and
  accountable to their owning Teammate.
- Tool access is an effective capability decision, not a prompt convention.
- Global tools, Leader-only tools, Teammate tools, Subagent tools, deferred
  tools, promoted tools, and temporary grants must converge through a shared
  resolver before they are exposed to a model or executed.
- Runtime budgets are token, reasoning-token, active-time, call-count, retry,
  and rework limits. Monetary amount limits are intentionally outside the
  runtime kernel.
- New extension surfaces must reuse the same capability, budget,
  observability, audit, and AgentLoop boundaries as built-in tools.

## Roadmap Principles

The roadmap is ordered by dependency risk:

1. Stabilize the kernel before expanding the ecosystem.
2. Put durable coordination in Graph and cross-cutting policy in middleware.
3. Make tool permission a shared resolver decision before adding more tools.
4. Make observability and typed context available before provider fallback.
5. Treat local `.codex` plans as execution details, not architecture decisions.
6. Convert directional future phases into numbered tasks only when the previous
   phase has executable evidence and a clear exit condition.

This ordering prevents later provider, MCP, skills, and product work from
amplifying weak observability, weak permissions, or metadata-heavy middleware
contracts.

## Current Kernel Phase

Task 35 is complete and closes TD-024 as a deliberate non-goal: the runtime
kernel does not implement amount-derived budgets, ledgers, or compatibility
fields.

The active kernel completion sequence is:

| Task | Phase | Status | Purpose | Exit condition |
| --- | --- | --- | --- | --- |
| Task 36 | Observability | Planned | Make production observability an AgentLoop middleware capability as well as durable query-table evidence. | TD-033 is closed: model/tool/agent spans, metrics, latency, dashboards, alerts, and trace visualization are available through documented exporter paths; Worker-only projection is not the only observability path. |
| Task 37 | Middleware | Planned | Mature `MiddlewareContext` into typed extension envelopes. | Middleware receives typed trace, capability, assignment, budget, handoff, and error-classification context without forcing unrelated middleware to depend on a monolithic context. |
| Task 38 | Governance | Planned | Converge capability resolution across solo, team, subagent, API inspection, validation, exposure, and execution paths. | P4 is fully complete: all runtime routes expose and execute tools through the same effective-policy model, with route-specific inputs rather than route-specific permission logic. |
| Task 39 | Provider | Planned | Add multi-model and multi-provider routing/fallback after accounting, permissions, observability, and error classification are stable. | Model routing can make provider decisions without bypassing token budget, capability policy, observability, or error-classification policy. |

The runtime kernel is stable only after Task 38 is complete. Task 39 is the
first provider-ecosystem task and must still preserve the kernel boundaries.

## Architecture Debt Carried Forward

| Item | Current disposition | Forward action |
| --- | --- | --- |
| P2: typed middleware contract | Partially complete. `MiddlewareContext` is typed but still too metadata-heavy. | Finish in Task 37 with focused typed envelopes for trace, capability decision, assignment, token budget, handoff, and error classification. Do not create one giant mutable context object. |
| P3: migrate team routes | Complete for forward distributed routes. `team-coding`, `team-role`, and `team-verifier` enter `TeamAgentLoop` and shared middleware; graphs retain durable coordination. | Keep slimming graph-owned policy opportunistically, but do not reopen P3 as a broad migration task. |
| P4: unified tool permission | Partially complete. Task 31 added a team-scoped `CapabilityResolver` / `EffectiveToolPolicy` foundation. | Finish full-route convergence in Task 38 across solo, team, subagent, verifier, API inspection, validation, exposure, and execution paths. |
| P5: team hardening | Mostly complete for local validation, same-child validation rework, patch conflict recovery, stress coverage, mailbox collaboration, bounded Leader plan repair, and policy-backed recovery budgets. | Continue with metrics-driven recovery tuning after observability and provider routing supply enough evidence. |

## Post-Kernel Long-Term Plan

The following phases are directional. They are not committed task numbers until
this roadmap is updated through change control.

| Phase | Direction | Entry criteria | Exit shape |
| --- | --- | --- | --- |
| Extension Phase | Add MCP, skills, and external tool ecosystems. | Task 38 complete; extension surfaces can call the shared capability resolver. | External tools use the same effective policy, audit records, token budgets, approval gates, and AgentLoop observability as built-in tools. |
| Provider Ecosystem Phase | Expand provider routing, fallback, model profiles, and provider-quality feedback. | Task 39 complete; routing attempts are observable and token-accounted. | Provider decisions are reliable, explainable, retry-safe, and tuned by measured runtime outcomes rather than hard-coded optimism. |
| Operations Phase | Improve dashboards, alerts, trace exploration, recovery metrics, and readiness diagnostics. | Task 36 complete; durable and OTel evidence paths exist. | Operators can diagnose latency, failure class, budget pressure, provider quality, recovery behavior, and worker health without reading raw logs. |
| Productization Phase | Build higher-level user workflows and UI/API surfaces. | Kernel boundaries stable; roadmap change defines target users and workflows. | Product surfaces inspect and control Runs without bypassing approvals, cancellation, capability policy, or durable evidence. |
| Team Intelligence Phase | Improve Leader planning, assignment quality, Verifier calibration, and recovery tuning. | P5 evidence exists across real team runs and provider metrics. | Team behavior improves through bounded, observable policy changes rather than hidden prompt growth. |

## Completed Milestones Summary

Detailed historical task notes are archived in
[`runtime-roadmap-archive.md`](runtime-roadmap-archive.md).

| Range | Milestone | Summary |
| --- | --- | --- |
| Tasks 01-06 | Durable solo foundation | Runtime contracts, repository intake, PostgreSQL queueing, Worker execution, provider-neutral model protocol, and checkpointed solo read-only loops. |
| Tasks 07-12 | Safe solo modification | Central tool execution, Docker shell, durable side-effect records, approvals, cancellation, deterministic validation/rework, lifecycle projections, and solo observability. |
| Tasks 13-18 | Team and operations foundation | Real team E2E, managed workspace cleanup, readiness checks, context compaction, distributed child-run skeleton, root-aware team budgets, and payload compaction. |
| Tasks 19-24 | AgentLoop architecture migration | Graph-version removal, ThinGraph/AgentLoop contracts, solo and team AgentLoop middleware migration, OTel span instrumentation, and durable graph versus policy boundary cleanup. |
| Tasks 25-35 | Distributed team hardening and governance | Teammate-local validation, same-child rework, patch conflict recovery, multi-worker stress, mailbox collaboration, roadmap lock, capability resolver foundation, bounded Leader replanning, policy-backed recovery budgets, provider-aware token accounting, and token-only budget governance. |

## Change Control

- Roadmap edits must follow
  [`roadmap-writing-rules.md`](roadmap-writing-rules.md).
- Every new numbered task must map to one row in this roadmap, one open
  technical debt item, or a documented production incident.
- If a future task needs to reorder the roadmap, first update this document in
  a standalone governance change that states the reason, dependency impact, and
  displaced work.
- Local plans under `.codex/exec-plans/active/` may decompose work but must not
  silently change architecture direction, phase ordering, or exit conditions.
- A task may close or narrow debt only when executable evidence exists in tests,
  health checks, traces, durable query APIs, or documented operational checks.
- Do not start broad provider, MCP, skill, UI, or DeerFlow-style expansion while
  a kernel-phase task remains open unless that work is the explicit purpose of
  the active kernel task.
- Do not raise quality scores unless executable evidence exists in tests,
  health checks, traces, durable query APIs, or documented operational checks.
