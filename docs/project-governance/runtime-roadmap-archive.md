# Runtime Roadmap Archive

This archive preserves historical roadmap detail moved out of
[`runtime-roadmap.md`](runtime-roadmap.md) on 2026-07-01. It is useful for
traceability, but the current roadmap file remains the source of truth for
future sequencing, architecture direction, and change control.

## Historical Completed Foundation

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
| Task 16 | Done | Artifact-backed solo context compaction, durable token ledgers, active Worker execution budgets, budget/compaction APIs and CLI, and team global budget guards. |
| Task 17 | Done | Distributed team child-run skeleton with durable lineage, assignments, mailbox, child results, recursive cancellation, inspection APIs/CLI, production Worker wiring, and PostgreSQL integration/E2E evidence. |
| Task 18 | Done | Root-aware distributed team budget checks, deferred assignment tool exposure, and artifact-backed compaction for large handoff, child-result, verifier evidence, and mailbox payloads. |
| Task 19 | Done | Pre-production graph-version removal, baseline migration squash, and ThinGraph, AgentLoop, middleware, and checkpoint-boundary contracts. |
| Task 20 | Done | `solo-readonly` now enters AgentLoop middleware stages, with read-only evidence, progress, context, compaction, and budget policy extracted from the graph. |
| Task 21 | Done | `solo-modifying` now enters AgentLoop middleware stages, with context, budget, tool execution, approval, artifact offload, evidence, validation, rework, and finalization policy extracted from the graph. |
| Task 22 | Done | Replaced deterministic `team-coding` role skeletons with model-driven Leader planning, assignment-scoped Teammate model/tool loops, Teammate-owned Subagents, structured Verifier decisions, targeted replacement rework, and patch-producing distributed E2E evidence. |
| Task 23 | Done | Real OTel spans on API endpoints, Worker graph boundaries, and migrated solo AgentLoop model/tool paths while preserving durable query tables. |
| Task 24 | Done | `team-coding`, `team-role`, and `team-verifier` now route Leader planning, Teammate/Subagent model/tool execution, Verifier decisions, and team observability through `TeamAgentLoop` middleware; durable child-run coordination remains graph-owned. |
| Task 25 | Done | Distributed writing Teammate child Runs now run deterministic validation before publishing completed patch results, with validation execution wrapped by `TeamAgentLoop`, durable validation reports, verification events, failed child-result semantics, and E2E evidence. |
| Task 26 | Done | Writing Teammate child Runs now perform bounded same-child validation rework for reworkable deterministic command failures before publishing patch artifacts or falling back to failed child-result semantics. |
| Task 27 | Done | Distributed team patch aggregation now classifies conflicting Teammate patches and recovers through bounded replacement Teammate child Runs before verifier creation. |
| Task 28 | Done | Added true concurrent multi-Worker stress coverage for distributed team Runs across sibling Teammates, Teammate-owned Subagents, Verifier, patch aggregation, mailbox/result persistence, and dispatch claim evidence. |
| Task 29 | Done | Added route-restricted Teammate mailbox collaboration through assignment-scoped mailbox tools, durable read/respond lifecycle, Leader root audit visibility, and Worker-path evidence. |
| Task 30 | Done | Locked the post-Task-29 runtime roadmap, architecture invariants, P2-P5 disposition, forward task sequence, and change-control rules. |
| Task 31 | Done | Added a team-scoped `CapabilityResolver` / `EffectiveToolPolicy` foundation for distributed team assignments. |
| Task 32 | Done | Added bounded Leader plan repair for Verifier-requested distributed team rework. |
| Task 33 | Done | Replaced hard-coded distributed team recovery defaults with `TeamRecoveryPolicy` and Worker settings. |
| Task 34 | Done | Replaced heuristic-only prompt token estimation with provider/model-aware token accounting. |
| Task 35 | Done | Removed amount limits and monetary compatibility fields from the runtime budget model. |

## Historical Later-Task Exit Evidence

| Task | Historical exit evidence |
| --- | --- |
| Task 21 | `solo-modifying` entered AgentLoop middleware stages with context, budget, tool execution, approval, artifact offload, evidence, validation, rework, and finalization policy extracted from the graph. |
| Task 22 | Full distributed team E2E covered Leader, Teammates, Verifier, Subagents, model calls, scoped tools, patch artifact generation, idempotent patch aggregation, traceability, and verifier rework. |
| Task 23 | API, `run.execute`, `graph.execute`, `agent.run`, `model.call`, and `tool.call` spans were created through `ObservabilityFacade` and AgentLoop observability middleware; exporter failures were isolated and trace IDs remained queryable through durable events. |
| Task 24 | Focused unit tests covered TeamAgentLoop, Leader planning, role model/tool calls, Verifier decisions, and Worker observability projection changes; distributed integration/E2E tests remained database-gated in local runs. |
| Task 25 | Focused unit tests covered pass, fail, skip, and AgentLoop metadata behavior; distributed E2E fixture validation recorded report, event, and span evidence before patch publication. |
| Task 26 | Focused unit tests covered fail-then-pass, exhaustion, non-reworkable failures, and feedback injection; distributed E2E covered same-child validation recovery without replacement child creation. |
| Task 27 | Focused unit tests covered aggregation classification, Leader replacement creation, superseded result filtering, budget exhaustion, and Verifier effective evidence; distributed E2E covered Worker-path conflict recovery with durable events and results. |
| Task 28 | Integration stress coverage ran multiple DurableWorkers concurrently against PostgreSQL and asserted no duplicate claims, assignments, child results, patch aggregation, or parent verifier races. |
| Task 29 | Unit tests covered route policy, repository visibility, role-loop tool exposure, and mailbox tool execution; distributed integration covered Teammate-to-Teammate question/response mailbox flow without weakening Subagent isolation or Verifier authority. |
| Task 30 | Runtime roadmap named Task 31-40 ordering, kernel-stability criteria, phase gates, and disallowed early expansions; local execution evidence recorded baseline and documentation validation. |
| Task 31 | Team planning, role-loop exposure, role tool execution, Subagent grants, Verifier review tools, and API inspection used resolver-derived effective tools and per-tool capabilities without weakening mailbox, delegation, write, or Subagent restrictions. |
| Task 32 | Leader repair decisions became structured, audited, budgeted, and able to replace or add Teammate child Runs while preserving assignment lineage, filtering superseded evidence, retiring failed Verifiers, and requiring a fresh Verifier pass after repaired children finished. |
| Task 33 | Verifier invalid-output attempts, verifier retry helpers, plan-repair budgets, patch-conflict rework budgets, model-output rework budgets, and unknown-failure fallback budgets became policy-owned, configurable, validated, and emitted in recovery events. |
| Task 34 | Budget checks, context compaction, team payload compaction, and model-request estimates used `TokenAccountant` profiles with estimator provenance and documented fallback error margins while provider-reported usage remained the durable ledger source. |
| Task 35 | TD-024 was closed as a deliberate non-goal: runtime budgets remain token, reasoning-token, active-time, call-count, retry, and rework limits without amount-derived gates or ledgers. |

## Historical Product Surface Setup

| Task | Historical exit evidence |
| --- | --- |
| Task 55 | README, quickstart, operations docs, Docker Compose API/Worker services, and structural tests covered Local CLI, Local API, Docker CLI, and Docker API/Web inspection lanes. |
| Task 56 | `awesome-agent tui` added a first API-backed local operator console for Run discovery, diagnostics, recent events, and approvals without direct database writes. |

## Task 07 Historical Detail

Task 07 made modifying solo Runs executable without weakening the runtime
safety model. It was completed on 2026-06-26.

Task 07 included:

- routing `coding + modifying` Runs to `solo-modifying`;
- moving read-only and modifying graph tool calls through the centralized tool
  execution boundary before adding write-capable tools;
- enforcing tool specification, capability, profile, timeout, sandbox,
  approval classification, and artifact handling in one execution path;
- adding versioned `repo.apply_patch`, `repo.diff`, Docker-backed
  `shell.execute`, and `artifact.read` tools while avoiding arbitrary
  `write_file`;
- executing write tools and shell commands sequentially, while allowing read
  tools to remain parallel only when they pass the same executor policy;
- using Docker for automatic shell execution;
- persisting side-effecting tool invocations with idempotency keys, path lists,
  preimage hashes, expected postimage hashes, status, result summaries, and
  artifact references;
- reconciling patch recovery deterministically: matching preimage applies,
  matching postimage is already done, and partial or ambiguous file state
  becomes `recovery_required`;
- treating unknown shell completion after a crash as `recovery_required`
  instead of replaying the command automatically;
- wiring artifact offload into the main agent loop;
- adding prompt guards for large single tool outputs;
- returning an explicitly unvalidated modifying completion state before Task 10
  added deterministic validation and rework;
- blocking non-loopback local API serving unless explicitly unsafe.

Task 07 did not include durable approvals, active cancellation, deterministic
project validation and rework, team-mode execution, automatic worktree cleanup,
full OpenTelemetry coverage, or production multi-user authentication.

## Task 22 Historical Breakdown

| Phase | Status | Purpose |
| --- | --- | --- |
| Task 22A | Done | Model-driven Leader `TeamPlan` creation, validation, retry-on-invalid, and Teammate child Run creation. |
| Task 22B | Done | Replace deterministic Teammate role completion with assignment-scoped model/tool loops using assigned tools and skills. |
| Task 22C | Done | Add durable Teammate-owned dynamic Subagent creation with depth and concurrency limits. |
| Task 22D | Done | Replace deterministic Verifier completion with model-driven verification. |
| Task 22E | Done | Add targeted replacement and rework when verification fails. |
| Task 22F | Done | Added full distributed team happy-path and verifier-rework E2E coverage, trace assertions, idempotent patch aggregation, and final documentation cleanup. |

## Historical Gap Disposition

| Gap | Disposition |
| --- | --- |
| Modifying Runs can be created but never claimed | Resolved in Task 07 |
| `scripts/check.ps1` cannot independently reproduce PostgreSQL test settings | Resolved in Task 07 |
| Read-only tools bypass the central `ToolExecutor` | Resolved in Task 07 |
| Approval API is still a placeholder | Resolved in Task 08 |
| Running Runs cannot be cancelled | Resolved in Task 09 for solo runtime paths |
| Deterministic validation and rework do not exist | Resolved in Task 10 for solo modifying runs |
| One successful read is not enough proof for answer correctness | Future read-only answer validation hardening; Task 10 covers modifying validation only |
| Context and checkpoints can grow quickly | Resolved for solo read-only and modifying paths in Task 16; distributed team payload hardening resolved in Task 18 |
| Lifecycle projections are inconsistent | Resolved in Task 11 for solo runtime paths |
| Observability score is too high for current evidence | Resolved in Task 12 for solo runtime paths |
| Artifact references are not connected to the main loop | Resolved in Task 07 |
| Team E2E is not real end-to-end execution | Resolved in Task 13 and Task 22F |
| Worktrees and branches accumulate permanently | Resolved in Task 14 for explicit managed workspace cleanup |
| API health and doctor are too optimistic | Resolved in Task 15 |
| Local API can bind non-loopback without authentication | Resolved in Task 07 with explicit unsafe gate |
| Direct ASGI hosting can bypass the CLI non-loopback gate | Resolved in Task 15 |
| Current capability docs drift from implementation | Resolved for Task 07 solo execution claims; future drift remains tracked by harness |

## Historical Sequencing Rules

These rules were useful while the early runtime was incomplete. They are now
archived because the current roadmap expresses the active ordering.

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
