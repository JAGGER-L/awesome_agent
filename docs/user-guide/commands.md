# Commands and Interaction

This page explains how to launch Awesome, control a live session, and interpret
command behavior. It is a task-oriented guide; use the
[command reference](../reference/commands.md) for the canonical inventory and
argument grammar.

## Launch Commands

Run Awesome from the project directory that should become the Workspace:

| Invocation | Result |
| --- | --- |
| `awesome` | Start a new Thread in the current directory. |
| `awesome --continue` | Resume the most recently updated Thread for this Workspace. |
| `awesome --resume` | Open a picker of recent Workspace Threads. |
| `awesome --resume <thread_id>` | Resume one matching Thread. |
| `awesome -V` or `awesome --version` | Print the numeric product version and exit. |
| `awesome -h` or `awesome --help` | Print launch help and exit. |

No other public launch flags are supported. The released `awesome` command
always starts the Ink TUI, which launches one private Python Core process. The
Core is not a separately managed service.

## Three Kinds of Input

The Composer trims leading whitespace and routes text by its first
non-whitespace character:

```text
natural language       -> Agent Turn
/command               -> slash command
! shell command        -> direct execute Operation
```

An `@path` inside natural language adds an explicit Workspace path reference:

```text
Compare @src/config.py with @tests/test_config.py and explain the mismatch.
```

`!` does not ask the model to choose or rewrite the command. It represents
explicit user authority, skips normal shell approval, and still passes through
Core's hard-deny policy, bounded process runner, redaction, audit, and Change
Journal observation.

## Conversation Commands

| Command | What it does |
| --- | --- |
| `/new` | Create and select a new Thread in this Workspace. |
| `/rename <title>` | Persist a user-selected title for the current Thread. |
| `/resume [thread_id]` | Pick or select a previous Thread from this Workspace. |
| `/thinking [on\|off]` | Inspect or set Thinking for future Turns in this Thread. |
| `/model [deepseek\|kimi]` | Choose a Provider and model for this Thread and the user default. |
| `/skills [auto\|off\|name]` | Inspect or select the Skill mode for future Turns. |

The first accepted natural-language message supplies a bounded automatic title
for a new Thread. `/rename` requires a nonempty title and rejects an overlong
title instead of silently changing it. `/new` takes no title argument.

Selecting a Thread restores its durable messages, Turns, model, Thinking, and
Skill choices. It also resets session permission authority to Request approval
and clears temporary grants. The previous Thread remains available through
`/resume`.

## Context and Inspection Commands

| Command | What it shows or changes |
| --- | --- |
| `/context` | Latest meaningful context manifest, estimates, and budget. |
| `/compact` | Summarize eligible older completed Turns for this Thread. |
| `/workspace` | Display path used to start the active Workspace; Core tracks canonical path and physical identity internally. |
| `/status` | Product, Thread, model, permission, extension, operation, and change summary. |
| `/usage` | Cumulative observed token, model, tool, retry, compression, and active-time usage. |
| `/config` | Configuration-source and credential-presence diagnostics; never secret values. |
| `/doctor` | Local state checks, Workspace-instruction diagnostics, and on-demand Provider validation. |
| `/tools` | Effective built-in and extension tool catalog and approval state. |

`/context` and `/usage` answer different questions. The former explains what
was assembled for the model; the latter accounts for what the Thread has
consumed. `/config` is intentionally not a raw configuration dump. `/doctor`
may make Provider network requests and renders bounded detail for failed
checks.

`/compact` can return a no-op when there is not enough completed history. While
it is running, the TUI keeps one pending result row and replaces that row with
the terminal outcome.

## Change Commands

| Command | What it does |
| --- | --- |
| `/diff [change_set_id]` | Render the latest or selected recorded file delta. |
| `/undo [change_set_id]` | Restore the selected applied ChangeSet's recorded file state. |
| `/redo [change_set_id]` | Reapply a successfully undone ChangeSet. |

These commands use exact ChangeSet lifecycles and conflict checks. They do not
claim to reverse arbitrary shell or MCP effects. See
[Review, undo, and redo](changes.md).

## Provider and Credential Commands

Run `/model` to choose DeepSeek or Kimi. If the Provider has no selected usable
credential, Awesome opens a masked secret input before the model picker. A
successful model choice updates the current Thread and the user default for
future Threads; it does not rewrite existing other Threads.

Use:

```text
/auth
/auth deepseek
/auth kimi
/auth mem0
```

`/auth` shows Environment and Awesome-managed credential sources separately.
Keys are never accepted as command arguments. A known-invalid Provider key is
not saved. When validation cannot reach a Provider, the TUI asks whether to
save the key as unverified. Deleting an Awesome-managed key does not revoke it
at the Provider and does not silently select an Environment value.

## Permission and Extension Commands

| Command | What it does |
| --- | --- |
| `/permissions [request_approval\|accept_edits\|full_access]` | Inspect or select session authority for the active Thread. |
| `/memory` | Choose Local Memory or Mem0 Cloud and inspect its state. |
| `/mcp [status [id]\|enable <id>\|disable <id>\|restart <id>]` | Inspect or manage configured MCP servers. |

Full access requires a second Thread-bound confirmation. Memory's explicit
list/add/replace/remove and Mem0 search/remove forms are documented in
[Memory](../extensions/memory.md). Workspace MCP enablement and user-configured
server rules are documented in [MCP](../extensions/mcp.md).

## TUI-Local Commands

These commands are implemented by Ink and do not ask Core to mutate product
state:

| Command | What it does |
| --- | --- |
| `/help [command]` | Render the whole command catalog or one matching row in transcript. |
| `/theme [system\|dark\|light]` | Inspect or set the local presentation theme. |
| `/copy` | Copy the latest assistant answer to the clipboard. |
| `/quit` | Coordinate Core shutdown and exit the TUI. |

`/help` is transcript content rather than a modal, so it does not hide the
conversation. `/copy` copies the latest completed assistant answer, not an
active stream or Tool detail.

## Keyboard Ownership

- Typing `/` opens command candidates. Up/Down changes selection, Tab completes
  the canonical command, Enter submits once, and Escape closes the menu without
  replacing the draft.
- A visible picker, Trust prompt, Approval prompt, or Auth prompt owns the
  keyboard. Up/Down selects, Enter confirms, and Escape cancels or denies as
  defined by that interaction.
- Ctrl+C requests cancellation of the active Operation. Input returns after a
  terminal event; if cleanup fails, the error remains visible.
- Ctrl+O expands or folds bounded details for Thinking, Tool sequences, and
  Undo/Redo paths. Details start folded.
- With an empty Composer, Up recalls the newest queued input into the draft.
  Repeated recall moves from newest to oldest.

## Queueing and Foreground Ordering

The TUI queues up to three later inputs while an Operation runs. Natural
language, slash commands, and direct commands start in submission order. A
queued `/quit` rejects additional queue entries until it is recalled or reaches
the front.

Core itself admits one mutable foreground owner atomically. If two operations
race, the loser receives `operation_busy` before a Turn or state mutation is
persisted. A pending interaction blocks new Operations and state changes with
`interaction_busy` until it is resolved.

At the private Core command boundary, only the following side-effect-free
snapshots are allowed during an active Operation:

```text
/context  /workspace  /tools  /mcp  /mcp status [id]
/status   /usage      /config
```

`/diff` is excluded because the active ChangeSet may still be changing.
`/doctor` is excluded because it can contact Providers. The current Ink TUI
does not submit this exception concurrently: it queues every later input,
including these commands, and displays the result after the Operation finishes.
The Core allowlist is a protocol/concurrency contract, not a live-monitoring
promise for the current user interface.

## Status as a Starting Point

When behavior is surprising, run `/status`. It identifies the product version,
Workspace, Thread and ID, model, selected credential availability, permission
mode, context use, Thinking and Skill modes, Memory state, MCP readiness,
foreground Operation, and recorded file-change count when present.

Then narrow the question:

- context mismatch: `/context`;
- exhausted budget: `/usage`;
- unexpected approval: `/tools` and `/permissions`;
- extension failure: `/mcp` or `/memory`;
- environment or Provider problem: `/config` and `/doctor`;
- file effects: `/diff`.

For exact syntax and ownership, see the [CLI reference](../reference/cli.md)
and [command reference](../reference/commands.md).
