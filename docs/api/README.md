# API

These documents are for clients and integrators using Awesome Agent through API
surfaces. They describe resource contracts at a product level and link to
generated reference material when a schema is needed.

- [Thread API](thread-api.md)
- [Diagnostics API](diagnostics-api.md)

## Contract Shape

API list endpoints return paginated envelopes with `items`, `limit`, `offset`,
and `has_more`. Error responses return `code`, `message`, `detail`, `hint`,
`request_id`, `trace_id`, and `recoverable`.

## Execution Boundary

API clients create threads, turns, attachments, and control actions. They do not
execute tools, call providers, or choose graph nodes directly.

Use the [user guide](../user-guide/README.md) for TUI behavior and the
[operations guide](../operations/README.md) for startup and readiness.
