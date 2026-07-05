# Diagnostics API

Diagnostics endpoints are operator-facing API resources for liveness,
readiness, and runtime inspection.

## Endpoints

| Endpoint | Purpose |
| --- | --- |
| `/health` | Process liveness. |
| `/ready?profile=api` | API readiness for client traffic. |
| `/ready?profile=runtime` | Runtime dependency readiness. |

Readiness may include configuration, database, migration, checkpoint storage,
sandbox, provider, extension catalog, and background execution checks.

## Related Documents

- [Diagnostics](../operations/diagnostics.md)
- [Observability](../architecture/observability.md)
