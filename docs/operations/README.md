# Operations Guide

This guide indexes local runtime operation and diagnosis.

- Startup: `make check`, `make install`, `make setup-sandbox`, `make dev`,
  `make docker-init`, and `make docker-start`.
- Split process mode: `awesome-agent serve` for API and `awesome-agent worker`
  for background execution.
- Readiness: `/health`, `/ready?profile=api`, `/ready?profile=runtime`, and
  `doctor`.
- Workspaces: `workspace list` and dry-run-first `workspace cleanup`.
- Diagnostics: Run diagnostics, recovery metrics, extension diagnostics, and
  budget/context projections.
- Security: local-only API bind by default, AIO Docker target sandbox for API
  Runs, LocalSandbox only for trusted local CLI/TUI, explicit approvals, and no
  committed secrets.

Use the [quickstart](../getting-started/quickstart.md) for the first local
startup path.

The Makefile commands are the primary startup contract. The existing
PowerShell scripts remain Windows fallback entrypoints. Docker API mode uses
`make docker-init` and `make docker-start`; local API development uses
`make check`, `make install`, `make setup-sandbox`, and `make dev`; local
interactive CLI uses `awesome` after Task 60.

See [runtime profiles and startup](../design-docs/runtime-profiles-and-startup.md)
for the durable startup, sandbox, and workspace contract.

## Local Run Modes

| Mode | Command | Use |
| --- | --- | --- |
| Local API development | `make check`, `make install`, `make setup-sandbox`, `make dev` | Host API + Worker development stack. |
| Docker API | `make docker-init`, `make docker-start` | Containerized API + Worker stack. |
| Quick Start fallback | `.\scripts\quickstart.ps1` | Windows first-run setup and verification. |
| Supervised local runtime fallback | `awesome-agent start` | API + Worker in one local command. |
| Split runtime | `awesome-agent serve` and `awesome-agent worker` | Process-manager or debugging setups. |
| PostgreSQL dependency | `docker compose up -d postgres` | Local durable storage. |
| Docker runtime fallback | `docker compose up -d --build postgres api worker` | Containerized API + Worker without sandbox service wiring. |

## Ports And Runtime Data

| Resource | Default | Purpose |
| --- | --- | --- |
| API port | `127.0.0.1:8000` local, `0.0.0.0:8000` inside Docker | Local inspection API. |
| PostgreSQL port | `54329` host, `5432` container | Durable runtime state. |
| Runtime data | `~/.awesome-agent/runs/` local, `/var/lib/awesome-agent/runs/` Docker | Per-run artifacts and runtime evidence. |
| Thread workspace | `~/.awesome-agent/threads/<thread_id>/workspace/` local, `/mnt/user-data/workspace/` in AIO Docker | Model-visible generated files and per-thread `.venv`. |
| Compose volume | `awesome_agent_runtime` | Container runtime state. |

## Readiness And Logs

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod "http://127.0.0.1:8000/ready?profile=api"
docker compose logs api
docker compose logs worker
docker compose down
```

The Docker API service binds to `0.0.0.0` inside the container so the host can
reach `http://127.0.0.1:8000`. Keep it local unless an external authentication
and network boundary is added.

API-created Runs default to the `aio-docker` sandbox provider. Until the AIO
HTTP sandbox service lands in Task 62, that provider fails clearly instead of
falling back to host execution or a one-shot Docker container. LocalSandbox is
reserved for the local CLI/TUI profile or explicit trusted local operation.

## TUI

`awesome` is the default chat-first local CLI/TUI. It can launch before the API
is running, and slash commands guide thread, status, model, and memory
inspection:

```powershell
awesome
awesome commands
```

Use `awesome-agent` subcommands for direct operations, diagnostics, scripting,
and API-backed approval workflows. The TUI uses API endpoints rather than
direct database access.
