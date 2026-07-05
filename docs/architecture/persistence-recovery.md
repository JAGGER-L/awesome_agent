# Persistence And Recovery

Persistence keeps runtime state recoverable across process interruption,
approval waits, retries, and replay. The database and local state stores are
implementation details behind repository contracts.

## Persisted State

The runtime records:

- threads, runs, turns, messages, and events
- model-call attempts and usage
- tool calls, approvals, and observations
- checkpoints and compaction records
- assignment, verifier, and rework evidence
- attachment and artifact metadata

Generated table reference lives in
[database schema](../reference/generated/db-schema.md).

## Replay

Replay should reuse persisted tool results when idempotency and version checks
prove the prior result is valid. Ambiguous side effects become recovery states
that require explicit handling instead of silent continuation.

## Recovery States

Common recovery states include approval wait, cancellation, provider timeout,
tool failure, ambiguous shell completion, checkpoint replay, and workspace
cleanup requirement. Operations docs explain how to inspect these states.

## Related Documents

- [Operations guide](../operations/README.md)
- [Runtime kernel](runtime-kernel.md)
- [Observability](observability.md)
