# CLI And Commands

Use `awesome` from the project directory you want the agent to work in:

```powershell
awesome
awesome doctor
awesome commands
```

`awesome` opens the chat-first local TUI. `awesome doctor` checks the local CLI
setup and reports actionable `OK`, `WARN`, `ERROR`, and `INFO` lines.

## Slash Commands

| Command | Purpose |
| --- | --- |
| `/help` | Show commands. |
| `/new` | Start a new conversation. |
| `/threads` | Switch conversation. |
| `/attach <path>` | Attach a local file to the next message. |
| `/model` | Choose provider, then model. |
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

## Keys

| Key | Action |
| --- | --- |
| `Ctrl+C` | Request cancellation of the current turn when one is active. |
| `Ctrl+O` | Expand or collapse the latest thought block when reasoning was streamed. |
| `Ctrl+R` | Retry the last failed conversation turn. |

Slash commands are a local interaction syntax. API routes use semantic
resources rather than slash-command names.
