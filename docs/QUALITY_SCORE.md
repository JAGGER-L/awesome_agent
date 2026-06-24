# Quality Score

Current phase: DeepSeek defaults and durable runtime projections complete.

| Area | Score | Evidence |
| --- | ---: | --- |
| Instructions | 4/5 | Separate engineering and runtime harness contracts |
| Environment | 4/5 | Python 3.12, locked `uv`, doctor, Docker and PostgreSQL |
| State and scope | 4/5 | Ignored local plans, runtime task state, durable debt tracker |
| Static validation | 4/5 | Ruff, strict mypy and architecture checks pass |
| Behavioral tests | 4/5 | Unit, structural, integration, and E2E gates |
| System tests | 4/5 | PostgreSQL restart, sandbox, worktree, API and Team E2E tests |
| Observability | 3/5 | OTel, structured events, SSE and query APIs |
| Security | 3/5 | Docker default, trusted-local gate, approval and memory filters |

Scores increase only with executable evidence.
