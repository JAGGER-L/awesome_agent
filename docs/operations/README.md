# Operations Guide

This guide indexes local runtime operation and diagnosis.

- Startup: `scripts/bootstrap.ps1`, `scripts/migrate.ps1`, and
  `awesome-agent start`.
- Split process mode: `awesome-agent serve` for API and `awesome-agent worker`
  for background execution.
- Readiness: `/health`, `/ready?profile=api`, `/ready?profile=runtime`, and
  `doctor`.
- Workspaces: `workspace list` and dry-run-first `workspace cleanup`.
- Diagnostics: Run diagnostics, recovery metrics, extension diagnostics, and
  budget/context projections.
- Security: local-only API bind by default, Docker shell sandbox, explicit
  approvals, and no committed secrets.

Use the [quickstart](../getting-started/quickstart.md) for the first local
startup path.

## Local Run Modes

| Mode | Command | Use |
| --- | --- | --- |
| Quick Start | `.\scripts\quickstart.ps1` | First local setup and verification. |
| Supervised local runtime | `awesome-agent start` | API + Worker in one local command. |
| Split runtime | `awesome-agent serve` and `awesome-agent worker` | Process-manager or debugging setups. |
| PostgreSQL dependency | `docker compose up -d postgres` | Local durable storage. |
