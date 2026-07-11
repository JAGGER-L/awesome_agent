# Awesome Agent

[English](README.md) | [简体中文](README.zh-CN.md)

```text
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃  ███  █   █ █████ █████  ███  █   █ █████        ┃
┃ █   █ █   █ █     █     █   █ ██ ██ █            ┃
┃ █████ █ █ █ ████  █████ █   █ █ █ █ ████         ┃
┃ █   █ ██ ██ █         █ █   █ █   █ █            ┃
┃ █   █ █   █ █████ █████  ███  █   █ █████        ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
```

Awesome Agent is a local-project AI coding agent. It is a lightweight local
development assistant that runs on your own machine. It can read repository
context, edit files, run commands, and help with debugging, refactoring, and
feature work. Unlike traditional code completion tools, Awesome works at the
task level: you describe a goal, and it reasons across the current repository,
edits, verifies, and prepares the work for review. The only product entry is
the local Ink `awesome` interface backed by a private Python Core process.



## Product Surface

| Mode | Use it when | Start command |
| --- | --- | --- |
| Local CLI | Work inside a local project from the terminal. It requires no API server, PostgreSQL, Worker, or Docker service. | `cd <your-project>` then `awesome` |

The public `awesome` command always
starts the local Ink interface and its private Python Core process.

Run `awesome` from the project directory. The launch directory becomes the
default thread context. If it is a Git checkout, runs inherit that repository;
otherwise Awesome uses workspace-only mode and still accepts user message
turns. Plain user messages are the only product execution creation path.

## Quick Start

The V1 one-command installer is delivered in the next Phase 4 PR. Until then,
use the contributor source preview:

```powershell
uv sync --extra memory --dev
npm --prefix tui ci
npm --prefix tui run build
$env:PATH = "$(Resolve-Path .venv\Scripts);$env:PATH"
node tui/dist/cli/index.js --help
```

See the [Quickstart](docs/getting-started/quickstart.md) for the POSIX equivalent
and workspace startup path.

## Configuration Basics

Awesome keeps its own user files outside your projects.

| Path | Purpose |
| --- | --- |
| `<AWESOME_HOME>/.env` | User-level model keys and local settings. |
| `<AWESOME_HOME>/config.yaml` | User-level Provider, budget, memory, skill, and MCP settings. |
| `<AWESOME_HOME>/skills/` | Personal skills available across projects. |
| `<your-project>/skills/` | Project skills for the current repository. |
| `<your-project>/.awesome/config.yaml` | Trusted workspace budget, skill, and MCP settings. |

On Windows, `AWESOME_HOME` defaults to `%LOCALAPPDATA%\Awesome`. On other
platforms, it defaults to `~/.awesome`. You can override it with the
`AWESOME_HOME` environment variable.

Provider keys are not read from your project `.env`.

## Common Commands

Run these inside `awesome`:

| Command | Purpose |
| --- | --- |
| `/help` | Show available commands. |
| `/config` | Show the resolved Awesome paths and key status. |
| `/status` | Show the current conversation status. |
| `/skills` | List available skills. |
| `/mcp` | Show configured MCP servers. |
| `/quit` | Exit the TUI. |

## Documentation

- [Documentation map](docs/README.md)
- [Quickstart](docs/getting-started/quickstart.md)
- [快速开始](docs/getting-started/quickstart.zh-CN.md)
- [User guide](docs/user-guide/README.md)
- [Architecture](ARCHITECTURE.md)
- [Security model](docs/architecture/security-model.md)

## Safety

Run Awesome only in projects you trust. Keep API keys out of Git and store them
in your operating-system environment or `<AWESOME_HOME>/.env`.
