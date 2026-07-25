# CLI and keyboard reference

The public `awesome` executable is the Ink terminal client. The official
installers bundle a private Node.js 22.23.1 runtime, so an installer user does
not need Node preinstalled. Running the TUI from source or installing its npm
package directly requires Node.js 22.23.1 or newer plus interactive stdin and
stdout. The client starts one private `awesome-core` process discovered through
its launch environment; Core performs every model, state, and tool operation.

## Launch syntax

```text
Usage: awesome [--continue | --resume [thread_id]]

Options:
  --continue            Resume the most recent thread in this workspace
  --resume [thread_id]  Choose a recent thread or resume the given thread
  -V, --version         Print the installed product version
  -h, --help            Show this help
```

| Invocation | Result |
| --- | --- |
| `awesome` | Create and select a new Thread in the current directory's workspace. |
| `awesome --continue` | Select the most recently updated Thread in this workspace. |
| `awesome --resume` | Open the recent-Thread picker. |
| `awesome --resume <thread_id>` | Resume one exact or accepted abbreviated Thread ID. |
| `awesome -V`, `awesome --version` | Print the numeric product version and exit. |
| `awesome -h`, `awesome --help` | Print help and exit. |

Flags cannot be combined, and no other public launch flags are accepted.
Unknown or malformed arguments print the same usage contract and exit with a
failure.

The startup directory is the workspace. Trust, local state compatibility, and
Core/TUI protocol compatibility are resolved before normal input is admitted.
See [files and state](files-and-state.md) and
[Protocol v3](protocol.md).

## Input classification

The Composer classifies the first non-whitespace character:

| Input | Route |
| --- | --- |
| ordinary text | `turn.submit`; the model receives a new Agent Turn |
| `/name ...` | Slash Command catalog; routed to Application or Ink ownership |
| `! command` | `direct.execute`; no model call or ordinary shell prompt—the exact input is direct authority independent of Thread mode; schema, hard-deny policy, Change Journal, timeout, cancellation, and audit still apply |
| empty/whitespace | No submission |

Slash arguments support single or double quotes and backslash escaping:

```text
/rename "Investigate startup race"
/memory add user 'Prefer tests near the changed boundary.'
```

An unmatched quote or trailing escape is `invalid_arguments`. Quoting is
handled by the TUI command tokenizer, not by a shell. For `! command`, everything
after `!` is sent as one direct command string to the host shell policy.

`@path` references in natural-language input are parsed by Core and snapshot a
bounded workspace file or directory selection into the Turn context. They do
not change the launch working directory.

## Composer editing

| Key | Behavior |
| --- | --- |
| Enter | Submit the current input. |
| Shift+Enter or Ctrl+J | Insert a newline. |
| Left / Right | Move by one grapheme. |
| Home / End | Move to the start or end of the current visual line. |
| Ctrl+A / Ctrl+E | Move to the start or end of the whole buffer. |
| Backspace / Delete | Delete before or at the cursor. |
| Ctrl+W | Delete the preceding word. |
| Ctrl+U / Ctrl+K | Delete to the start or end of the current line. |
| Up / Down with an empty Composer | Navigate submitted history, or recall pending input according to current ownership. |

Text is grapheme-aware and display-width-aware. The real terminal cursor is
used; IME pre-edit rendering remains the terminal host's responsibility.

## Command menu and interactions

- Typing `/` opens searchable command candidates.
- Up/Down moves the selection, including through the scrolling ten-row window.
- Tab completes only the canonical `/command` name.
- Enter executes the selected command once.
- Escape closes the command list without changing the draft.
- Pickers and Trust, Approval, Auth, Secret, Recovery, and Fatal surfaces own
  input exclusively while visible. Up/Down chooses, Enter confirms, and Escape
  cancels or denies according to that surface.
- Ctrl+O toggles all bounded expandable detail, including Thinking, tool
  sequences, diagnostics, Diff, Undo, and Redo output.

## Cancellation, pending input, and exit

Ctrl+C has context-dependent behavior:

1. during an active operation, request cancellation;
2. with a non-empty Composer, clear the draft;
3. while idle with an empty Composer, show an exit hint;
4. a second idle Ctrl+C within two seconds exits.

Ctrl+D exits only while the Composer is empty. `/quit` performs the normal Core
shutdown path.

While an operation is active, the TUI stores at most three pending submissions
in a session-only FIFO. Natural-language messages, Slash Commands, and direct
commands share the queue. They are not parsed, sent, bound to a Thread, or
written into transcript history until promoted. With an empty Composer, Up
recalls the newest pending item first; recall order is LIFO but execution order
remains FIFO.

A queued `/new` or `/resume` finishes its authoritative Thread transition before
the next pending item is parsed. A queued `/quit` is a barrier: no later item is
accepted unless `/quit` is recalled before execution.

## Terminal and process failures

The CLI exits before startup when Node is older than 22 or either terminal
stream is not a TTY. Loss of Core, malformed NDJSON, protocol or version
incompatibility, and unexpected UI exceptions are fatal surfaces. Request-level
product errors remain transcript feedback and do not masquerade as process
failure.

For command grammar, see [Slash Commands](commands.md). For shell safety and
timeouts, see [built-in tools](built-in-tools.md).
