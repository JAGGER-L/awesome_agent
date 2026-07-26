# Slash Command reference

Awesome has one closed command catalog. Twenty-one commands are owned by the
Python Application and four are owned by Ink. A Slash Command is product
control input: it is displayed in the terminal transcript but is not stored as
a model conversation message.

## Application commands

| Command | Exact public syntax | Effect |
| --- | --- | --- |
| `/new` | no arguments | Create and select a new Thread; reset Thread-scoped permission state. |
| `/rename <title>` | one or more title tokens | Persist a manual title for the selected Thread. |
| `/resume [thread_id]` | zero or one ID/prefix | Open a picker or select one workspace Thread. |
| `/context` | no arguments | Show the latest active context categories, realized token count, and budget. |
| `/compact` | no arguments | Build and persist a new conversation summary now. |
| `/auth [deepseek\|kimi\|mem0]` | zero or one service in normal use | Select and manage Environment or Awesome API-key sources through pickers. |
| `/model [deepseek\|kimi]` | zero or one Provider in normal use | Choose a Provider/model; update this Thread and the user default. |
| `/thinking [on\|off]` | zero or one value | Show a picker or set future-Turn Thinking on the current Thread. |
| `/permissions [request_approval\|accept_edits\|full_access]` | zero or one mode | Inspect or change the session permission mode; Full access requires a separate confirmation. |
| `/workspace` | no arguments | Show the active workspace display path. |
| `/diff [change_set_id]` | zero or one ID | Render the latest or selected ChangeSet diff. |
| `/undo [change_set_id]` | zero or one ID | Restore the before-state of an applied reversible ChangeSet. |
| `/redo [change_set_id]` | zero or one ID | Restore the after-state of an undone ChangeSet. |
| `/tools` | no arguments | List the effective catalog and approval requirement under the current mode. |
| `/skills [auto\|off\|name]` | zero or one mode/name | Inspect or set the current Thread's Skill mode. |
| `/mcp [status [id]\|enable <id>\|disable <id>\|restart <id>]` | as shown | Inspect or manage MCP servers. |
| `/memory [local ...\|mem0 ...]` | see below | Inspect, enable, search, or mutate Memory. |
| `/status` | no arguments | Show the selected Thread and runtime status snapshot. |
| `/usage` | no arguments | Show cumulative observed usage for the selected Thread. |
| `/doctor` | no arguments | Check configuration, state/checkpoints, workspace instructions, and configured Providers. |
| `/config` | no arguments | Show source categories and credential presence/selection, never secret values. |

`/rename` joins parsed tokens with spaces. Blank titles are rejected. Titles
longer than 100 visible characters are rejected rather than truncated. The
first accepted natural-language message supplies an automatic title of at most
48 visible characters until `/rename` sets a manual one.

`/resume` accepts an exact Thread ID or an unambiguous `thread_` prefix of 8–32
lowercase hexadecimal digits. Ambiguous prefixes open a picker; cross-workspace
Threads are never selected.

`/auth` never accepts a key as a command argument. The picker may generate
internal continuation tokens for source selection, replacement, and deletion;
use the masked interaction rather than scripting those tokens. Saving a
DeepSeek or Kimi key performs one short Provider validation request. An
unreachable model Provider offers an explicit save-unverified choice; a key
that the model Provider rejects is not saved. Mem0 is different: `/auth mem0`
performs local input/storage validation only and saves the value without a
remote credential check. An invalid Mem0 key is discovered later when cloud
Memory is enabled or called.

`/model` with a selected Provider first ensures that Provider has an available
selected credential, then offers only the curated models in the
[configuration reference](configuration.md).

The `Changes` row in `/status` is the unique path count from the newest sealed
Agent ChangeSet associated with the selected Thread. It excludes direct shell
operations and is zero when that ChangeSet has been undone; it is not a Git
working-tree dirty count. `/doctor` reports `Unverified` when runtime readiness
cannot be established instead of assuming that configuration, Application
SQLite, or checkpoint services are healthy. Application SQLite runs its bounded
read-only `quick_check` through the process-owned connection, while checkpoint
readiness is checked through the checkpoint saver. Neither check repairs or
rewrites state.

## Memory subcommands

| Syntax | Result |
| --- | --- |
| `/memory` | Choose Local or Cloud Memory, then choose On/Off. |
| `/memory local` | Open the Local Memory On/Off picker. |
| `/memory local on\|off` | Persist and apply local Memory enablement. |
| `/memory list user\|workspace` | Return entries and the current content hash. |
| `/memory add user\|workspace <content>` | Add one policy-valid local entry. |
| `/memory replace user\|workspace <entry_id> <content>` | Replace one local entry. |
| `/memory remove user\|workspace <entry_id>` | Remove one local entry. |
| `/memory mem0` | Open the Mem0 On/Off picker. |
| `/memory mem0 on\|off` | Persist and apply Mem0 enablement. |
| `/memory mem0 search <query>` | Search scoped Mem0 records. |
| `/memory mem0 remove <memory_id>` | Verify ownership/scope, then delete one cloud record. |

Local command mutations snapshot the document immediately before applying a
compare-and-swap update. Agent Memory tools expose the content hash explicitly.
See [Memory](../extensions/memory.md).

## MCP subcommands

| Syntax | Result |
| --- | --- |
| `/mcp`, `/mcp status` | Show every server. |
| `/mcp status <id>` | Show one server or `mcp_server_not_found`. |
| `/mcp enable <id>` | Persist a config-hash-bound enablement for a workspace server. |
| `/mcp disable <id>` | Remove workspace enablement and its registry namespace. |
| `/mcp restart <id>` | Remove the current namespace/client and reconnect if effective. |

User servers are enabled only by user YAML; `enable` and `disable` return
`user_config_required` for them. See [MCP](../extensions/mcp.md).

## Ink-local commands

| Command | Syntax | Effect |
| --- | --- | --- |
| `/help [command]` | zero or one command, optional leading `/` | Render the whole catalog or one focused help row. |
| `/theme [system\|dark\|light]` | zero or one theme | Show a picker or atomically update `ui.json`. |
| `/copy` | no arguments | Copy the newest durable assistant message; live incomplete text is not copied. |
| `/quit` | no arguments | Shut down Core and exit. |

Ink-local ownership means no `command.execute` RPC. It does not mean a command
can violate lifecycle rules: `/quit` still waits for the coordinated shutdown,
and UI preferences remain bounded local state.

## Foreground admission

Core atomically admits one foreground operation or state-changing/external
command. Admission occurs before creating a Turn or mutating state. During an
active operation, only these Application snapshots are allowed:

```text
/context
/workspace
/tools
/mcp
/mcp status
/mcp status <id>
/status
/usage
/config
```

`/diff` is excluded because it can read a ChangeSet while it is changing.
`/doctor` is excluded because it may contact Providers. Every other Application
command returns `operation_busy` if it reaches Core concurrently; the TUI
normally queues it instead.

A pending interaction blocks new operations and state changes. Snapshot
commands, cancellation, and the matching `interaction.respond` are exceptions.
A Tool approval is a continuation of its existing operation and therefore
bypasses the ordinary exclusive gate only after Thread, Turn, operation, and
interaction identities match.

## Result forms

Application commands return exactly one `CommandOutcome` branch:

- `result`: a typed payload such as `status`, `diff`, `tools`, or
  `thread_transition`;
- `interaction`: a typed selection, secret prompt, or Application interaction,
  optionally with a context payload;
- `error`: a stable code and bounded user-facing message.

Command input and result remain separate terminal blocks. Picker cancellation,
invalid arguments, and Core errors therefore preserve the exact submitted
command. The full wire schemas are covered by
[Protocol v3](protocol.md).
