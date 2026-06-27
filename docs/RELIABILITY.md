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
  required graph identities; a stale or missing Worker heartbeat makes runtime
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
  `solo-modifying@1`: the approval row and LangGraph checkpoint are durable
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
  `runtime_probe`; when a model provider is configured, they also claim
  `solo-readonly@1`, `solo-modifying@1`, and `team-coding@1`.
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
  model-call summaries, latency metrics, and trace/span IDs. OpenTelemetry
  exporter failures are logged and never change Run status.
- Readiness exposes `healthy`, `degraded`, and `unhealthy`. Required dependency
  failures make readiness `unhealthy`; optional or advisory failures make it
  `degraded`. `/health` remains process liveness only. `/ready` returns 200 for
  `healthy` and `degraded`, and 503 for `unhealthy`; `doctor` exits 0 for
  `healthy` and `degraded`, and 1 for `unhealthy`.

Deterministic fault-injection tests must cover worker death around checkpoint
and projection commits, lease expiry, stale fencing, approval wait, active
sandbox execution, and validation.
