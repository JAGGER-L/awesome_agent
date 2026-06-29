# Reliability

V1 reliability requirements:

- interrupted runs resume from PostgreSQL checkpoints
- runtime events retain deterministic sequence order
- Mem0 outages do not fail runs
- tool and model calls support timeout and cancellation
- sandbox termination does not corrupt the target repository
- task and agent state transitions are validated
- SSE clients can reconnect from an event cursor
- run/model/tool/sandbox observability records are queryable by Run
- readiness checks expose dependency status without being confused with cheap
  process liveness
- failures record cause, retry target, and remaining uncertainty

Retries must be bounded and evidence-driven. Silent infinite retries are
forbidden.

## Durable Execution Contract

- `RunStatus` describes product lifecycle; `DispatchStatus` separately
  describes queue and worker scheduling.
- Visible Run, Agent, and Todo lifecycle changes update projections and append
  matching events in one transaction.
- Agent and Todo lifecycle changes increment `revision` and maintain
  `updated_at`; heartbeat remains lease liveness and does not imply product
  state change.
- PostgreSQL time is authoritative for lease acquisition and expiry.
- Every worker claim receives a monotonically increasing fencing token.
- Claims use `FOR UPDATE SKIP LOCKED`; two workers cannot validly claim one Run.
- Heartbeat extends a lease only when Run, worker UUID, fencing token, and
  unexpired lease all match.
- Worker process liveness is recorded separately in `worker_heartbeats`.
  Runtime readiness requires a fresh online Worker heartbeat covering the
  required runtime routes; a stale or missing Worker heartbeat makes runtime
  readiness `unhealthy` even when `/health` liveness still returns 200.
- Defaults are a 60-second lease, 15-second heartbeat interval, and three
  maximum claims.
- A stale worker cannot commit protected projections, events, or side effects
  after lease loss.
- Graph transitions that may replay have stable transition IDs.
- Tool side effects use stable invocation/idempotency IDs or are explicitly
  non-retryable.
- Checkpoint-ahead recovery replays only idempotent projection updates.
- Modifying tool recovery reuses completed durable tool results and skips the
  completed external side effect; ambiguous patch or shell state enters
  `recovery_required`.
- Ambiguous state enters `recovery_required` and preserves the workspace,
  diff, artifacts, and failure evidence.
- Durable approval waits are implemented for exact tool invocations in
  `solo-modifying`: the approval row and LangGraph checkpoint are durable
  before the worker releases the lease as `paused + waiting`.
- Durable cancellation is implemented for current solo runtime paths. Queued,
  retry-scheduled, and waiting-approval Runs cancel atomically. Claimed and
  executing Runs store a durable cancellation request that the owning Worker
  observes before committing `cancelled + terminal`.
- Worktree cleanup is explicit in V1 and never deletes a user-owned or
  unconfirmed path.
- Run intake writes a private reservation before creating a branch or
  worktree. The public Run, Leader, initial events, and reservation publication
  commit atomically only after the workspace is ready.
- Startup reconciliation rolls back incomplete owned intake side effects.
  Accepted Run workspaces remain retained until an explicit workspace cleanup
  preview/apply request. Background automatic cleanup is not implemented.
- Workspace cleanup re-evaluates ownership and Git state immediately before
  deletion. Normal cleanup removes only clean managed workspaces for terminal
  completed or cancelled Runs. Failed or dirty workspaces require force with a
  reason; `recovery_required` workspaces are retained as recovery evidence.
- Expired leases requeue before the attempt limit. At the limit, the Run enters
  `recovery_required + terminal` and preserves its workspace.
- Each Worker process executes at most one Run. Workers always claim
  `runtime_probe`, `team-role`, and `team-verifier`. Without a configured model
  provider, `team-role` keeps only its legacy deterministic fallback. When a
  model provider is configured, Workers also claim `solo-readonly`,
  `solo-modifying`, scoped `team-coding-scoped`, the model-driven distributed
  Leader root graph `team-coding`, and assignment-scoped model/tool execution
  inside `team-role`.
- Probe checkpoints use synchronous LangGraph durability. Process-crash tests
  prove lease expiry, fencing-token increment, and checkpoint resume.
- Graceful Worker shutdown stops new claims, retains heartbeat during a
  bounded grace period, and leaves ownership to expire if safe completion does
  not occur.
- Active cancellation cancels the graph task, preserves `CancelledError`
  propagation, and uses a cancellable subprocess runner. Docker shell execution
  uses managed container names and attempts `docker rm -f` on timeout or
  cancellation.
- SSE consumers poll ordered PostgreSQL events, so API restarts and separate
  Worker processes do not lose durable history.
- Read-only model/tool execution uses synchronous checkpoints and explicit
  graph back edges. Read tools are replay-safe, and stable transition IDs
  deduplicate audit events after replay.
- Correctable tool failures return to the model loop. Retryable provider
  failures release the lease for delayed retry; understood permanent failures
  use `failed`, not `recovery_required`.
- Modifying completion is validation-gated. The graph must apply at least one
  patch, inspect the final diff after the last write, and pass required
  validation gates before reporting completed. Required gate failure feeds a
  bounded rework loop; exhausted or non-reworkable failure marks the Run
  failed.
- Approval decisions are compare-and-set, idempotent for already-decided
  records, and requeue waiting Runs. Expired pending approvals are marked
  expired by the worker recovery cadence and resume as structured tool errors.
- Runtime observability writes are failure-isolated from Run execution.
  PostgreSQL query tables store run, graph, model, tool, and sandbox spans,
  model-call summaries, latency metrics, and trace/span IDs. API endpoints,
  Worker `run.execute`/`graph.execute` boundaries, and migrated solo AgentLoop
  `agent.run`/`model.call`/`tool.call` stages create real OpenTelemetry spans
  through a failure-isolated facade. OTel exporter failures must not alter Run
  status or HTTP responses.
- Readiness exposes `healthy`, `degraded`, and `unhealthy`. Required dependency
  failures make readiness `unhealthy`; optional or advisory failures make it
  `degraded`. `/health` remains process liveness only. `/ready` returns 200 for
  `healthy` and `degraded`, and 503 for `unhealthy`; `doctor` exits 0 for
  `healthy` and `degraded`, and 1 for `unhealthy`.
- Distributed team Runs release parent Runs to child-wait states instead of
  holding a Worker lease. Child completion records assignment terminal status
  and requeues the waiting parent. Parent cancellation recursively cancels
  nonterminal descendants while preserving terminal child evidence.
- Distributed team patch aggregation is idempotent. The Leader applies a
  Teammate patch artifact when the preimage matches and treats an already
  present postimage as aggregated; partial or conflicting patch state still
  fails the parent Run for explicit recovery or rework.

Deterministic fault-injection tests must cover worker death around checkpoint
and projection commits, lease expiry, stale fencing, approval wait, active
sandbox execution, and validation.

## Context And Budget Reliability

- Solo read-only and modifying graphs compact context before provider calls
  when the soft context threshold is crossed.
- Removed messages and oversized tool observations are persisted as artifacts
  before checkpoint state is reduced.
- Hard context pressure disables further tool calls and forces a bounded final
  response instead of allowing unbounded prompt growth.
- Per-Run token ledgers persist input, output, reasoning tokens, model-call
  count, threshold status, and active Worker execution seconds.
- Active budget time starts only while a Worker is executing graph work and is
  closed before completion, failure, retry, cancellation, or approval wait is
  projected.
- Token estimation is heuristic in Task 16; provider/tokenizer-specific
  accounting remains tracked as technical debt.
- Distributed team graph boundaries evaluate root-aware budget snapshots across
  the root Run and all descendants before creating child work, recording child
  results, or verifying results.
- Large distributed team handoff, child-result, verifier evidence, and mailbox
  payloads are written to artifacts before inline payloads are reduced.
  Compaction records keep before/after token estimates and artifact refs for
  frontend inspection.
- Assignment tool exposure is filtered as
  `allowed_tools - (deferred_tools - promoted_tools)` so hidden tools are not
  presented to Teammates or inherited by Subagents before promotion.
