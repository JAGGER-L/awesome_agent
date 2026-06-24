# Technical Debt Tracker

Record deferred work that is outside the active milestone.

| ID | Status | Area | Description | Evidence | Priority | Exit condition |
| --- | --- | --- | --- | --- | --- | --- |
| TD-001 | Resolved | Environment | Install Python 3.12 and `uv` | System checks 2026-06-25 | High | `uv run python --version` reports 3.12 |
| TD-002 | Resolved | Environment | Start Docker daemon | System tests 2026-06-25 | High | Docker client can reach server |
| TD-003 | Open | Tests | FastAPI TestClient emits an upstream httpx deprecation warning | Test suite 2026-06-24 | Low | FastAPI/Starlette test client no longer emits the warning |
| TD-004 | Resolved | Persistence | Local API projections were process-local while durable graph state used PostgreSQL checkpoints | `tests/integration/test_runtime_restart.py`, system tests 2026-06-25 | Medium | API run/agent/task projections reload from PostgreSQL after process restart |
