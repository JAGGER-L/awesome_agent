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

## Approval Wait Persistence

A waiting approval stores the approval record, runtime event, dispatch state,
and enough continuation data to resume the original tool call. Recovery must
distinguish approval wait from retry after execution failure.

When approval is decided, resume reconstructs the assistant tool-call message,
executes or rejects the tool result, appends the tool result, and then re-enters
the model loop so the model can produce the user-facing answer from the actual
tool observation. The runtime must not replace this with a synthetic final
answer.

Approved or denied decisions can also be reused as bounded grants for later
matching tool calls in the same run. Grant reuse does not create a new approval
row; it records an `approval.reused` event that points back to the source
approval and the bounded resource scope.

## Local Coding Product Contract

The local coding path is considered healthy only when a turn can create
non-empty workspace files through the public `WriteFile` tool, validate them
through `Bash`, persist changed-file metadata, and finish with a provider
generated assistant message after tool results re-enter the model context.
Safe workspace writes and allowlisted validation commands must not request
approval. Sensitive writes must pause once, replay deterministically after
approval, and reuse a bounded same-run grant for the same approved resource.

## Related Documents

- [Runtime kernel](runtime-kernel.md)
- [Observability](observability.md)
