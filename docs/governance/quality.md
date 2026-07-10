# Quality

Quality claims require executable evidence against the target product.

| Area | Current posture | Evidence |
| --- | --- | --- |
| Instructions | Repository rules distinguish rewrite validation from legacy compatibility and define the architecture integration branch. | `AGENTS.md` |
| State and scope | Local execution plans are ignored; durable direction lives in governance and architecture documents. | `.codex/exec-plans/`, `docs/governance/` |
| Static validation | Ruff and mypy run against the current target-owned module set. | `scripts/check.ps1` |
| Behavioral tests | A small target baseline covers model/provider contracts, local memory, path safety, process lifecycle, and local SQLite checkpoints. | `tests/README.md`, `tests/` |
| Product validation | Target CLI/TUI E2E, smoke, recovery, and performance gates are deferred until those target flows exist. | `docs/development/testing.md` |
| Security | Workspace trust is local consent; path escape and secret redaction remain covered while the target tool permission model is rebuilt. | `tests/unit/test_repository_policy.py`, `tests/unit/test_redaction.py` |

Deleted legacy test count is not a quality regression by itself. Reintroducing
removed architecture solely to recover an old coverage number would be a
quality regression because it increases product complexity without protecting
the target user experience.

Do not raise readiness or coverage claims until the relevant target gate has
run and its evidence has been recorded.
