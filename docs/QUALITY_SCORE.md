# Quality Score

Current phase: durable solo read-only, solo modifying, and explicit scoped
team Coding loops execute locally with exact-invocation approvals, active
cancellation for solo paths, validation-gated modifying completion, coherent
lifecycle projections, real team verification/rework evidence, and queryable
runtime observability. Dependency-aware readiness is implemented; distributed
multi-Worker team execution and context/budget hardening remain roadmap work.

| Area | Score | Evidence |
| --- | ---: | --- |
| Instructions | 4/5 | Separate engineering and runtime harness contracts |
| Environment | 4/5 | Python 3.12, locked `uv`, structured doctor/readiness, Docker and PostgreSQL |
| State and scope | 4/5 | Ignored local plans, runtime task state, durable debt tracker |
| Static validation | 4/5 | Ruff, strict mypy and architecture checks pass |
| Behavioral tests | 4/5 | Unit, structural, integration, and E2E gates |
| System tests | 4/5 | PostgreSQL restart, Worker crash/resume, sandbox, worktree, durable approval, active cancellation, validation evidence, lifecycle projection, API tests, and real scoped team-runtime E2E through Worker, checkpoint, provider, tools, verifier, and observability records |
| Observability | 3/5 | Runtime events carry trace IDs; PostgreSQL query tables and APIs expose run/model/tool/sandbox spans, metrics, model calls, token usage, and latency; readiness exposes dependency state; dashboards and cost budgets remain roadmap work |
| Security | 4/5 | Deny-all allowed roots, UUID-only API intake, clean-base named worktrees, centralized shell approval policy |

Scores increase only with executable evidence.
