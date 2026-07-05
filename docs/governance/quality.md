# Quality

Quality increases only with executable evidence.

| Area | Current posture | Evidence |
| --- | --- | --- |
| Instructions | Repository and runtime instructions are separated. | `AGENTS.md`, `docs/development/`, `docs/architecture/` |
| Environment | Python 3.12, locked `uv`, doctor/readiness checks, Docker and PostgreSQL paths are documented. | Makefile/script tests and operations docs |
| State and scope | Local execution plans are ignored; durable direction lives in governance docs. | `.codex/exec-plans/`, `docs/governance/` |
| Static validation | Ruff, mypy, and structural tests are available. | Repository test suite |
| Behavioral tests | Unit, structural, integration, and E2E gates cover runtime paths. | `tests/` |
| Observability | Durable query tables, model-call records, runtime events, and OTel export paths exist. | `docs/architecture/observability.md`, diagnostics APIs |
| Security | Trusted local mode is documented as local trust, not a security boundary. | `docs/architecture/security-model.md`, operations docs |

Do not raise quality claims unless verification commands or durable operational
evidence support the change.
