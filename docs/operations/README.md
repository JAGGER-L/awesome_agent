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
