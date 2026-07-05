# Product Surfaces

Product surfaces are clients of the runtime. A surface client can submit user
intent, render projected state, and expose controls, but it must not own model
calls, graph execution, or tool authority.

## Primary Product Contract

Plain user messages are the only execution creation path. Slash commands and
API controls configure, inspect, cancel, retry, attach context, or change local
state; they do not create a separate product execution route.

User message input enters the Leader AgentLoop. Simple questions are Leader
turns with no tool calls when the model can answer from available context. More
complex work may use tools, teammates, subagents, or verification under runtime
policy.

TUI never imports provider implementations. Provider selection, model calls,
tool execution, and durable transitions stay behind runtime services.

## Local CLI/TUI Profile

`awesome` is the default Local CLI/TUI profile. It runs in embedded local
runtime mode and does not require an API server. The launch directory becomes
the default thread context. If the directory is a Git checkout, runs inherit
that repository; otherwise the product uses workspace-only mode and still
accepts user message turns.

Use `awesome --api-url <url>` only when the TUI should connect to an existing
API service.

## Local API Development Profile

This section defines the Local API development profile.

Local API development uses host services and is currently documented for
Windows:

```powershell
make check
make install
make setup-sandbox
make dev
```

This profile is for clients, diagnostics, browser API docs, and integration
work. It is not the required path for ordinary Local CLI use.

## Docker API Profile

Docker API profile starts the API stack through Docker:

```powershell
make docker-init
make docker-start
```

Docker mode does not start the CLI. Run `awesome` separately for terminal chat.
The Docker stack includes AIO Docker sandbox support and LocalSandbox
configuration for command execution boundaries.

## Fallback Command

`awesome-agent start` is a fallback/debug supervisor for local API development.
Normal product use should prefer `awesome`, `make dev`, or `make docker-start`
depending on the chosen profile.

## Related Documents

- [User guide](../user-guide/README.md)
- [Operations startup modes](../operations/startup-modes.md)
- [Runtime kernel](runtime-kernel.md)
