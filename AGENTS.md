# AGENTS.md

Instructions for coding agents working in this repository.

## Project Intent

This project explores a coding agent built around an agent team workflow: planning, implementation, review, verification, and progress tracking.

## Working Rules

- Keep changes small, explicit, and easy to review.
- Prefer project-local conventions over introducing new tools.
- Update `PROGRESS.md` when meaningful work is started, completed, blocked, or deferred.
- Update `DECISIONS.md` when a technical choice affects architecture, dependencies, data model, agent roles, or long-term maintenance.
- Do not commit secrets, API keys, credentials, local cache files, or generated build output.

## Expected Checks

This project does not have a runtime or test suite yet. When those are added, document the commands here.

```powershell
# Example placeholders
# python -m pytest
# npm test
```

## Repository Map

- `README.md`: human-facing project entry point.
- `AGENTS.md`: agent-facing operating instructions.
- `PROGRESS.md`: current status and work log.
- `DECISIONS.md`: lightweight decision log.
- `docs/ARCHITECTURE.md`: architecture notes for the agent team design.

