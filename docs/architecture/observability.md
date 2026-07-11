# Observability

Observability exists to make runtime behavior explainable and recoverable. It
should produce bounded evidence that users, operators, and contributors can use
without reading raw process output first.

## Signals

The system records or exposes:

- thread, run, turn, and event state
- model-call attempts, timing, usage, and errors
- tool calls, approvals, and observations
- checkpoint and compaction records
- diagnostic readiness summaries
- optional OpenTelemetry export

## Reader Split

User-facing status belongs in the TUI and [user guide](../user-guide/README.md).
User-visible status and diagnostics belong in the local product surface.
Durable design rationale belongs in this architecture document.

## Related Documents

- [Persistence and recovery](persistence-recovery.md)
