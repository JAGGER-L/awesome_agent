# Quality Score

Current phase: durable Worker plus structured streaming model protocol complete;
Coding Runs remain queued pending the read-only model/tool loop.

| Area | Score | Evidence |
| --- | ---: | --- |
| Instructions | 4/5 | Separate engineering and runtime harness contracts |
| Environment | 4/5 | Python 3.12, locked `uv`, doctor, Docker and PostgreSQL |
| State and scope | 4/5 | Ignored local plans, runtime task state, durable debt tracker |
| Static validation | 4/5 | Ruff, strict mypy and architecture checks pass |
| Behavioral tests | 4/5 | Unit, structural, integration, and E2E gates |
| System tests | 4/5 | PostgreSQL restart, Worker crash/resume, sandbox, worktree, API and Team E2E tests |
| Observability | 4/5 | OTel, structured events, PostgreSQL-polled SSE and query APIs |
| Security | 4/5 | Deny-all allowed roots, UUID-only API intake, clean-base named worktrees |

Scores increase only with executable evidence.
