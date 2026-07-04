# User Guide

This guide indexes user-facing runtime surfaces. Detailed pages can be split
out as those surfaces mature.

- Interactive CLI: `awesome`, `awesome commands`, and slash commands.
- User message turns: chat-first input creates internal conversation Runs with
  Leader agents.
- Run inspection: `status`, `agents`, and `todos` inspect existing runtime
  evidence.
- Team mode: distributed Leader, Teammate, Verifier, assignments, and mailbox
  remain runtime capabilities; chat-first product controls are roadmap work.
- Approvals: `approve`, `resume`, and durable approval records.
- Extensions: project `skills/`, `awesome-agent.yaml`, and MCP sources.
- Diagnostics: `probe`, `diagnostics`, `recovery-metrics`, `budget`, and
  `context-compactions`.

Start with the [quickstart](../getting-started/quickstart.md) before sending
model-backed user message turns.

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
`/mcp`, `/usage`, and `/config`.

Plain user messages are the only product execution creation path. A user
message turn creates an internal conversation Run with a Leader Agent and
executes through the embedded local runtime. Conversation and Run failures are
rendered as structured transcript items with request IDs, retryability, and
remediation hints when the API provides them. Use `Ctrl+R` to retry the last
failed conversation turn and `Ctrl+C` to cancel the current Run.

| Command | Purpose |
| --- | --- |
| `/help` | Show commands. |
| `/new` | Start a new conversation. |
| `/threads` | Switch conversation. |
| `/model` | Choose model. |
| `/thinking` | Choose thinking mode. |
| `/memory` | Manage memory. |
| `/skills` | Apply skills to the next turn. |
| `/tools` | Show leader-visible tools. |
| `/mcp` | Show MCP server status. |
| `/status` | Show current thread, run, and runtime status. |
| `/usage` | Show token usage and context. |
| `/config` | Show configuration. |
| `/details` | Choose detail level. |
| `/quit` | Exit the TUI. |

Useful chat-first TUI keys:

| Key | Action |
| --- | --- |
| `Ctrl+C` | Cancel the current Run when one is active. |
| `Ctrl+O` | Expand or collapse the latest thought block when reasoning was streamed. |
| `Ctrl+R` | Retry the last failed conversation turn. |

Slash commands are CLI/TUI interaction syntax. API routes should expose
semantic resources such as threads, read-only runs, runtime probes, models,
memory, readiness, and approvals rather than slash-command route names.

Model self-descriptions are not authoritative identity evidence. Use
`/model` to choose the current model and inspect configured model names,
provider, base URL, API-key presence, and the last completed turn's requested
and observed response model.
Gateways and compatible base URLs may route aliases internally; when response
metadata is absent, the provider did not return it.

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
