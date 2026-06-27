# Technical Debt Tracker

Record durable project debt that remains after a development task ends.
Task-specific notes and handoffs belong in ignored `.codex/exec-plans/`.

| ID | Status | Area | Description | Evidence | Priority | Exit condition |
| --- | --- | --- | --- | --- | --- | --- |
| TD-001 | Resolved | Environment | Install Python 3.12 and `uv` | System checks 2026-06-25 | High | `uv run python --version` reports 3.12 |
| TD-002 | Resolved | Environment | Start Docker daemon | System tests 2026-06-25 | High | Docker client can reach server |
| TD-003 | Open | Tests | FastAPI TestClient emits an upstream httpx deprecation warning | Test suite 2026-06-24 | Low | FastAPI/Starlette test client no longer emits the warning |
| TD-004 | Resolved | Persistence | Local API projections were process-local while durable graph state used PostgreSQL checkpoints | `tests/integration/test_runtime_restart.py`, system tests 2026-06-25 | Medium | API run/agent/task projections reload from PostgreSQL after process restart |
| TD-005 | Resolved | Runtime routing | Modifying Coding Runs could be accepted without an executable graph route | Task 07 modifying graph tests 2026-06-26 | High | `coding + modifying` Runs route to `solo-modifying@1`, and Workers advertise that graph when model providers are configured |
| TD-006 | Resolved | Validation | Standard local checks depended on PostgreSQL environment outside `scripts/check.ps1` | Task 07 preflight 2026-06-26 | High | `scripts/check.ps1` supplies the project PostgreSQL defaults and prints actionable migration guidance |
| TD-007 | Resolved | Tool safety | Read-only graph tool calls bypassed the central execution boundary | Task 07 central executor tests 2026-06-26 | High | Read-only graph tools execute through `ToolExecutor`, including capability, approval-policy, and timeout enforcement |
| TD-008 | Resolved | Approval | Approval API was a placeholder and could not durably approve, deny, expire, or resume one exact invocation | Task 08 tests 2026-06-26 | High | Durable approval requests, decisions, expiry, worker release, and graph resume are covered by unit tests |
| TD-009 | Resolved | Cancellation | Active Runs could not be cancelled through model, tool, Docker, or subprocess boundaries | Task 09 tests 2026-06-26 | High | Solo running and waiting Runs cancel without corrupting checkpoints, projections, or worktrees |
| TD-010 | Resolved | Validation | Deterministic validation and rework did not exist for modifying output | Task 10 tests 2026-06-26 | High | Solo modifying Runs require durable validation gate evidence and use bounded rework before terminal failure |
| TD-011 | Open | Context | Tool output, messages, fingerprints, and checkpoints can grow without a full budget system | Task 07 triage 2026-06-26 | High | Artifact-backed prompt, checkpoint, token, wall-clock, and cost budgets are enforced |
| TD-012 | Resolved | Lifecycle | Run, Agent, Todo, event, revision, and timestamp projections were not consistently maintained | Task 11 lifecycle projection tests 2026-06-27 | Medium | Solo runtime lifecycle transitions have matching events, Agent/Todo revisions, and `updated_at` maintenance |
| TD-013 | Open | Observability | Current observability claims exceed implemented spans, metrics, costs, latency, and query tables | Task 07 triage 2026-06-26 | Medium | Run/model/tool/sandbox spans, metrics, costs, latencies, retry/recovery indicators, and query tables are covered by tests |
| TD-014 | Open | Team runtime | Team E2E tests construct state manually instead of exercising Worker, model, tools, database, checkpoint, and patch integration | Task 07 triage 2026-06-26 | Medium | Team E2E uses the real runtime path through verification and durable evidence |
| TD-015 | Open | Workspace | Accepted Run worktrees and branches accumulate without retention or cleanup | Task 07 triage 2026-06-26 | Medium | Owned inactive worktrees and branches can be listed, retained, or safely removed |
| TD-016 | Open | Operations | `/health` and `doctor` do not verify all critical runtime dependencies | Task 07 triage 2026-06-26 | Medium | Health checks cover PostgreSQL, migrations, checkpoint store, provider keys, worker heartbeat, workspace root, and model routes |
| TD-017 | Resolved | Security | Project CLI could bind FastAPI non-loopback without authentication or an explicit unsafe gate | Task 07 preflight 2026-06-26 | High | `serve` and `start` reject non-loopback hosts unless `--unsafe-bind-public` is explicitly set |
| TD-018 | Resolved | Documentation | Capability documentation overstated Coding Run execution, traceability, and implemented runtime records | Task 07 documentation sync 2026-06-26 | High | README, architecture, reliability, product spec, and quality docs match implemented solo read-only and modifying behavior |
| TD-019 | Open | Security | Direct ASGI hosting can bypass the project CLI non-loopback unsafe-bind guard | Task 07 completion audit 2026-06-26 | Medium | Local API bind policy is enforced or clearly authenticated outside the CLI entrypoint |
