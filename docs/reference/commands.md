# Slash Command reference

Awesome has one closed command catalog. Twenty-six commands are owned by the
Python Application and four are owned by Ink. A Slash Command is product
control input: it is displayed in the terminal transcript but is not stored as
a model conversation message.

## Application commands

| Command | Exact public syntax | Effect |
| --- | --- | --- |
| `/new` | no arguments | Create and select a new Thread; reset Thread-scoped permission state. |
| `/rename <title>` | one or more title tokens | Persist a manual title for the selected Thread. |
| `/resume [thread_id]` | zero or one ID/prefix | Open a picker or select one workspace Thread. |
| `/fork [turn_id]` | zero or one Turn ID | Materialize and select an independent Thread through one terminal Turn. |
| `/retry [turn_id]` | zero or one Turn ID | Materialize a fork before one terminal Turn and freshly execute its request. |
| `/search <query> [thread_id]` | one query token (quote multiple words), then optional exact result ID | Search this Workspace, open a picker, or resume the selected matching Thread. |
| `/context` | no arguments | Show the latest active context categories, realized token count, and budget. |
| `/compact` | no arguments | Build and persist a new conversation summary now. |
| `/auth [deepseek\|kimi\|mem0]` | zero or one service in normal use | Select and manage Environment or Awesome API-key sources through pickers. |
| `/model [deepseek\|kimi]` | zero or one Provider in normal use | Choose a Provider/model; update this Thread and the user default. |
| `/thinking [on\|off]` | zero or one value | Show a picker or set future-Turn Thinking on the current Thread. |
| `/permissions [request_approval\|accept_edits\|full_access]` | zero or one mode | Inspect or change the session permission mode; Full access requires a separate confirmation. |
| `/workspace` | no arguments | Show the active workspace display path. |
| `/diff [change_set_id]` | zero or one ID | Render the latest or selected ChangeSet diff. |
| `/export <workspace-relative-path> [markdown\|json]` | one path and optional format; default `markdown` | Deterministically export the current Thread through a safe, journaled workspace write. |
| `/undo [change_set_id]` | zero or one ID | Restore the before-state of an applied reversible ChangeSet. |
| `/redo [change_set_id]` | zero or one ID | Restore the after-state of an undone ChangeSet. |
| `/tools` | no arguments | List the effective catalog and approval requirement under the current mode. |
| `/skills [auto\|off\|name]` | zero or one mode/name | Inspect or set the current Thread's Skill mode. |
| `/mcp [status [id]\|enable <id>\|disable <id>\|restart <id>]` | as shown | Inspect or manage MCP servers. |
| `/web [on\|off\|status\|revoke]` | zero or one action | Inspect or atomically enable/disable Tavily Web tools, or clear the active Thread's network grant. |
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

`/fork` and `/retry` accept at most one exact Turn ID from the selected Thread.
When omitted, the latest terminal Turn by transcript order is selected. An
in-progress target is rejected. `/fork` physically copies the durable prefix
through the target; `/retry` copies only the prefix before the target, appends
the target user request with fresh entry/client identities, and starts a fresh
Turn. That Turn freezes the original target's Provider, model, Thinking, Skill,
and complete budget snapshot even if the source Thread settings later change.
Every copied Thread, entry, and Turn receives a new identity, and the new
Thread records only its immediate source Thread/Turn lineage; no shared DAG is
constructed. Summary, checkpoint, ToolActivity, and ChangeSet records are not
copied. Retry executes through the ordinary Turn path, without replaying old
tool calls and without automatically undoing their prior side effects.

`/search` accepts one query argument. Quote a multi-word query, for example
`/search "provider retry"`; after selection the TUI appends the chosen exact
Thread ID to that original query. Search is isolated to the active Workspace
and matches ASCII-case-insensitive literal substrings in Thread titles and all
durable transcript entry content, including user, assistant, and direct-command
entries. It does not search ToolActivity, summaries, checkpoints, or metadata,
and does not provide FTS, tokenization, snippets, relevance ranking, or full
Unicode case folding. Results use `updated_at DESC, id DESC` order. The picker
shows at most the 50 most recently updated matches; when more exist, its prompt
asks the user to refine the query. Each search and selected-result revalidation
has a 5,000,000 SQLite VM-op scan budget and returns `result_too_large` when the
budget is exhausted. Protocol clients can instead continue matching pages with
the keyset cursor returned by `thread.search`.

`/export` accepts a Workspace-relative destination and optional `markdown` or
`json` format; Markdown is the default. The same Thread produces deterministic
bytes up to a 5 MiB output limit. Rendering runs away from the event loop.
Citations stay attached to their assistant entry: cited Markdown entries render
their own Sources section, and JSON assistant entries always expose a
`citations` list. Exports contain public conversation data only:
they never expose the internal workspace key or private entry metadata. The
destination passes the normal workspace identity and safe-write checks, and its
normalized path must be 1–1,000 characters before mutation begins. A created or
updated file records a ChangeSet and can be reverted with `/undo`; an unchanged
write records no ChangeSet. A failed attempt with no reconciled file evidence
does not publish an empty ChangeSet; if bytes landed, recovery retains their
actual evidence.

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

## Web subcommands

| Syntax | Result |
| --- | --- |
| `/web`, `/web status` | Show enabled/runtime availability, Tavily credential and explicit proxy presence, active Thread authorization, request budget, diagnostic code, and disclosure. |
| `/web on` | Require `TAVILY_API_KEY`, validate the explicit proxy, atomically persist `web.enabled: true`, and rebuild the runtime before reporting success. |
| `/web off` | Atomically persist `web.enabled: false`, rebuild without `web_search` or `web_fetch`, and clear every Thread network grant. |
| `/web revoke` | Clear the selected Thread's `network.read` grant without disabling Web. |

Enabling Web discloses that Search queries and requested Fetch URLs are sent to
Tavily under its
[Privacy Policy](https://www.tavily.com/privacy) and
[Platform Terms](https://www.tavily.com/terms). A failed apply rolls the user
configuration back; if safe recovery cannot be proven, later Web mutations are
fenced with `web_configuration_recovery_required`.

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
/web
/web status
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
[Protocol v4](protocol.md).
