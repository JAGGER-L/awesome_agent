# Runtime Profiles And Startup

This document defines the target startup, workspace, and sandbox contract for
`awesome_agent`. It is a product-runtime contract, not a local Codex execution
plan.

## Product Principle

Startup commands should map to user intent:

- Docker API profile: run the API service stack.
- Local API development profile: develop and inspect API/Worker locally.
- Local CLI/TUI profile: enter the interactive coding-agent interface with one
  command. This profile uses embedded local runtime mode by default.

## Profile Matrix

| Profile | Primary user | Target command | Starts API? | Starts CLI/TUI? | Default sandbox |
| --- | --- | --- | --- | --- | --- |
| Docker API profile | User/operator who wants containerized services | `make docker-init`, then `make docker-start` | Yes | No | AIO Docker |
| Local API development profile | Runtime developer | `make check`, `make install`, `make setup-sandbox`, `make dev` | Yes | No | AIO Docker |
| Local CLI/TUI profile | Local coding-agent user | `awesome`, `awesome commands` | No, unless `--api-url` is passed | Yes | LocalSandbox |

## Sandbox Targets

`LocalSandbox` executes local shell commands for the local CLI/TUI profile. It
is not the default backend for API-created Runs. Current LocalSandbox command
policy is intentionally permissive for trusted local use and must be hardened
in a later security task.

`AIO Docker` is a long-lived Linux development container with a
thread-mounted workspace directory and an `agent-sandbox` HTTP service. API
profiles use AIO Docker by default. The current service foundation executes
Python commands; full Node/npm/ripgrep toolchain hardening is tracked as the
next sandbox hardening step.

## Storage Contract

Model-visible generated files use one logical path in every execution mode:

```text
/mnt/user-data/workspace/
```

On the host, that logical workspace persists at:

```text
~/.awesome-agent/threads/<thread_id>/workspace/
```

Run audit evidence remains separate:

```text
~/.awesome-agent/runs/<run_id>/artifacts/
```

Docker API mode mounts the shared `awesome_agent_user_data` volume at
`/mnt/user-data/` in API, Worker, and sandbox containers, so files written to
`/mnt/user-data/workspace/` are visible to all three services. LocalSandbox uses
a path mapper to translate the same logical path to the host thread workspace
before command execution. Repository-root `output/` and `e2e-output/` are not
formal runtime output locations.

## Command Targets

Docker API:

```bash
make docker-init
make docker-start
```

Local API development:

```bash
make check
make install
make setup-sandbox
make dev
```

Local CLI/TUI:

```bash
awesome
awesome commands
```

The local CLI/TUI profile defaults to embedded local runtime mode and
LocalSandbox. It does not require an API server before ordinary conversation or
local coding-agent work can begin. Use `awesome --api-url <url>` only when the
TUI should connect to a local, Docker, or remote API server.

Ordinary text input is the main execution entry. The runtime chooses the
appropriate execution mode for the turn: lightweight model response,
tool-capable foreground coding work, background work, or resume of an
interrupted Run. `/run` remains an advanced/manual command for explicit
execution control; it is not required for normal work.

Slash commands such as `/new`, `/status`, `/models`, `/memory`, `/resume`, and
`/help` are local interaction syntax over semantic runtime operations. The API
remains resource-oriented: `POST /threads`, `POST /runs`, readiness, models,
memory, and approval resources, not slash-command route names.

## Non-Goals

- Docker mode does not start the CLI.
- CLI/TUI profile does not require configuring an API before launch.
- CLI/TUI profile does not require `/run` before ordinary agent work can begin.
- Slash commands are CLI/TUI interaction syntax; API should expose semantic
  resources such as threads, runs, models, memory, and status instead of
  slash-command strings.
- Subagents are not redesigned in this phase; they remain part of the agent
  team architecture.
- Monetary amount limits remain outside runtime governance.
