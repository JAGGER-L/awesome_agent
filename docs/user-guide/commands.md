# Commands

## Launch flags

| Invocation | Result |
| --- | --- |
| `awesome` | Start a new thread in the current workspace. |
| `awesome --continue` | Resume the most recent thread in this workspace. |
| `awesome --resume` | Choose a recent thread. |
| `awesome --resume <thread_id>` | Resume the specified thread. |
| `awesome -V`, `awesome --version` | Print the numeric product version. |
| `awesome -h`, `awesome --help` | Print command-line help. |

No other public launch flags are supported.

## Application commands

| Command | Purpose |
| --- | --- |
| `/new` | Start a new thread. |
| `/resume [thread_id]` | Choose or resume a previous workspace thread. |
| `/context` | Show the active context manifest and budget. |
| `/compact` | Compact the current context now. |
| `/auth [deepseek\|kimi\|mem0]` | Select or manage model and Memory Provider credentials. |
| `/model [deepseek\|kimi]` | Choose a Provider, then choose one of its models. |
| `/thinking [on\|off]` | Show the current mode with a selector, or set it explicitly. |
| `/permissions [request_approval\|full_access]` | Show or choose the active Thread's permission mode. |
| `/workspace` | Show the current workspace path. |
| `/diff` | Show the latest or selected Change Journal change set. |
| `/undo` | Undo the latest or selected reversible change set. |
| `/redo` | Redo the latest or selected undone change set. |
| `/tools` | List the effective built-in and extension tools. |
| `/skills [auto\|off\|name]` | List Skills or select thread Skill mode. |
| `/mcp` | Show MCP server status. |
| `/memory` | Choose Local or Cloud Memory, then switch it On or Off. |
| `/status` | Show the current product and thread status. |
| `/usage` | Show cumulative token and operation usage for the current thread. |
| `/doctor` | Check configuration, embedded state, checkpoints, and Provider readiness. |
| `/config` | Show effective source and credential-presence diagnostics, never secret values. |

`/thinking` defaults to off. A bare `/thinking` reports the current value and
offers on/off choices.

## Provider and model commands

Run `/model` to choose DeepSeek or Kimi. If that Provider has no credential,
Awesome opens a masked API-key input before showing its model picker. Selecting
a model updates the current Thread and the user default for future Threads; it
does not rewrite other existing Threads.

Run `/auth`, `/auth deepseek`, or `/auth kimi` to manage credentials. Keys are
never accepted as command arguments. A rejected key is not saved. When the
Provider cannot be reached, Awesome asks whether to save the key unverified.
Removing a local credential does not revoke it at the Provider.

## Ink-local commands

| Command | Purpose |
| --- | --- |
| `/help [command]` | Show command help. |
| `/theme [system\|dark\|light]` | Show or select the TUI theme. |
| `/copy` | Copy the latest assistant answer. |
| `/quit` | Shut down Core and exit. |

`@path` adds a workspace path reference to a message. `! command` runs the
command directly through Awesome's normal Core shell policy, without asking the
model to decide how to run it. Ink never executes tools itself.

## Keyboard behavior

- Typing `/` opens command candidates. Up/Down changes the selection, Tab
  completes only the canonical `/command` text, Enter executes the selected
  command once, and Escape closes the candidates without changing the draft.
  Search covers the complete catalog; the menu displays a scrolling ten-row
  window, so Up/Down can reach every matching command.
- Pickers, Trust, Approval, and Auth exclusively own input while visible.
  Up/Down selects, Enter confirms, and Escape cancels or denies according to
  the prompt.
- Ctrl+C cancels an active operation. Input returns after the terminal event;
  a failed cancellation remains visible and can be retried.
- Ctrl+O expands or folds bounded details globally, including Tool sequences,
  Thinking, and Undo/Redo paths. Details are folded by default.
- While a task is running, Awesome queues up to three inputs. Natural-language
  messages, Slash Commands, and `! shell` run in submission order. Pending
  inputs appear between the active task and the Composer.
- With an empty Composer, Up recalls the newest pending input back into the
  draft. Repeated recall therefore moves from newest to oldest, one draft at a
  time. A non-empty draft, Command Menu, Picker, Approval, Trust, or Auth keeps
  ownership of Up instead.
- A queued `/quit` prevents additional queue entries. Recall it before adding
  more input, or let it exit at its ordered position.

`/help` is written into normal transcript history rather than opening a modal.
It renders one command per aligned row with usage and description. Use
`/help <command>` for one focused row; internal command ownership is not shown.
`/new` starts a clean conversation and redraws Awesome from the Welcome panel.
The previous conversation remains available through `/resume`. `/resume`
redraws Awesome with only the selected conversation's saved messages. When
queued behind a running task, either command completes its Thread switch before
the next queued input starts. A new Thread also resets Thread-scoped permission
grants.

## Context and change lifecycles

`/compact` writes one `Compressing context...` result while the request is
pending, then replaces that same result with `Context compressed` or the exact
failure. It never emits both pending and terminal lines.

`/diff` renders the ChangeSet ID and bounded terminal Diff. When the workspace
has no recorded changes, it shows an explicit empty result. `/undo` and `/redo`
show the action, affected file count, and resulting lifecycle on one folded
line; Ctrl+O reveals the ChangeSet ID, each affected path, and any warning.
Missing ChangeSets, workspace conflicts, irreversible changes, and invalid
lifecycles remain distinct errors.

## `/status` fields

`/status` renders:

- `Version`: one numeric value such as `1.1.0`;
- `Workspace`: the workspace path, without trust or Git-branch suffixes;
- `Thread`: the title and resumable Thread ID;
- `Model`: the full Provider/model ID;
- `Credentials`: the selected credential source and its availability;
- `Permissions`: the current Thread permission mode;
- `Context`: used Tokens and the active budget;
- `Thinking` and `Skill`: their current modes;
- `Memory`: Local memory and Mem0 Cloud on/off states;
- `MCP`: ready and degraded server counts;
- `Operation`: idle or the active operation ID;
- `Changes`: the number of modified files, when present.

Context details and token/operation usage are intentionally separate in
`/context` and `/usage`. `/context` shows the latest meaningful active Context;
`/usage` shows cumulative observed Usage for the current Thread.
