# Operations Guide

This guide covers local startup, readiness, diagnostics, runtime data, and
troubleshooting. Use the [quickstart](../getting-started/quickstart.md) for the
first local setup path.

- [Startup modes](startup-modes.md)
- [Diagnostics](diagnostics.md)
- [Runtime data](runtime-data.md)
- [Troubleshooting](troubleshooting.md)

## Choose A Mode

Use Local CLI for ordinary repository work. Use Local API when another client
needs HTTP/SSE contracts. Use Docker API when you need the API, Worker,
PostgreSQL, and sandbox services started together with container-managed
dependencies.

## Readiness Signals

`/health` proves only that the API process can respond. `/ready` and
`awesome-agent doctor` inspect dependencies such as database migrations,
checkpoint storage, provider configuration, sandbox health, extension catalog
health, and worker heartbeats.

`awesome-agent start` is a fallback/debug supervisor for local API development,
not the normal Local CLI path.

For durable runtime boundaries, see the
[architecture guide](../architecture/README.md). For thread and diagnostics API
resources, see the [API guide](../api/README.md).
