# Quickstart

This guide shows the local Windows PowerShell path for configuring, starting,
verifying, and running `awesome_agent`.

## Automated Path

```powershell
.\scripts\quickstart.ps1
```

The script installs local dependencies, ensures `.env` exists, starts
PostgreSQL, runs migrations, starts API + Worker, creates an ignored sample
repository, verifies a diagnostic probe, and prints the first read-only run
command. It does not require a model key unless you pass `-RunReadOnly`.

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

## Prerequisites

- Python 3.12
- `uv`
- Docker Desktop or a compatible Docker engine
- Git
- Windows PowerShell

## Configuration Files

| File | Purpose |
| --- | --- |
| `.env` | Local secrets and runtime settings loaded by `Settings`. Copy from `.env.example`; do not commit real values. |
| `awesome-agent.yaml` | Project extension sources such as skills and MCP. Do not store secrets here. |
| `skills/` | Project skill packages containing `SKILL.md`. |
| `~/.awesome-agent/config.toml` | Local allowed-root state managed by `awesome-agent config root add/list/remove`. |
| `~/.awesome-agent/artifacts/` | Default local artifact storage unless `AWESOME_AGENT_ARTIFACT_ROOT` overrides it. |

Model provider settings currently use `AWESOME_AGENT_DEEPSEEK_*` values in
`.env`. The default role models are `deepseek-v4-pro` for Leader and
`deepseek-v4-flash` for Teammate, Verifier, and Subagent.

## Manual Path

```powershell
.\scripts\bootstrap.ps1
Copy-Item .env.example .env
docker compose up -d postgres
.\scripts\migrate.ps1
.\.venv\Scripts\awesome-agent.exe doctor --profile api
.\.venv\Scripts\awesome-agent.exe start
```

The API address is `http://127.0.0.1:8000`.

## Run Modes

- `awesome-agent start`: local supervisor for API + Worker.
- `awesome-agent serve`: API only, for external process managers or debugging.
- `awesome-agent worker`: Worker only, for external process managers or
  debugging.

## Verify Without A Model Key

```powershell
.\.venv\Scripts\awesome-agent.exe config root add <parent-directory>
.\.venv\Scripts\awesome-agent.exe repo add <repository-path>
.\.venv\Scripts\awesome-agent.exe probe --repo <repository-path>
.\.venv\Scripts\awesome-agent.exe diagnostics <run-id>
```

`/health` is process liveness. `/ready?profile=api` checks API dependencies.
`/ready?profile=runtime` also checks runtime dependencies such as provider
configuration and Worker heartbeat.

## First Read-Only Run

Set `AWESOME_AGENT_DEEPSEEK_API_KEY` in `.env`, restart the runtime, then run:

```powershell
.\.venv\Scripts\awesome-agent.exe run "Inspect this repository" --repo <repository-path> --read-only
```

Use `--team` only when you want the distributed Leader, Teammate, and Verifier
runtime.

## Local Resource Guidance

For external API models, start with 4 vCPU, 8 GB memory, and 20 GB free disk for
a single local development session. Use more memory and disk for multiple
concurrent Runs, team mode, or large repository workspaces.
