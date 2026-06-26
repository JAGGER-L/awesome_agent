# Quality Score

Current phase: durable read-only Coding loop complete; modifying Runs remain
queued pending isolated mutation and sandbox shell tools.

| Area | Score | Evidence |
| --- | ---: | --- |
| Instructions | 4/5 | Separate engineering and runtime harness contracts |
| Environment | 4/5 | Python 3.12, locked `uv`, doctor, Docker and PostgreSQL |
| State and scope | 4/5 | Ignored local plans, runtime task state, durable debt tracker |
| Static validation | 4/5 | Ruff, strict mypy and architecture checks pass |
| Behavioral tests | 4/5 | Unit, structural, integration, and E2E gates |
| System tests | 3/5 | PostgreSQL restart, Worker crash/resume, sandbox, worktree, and API tests; Team E2E still uses manually constructed state |
| Observability | 2/5 | Structured events, PostgreSQL-polled SSE, and query APIs exist; full run/model/tool/sandbox spans, metrics, cost, latency, and recovery indicators remain roadmap work |
| Security | 4/5 | Deny-all allowed roots, UUID-only API intake, clean-base named worktrees |

Scores increase only with executable evidence.
