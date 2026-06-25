# Reliability

V1 reliability requirements:

- interrupted runs resume from PostgreSQL checkpoints
- runtime events retain deterministic sequence order
- Mem0 outages do not fail runs
- tool and model calls support timeout and cancellation
- sandbox termination does not corrupt the target repository
- task and agent state transitions are validated
- SSE clients can reconnect from an event cursor
- failures record cause, retry target, and remaining uncertainty

Retries must be bounded and evidence-driven. Silent infinite retries are
forbidden.

## Durable Execution Contract

- `RunStatus` describes product lifecycle; `DispatchStatus` separately
  describes queue and worker scheduling.
- PostgreSQL time is authoritative for lease acquisition and expiry.
- Every worker claim receives a monotonically increasing fencing token.
- Claims use `FOR UPDATE SKIP LOCKED`; two workers cannot validly claim one Run.
- Heartbeat extends a lease only when Run, worker UUID, fencing token, and
  unexpired lease all match.
- Defaults are a 60-second lease, 15-second heartbeat interval, and three
  maximum claims.
- A stale worker cannot commit protected projections, events, or side effects
  after lease loss.
- Graph transitions that may replay have stable transition IDs.
- Tool side effects use stable invocation/idempotency IDs or are explicitly
  non-retryable.
- Checkpoint-ahead recovery replays only idempotent projection updates.
- Projection-ahead recovery reuses the recorded result and skips the completed
  external side effect.
- Ambiguous state enters `recovery_required` and preserves the workspace,
  diff, artifacts, and failure evidence.
- Cancellation is durable and checked before graph, model, and tool boundaries.
- Active subprocess and Docker process trees must terminate within a bounded
  interval.
- Approval waits checkpoint before releasing the worker lease.
- Worktree cleanup is explicit in V1 and never deletes a user-owned or
  unconfirmed path.
- Run intake writes a private reservation before creating a branch or
  worktree. The public Run, Leader, initial events, and reservation publication
  commit atomically only after the workspace is ready.
- Startup reconciliation rolls back incomplete owned intake side effects.
  Accepted Run worktrees remain retained; automatic accepted-run cleanup is not
  implemented.
- Expired leases requeue before the attempt limit. At the limit, the Run enters
  `recovery_required + terminal` and preserves its workspace.
- Each Worker process executes at most one Run and currently claims only
  `runtime_probe`.
- Probe checkpoints use synchronous LangGraph durability. Process-crash tests
  prove lease expiry, fencing-token increment, and checkpoint resume.
- Graceful Worker shutdown stops new claims, retains heartbeat during a
  bounded grace period, and leaves ownership to expire if safe completion does
  not occur.
- SSE consumers poll ordered PostgreSQL events, so API restarts and separate
  Worker processes do not lose durable history.
- Read-only model/tool execution uses synchronous checkpoints and explicit
  graph back edges. Read tools are replay-safe, and stable transition IDs
  deduplicate audit events after replay.
- Correctable tool failures return to the model loop. Retryable provider
  failures release the lease for delayed retry; understood permanent failures
  use `failed`, not `recovery_required`.

Deterministic fault-injection tests must cover worker death around checkpoint
and projection commits, lease expiry, stale fencing, approval wait, active
sandbox execution, and validation.
