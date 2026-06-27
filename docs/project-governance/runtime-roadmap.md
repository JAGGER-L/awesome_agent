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
| Task 12 | Done | Solo runtime observability with trace IDs, query-table spans, metrics, model calls, latency, and exporter isolation. |
| Task 13 | Done | Explicit scoped `team-coding@1` runtime with real Worker, PostgreSQL, checkpoint, provider, tool, verifier, rework, validation, and observability E2E evidence. |

## Task 07: Isolated Mutation Sandbox and Shell

Task 07 makes modifying solo Runs executable without weakening the runtime
safety model.

Status: completed on 2026-06-26.

Task 07 includes:

- route `coding + modifying` Runs to `solo-modifying@1` and make Workers claim
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
| Task 08 | Durable approval and command policy | Replace placeholder approvals with durable approve/deny/expire flows bound to one exact invocation. | Implemented for solo modifying runs; approval requests, decisions, expiry, worker release, and resume semantics pass unit tests. |
| Task 09 | Active cancellation | Propagate cancellation through graph boundaries, provider calls, tool calls, Docker, and subprocess trees. | Implemented for solo runtime paths; queued, waiting, claimed, and executing solo Runs can cancel without corrupting projections, checkpoints, or worktrees. |
| Task 10 | Validation and rework loop | Add deterministic validation gates, verifier feedback, and model rework until pass/fail is evidenced. | Implemented for solo modifying runs; configured or conservatively detected gates gate completion, rework is bounded, validation evidence is durable, and terminal validation failure fails the Run. |
| Task 11 | Lifecycle projection consistency | Make Run, Agent, Todo, event, revision, and `updated_at` transitions coherent and frontend-ready. | Implemented for solo runtime paths; visible Run, Agent, and Todo lifecycle transitions now update projections, revisions, timestamps, and matching durable events in one transaction. |
| Task 14 | Worktree and branch retention | Add explicit cleanup, retention policy, and safe branch/worktree pruning for completed Runs. | Owned inactive worktrees can be listed, preserved, or safely removed without touching user-owned paths or unexported diffs. |
| Task 15 | Health and doctor readiness | Make `/health` and `doctor` report real dependencies instead of optimistic process liveness. | PostgreSQL, migrations, checkpoint store, provider keys, worker heartbeat, workspace root, and model route availability are checked. |
| Task 16 | Context, checkpoint, and budget management | Add stronger token-window management, summaries, artifact-backed context, wall-clock budget, and cost budget. | Long Runs remain bounded without losing required evidence or replay data. |
| Task 17 | Distributed team child Runs | Promote graph-internal Teammates into Leader-created child Runs that independent Workers can claim. | Parent/child Run lineage, status propagation, checkpoint coordination, cross-Run cancellation, and result aggregation pass E2E. |

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
| Context and checkpoints can grow quickly | Minimal artifact/prompt guard in Task 07; full budget system in Task 16 |
| Lifecycle projections are inconsistent | Resolved in Task 11 for solo runtime paths |
| Observability score is too high for current evidence | Resolved in Task 12 for solo runtime paths |
| Artifact references are not connected to the main loop | Task 07 |
| Team E2E is not real end-to-end execution | Resolved in Task 13 for scoped single-Run team runtime; distributed child-Run team runtime remains Task 17 |
| Worktrees and branches accumulate permanently | Task 14 |
| API health and doctor are too optimistic | Task 15 |
| Local API can bind non-loopback without authentication | Resolved in Task 07 with explicit unsafe gate; production auth remains out of scope |
| Direct ASGI hosting can bypass the CLI non-loopback gate | Task 15 |
| Current capability docs drift from implementation | Resolved for Task 07 solo execution claims; future drift remains tracked by harness |

## Sequencing Rules

- Do not start Task 08 until Task 07 has a safe modifying graph and persistent
  tool invocation records.
- Do not start Task 10 until Task 07 can produce durable diffs and Task 08 can
  gate ambiguous or dangerous commands.
- Do not claim distributed multi-Worker team runtime capability until Task 17
  passes real E2E. Current team mode is scoped to one Run, one Worker, and one
  checkpoint thread.
- Do not raise quality scores unless executable evidence exists in tests,
  health checks, traces, or durable query APIs.
