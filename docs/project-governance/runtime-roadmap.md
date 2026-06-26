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

## Task 07: Isolated Mutation Sandbox and Shell

Task 07 is the next implementation task. Its goal is to make modifying solo
Runs executable without weakening the runtime safety model.

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
- add a minimum prompt budget so large tool output is summarized or referenced
  instead of being copied repeatedly into checkpoints;
- return a modifying completion state that is explicitly unvalidated until
  Task 10 adds deterministic validation and rework;
- block or require explicit unsafe configuration for non-loopback FastAPI
  serving without authentication;
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
| Task 08 | Durable approval and command policy | Replace placeholder approvals with durable approve/deny/expire flows bound to one exact invocation. | Approval requests, decisions, expiry, changed-argument rejection, and resume semantics pass integration tests. |
| Task 09 | Active cancellation | Propagate cancellation through graph boundaries, provider calls, tool calls, Docker, and subprocess trees. | Running and waiting Runs can cancel without corrupting projections, checkpoints, or worktrees. |
| Task 10 | Validation and rework loop | Add deterministic validation gates, verifier feedback, and model rework until pass/fail is evidenced. | Modifying Runs cannot report validated success without passing configured or conservatively detected gates, and failed gates feed a bounded correction loop. |
| Task 11 | Lifecycle projection consistency | Make Run, Agent, Todo, event, revision, and `updated_at` transitions coherent and frontend-ready. | Every visible status transition has a matching durable event, monotonically revised projection, and consistent timestamp. |
| Task 12 | Observability hardening | Add real run/model/tool/sandbox spans, trace ID injection, metrics, cost, latency, retry, recovery, and exporter isolation. | Observability score is backed by executable span, metric, and query-table evidence. |
| Task 13 | Real team-runtime E2E | Replace hand-constructed team tests with Worker, model, tools, database, checkpoint, verifier, and patch integration. | A team Run executes through the real runtime, creates Teammates/Subagents, verifies work, and records inspectable evidence. |
| Task 14 | Worktree and branch retention | Add explicit cleanup, retention policy, and safe branch/worktree pruning for completed Runs. | Owned inactive worktrees can be listed, preserved, or safely removed without touching user-owned paths or unexported diffs. |
| Task 15 | Health and doctor readiness | Make `/health` and `doctor` report real dependencies instead of optimistic process liveness. | PostgreSQL, migrations, checkpoint store, provider keys, worker heartbeat, workspace root, and model route availability are checked. |
| Task 16 | Context, checkpoint, and budget management | Add stronger token-window management, summaries, artifact-backed context, wall-clock budget, and cost budget. | Long Runs remain bounded without losing required evidence or replay data. |

## Gap Disposition

| Gap | Disposition |
| --- | --- |
| Modifying Runs can be created but never claimed | Task 07 |
| `scripts/check.ps1` cannot independently reproduce PostgreSQL test settings | Task 07 |
| Read-only tools bypass the central `ToolExecutor` | Task 07 |
| Approval API is still a placeholder | Task 08 |
| Running Runs cannot be cancelled | Task 09 |
| Deterministic validation and rework do not exist | Task 10 |
| One successful read is not enough proof for answer correctness | Task 10, with read-only completion hardening folded into validation policy |
| Context and checkpoints can grow quickly | Minimal artifact/prompt guard in Task 07; full budget system in Task 16 |
| Lifecycle projections are inconsistent | Task 11 |
| Observability score is too high for current evidence | Task 12, with current score corrected immediately |
| Artifact references are not connected to the main loop | Task 07 |
| Team E2E is not real end-to-end execution | Task 13 |
| Worktrees and branches accumulate permanently | Task 14 |
| API health and doctor are too optimistic | Task 15 |
| Local API can bind non-loopback without authentication | Task 07 minimum block/unsafe gate; production auth remains out of scope |
| Current capability docs drift from implementation | Task 07 documentation synchronization |

## Sequencing Rules

- Do not start Task 08 until Task 07 has a safe modifying graph and persistent
  tool invocation records.
- Do not start Task 10 until Task 07 can produce durable diffs and Task 08 can
  gate ambiguous or dangerous commands.
- Do not claim team-mode runtime capability until Task 13 passes real E2E.
- Do not raise quality scores unless executable evidence exists in tests,
  health checks, traces, or durable query APIs.
