# User Guide

This guide indexes user-facing runtime surfaces. Detailed pages can be split
out as those surfaces mature.

- Interactive CLI: `awesome`, `awesome commands`, and slash commands.
- Runs: `awesome-agent run`, `status`, `agents`, and `todos`.
- Team mode: `awesome-agent run --team`, child Runs, assignments, and mailbox.
- Approvals: `approve`, `resume`, and durable approval records.
- Extensions: project `skills/`, `awesome-agent.yaml`, and MCP sources.
- Diagnostics: `diagnostics`, `recovery-metrics`, `budget`, and
  `context-compactions`.

Start with the [quickstart](../getting-started/quickstart.md) before creating
real coding Runs.

## Interactive CLI

Use `awesome` when you want the local coding-agent entrypoint without first
choosing API topology:

```powershell
awesome
awesome commands
```

The required slash commands are:

The local TUI is intentionally chat-first. It shows a welcome panel at launch,
then keeps the main screen focused on the transcript and input prompt. Runtime
details are available through slash commands such as `/status`, `/tools`,
`/mcp`, `/artifacts`, `/usage`, and `/config`.

| Command | Purpose |
| --- | --- |
| `/new` | Start a new durable local conversation/thread. |
| `/threads` | List known threads. |
| `/switch` | Alias for `/threads`. |
| `/resume` | Resume a thread by id or title when supported. |
| `/status` | Show current thread/run/runtime status. |
| `/model` | Alias for `/models`. |
| `/models` | List configured model profiles. |
| `/skills` | Browse enabled and available skills. |
| `/tools` | Show built-in, MCP, and sandbox tools. |
| `/mcp` | Show MCP server status. |
| `/memory` | Show memory configuration and current memory summary. |
| `/uploads` | Show uploaded files for this thread. |
| `/artifacts` | Show generated artifacts. |
| `/details` | Toggle verbose activity rendering. |
| `/usage` | Show token usage and context. |
| `/config` | Show resolved config paths and overrides. |
| `/help` | Show help. |
| `/quit` | Exit the TUI. |

Slash commands are CLI/TUI interaction syntax. API routes should expose
semantic resources such as threads, runs, models, memory, readiness, and
approvals rather than slash-command route names.

## TUI Operator Console

Use `awesome-agent tui` when you want an interactive local view over active and
recent Runs. The console reads from the API and uses the same approval, cancel,
and resume endpoints as the CLI.

Useful keys:

| Key | Action |
| --- | --- |
| `r` | Refresh |
| `c` | Cancel selected Run |
| `u` | Resume selected Run |
| `a` | Approve latest pending approval for selected Run |
| `d` | Deny latest pending approval for selected Run |
| `q` | Quit |
