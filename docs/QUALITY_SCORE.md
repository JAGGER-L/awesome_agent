# Quality Score

Current phase: durable solo read-only and modifying Coding loops execute
locally with exact-invocation approvals, active cancellation, validation-gated
modifying completion, coherent lifecycle projections, and queryable solo
runtime observability. Team/subagent runtime and operational health hardening
remain roadmap work.

| Area | Score | Evidence |
| --- | ---: | --- |
| Instructions | 4/5 | Separate engineering and runtime harness contracts |
| Environment | 4/5 | Python 3.12, locked `uv`, doctor, Docker and PostgreSQL |
| State and scope | 4/5 | Ignored local plans, runtime task state, durable debt tracker |
| Static validation | 4/5 | Ruff, strict mypy and architecture checks pass |
| Behavioral tests | 4/5 | Unit, structural, integration, and E2E gates |
| System tests | 3/5 | PostgreSQL restart, Worker crash/resume, sandbox, worktree, durable approval, active cancellation, validation evidence, lifecycle projection, and API tests; Team E2E still uses manually constructed state |
| Observability | 3/5 | Runtime events carry trace IDs; PostgreSQL query tables and APIs expose run/model/tool/sandbox spans, metrics, model calls, token usage, and latency; dashboards, cost budgets, and dependency-aware health remain roadmap work |
| Security | 4/5 | Deny-all allowed roots, UUID-only API intake, clean-base named worktrees, centralized shell approval policy |

Scores increase only with executable evidence.
