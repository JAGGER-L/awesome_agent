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
is not the default backend for Worker-owned API profile Runs and is not a
security boundary.
Trusted local execution uses Hermes-style soft guardrails: validation and
read-only commands may run automatically, risky mutation commands require
approval, extreme destructive commands are blocked, patch writes can be
bounded by `AWESOME_AGENT_WRITE_SAFE_ROOT`, and subprocess environments are
scrubbed of provider/API secret-looking names. Task 94 adds output redaction.

`AIO Docker` is a long-lived Linux development container with a
thread-mounted workspace directory and an `agent-sandbox` HTTP service. API
profiles use AIO Docker by default. The current service foundation executes
Python commands; full Node/npm/ripgrep toolchain hardening is tracked as the
next sandbox hardening step.

## Storage Contract

`AWESOME_HOME` defaults to `%LOCALAPPDATA%\awesome-agent` on Windows and
`~/.awesome-agent` on other platforms.

For embedded local user message turns, the model-visible working directory is the
thread context path. When a thread is created without an explicit context path,
that path is the process launch/current working directory. Reads, writes, and
commands therefore target the same project tree a user would target from their
terminal.

Run audit evidence remains separate and internal:

```text
<AWESOME_HOME>/runs/<run_id>/artifacts/
```

The previous logical thread workspace remains a Docker/API design input rather
than the default product-closure path:

```text
<AWESOME_HOME>/threads/<thread_id>/workspace/
```

Docker API mode and AIO Docker path equivalence are deferred hardening work.
They must not be required for the embedded local product path to create a
durable conversation Run, call tools, write files in cwd, and recover
conversation state.

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
LocalSandbox. It does not require an API server before user message
conversation or local coding-agent work can begin. Use `awesome --api-url
<url>` only when the TUI should connect to a local, Docker, or remote API
server.

User message input is the only product execution creation entry. It creates an
internal durable conversation Run with an initial Leader Agent, then executes
through the dispatcher, Worker, `conversation-turn` graph route, and Leader
AgentLoop. `ConversationService` only starts the internal Run and projects
runtime events; the graph owns model calls, tool calls, thread message writes,
usage metadata, changed files, and terminal state.

Slash commands such as `/new`, `/threads`, `/model`, `/thinking`, `/memory`,
`/status`, and `/help` are local interaction syntax over semantic runtime
operations. The API remains resource-oriented: `POST /threads`,
`POST /threads/{thread_id}/turns/stream`, read-only Run inspection, readiness,
`POST /runtime/probes`, models, memory, and approval resources, not
slash-command route names.

## Non-Goals

- Docker mode does not start the CLI.
- CLI/TUI profile does not require configuring an API before launch.
- CLI/TUI profile uses user message input for product execution creation.
- Slash commands are CLI/TUI interaction syntax; API should expose semantic
  resources such as threads, runs, models, memory, and status instead of
  slash-command strings.
- Subagents are not redesigned in this phase; they remain part of the agent
  team architecture.
- Monetary amount limits remain outside runtime governance.
