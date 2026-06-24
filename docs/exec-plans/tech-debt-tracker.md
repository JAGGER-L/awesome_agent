# Technical Debt Tracker

Record deferred work that is outside the active milestone.

| ID | Area | Description | Evidence | Priority | Exit condition |
| --- | --- | --- | --- | --- | --- |
| TD-001 | Environment | Install Python 3.12 and `uv` | Baseline check 2026-06-24 | High | `uv run python --version` reports 3.12 |
| TD-002 | Environment | Start Docker daemon | Baseline check 2026-06-24 | High | Docker client can reach server |
| TD-003 | Tests | FastAPI TestClient emits an upstream httpx deprecation warning | Test suite 2026-06-24 | Low | FastAPI/Starlette test client no longer emits the warning |
| TD-004 | Persistence | Local API projections are process-local while durable graph state uses PostgreSQL checkpoints | V1 architecture review | Medium | API run/agent/task projections reload from PostgreSQL after process restart |
