# Quickstart

This guide shows how to configure, start, verify, and run `awesome_agent`
through local CLI, local API, Docker CLI, and Docker API/Web lanes.

The current "Web" surface is the local FastAPI inspection surface and generated
API docs. It is not yet a hosted multi-user web application.

The Makefile commands are the primary startup contract. Docker API mode uses
`make docker-init` and `make docker-start`; local API development uses
`make check`, `make install`, `make setup-sandbox`, and `make dev`; local
interactive CLI uses `awesome` after Task 60. The existing PowerShell scripts
remain Windows fallback entrypoints.

The durable profile and storage contract is defined in
[runtime profiles and startup](../design-docs/runtime-profiles-and-startup.md).

## Prerequisites

- Python 3.12
- `uv`
- GNU Make for the primary Makefile commands
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
| Local CLI | Interactive local coding-agent entrypoint | `awesome`, `awesome commands` | Slash commands print without a running API. |
| Local API | API + Worker inspection from host Python | `make check`, `make install`, `make setup-sandbox`, `make dev` | `/health` and `/ready?profile=api` return healthy JSON. |
| Docker API/Web | Browser/API inspection against containerized API | `make docker-init`, `make docker-start` | `http://127.0.0.1:8000/docs` opens the FastAPI docs. |
| Local CLI fallback | First local run and development | `.\scripts\quickstart.ps1` | Probe Run completes and diagnostics are printable. |
| Docker CLI | Containerized runtime with CLI-driven inspection | `.\scripts\docker-quickstart.ps1` | Docker API becomes ready and CLI can point at `--api-url`. |

## Local API

Run the Makefile-first local API path:

```powershell
make check
make install
make setup-sandbox
make dev
```

`make setup-sandbox` prepares the AIO Docker sandbox assets. Until Task 62 adds
`sandbox/aio/Dockerfile`, it fails clearly with a Task 62 dependency message.
`make dev` starts PostgreSQL, runs migrations, starts API + Worker, and prints
the local API and docs URLs. It does not start the CLI/TUI.

## Local CLI

Open the local interactive entrypoint:

```powershell
awesome
awesome commands
```

`awesome` does not require an API before launch. It defaults to the local CLI
profile and LocalSandbox, reports first-run setup state, and lists the stable
slash commands that Task 61 will use in the chat-first TUI.

## Local CLI Fallback

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

## Manual Local API Fallback

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

## Docker API/Web

Prepare and start the Docker API stack:

```powershell
make docker-init
make docker-start
```

Docker mode does not start the CLI. Use `awesome` locally for CLI/TUI after
Task 60. Until Task 63 wires the sandbox service into Compose,
`make docker-start` fails clearly with a Task 63 dependency message.

## Docker CLI Fallback

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

## Manual Docker API Fallback

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
