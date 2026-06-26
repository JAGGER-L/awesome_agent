# Quality Score

Current phase: durable solo read-only and modifying Coding loops execute
locally with exact-invocation approvals and active cancellation. Modifying
output is still unvalidated; deterministic validation, team/subagent runtime,
and full observability remain roadmap work.

| Area | Score | Evidence |
| --- | ---: | --- |
| Instructions | 4/5 | Separate engineering and runtime harness contracts |
| Environment | 4/5 | Python 3.12, locked `uv`, doctor, Docker and PostgreSQL |
| State and scope | 4/5 | Ignored local plans, runtime task state, durable debt tracker |
| Static validation | 4/5 | Ruff, strict mypy and architecture checks pass |
| Behavioral tests | 4/5 | Unit, structural, integration, and E2E gates |
| System tests | 3/5 | PostgreSQL restart, Worker crash/resume, sandbox, worktree, durable approval, active cancellation, and API tests; Team E2E still uses manually constructed state |
| Observability | 2/5 | Structured events, PostgreSQL-polled SSE, and query APIs exist; full run/model/tool/sandbox spans, metrics, cost, latency, and recovery indicators remain roadmap work |
| Security | 4/5 | Deny-all allowed roots, UUID-only API intake, clean-base named worktrees, centralized shell approval policy |

Scores increase only with executable evidence.
