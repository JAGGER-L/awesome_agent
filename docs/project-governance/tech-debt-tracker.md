# Technical Debt Tracker

Record durable project debt that remains after a development task ends.
Task-specific notes and handoffs belong in ignored `.codex/exec-plans/`.

| ID | Status | Area | Description | Evidence | Priority | Exit condition |
| --- | --- | --- | --- | --- | --- | --- |
| TD-001 | Resolved | Environment | Install Python 3.12 and `uv` | System checks 2026-06-25 | High | `uv run python --version` reports 3.12 |
| TD-002 | Resolved | Environment | Start Docker daemon | System tests 2026-06-25 | High | Docker client can reach server |
| TD-003 | Open | Tests | FastAPI TestClient emits an upstream httpx deprecation warning | Test suite 2026-06-24 | Low | FastAPI/Starlette test client no longer emits the warning |
| TD-004 | Resolved | Persistence | Local API projections were process-local while durable graph state used PostgreSQL checkpoints | `tests/integration/test_runtime_restart.py`, system tests 2026-06-25 | Medium | API run/agent/task projections reload from PostgreSQL after process restart |
| TD-005 | Open | Runtime routing | Modifying Coding Runs can be accepted without an executable graph route | Task 07 triage 2026-06-26 | High | `coding + modifying` Runs route to a claimed modifying graph or are rejected before queue publication |
| TD-006 | Open | Validation | Standard local checks depend on PostgreSQL environment outside `scripts/check.ps1` | Task 07 triage 2026-06-26 | High | The check script documents or supplies required local test configuration and fails with actionable guidance |
| TD-007 | Open | Tool safety | Read-only graph tool calls bypass the central execution boundary | Task 07 triage 2026-06-26 | High | Graph tools execute through one policy path for specification, capability, profile, timeout, approval, sandbox, and artifact handling |
| TD-008 | Open | Approval | Approval API is a placeholder and cannot durably approve, deny, expire, or resume one exact invocation | Task 07 triage 2026-06-26 | High | Durable approval requests and decisions pass race and resume tests |
| TD-009 | Open | Cancellation | Active Runs cannot be cancelled through model, tool, Docker, or subprocess boundaries | Task 07 triage 2026-06-26 | High | Running and waiting Runs cancel without corrupting checkpoints, projections, or worktrees |
| TD-010 | Open | Validation | Deterministic validation and rework do not exist for modifying output | Task 07 triage 2026-06-26 | High | Validation gates and bounded rework produce durable pass/fail evidence |
| TD-011 | Open | Context | Tool output, messages, fingerprints, and checkpoints can grow without a full budget system | Task 07 triage 2026-06-26 | High | Artifact-backed prompt, checkpoint, token, wall-clock, and cost budgets are enforced |
| TD-012 | Open | Lifecycle | Run, Agent, Todo, event, revision, and timestamp projections are not consistently maintained | Task 07 triage 2026-06-26 | Medium | Every visible lifecycle transition has matching events, revisions, and `updated_at` maintenance |
| TD-013 | Open | Observability | Current observability claims exceed implemented spans, metrics, costs, latency, and query tables | Task 07 triage 2026-06-26 | Medium | Run/model/tool/sandbox spans, metrics, costs, latencies, retry/recovery indicators, and query tables are covered by tests |
| TD-014 | Open | Team runtime | Team E2E tests construct state manually instead of exercising Worker, model, tools, database, checkpoint, and patch integration | Task 07 triage 2026-06-26 | Medium | Team E2E uses the real runtime path through verification and durable evidence |
| TD-015 | Open | Workspace | Accepted Run worktrees and branches accumulate without retention or cleanup | Task 07 triage 2026-06-26 | Medium | Owned inactive worktrees and branches can be listed, retained, or safely removed |
| TD-016 | Open | Operations | `/health` and `doctor` do not verify all critical runtime dependencies | Task 07 triage 2026-06-26 | Medium | Health checks cover PostgreSQL, migrations, checkpoint store, provider keys, worker heartbeat, workspace root, and model routes |
| TD-017 | Open | Security | FastAPI can bind non-loopback without authentication or an explicit unsafe gate | Task 07 triage 2026-06-26 | High | Non-loopback serving requires authentication or explicit unsafe local configuration |
| TD-018 | Open | Documentation | Current capability documentation overstates Coding Run execution, traceability, and implemented runtime records | Task 07 triage 2026-06-26 | High | README, architecture, reliability, product spec, and quality docs match implemented behavior |
