# Quickstart

[English](quickstart.md) | [简体中文](quickstart.zh-CN.md)

This guide shows how to configure, start, verify, and run `awesome_agent`
through local CLI, local API, and Docker API/Web lanes.

The current "Web" surface is the local FastAPI inspection surface and generated
API docs. It is not yet a hosted multi-user web application.

The Makefile commands are the primary startup contract. Docker API mode uses
`make docker-init` and `make docker-start`; local API development uses
`make check`, `make install`, `make setup-sandbox`, and `make dev`; local
interactive CLI uses `awesome`. The existing PowerShell scripts
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

## Source Checkout

Start from the repository source:

```powershell
git clone https://github.com/JAGGER-L/awesome_agent.git
cd awesome_agent
make check
make install
```

`make check` validates host prerequisites. `make install` syncs the Python
environment and installs the local `awesome` and `awesome-agent` commands.
All startup lanes below assume this source checkout and installed environment.

## Configuration

| File | Purpose |
| --- | --- |
| `<AWESOME_HOME>/.env` | User-level Awesome secrets and runtime settings loaded by `Settings`; do not commit real values. |
| `awesome-agent.yaml` | Project extension sources such as skills. Do not store secrets here. |
| `skills/` | Project skill packages containing `SKILL.md`. |
| `<AWESOME_HOME>/awesome-agent.yaml` | User-level extension sources, including MCP sources. |
| `<AWESOME_HOME>/skills/` | User-level skill packages containing `SKILL.md`. |
| `<AWESOME_HOME>/config.toml` | Local allowed-root state managed by `awesome-agent config root add/list/remove`. |
| `<AWESOME_HOME>/threads/<thread_id>/workspace/` | Durable model-visible workspace for a Thread/Conversation. AIO Docker sees this as `/mnt/user-data/workspace/`. |
| `<AWESOME_HOME>/runs/<run_id>/artifacts/` | Default local artifact storage. `AWESOME_AGENT_ARTIFACT_ROOT` overrides the runs root, not the per-run suffix. |

`AWESOME_HOME` defaults to `%LOCALAPPDATA%\awesome-agent` on Windows and
`~/.awesome-agent` on other platforms. Set `AWESOME_HOME` to override it.

Create the user-level Awesome home and env file:

```powershell
awesome init
```

Awesome Agent currently supports the official DeepSeek provider for product
conversation turns. Configure `AWESOME_AGENT_DEEPSEEK_API_KEY` in your local
Awesome env file or shell environment. Project `.env` files are not used for
Awesome provider credentials. Custom DeepSeek-compatible base URLs are not
supported by the product runtime. The default role models are
`deepseek-v4-pro` for Leader and `deepseek-v4-flash` for Teammate, Verifier,
and Subagent.

## Quickstart Matrix

| Mode | Best for | Command | Success signal |
| --- | --- | --- | --- |
| Local CLI | Interactive local coding-agent entrypoint | `awesome`, `awesome commands` | Slash commands print without a running API. |
| Local API | API + Worker inspection from host Python | `make check`, `make install`, `make setup-sandbox`, `make dev` | `/health` and `/ready?profile=api` return healthy JSON. |
| Docker API/Web | Browser/API inspection against containerized API | `make docker-init`, `make docker-start` | `http://127.0.0.1:8000/docs` opens the FastAPI docs. |
| Local CLI fallback | First local run and development | `.\scripts\quickstart.ps1` | Probe Run completes and diagnostics are printable. |

## Local API

Run the Makefile-first local API path:

```powershell
make check
make install
make setup-sandbox
make dev
```

`make setup-sandbox` builds the AIO Docker sandbox service image
`awesome-agent-sandbox:aio`.
`make dev` starts PostgreSQL, runs migrations, starts API + Worker, and prints
the local API and docs URLs. It does not start the CLI/TUI.

## Local CLI

For a first local CLI launch:

```powershell
awesome init
awesome doctor
cd E:\my-project
awesome
```

`awesome init` creates `<AWESOME_HOME>/config.yaml`, `<AWESOME_HOME>/.env`,
`<AWESOME_HOME>/awesome-agent.yaml`, and the runtime `skills`, `state`, `runs`,
and `logs` directories without overwriting existing secrets. Set
`AWESOME_AGENT_DEEPSEEK_API_KEY` in your shell, operating-system environment,
password manager, or `<AWESOME_HOME>/.env` before model-backed use.

`awesome doctor` checks only the local CLI first-run path: user config,
the effective `AWESOME_AGENT_DEEPSEEK_API_KEY` from Settings, official DeepSeek
base URL, current project config presence, and Awesome user env presence. It
does not check API server, Docker, PostgreSQL, Worker, or sandbox health. Use
`awesome-agent doctor --profile api` or
`awesome-agent doctor --profile runtime` for developer/operator diagnostics.

Open the local interactive entrypoint:

```powershell
cd E:\my-project
awesome
awesome commands
```

Run `awesome` from the project directory you want the agent to work on. The
launch directory becomes the default thread context. If it is a Git checkout,
Runs inherit that repository. If it is not a Git checkout, the CLI uses
workspace-only mode and still accepts user message turns.

`awesome` does not require an API before launch. It defaults to the local CLI
profile and LocalSandbox, then opens the chat-first local CLI/TUI. This is a
trusted-local convenience mode; API profiles use AIO Docker by default. Use
`awesome-agent` subcommands for direct operations, diagnostics, and scripting.

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

The script installs local dependencies, ensures the Awesome user env exists, starts
PostgreSQL, runs migrations, starts API + Worker, creates an ignored sample
repository, verifies a diagnostic probe, and prints the first read-only run
inspection steps. It does not require a model key unless you pass
`-RunReadOnly`.

## Manual Local API Fallback

Start local dependencies and the supervised runtime manually:

```powershell
.\scripts\bootstrap.ps1
awesome init
docker compose up -d postgres
.\scripts\migrate.ps1
.\.venv\Scripts\awesome-agent.exe doctor --profile api
.\.venv\Scripts\awesome-agent.exe start
```

`awesome-agent start` is a fallback/debug supervisor for API + Worker in one
local process group. Prefer `make dev` for normal local API development.

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

Docker mode does not start the CLI. Use `awesome` locally for CLI/TUI. Docker
Compose starts PostgreSQL, the AIO sandbox service, API, and Worker. Open
`http://127.0.0.1:8000/docs` after startup.

## Docker API Compatibility Script

Run the containerized API + Worker lane:

```powershell
.\scripts\docker-quickstart.ps1
```

Preview the Docker steps:

```powershell
.\scripts\docker-quickstart.ps1 -PlanOnly
```

The script ensures the Awesome user env exists, runs
`docker compose up -d --build postgres sandbox api worker`, waits for API
readiness, and prints CLI next steps that target the containerized API with
`--api-url`. This is a developer/operator compatibility path, not the main
local CLI product first-run path.

## Manual Docker API Fallback

Start the Docker services directly:

```powershell
docker compose up -d --build postgres sandbox api worker
```

Inspect the API:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod "http://127.0.0.1:8000/ready?profile=api"
```

Open `http://127.0.0.1:8000/docs` for generated FastAPI documentation.

Docker runtime data lives in the `awesome_agent_runtime` volume. Per-run
artifacts are stored under `/var/lib/awesome-agent/runs/<run_id>/artifacts/`
inside the container. Model-visible workspace files live in the
`awesome_agent_user_data` volume and are mounted as `/mnt/user-data/workspace/`.

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

## First Model-Backed User Message

Set `AWESOME_AGENT_DEEPSEEK_API_KEY` in the OS environment or
`<AWESOME_HOME>/.env`, restart the local interactive runtime, open `awesome`
from the project directory, then send a plain user message:

```text
Build a single-file HTML timer in this folder.
```

The message creates an internal conversation Run with a Leader Agent and
executes through the embedded local runtime path.

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
