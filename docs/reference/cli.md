# CLI and keyboard reference

The public `awesome` executable provides an Ink terminal interface and a
headless single-Turn mode. The official
installers bundle a private Node.js 22.23.1 runtime, so an installer user does
not need Node preinstalled. Running from source or installing the npm package
directly requires Node.js 22.23.1 or newer. The Ink interface additionally
requires interactive stdin and stdout; `awesome run` is the supported
non-interactive surface. The client starts one private `awesome-core` process discovered through
its launch environment; Core performs every model, state, and tool operation.

## Launch syntax

```text
Usage: awesome [--continue | --resume [thread_id]]
       awesome run <prompt> [--new | --thread <id>] [options]

Options:
  --continue            Resume the most recent thread in this workspace
  --resume [thread_id]  Choose a recent thread or resume the given thread
  -V, --version         Print the installed product version
  -h, --help            Show this help

Headless run options:
  --new                  Create a new thread (default)
  --thread <id>          Run in the selected existing thread
  --format <text|json>   Select final output format (default: text)
  --trust-workspace      Trust this workspace for the current startup flow
  --permission-mode <request_approval|accept_edits|full_access>
                         Select the process-local permission mode
  --allow-network        Declare network intent for this process only
```

| Invocation | Result |
| --- | --- |
| `awesome` | Create and select a new Thread in the current directory's workspace. |
| `awesome --continue` | Select the most recently updated Thread in this workspace. |
| `awesome --resume` | Open the recent-Thread picker. |
| `awesome --resume <thread_id>` | Resume one exact or accepted abbreviated Thread ID. |
| `awesome run "<prompt>"` | Run one Turn in a new Thread and print its final answer. |
| `awesome run "<prompt>" --thread <id>` | Run one Turn in the exact existing Thread. |
| `awesome -V`, `awesome --version` | Print the numeric product version and exit. |
| `awesome -h`, `awesome --help` | Print help and exit. |

Interactive launch flags cannot be combined. Headless options apply only after
`run`; `--new` and `--thread` are mutually exclusive. No other public launch
flags are accepted. Unknown or malformed arguments print the same usage
contract to stderr and exit with code 2.

The startup directory is the workspace. Trust, local state compatibility, and
Core/TUI protocol compatibility are resolved before normal input is admitted.
See [files and state](files-and-state.md) and
[Protocol v4](protocol.md).

## Headless run

`awesome run` executes exactly one natural-language Agent Turn without Ink:

```text
awesome run "Summarize the failing tests" --trust-workspace
awesome run "Continue the analysis" --thread <thread_id> --format json
awesome run "Apply the reviewed fix" --permission-mode accept_edits
```

The quoted prompt is one required argument. A new Thread is the default;
`--thread <id>` selects one exact existing Thread instead. Startup uses the
same trust, state preflight, configuration, Thread/Turn lifecycle, private
Core, and Application facade as the interactive surface. It does not create a
second runtime or a public remote API.

`--trust-workspace` accepts the trust prompt for the canonical startup
Workspace. Without it, required trust or any other unresolved startup
interaction exits with code 3. `--permission-mode` requests one of the three
normal modes for the selected Thread. The `full_access` spelling is itself the
explicit warning confirmation for this headless process; it remains
Thread/session scoped and cannot override hard denials. If the Turn later
requires any interaction that the runner cannot resolve, Awesome requests
cancellation and exits with code 3.

`--allow-network` authorizes this process to resolve only an exact
`network.read` prompt for the active headless Turn as `allow_once`. It does not
enable Web by itself, cannot create a Thread grant or resolve another
interaction, and never bypasses a hard denial.

With `--format text`, stdout contains only the durable final assistant text
followed by one newline. With `--format json`, stdout contains one compact JSON
document followed by one newline:

```json
{"version":2,"type":"awesome.run.result","thread_id":"...","turn_id":"...","text":"... [[S1]]","citations":[{"id":"S1","title":"Example","url":"https://example.com/source"}],"termination_reason":null,"usage":{"input_tokens":0,"output_tokens":0,"reasoning_tokens":0,"cache_read_tokens":0,"cache_write_tokens":0,"model_calls":0,"tool_calls":0,"provider_retries":0,"compressions":0,"web_requests":1,"active_execution_seconds":0}}
```

The JSON document is versioned independently from Protocol v4. Version 2 adds
the ordered `citations` array and `usage.web_requests`; it reports the durable
answer and Turn facts, not a stream of protocol events. On every
nonzero exit, stdout is empty and diagnostics go to stderr.

| Exit code | Meaning |
| ---: | --- |
| `0` | The Turn completed and the final text or JSON document was written. |
| `1` | The run failed, including an unexpected Core launch, model/configuration, Turn, transport, or durable-result failure. |
| `2` | Arguments or a known CLI/runtime prerequisite were invalid, including a recognized Core executable startup failure. |
| `3` | Trust, state reset, Thread selection, approval, or another interaction remains unresolved. |
| `130` | SIGINT was received; Awesome first requests cancellation of the active Operation, then shuts down Core. |

SIGINT never prints a partial answer. The runner makes a bounded attempt to
confirm cancellation before returning 130; if confirmation times out, stderr
reports that fact and the launcher proceeds to bounded shutdown of the same
Surface and Core process used for the Turn.

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

The CLI exits before startup when Node is older than 22. Interactive launches
also require both terminal streams to be TTYs; `awesome run` does not. Loss of
Core, malformed NDJSON, protocol or version
incompatibility, and unexpected UI exceptions are fatal surfaces. Request-level
product errors remain transcript feedback and do not masquerade as process
failure.

For command grammar, see [Slash Commands](commands.md). For shell safety and
timeouts, see [built-in tools](built-in-tools.md).
