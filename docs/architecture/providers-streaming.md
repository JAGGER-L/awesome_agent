# Providers And Streaming

Provider integrations are adapters behind a provider-neutral model protocol.
They do not own graph state, tool permission, workspace authority, or runtime
budgets.

## Provider Boundary

Provider adapters handle:

- request serialization
- streamed response parsing
- usage extraction
- retryable provider errors
- model-specific options supported by the configured profile

The runtime owns deadlines, cancellation, tool execution, persisted model-call
records, and surface projections.

## Streaming

Streaming events are normalized before surfaces render them. Surfaces may show
assistant text, reasoning summaries, status, usage, and tool progress, but they
must not infer runtime state from provider-specific chunks.

## Related Documents
