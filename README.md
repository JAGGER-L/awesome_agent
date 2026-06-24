# awesome_agent

`awesome_agent` is a local-first Python coding agent designed around an
observable Agent Team:

- one Leader exists at startup
- the Leader creates Teammates only for complex work
- Teammates may create isolated, bounded Subagents
- Team mode always includes an independent Verifier
- conversations, tools, tasks, artifacts, and verification remain traceable

## Current Status

The initial framework is locally runnable. It includes orchestration primitives,
PostgreSQL checkpoints, sandbox backends, Team/Subagent/Verifier lifecycle,
memory adapters, traceable events, artifacts, CLI, and FastAPI inspection APIs.

The completed implementation plan is:

[Initial Python Agent Team Runtime](docs/exec-plans/completed/initial-python-agent-team.md)

## Planned Stack

- Python 3.12
- `uv`
- LangGraph
- OpenAI through a project-owned provider interface
- PostgreSQL and LangGraph PostgreSQL checkpointing
- Typer CLI and local FastAPI API
- Docker sandbox with explicit trusted-local opt-in
- OpenTelemetry without LangSmith
- optional built-in memory and optional Mem0 Platform integration

## Documentation

- [Agent instructions](AGENTS.md)
- [Architecture](ARCHITECTURE.md)
- [Design documents](docs/design-docs/index.md)
- [Execution plans](docs/PLANS.md)
- [Product specification](docs/product-specs/local-coding-agent.md)
- [Quality](docs/QUALITY_SCORE.md)
- [Reliability](docs/RELIABILITY.md)
- [Security](docs/SECURITY.md)

## Development

```powershell
.\scripts\bootstrap.ps1
.\scripts\check.ps1
.\scripts\system-test.ps1
.\.venv\Scripts\awesome-agent.exe doctor
.\.venv\Scripts\awesome-agent.exe serve
```

The bootstrap command uses Python 3.12 through `uv`, synchronizes the locked
environment, and checks Python, Git, and Docker health.

V1 is a framework foundation. Real OpenAI execution requires
`OPENAI_API_KEY`/`OPENAI_MODEL`, and the local API read model is currently
process-local while LangGraph checkpoint state is durable in PostgreSQL.
