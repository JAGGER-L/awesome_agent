# Quickstart

This guide shows how to configure, start, verify, and run `awesome_agent`
through local CLI, local API, Docker CLI, and Docker API/Web lanes.

The current "Web" surface is the local FastAPI inspection surface and generated
API docs. It is not yet a hosted multi-user web application.

The current repository still supports the PowerShell quickstart scripts. The
target startup model is being migrated to Makefile commands: Docker API uses
`make docker-init` and `make docker-start`; local API development uses
`make check`, `make install`, `make setup-sandbox`, and `make dev`; local
interactive CLI uses `awesome`.

The durable profile and storage contract is defined in
[runtime profiles and startup](../design-docs/runtime-profiles-and-startup.md).

## Prerequisites

- Python 3.12
- `uv`
- Docker Desktop or a compatible Docker engine
- Git
- Windows PowerShell for the helper scripts

## Configuration

| File | Purpose |
| --- | --- |
| `.env` | Local secrets and runtime settings loaded by `Settings`. Copy from `.env.example`; do not commit real values. |
| `awesome-agent.yaml` | Project extension sources such as skills and MCP. Do not store secrets here. |
| `skills/` | Project skill packages containing `SKILL.md`. |
| `~/.awesome-agent/config.toml` | Local allowed-root state managed by `awesome-agent config root add/list/remove`. |
| `~/.awesome-agent/threads/<thread_id>/workspace/` | Durable model-visible workspace for a Thread/Conversation. AIO Docker sees this as `/mnt/user-data/workspace/`. |
| `~/.awesome-agent/runs/<run_id>/artifacts/` | Default local artifact storage. `AWESOME_AGENT_ARTIFACT_ROOT` overrides the runs root, not the per-run suffix. |

Create local configuration:

```powershell
Copy-Item .env.example .env
```

Model provider settings currently use `AWESOME_AGENT_DEEPSEEK_*` values in
`.env`. The default role models are `deepseek-v4-pro` for Leader and
`deepseek-v4-flash` for Teammate, Verifier, and Subagent.

## Quickstart Matrix

| Mode | Best for | Command | Success signal |
| --- | --- | --- | --- |
| Local CLI | First local run and development | `.\scripts\quickstart.ps1` | Probe Run completes and diagnostics are printable. |
| Local API | API + Worker inspection from host Python | `.\.venv\Scripts\awesome-agent.exe start` | `/health` and `/ready?profile=api` return healthy JSON. |
| Docker CLI | Containerized runtime with CLI-driven inspection | `.\scripts\docker-quickstart.ps1` | Docker API becomes ready and CLI can point at `--api-url`. |
| Docker API/Web | Browser/API inspection against containerized API | `docker compose up -d --build postgres api worker` | `http://127.0.0.1:8000/docs` opens the FastAPI docs. |

## Local CLI

Run the automated local path:

```powershell
.\scripts\quickstart.ps1
```

Preview the steps without side effects:

```powershell
.\scripts\quickstart.ps1 -PlanOnly
```

Keep the runtime running after the script exits:

```powershell
.\scripts\quickstart.ps1 -KeepRuntime
```

Use an already running API + Worker:

```powershell
.\scripts\quickstart.ps1 -UseExistingRuntime
```

The script installs local dependencies, ensures `.env` exists, starts
PostgreSQL, runs migrations, starts API + Worker, creates an ignored sample
repository, verifies a diagnostic probe, and prints the first read-only run
command. It does not require a model key unless you pass `-RunReadOnly`.

## Local API

Start local dependencies and the supervised runtime manually:

```powershell
.\scripts\bootstrap.ps1
Copy-Item .env.example .env
docker compose up -d postgres
.\scripts\migrate.ps1
.\.venv\Scripts\awesome-agent.exe doctor --profile api
.\.venv\Scripts\awesome-agent.exe start
```

The API address is `http://127.0.0.1:8000`.

Check readiness:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod "http://127.0.0.1:8000/ready?profile=api"
```

## Docker CLI

Run the containerized API + Worker lane:

```powershell
.\scripts\docker-quickstart.ps1
```

Preview the Docker steps:

```powershell
.\scripts\docker-quickstart.ps1 -PlanOnly
```

The script ensures `.env` exists, runs
`docker compose up -d --build postgres api worker`, waits for API readiness,
and prints CLI next steps that target the containerized API with `--api-url`.

## Docker API/Web

Start the Docker services directly:

```powershell
docker compose up -d --build postgres api worker
```

Inspect the API:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod "http://127.0.0.1:8000/ready?profile=api"
```

Open `http://127.0.0.1:8000/docs` for generated FastAPI documentation.

Docker runtime data lives in the `awesome_agent_runtime` volume. Per-run
artifacts are stored under `/var/lib/awesome-agent/runs/<run_id>/artifacts/`
inside the container.

## Verify Without A Model Key

Authorize a parent directory and register a clean Git checkout:

```powershell
.\.venv\Scripts\awesome-agent.exe config root add <parent-directory>
.\.venv\Scripts\awesome-agent.exe repo add <repository-path>
```

Verify the durable runtime without a model key:

```powershell
.\.venv\Scripts\awesome-agent.exe probe --repo <repository-path>
.\.venv\Scripts\awesome-agent.exe diagnostics <run-id>
```

For Docker API mode, add `--api-url http://127.0.0.1:8000` to the CLI commands.

`/health` is process liveness. `/ready?profile=api` checks API dependencies.
`/ready?profile=runtime` also checks runtime dependencies such as provider
configuration and Worker heartbeat.

## First Model-Backed Read-Only Run

Set `AWESOME_AGENT_DEEPSEEK_API_KEY` in `.env`, restart the runtime, then run:

```powershell
.\.venv\Scripts\awesome-agent.exe run "Inspect this repository" --repo <repository-path> --read-only
```

Use `--team` only when you want the distributed Leader, Teammate, and Verifier
runtime.

## Shutdown And Cleanup

Stop local supervised runtime with `Ctrl+C`.

Stop Docker services:

```powershell
docker compose down
```

Inspect or clean managed workspaces:

```powershell
.\.venv\Scripts\awesome-agent.exe workspace list
.\.venv\Scripts\awesome-agent.exe workspace cleanup --run-id <run-id>
```

## Troubleshooting

- If `/health` fails, the API process is not reachable.
- If `/ready?profile=api` fails, inspect PostgreSQL, migrations, or settings.
- If Docker API logs are needed, run `docker compose logs api`.
- If Docker Worker logs are needed, run `docker compose logs worker`.
- If a Run is stuck, run `awesome-agent diagnostics <run-id>`.

## Local Resource Guidance

For external API models, start with 4 vCPU, 8 GB memory, and 20 GB free disk for
a single local development session. Use more memory and disk for multiple
concurrent Runs, team mode, Docker image builds, or large repository workspaces.
