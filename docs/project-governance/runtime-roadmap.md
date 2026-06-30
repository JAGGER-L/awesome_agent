# Runtime Roadmap

This roadmap tracks durable `awesome_agent` product work. Local execution
plans under `.codex/exec-plans/` are implementation notes for repository
agents; they are not the durable product roadmap.

## Completed Foundation

| Task | Status | Outcome |
| --- | --- | --- |
| Task 01 | Done | Durable runtime contracts, repository harness boundaries, and target state authority. |
| Task 02 | Done | Repository-aware Run intake, allowed-root validation, Git worktree preparation, and durable queued Runs. |
| Task 03 | Done | PostgreSQL dispatch queue, leases, heartbeats, fencing, and retry eligibility. |
| Task 04 | Done | One-Run-per-worker execution, LangGraph checkpointed probe graph, and crash recovery. |
| Task 05 | Done | Provider-neutral model protocol, streaming reasoning/text/tool events, usage, continuation, and classified model failures. |
| Task 06 | Done | Checkpointed solo read-only model/tool loop with explicit graph back edges and evidence-backed final answers. |
| Task 07 | Done | Isolated solo modifying runtime with central tool execution, Docker shell, durable side-effect records, artifact offload, prompt guard, and recovery-required handling. |
| Task 08 | Done | Durable approval requests, decisions, expiry, worker release, and resume semantics for solo modifying runs. |
| Task 09 | Done | Active cancellation through solo graph, provider, tool, Docker, subprocess, checkpoint, projection, and worktree boundaries. |
| Task 10 | Done | Deterministic validation gates, verifier feedback, bounded model rework, durable validation evidence, and terminal failure semantics. |
| Task 11 | Done | Consistent Run, Agent, Todo, event, revision, and `updated_at` lifecycle projections for solo runtime paths. |
| Task 12 | Done | Solo runtime observability with trace IDs, query-table spans, metrics, model calls, latency, and exporter isolation. |
| Task 13 | Done | Explicit scoped `team-coding-scoped` runtime with real Worker, PostgreSQL, checkpoint, provider, tool, verifier, rework, validation, and observability E2E evidence. |
| Task 14 | Done | Explicit managed workspace listing and dry-run-first cleanup with ownership, lease, branch, dirty, force, and recovery evidence protections. |
| Task 15 | Done | Split `/health` liveness from structured `/ready` and `doctor --profile` readiness; checks PostgreSQL, migrations, checkpoint store, workspace root, provider keys, model routes, API bind policy, and Worker heartbeat registry. |
| Task 16 | Done | Artifact-backed solo context compaction, durable token ledgers, active Worker execution budgets, budget/compaction APIs and CLI, and team global budget guards; money cost budget remains deferred. |
| Task 17 | Done | Distributed team child-run skeleton with durable lineage, assignments, mailbox, child results, recursive cancellation, inspection APIs/CLI, production Worker wiring, and PostgreSQL integration/E2E evidence. |
| Task 18 | Done | Root-aware distributed team budget checks, deferred assignment tool exposure, and artifact-backed compaction for large handoff, child-result, verifier evidence, and mailbox payloads. |
| Task 19 | Done | Pre-production graph-version removal, baseline migration squash, and ThinGraph, AgentLoop, middleware, and checkpoint-boundary contracts. |
| Task 20 | Done | `solo-readonly` now enters AgentLoop middleware stages, with read-only evidence, progress, context, compaction, and budget policy extracted from the graph. |

## Completed Detail: Task 07 Isolated Mutation Sandbox and Shell

Task 07 made modifying solo Runs executable without weakening the runtime
safety model. It was completed on 2026-06-26.

Task 07 includes:

- route `coding + modifying` Runs to `solo-modifying` and make Workers claim
  that graph so `awesome-agent run "fix bug" --repo ...` cannot remain queued
  forever solely because no route exists;
- move read-only and modifying graph tool calls through the centralized tool
  execution boundary before adding write-capable tools;
- enforce tool specification, capability, profile, timeout, sandbox, approval
  classification, and artifact handling in one execution path;
- add versioned `repo.apply_patch`, `repo.diff`, Docker-backed
  `shell.execute`, and `artifact.read` tools, while avoiding arbitrary
  `write_file`;
- execute write tools and shell commands sequentially, with read tools allowed
  to remain parallel only when they pass the same executor policy;
- use Docker for automatic shell execution in Task 07; trusted-local modifying
  execution remains out of scope until durable approval and local-risk binding
  exist;
- persist side-effecting tool invocations with idempotency keys, path lists,
  preimage hashes, expected postimage hashes, status, result summaries, and
  artifact references;
- reconcile patch recovery deterministically: matching preimage applies,
  matching postimage is treated as already done, and partial or ambiguous file
  state becomes `recovery_required`;
- treat unknown shell completion after a crash as `recovery_required` rather
  than replaying the command automatically;
- wire artifact offload into the main agent loop, including persistent artifact
  metadata and `artifact_refs` for oversized tool and shell output;
- add a minimum prompt guard so large single tool outputs are summarized or
  referenced instead of being copied directly into checkpoints;
- return a modifying completion state that was explicitly unvalidated before
  Task 10 added deterministic validation and rework;
- block or require explicit unsafe configuration for non-loopback local API
  serving through the project CLI; direct ASGI hosting remains an operator risk
  tracked separately;
- make the standard local check script self-explanatory and reproducible for
  required PostgreSQL test settings;
- update capability documentation that currently describes coding execution,
  tool traceability, or current graph support too strongly.

Task 07 does not include:

- durable Approval API implementation;
- cancellation propagation into active model, tool, Docker, or subprocess work;
- deterministic project validation and rework;
- team-mode execution;
- automatic worktree or branch cleanup;
- full OpenTelemetry span coverage, metrics, cost, latency, and query tables;
- production multi-user authentication.

## Later Tasks

| Task | Name | Purpose | Exit condition |
| --- | --- | --- | --- |
| Task 21 | Done | `solo-modifying` now enters AgentLoop middleware stages, with context, budget, tool execution, approval, artifact offload, evidence, validation, rework, and finalization policy extracted from the graph. |
| Task 22 | Done | Replaced deterministic `team-coding` role skeletons with model-driven Leader planning, assignment-scoped Teammate model/tool loops, Teammate-owned Subagents, structured Verifier decisions, targeted replacement rework, and patch-producing distributed E2E evidence. | Full distributed team E2E covers Leader, Teammates, Verifier, Subagents, model calls, scoped tools, patch artifact generation, idempotent patch aggregation, traceability, and verifier rework. |
| Task 23 | Done | Real OTel spans on API endpoints, Worker graph boundaries, and migrated solo AgentLoop model/tool paths while preserving durable query tables. | API, `run.execute`, `graph.execute`, `agent.run`, `model.call`, and `tool.call` spans are created through `ObservabilityFacade` and AgentLoop observability middleware; exporter failures are isolated and trace IDs remain queryable through durable events. |
| Task 24 | Done | `team-coding`, `team-role`, and `team-verifier` now route Leader planning, Teammate/Subagent model/tool execution, Verifier decisions, and team observability through `TeamAgentLoop` middleware; durable child-run coordination remains graph-owned and helper modules are split along the new boundary. | Focused unit tests cover TeamAgentLoop, Leader planning, role model/tool calls, Verifier decisions, and Worker observability projection changes; distributed integration/E2E tests remain database-gated in local runs. |
| Task 25 | Done | Distributed writing Teammate child Runs now run deterministic validation before publishing completed patch results, with validation execution wrapped by `TeamAgentLoop`, durable validation reports, verification events, failed child-result semantics, and E2E evidence. | Focused unit tests cover pass, fail, skip, and AgentLoop metadata behavior; distributed E2E fixture validation records report/event/span evidence before patch publication. |
| Task 26 | Done | Writing Teammate child Runs now perform bounded same-child validation rework for reworkable deterministic command failures before publishing patch artifacts or falling back to failed child-result semantics. | Focused unit tests cover fail-then-pass, exhaustion, non-reworkable failures, and feedback injection; distributed E2E covers same-child validation recovery without replacement child creation. |
| Task 27 | Done | Distributed team patch aggregation now classifies conflicting Teammate patches and recovers through bounded replacement Teammate child Runs before verifier creation. | Focused unit tests cover aggregation classification, Leader replacement creation, superseded result filtering, budget exhaustion, and Verifier effective evidence; distributed E2E covers Worker-path conflict recovery with durable events/results. |
| Task 28 | Done | Added true concurrent multi-Worker stress coverage for distributed team Runs across sibling Teammates, Teammate-owned Subagents, Verifier, patch aggregation, mailbox/result persistence, and dispatch claim evidence. | Integration stress test runs multiple DurableWorkers concurrently against PostgreSQL and asserts no duplicate claims, assignments, child results, patch aggregation, or parent verifier races. |
| Task 29 | Done | Added route-restricted Teammate mailbox collaboration through assignment-scoped mailbox tools, durable read/respond lifecycle, Leader root audit visibility, and Worker-path evidence. | Unit tests cover route policy, repository visibility, role-loop tool exposure, and mailbox tool execution; distributed integration covers Teammate-to-Teammate question/response mailbox flow without weakening Subagent isolation or Verifier authority. |
| Task 30 | Done | Locked the post-Task-29 runtime roadmap, architecture invariants, P2-P5 disposition, forward task sequence, and change-control rules so later work does not re-plan the kernel from scratch after every task. | Runtime roadmap names Task 31-40 ordering, kernel-stability criteria, phase gates, and disallowed early expansions; local execution evidence records baseline and documentation validation. |
| Task 31 | Done | Added a team-scoped `CapabilityResolver` / `EffectiveToolPolicy` foundation for distributed team assignments. | Team planning, role-loop exposure, role tool execution, Subagent grants, Verifier review tools, and API inspection use resolver-derived effective tools and per-tool capabilities without weakening mailbox, delegation, write, or Subagent restrictions. |
| Task 32 | Done | Added bounded Leader plan repair for Verifier-requested distributed team rework. | Leader repair decisions are structured, audited, budgeted, and can replace or add Teammate child Runs while preserving assignment lineage, filtering superseded evidence, retiring failed Verifiers, and requiring a fresh Verifier pass after repaired children finish. |
| Task 33 | Done | Replaced hard-coded distributed team recovery defaults with `TeamRecoveryPolicy` and Worker settings. | Verifier invalid-output attempts, verifier retry helpers, plan-repair budgets, patch-conflict rework budgets, model-output rework budgets, and unknown-failure fallback budgets are policy-owned, configurable, validated, and emitted in recovery events. |
| Task 34 | Done | Replaced heuristic-only prompt token estimation with provider/model-aware token accounting. | Budget checks, context compaction, team payload compaction, and model-request estimates use `TokenAccountant` profiles with estimator provenance and documented fallback error margins while provider-reported usage remains the durable ledger source. |

## Runtime Architecture Invariants

The long-term goal is a local coding-agent runtime kernel, not a DeerFlow
clone. DeerFlow remains a useful comparison point for team orchestration,
role separation, and workflow clarity, but this project optimizes for durable
local execution, auditable side effects, least-privilege tools, and bounded
recovery.

These invariants override older task-level handoffs when they conflict:

- `Graph` modules own durable coordination only: checkpoint identity,
  state transitions, interrupts, resume, cancellation, child-run creation and
  waits, result persistence, patch aggregation, mailbox persistence, and
  terminal projection.
- `AgentLoop` owns model-to-tool iteration for one agent operation.
- Middleware and hooks own cross-cutting runtime policy: context assembly,
  tool exposure, permission checks, budget checks, validation policy,
  observability, retry wrapping, error classification, approval waits,
  artifact offload, and terminal loop cleanup.
- Leader authority is durable and explicit. The Leader assigns Teammates,
  grants capabilities, observes mailbox audit evidence, integrates results,
  decides bounded replanning, and creates the Verifier. The Leader does not
  directly create Subagents.
- Teammates own bounded execution and may create Subagents only when their
  durable assignment grants delegation. Subagents stay read-only, scoped,
  mailbox-less, non-delegating, and accountable to their owning Teammate.
- Tool access is an effective capability decision, not a prompt convention.
  Global tools, Leader-only tools, Teammate tools, Subagent tools, deferred
  tools, promoted tools, and temporary grants must converge through a shared
  resolver before they are exposed to a model or executed.
- Durable state is reserved for facts needed after a crash, resume, audit, or
  UI inspection. Prompt scaffolding, transient model-call shaping, in-memory
  retries, and per-turn scratch policy remain ephemeral unless a middleware
  crosses an explicit durable boundary.

## P2-P5 Disposition After Task 29

| Prior item | Current disposition | Forward action |
| --- | --- | --- |
| P2: typed middleware contract | Partially complete. `MiddlewareContext` is typed but still thin and metadata-heavy. | Finish later as Task 37 with typed extension envelopes for trace, capability decision, assignment, budget, handoff, and error classification. Do not make one giant context object. |
| P3: migrate team routes | Complete for the forward distributed routes. `team-coding`, `team-role`, and `team-verifier` enter `TeamAgentLoop` and shared middleware; graphs retain durable coordination. | Keep slimming graph-owned policy opportunistically, but do not reopen P3 as a broad migration task. |
| P4: unified tool permission | Partially complete. Task 31 added the team-scoped `CapabilityResolver` / `EffectiveToolPolicy` foundation for distributed team assignments. | Finish full-route convergence in Task 38 so solo, team, Subagent, API inspection, and validation paths share the same effective-policy model. |
| P5: team hardening | Mostly complete for local validation, same-child validation rework, patch conflict recovery, stress coverage, mailbox collaboration, bounded Leader plan repair, and policy-backed recovery budgets. | Continue later with metrics-driven recovery tuning after accounting and observability are stable. |

## Locked Forward Roadmap

Task 30 fixes the default sequence below. Future changes may reorder it only
through a documented roadmap change, not as an incidental implementation-plan
choice.

Task 34 is complete and recorded in the completed-task table above. The
remaining locked sequence starts at Task 35.

| Task | Phase | Purpose | Exit condition |
| --- | --- | --- | --- |
| Task 35 | Accounting | Remove money cost budget concepts and keep runtime budget enforcement token-based. | TD-024 is closed by deleting amount/cost-budget fields, docs, settings, and compatibility paths; token budgets remain the sole runtime budget control. |
| Task 36 | Observability | Make production observability an AgentLoop middleware capability as well as durable query-table evidence. | TD-033 is closed: model/tool/agent spans, metrics, latency, dashboards, alerts, and trace visualization are available through documented exporter paths; Worker-only projection is not the only observability path. |
| Task 37 | Middleware | Mature `MiddlewareContext` into typed extension envelopes. | Middleware receives typed trace, capability, assignment, budget, handoff, and error-classification context without forcing unrelated middleware to depend on a monolithic context. |
| Task 38 | Governance | Converge capability resolution across solo, team, subagent, API inspection, and validation paths. | P4 is fully complete: all runtime routes expose and execute tools through the same effective-policy model, with route-specific inputs rather than route-specific permission logic. |
| Task 39 | Provider | Add multi-model and multi-provider routing/fallback after accounting and permissions are stable. | Model routing can make provider decisions without bypassing token, capability, observability, or error-classification policy. |
| Task 40 | Extension | Add MCP, skills, or DeerFlow-style expansion only after the runtime kernel is stable. | External tool/skill expansion uses the same capability resolver, audit records, budgets, and AgentLoop observability as built-in tools. |

The runtime kernel is considered stable only after the remaining kernel tasks
through Task 38 are complete. Before then, new provider, MCP, skill, or
product-surface work must be limited to changes required to finish those tasks.

## Roadmap Change Control

- Every new task must map to one row in the locked roadmap, one open technical
  debt item, or a documented production incident.
- If a future task needs to reorder the roadmap, first update this document in
  a standalone governance commit that states the reason, dependency impact,
  and displaced work.
- Local plans under `.codex/exec-plans/active/` may decompose work but must not
  silently change architecture direction, phase ordering, or exit conditions.
- A task may close or narrow debt only when executable evidence exists in
  tests, health checks, traces, durable query APIs, or documented operational
  checks.
- Do not start broad refactors, multi-provider work, MCP expansion, or
  DeerFlow-style skill orchestration while a kernel-phase task remains open,
  unless the refactor is the explicit purpose of that kernel task.

## Task 22 Breakdown

| Phase | Status | Purpose |
| --- | --- | --- |
| Task 22A | Done | Model-driven Leader `TeamPlan` creation, validation, retry-on-invalid, and Teammate child Run creation. |
| Task 22B | Done | Replace deterministic Teammate role completion with assignment-scoped model/tool loops using assigned tools and skills. |
| Task 22C | Done | Add durable Teammate-owned dynamic Subagent creation with depth and concurrency limits. |
| Task 22D | Done | Replace deterministic Verifier completion with model-driven verification. |
| Task 22E | Done | Add targeted replacement and rework when verification fails. |
| Task 22F | Done | Added full distributed team happy-path and verifier-rework E2E coverage, trace assertions, idempotent patch aggregation, and final documentation cleanup. |

## Gap Disposition

| Gap | Disposition |
| --- | --- |
| Modifying Runs can be created but never claimed | Resolved in Task 07 |
| `scripts/check.ps1` cannot independently reproduce PostgreSQL test settings | Resolved in Task 07 |
| Read-only tools bypass the central `ToolExecutor` | Resolved in Task 07 |
| Approval API is still a placeholder | Resolved in Task 08 |
| Running Runs cannot be cancelled | Resolved in Task 09 for solo runtime paths |
| Deterministic validation and rework do not exist | Resolved in Task 10 for solo modifying runs |
| One successful read is not enough proof for answer correctness | Future read-only answer validation hardening; Task 10 covers modifying validation only |
| Context and checkpoints can grow quickly | Resolved for solo read-only and modifying paths in Task 16; distributed team payload hardening resolved in Task 18; model-driven team loops use root-aware budget guards and compacted handoff/result payloads |
| Lifecycle projections are inconsistent | Resolved in Task 11 for solo runtime paths |
| Observability score is too high for current evidence | Resolved in Task 12 for solo runtime paths |
| Artifact references are not connected to the main loop | Task 07 |
| Team E2E is not real end-to-end execution | Resolved in Task 13 for scoped single-Run team runtime and Task 22F for distributed model-driven child-Run runtime |
| Worktrees and branches accumulate permanently | Resolved in Task 14 for explicit managed workspace cleanup; background automatic cleanup remains out of scope |
| API health and doctor are too optimistic | Resolved in Task 15 |
| Local API can bind non-loopback without authentication | Resolved in Task 07 with explicit unsafe gate; production auth remains out of scope |
| Direct ASGI hosting can bypass the CLI non-loopback gate | Resolved in Task 15 |
| Current capability docs drift from implementation | Resolved for Task 07 solo execution claims; future drift remains tracked by harness |

## Sequencing Rules

- Do not start Task 08 until Task 07 has a safe modifying graph and persistent
  tool invocation records.
- Do not start Task 10 until Task 07 can produce durable diffs and Task 08 can
  gate ambiguous or dangerous commands.
- Do not claim distributed multi-Worker team runtime capability until Task 17
  passes real E2E with Leader, Teammate, Verifier, and depth-2 Subagent child
  Runs, independent Worker claims, patch aggregation, mailbox evidence, and
  recursive cancellation.
- Do not claim new runtime routes use the middleware architecture unless those
  routes have focused tests proving model/tool policy enters AgentLoop
  middleware and graph modules retain only durable coordination.
- Do not claim concurrent distributed team stress hardening, mailbox
  collaboration, advanced replanning, or team middleware architecture until the
  corresponding technical debts or later tasks are closed with executable
  evidence.
- Do not prioritize DeerFlow-style skills or MCP expansion until the runtime
  kernel is stable behind the ThinGraph, AgentLoop, middleware, and hooks
  boundary.
- Do not raise quality scores unless executable evidence exists in tests,
  health checks, traces, or durable query APIs.
