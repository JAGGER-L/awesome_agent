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
- Approvals: exact-invocation approve once, deny, cancel Run, and durable
  approval records.
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
failed conversation turn and `Ctrl+C` to request cancellation of the current
Run.

Type `continue` to continue the latest paused or waiting response in the
current conversation. `continue` is a control action; it is not sent to the
model as a new user message.

`Ctrl+C` requests cancellation of the active Run. Cancellation is confirmed only
after the runtime reaches terminal `cancelled` state or reports
`recovery_required`.

Approval prompts support `approve once`, `deny`, and `cancel run`. Denying one
tool call lets the agent continue with a denied tool result; cancelling stops
the Run. Session-wide and always approval grants are intentionally not part of
this lifecycle.

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

Useful chat-first TUI keys:

| Key | Action |
| --- | --- |
| `Ctrl+C` | Request cancellation of the current Run when one is active. |
| `Ctrl+O` | Expand or collapse the latest thought block when reasoning was streamed. |
| `Ctrl+R` | Retry the last failed conversation turn. |

Slash commands are CLI/TUI interaction syntax. API routes should expose
semantic resources such as threads, read-only runs, runtime probes, models,
memory, readiness, and approvals rather than slash-command route names.

## API Resource Shape

The product API is thread-first. Create or resume a conversation through
`/threads`, then send turns with `/threads/{thread_id}/turns/stream` or resume
the latest recoverable turn with `/threads/{thread_id}/turns/continue/stream`.

Thread resources expose the product state used by the TUI:

- `/threads/{thread_id}/messages`
- `/threads/{thread_id}/runs`
- `/threads/{thread_id}/runs/{run_id}/events`
- `/threads/{thread_id}/runs/{run_id}/messages`
- `/threads/{thread_id}/runs/{run_id}/artifacts`
- `/threads/{thread_id}/runs/{run_id}/usage`
- `/threads/{thread_id}/attachments`
- `/threads/{thread_id}/config`
- `/threads/{thread_id}/memory`

List resources that can grow return a bounded envelope:

```json
{
  "items": [],
  "limit": 50,
  "offset": 0,
  "has_more": false
}
```

Run-first endpoints are read-only diagnostics. They are useful for debugging
runtime evidence, traces, metrics, and model calls, but they are not the main
chat product entry point. Run mutation and approval decisions require a thread
scope.

File input is represented by thread attachments. The legacy
`/threads/{thread_id}/uploads` inspection endpoint is not part of the product
API.

Errors use a structured JSON shape with `code`, `message`, `detail`,
`request_id`, and `recoverable`. This local API does not add authentication or
rate limiting.

`/model` opens a provider picker first. The current product build exposes only
DeepSeek. Selecting DeepSeek opens the DeepSeek model picker and updates the
current conversation default model. Team role model configuration remains a
settings-level runtime concern.

Model self-descriptions are not authoritative identity evidence. Use `/model`
to choose the current model and inspect configured model names, provider,
API-key presence, and the last completed turn's requested and observed response
model. Custom DeepSeek-compatible base URLs are not supported by the product
runtime.

## Thread Attachments

Use `/attach <path>` in the chat-first TUI to attach a local file to the next
message in the current conversation. Pending attachments are shown above the
input area and are cleared only after the next turn has started successfully.
If turn creation fails before `turn.started`, the pending attachment remains
available for retry.

Attachments are copied into Awesome Agent's local attachment store. They are
not copied into the project directory, not written to memory, and not treated
as generated artifacts. Small UTF-8 text attachments may be injected into the
current Run as bounded untrusted context; binary files are exposed as metadata
only. Deleting an attachment removes its stored content, so the content cannot
be downloaded again.

## TUI Operator Console

Use `awesome-agent tui` when you want an interactive local view over active and
recent Runs. The console reads from the API and uses thread-scoped approval and
cancel endpoints; user-facing continuation belongs to the current thread turn,
not to a run-first resume endpoint.

Useful keys:

| Key | Action |
| --- | --- |
| `r` | Refresh |
| `c` | Cancel selected Run |
| `a` | Approve latest pending approval for selected Run |
| `d` | Deny latest pending approval for selected Run |
| `q` | Quit |
