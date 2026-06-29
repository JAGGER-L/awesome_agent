# Quality Score

Current phase: durable solo read-only, solo modifying, explicit scoped team
Coding, and model-driven distributed team child-run loops execute locally.
Solo paths have exact-invocation approvals, active cancellation,
validation-gated modifying completion, coherent lifecycle projections, and
queryable runtime observability. Distributed team child Runs now have durable
lineage, model-generated Leader TeamPlans, assignment-scoped Teammate
model/tool loops, mailbox, recursive cancellation, inspection APIs/CLI,
root-aware budgets, deferred tool exposure, team payload compaction, and
PostgreSQL E2E evidence. Teammate-owned dynamic Subagent creation is wired into
the distributed path, Verifier child Runs persist structured model decisions,
targeted rework creates replacement Teammate child Runs with immutable attempt
lineage, and Task 22F covers patch-producing happy-path plus verifier-rework
distributed E2E.

| Area | Score | Evidence |
| --- | ---: | --- |
| Instructions | 4/5 | Separate engineering and runtime harness contracts |
| Environment | 4/5 | Python 3.12, locked `uv`, structured doctor/readiness, Docker and PostgreSQL |
| State and scope | 4/5 | Ignored local plans, runtime task state, durable debt tracker |
| Static validation | 4/5 | Ruff, strict mypy and architecture checks pass |
| Behavioral tests | 4/5 | Unit, structural, integration, and E2E gates |
| System tests | 4/5 | PostgreSQL restart, Worker crash/resume, sandbox, worktree, durable approval, active cancellation, validation evidence, lifecycle projection, API tests, real scoped team-runtime E2E, and distributed team child-run PostgreSQL E2E |
| Observability | 3/5 | Runtime events carry trace IDs; PostgreSQL query tables and APIs expose run/model/tool/sandbox spans, metrics, model calls, token usage, and latency; readiness exposes dependency state; dashboards and cost budgets remain roadmap work |
| Security | 4/5 | Deny-all allowed roots, UUID-only API intake, clean-base named worktrees, centralized shell approval policy |

Scores increase only with executable evidence.
