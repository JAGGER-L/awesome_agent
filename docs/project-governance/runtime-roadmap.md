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
| Task 22 | In progress | Replace deterministic `team-coding` role skeletons with model-driven assignment, scoped tool execution, verifier rework, and real patch-producing child Runs. Task 22A is complete: the Leader now creates a validated model-generated `TeamPlan` before creating Teammate child Runs. | Distributed team E2E covers Leader, Teammates, Verifier, Subagents, model calls, central tools, patch aggregation, validation, and rework. |
| Task 23 | OpenTelemetry runtime instrumentation | Add real OTel spans/metrics on API and Worker production paths while preserving durable query tables. | Worker/model/tool/sandbox/API spans are created, exporter failures are isolated, and trace IDs remain queryable through durable events. |
| Task 24 | Graph file size reduction | Split remaining oversized graph files after middleware migration makes stable extraction points clear. | Large graph modules are reduced to durable orchestration with focused unit tests for extracted components. |

## Task 22 Breakdown

| Phase | Status | Purpose |
| --- | --- | --- |
| Task 22A | Done | Model-driven Leader `TeamPlan` creation, validation, retry-on-invalid, and Teammate child Run creation. |
| Task 22B | Planned | Replace deterministic Teammate role completion with model/tool loops using assigned tools and skills. |
| Task 22C | Planned | Add durable Teammate-owned dynamic Subagent creation with depth and concurrency limits. |
| Task 22D | Planned | Replace deterministic Verifier completion with model-driven verification. |
| Task 22E | Planned | Add targeted replacement and rework when verification fails. |
| Task 22F | Planned | Add full distributed team E2E coverage and final documentation cleanup. |

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
| Context and checkpoints can grow quickly | Resolved for solo read-only and modifying paths in Task 16; distributed team payload hardening resolved in Task 18; model-driven team loops need new budget integration when Task 22 lands |
| Lifecycle projections are inconsistent | Resolved in Task 11 for solo runtime paths |
| Observability score is too high for current evidence | Resolved in Task 12 for solo runtime paths |
| Artifact references are not connected to the main loop | Task 07 |
| Team E2E is not real end-to-end execution | Resolved in Task 13 for scoped single-Run team runtime; distributed Teammate/Verifier/Subagent child-Run runtime remains Task 17 |
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
- Do not claim modifying or team middleware-based runtime architecture until
  Task 21 and later tasks migrate those routes behind AgentLoop middleware.
- Do not claim model-driven distributed team autonomy until Task 22 replaces
  deterministic child role skeletons with model/tool/verifier evidence.
- Do not describe observability as OpenTelemetry instrumentation until Task 23
  creates actual OTel spans on production API and Worker paths.
- Do not raise quality scores unless executable evidence exists in tests,
  health checks, traces, or durable query APIs.
