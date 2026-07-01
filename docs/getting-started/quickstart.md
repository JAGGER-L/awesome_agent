# Quickstart

This is the manual local golden path. Task 54 will add an automation script for
the same path.

## Prerequisites

- Python 3.12
- `uv`
- Docker Desktop or a compatible Docker engine
- Git
- Windows PowerShell for the current scripts

## Start The Local Runtime

```powershell
.\scripts\bootstrap.ps1
Copy-Item .env.example .env
docker compose up -d postgres
.\scripts\migrate.ps1
.\.venv\Scripts\awesome-agent.exe doctor --profile api
.\.venv\Scripts\awesome-agent.exe start
```

`awesome-agent start` supervises the local API and Worker together. Use
`awesome-agent serve` and `awesome-agent worker` separately when a process
manager or debugger should own each process.

## Register A Repository

Authorize a parent directory, then register a clean Git checkout under it:

```powershell
.\.venv\Scripts\awesome-agent.exe config root add <parent-directory>
.\.venv\Scripts\awesome-agent.exe repo add <repository-path>
```

## Verify The Runtime Without A Model Key

A diagnostic probe verifies the Worker, lease, checkpoint, repository, and
event path without executing a coding goal:

```powershell
.\.venv\Scripts\awesome-agent.exe probe --repo <repository-path>
.\.venv\Scripts\awesome-agent.exe diagnostics <run-id>
```

## Run A Read-Only Coding Task

Set `AWESOME_AGENT_DEEPSEEK_API_KEY` in `.env`, restart the runtime, then run:

```powershell
.\.venv\Scripts\awesome-agent.exe run "Inspect this repository" --repo <repository-path> --read-only
```

Use `--team` only when you want the distributed Leader, Teammate, and Verifier
runtime.
