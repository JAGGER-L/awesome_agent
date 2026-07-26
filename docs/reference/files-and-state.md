# Files and state reference

Awesome separates user-owned configuration, replaceable runtime state,
workspace-owned inputs, and installed program files. That separation determines
what may be backed up, reset, trusted, or upgraded independently.

## Root locations

| Platform | Default `AWESOME_HOME` | Default install directory |
| --- | --- | --- |
| Windows | `%LOCALAPPDATA%\Awesome` | `%LOCALAPPDATA%\Programs\Awesome` |
| macOS/Linux | `~/.awesome` | `~/.local/share/awesome` |

`AWESOME_HOME` overrides the user-data root. The official installers and their
generated launchers currently use the fixed installation roots in the table;
`AWESOME_INSTALL_DIR` is read only into an otherwise unused low-level
`AwesomePaths.install_dir` field and does not relocate or discover a release
installation. If Windows has no `LOCALAPPDATA`, Awesome's path resolver falls
back to `~/AppData/Local`. Paths below use `<HOME>` for the resolved Awesome
home, not the operating-system home.

```text
<HOME>/
├── .env
├── .env.lock
├── .provider-credential-transaction.json
├── .provider-credential-transaction.env
├── .state.lock
├── .config.yaml.lock
├── config.yaml
├── ui.json
├── skills/
├── memory/
│   ├── .USER.md.lock
│   └── USER.md
├── workspaces/
│   └── <workspace_key>/
│       ├── .MEMORY.md.lock
│       └── MEMORY.md
├── state/
│   ├── application.db
│   ├── checkpoints.db
│   ├── provider-model-transaction.json
│   └── change-journal/
│       └── blobs/
├── .workspace-leases/
│   └── <workspace_key>/.state.lock
└── .workspace-entity-leases/
    └── <entity_key>/.state.lock
```

Directories and files are created lazily. Their absence is often the normal
default, not corruption.

## User-owned files

### `<HOME>/config.yaml`

Strict user configuration schema version `1`: Provider defaults, credential
source selection, budgets, Memory switches, disabled Skills, and user MCP
declarations. It contains no secret values. See
[configuration](configuration.md).

### `<HOME>/.env`

The Awesome-managed credential store for `DEEPSEEK_API_KEY`,
`MOONSHOT_API_KEY`, and `MEM0_API_KEY`. `/auth` writes through a same-directory
temporary file, flushes it, and atomically replaces the destination. On POSIX,
Awesome creates the directory for owner-only access and the file with owner
read/write mode.

This is not a general dotenv contract. Arbitrary entries are not treated as
configuration, and values are not automatically forwarded to MCP servers.
Never commit or copy this file into a workspace.

### `<HOME>/ui.json`

Ink-owned UI preferences. Current schema:

```json
{
  "schema_version": 1,
  "theme": "system"
}
```

`theme` is `system`, `dark`, or `light`. Missing state defaults silently to
`system`; unreadable or invalid state reports a warning and also falls back to
`system`. `/theme` writes atomically through a temporary sibling file. Core does
not own this document.

### `<HOME>/skills/`

User Skill packages, normally `<HOME>/skills/<name>/SKILL.md` plus optional
resources. User Skills are local trusted input and retain existing link
behavior; the stricter no-reparse package rule applies to Workspace Skills.
See [Skills](../extensions/skills.md).

### Local Memory files

`<HOME>/memory/USER.md` stores user-scoped facts. A workspace-scoped document
lives at `<HOME>/workspaces/<workspace_key>/MEMORY.md`; it is intentionally
outside the repository so a remembered fact cannot become a commit by accident.
Both are bounded managed Markdown documents with stable entry IDs and a content
hash. See [Memory](../extensions/memory.md).

### User-state mutation locks

Core serializes read-modify-write transactions for `config.yaml`, `.env`,
`USER.md`, and each Workspace `MEMORY.md` across threads and processes. It uses
a persistent one-byte sibling named `.<resource>.lock`; an already-hidden
resource such as `.env` uses `.env.lock`, not a second leading dot. Waiting for
these locks is bounded and runs outside the event-loop thread, so another
process cannot freeze foreground cancellation or status rendering. A cancelled
mutation finishes its already-started filesystem transaction within a bounded
cleanup window before cancellation is reported; a worker that misses that
window cannot later publish an in-memory state commit. Its filesystem outcome
must be treated as uncertain until a later process reloads the durable files.

A lock wait that reaches its deadline is reported as retryable
`operation_busy` for commands and credential RPCs, or retryable `timeout` for a
Memory tool call. An unsafe or unavailable sidecar/platform lock has two typed
envelopes: an Application command or RPC returns retryable
`state_unavailable` with bounded `state_directory` metadata, while a Memory
tool returns a non-retryable `state_unavailable` `ToolOutput`. These errors are
fixed and sanitized; they never expose the sidecar path or the operating-system
exception.

These sidecars are coordination artifacts, not configuration or Memory
content. Do not edit or delete them while an Awesome process may be running.
Their absence before the first mutation is normal; Core creates them lazily and
rejects a sidecar that is a link, reparse point, non-regular file, or whose
opened identity does not match its path.

## Workspace-owned files

The active working directory is resolved to a canonical directory and bound to
its filesystem identity. Its opaque key is `ws_` plus 32 hexadecimal characters
derived from the normalized canonical path. The key avoids placing raw paths in
secondary storage names; the separately captured root identity detects a path
that is replaced during a session.

Awesome recognizes these repository-controlled inputs:

```text
<workspace>/
├── AGENTS.md
└── .awesome/
    ├── config.yaml
    └── skills/
        └── <name>/
            ├── SKILL.md
            └── ... resources
```

None is opened before workspace trust. `AGENTS.md` is a bounded immutable
session snapshot, and Workspace Skills receive component-by-component anti-link
and identity checks. Changes on disk during a running session are not a
supported hot-reload mechanism. This does not make every Skill resource a
discovery-time snapshot: safe resource replacements completed before a lazy
read can be observed, while pinned package/`SKILL.md` replacement and unsafe
resource traversal fail closed.

`.awesome/config.yaml` is schema-validated after trust, but its current file
read is not identity-pinned or size-bounded and may follow a link/reparse point.
That is a known security-hardening gap, not the same guarantee as `AGENTS.md` or
Workspace Skill loading. Treat a trusted workspace configuration as privileged
input and see the [configuration reference](configuration.md#workspace-configuration).

## Application database

`<HOME>/state/application.db` is the authoritative embedded Application SQLite
database. Current `PRAGMA user_version` is **7**. Connections enable foreign
keys, a five-second busy timeout, WAL journal mode, and normal synchronous mode.

Its logical ownership is:

| Records | Purpose |
| --- | --- |
| `trusted_workspaces` | Accepted workspace key, canonical path, and trust time |
| `threads` | Workspace association, title/source, selected model, Thinking and Skill mode |
| `thread_entries` | Durable user messages, assistant messages, and direct commands in sequence |
| `turns` | Turn lifecycle, immutable execution choices/budgets, usage, context manifest, and checkpoint key |
| `thread_summaries` | Bounded conversation summary and covered sequence/count |
| `tool_activities` | One terminal audit row per operation/call, without raw argument/result bodies |
| `change_sets` | Change lifecycle, reversibility, summaries, and ownership |
| `pending_mutations` | Write-ahead mutation intent used for reconciliation |
| `mcp_enablements` | Workspace server approval bound to its configuration hash |

Slash Commands are control input, not model conversation entries. Direct
commands are durable transcript entries but have no Turn ID. Tool activities
have a unique `(operation_id, call_id)` boundary so completion cannot be
silently duplicated.

Do not edit this database manually. Row invariants, foreign keys, the Checkpoint
store, and Change Journal blobs form one recovery contract even though they use
separate files.

## Provider model transaction journal

`<HOME>/state/provider-model-transaction.json` closes the atomicity gap between
the default model in `config.yaml` and the selected model on a Thread in
`application.db`. Those resources cannot participate in one database
transaction. A model change therefore writes a durable `prepared` record with
the previous and target model identities, replaces and reloads configuration,
updates the Thread, verifies both resources, changes the record to `committed`,
and only then removes it.

Startup rolls a `prepared` record back to its previous values and rolls a
`committed` record forward to its target values. Reconciliation is idempotent
and clears the journal only after both sides verify. A malformed or
unreconcilable journal fails activation with `recovery_required`. If the same
condition is detected at runtime, new operations and state mutations are
fenced; snapshot reads, cancellation, and shutdown remain available.

The journal is strict, bounded UTF-8 JSON and never contains credentials. Core
rejects a linked/reparse parent, a symlink/reparse file, a hard-linked or
non-regular file, an identity change while opening, duplicate keys, non-finite
JSON values, and content over 4 KiB. Do not edit or delete this file: its
presence is recovery intent, not disposable cache state.

## Provider credential transaction files

`/auth` may need to change both the full `<HOME>/.env` document and the selected
credential source in `<HOME>/config.yaml`. They cannot share one filesystem
commit, so Core coordinates them with two hidden files at the `<HOME>` root:

- `.provider-credential-transaction.json` is a strict, non-secret journal with
  the service, action, phase, source choices, and whole-file hashes;
- `.provider-credential-transaction.env` is an exact byte-for-byte backup of
  the previous `.env`, including comments and unrelated entries.

The backup is staged before `PREPARED` is published. Startup reconciles both
`PREPARED` and `SECRET_COMMITTED` by restoring the complete previous `.env` and
previous source; `COMMITTED` is rolled forward to the target source only after
the target `.env` hash matches. The files are removed only after both durable
facts verify. Reconciliation runs before the first real configuration load,
state preflight/reset, or workspace-trust handling, so a half-written secret
cannot influence startup.

The JSON file is capped at 4 KiB and never stores a credential. The backup is
capped at 1 MiB, contains secrets, and is owner-readable/writable only on
POSIX. Both reject symlinks, reparse points, hard links, non-regular files, and
identity changes while opening. `.env` is likewise a bounded strict UTF-8
input; NUL bytes and unsafe file identities fail closed. Do not delete either
transaction file manually. An invalid or inconsistent record produces
`recovery_required` rather than guessing which write succeeded.

## LangGraph checkpoints

`<HOME>/state/checkpoints.db` is owned by the LangGraph SQLite saver. A Turn ID
is also its checkpoint key. On recovery, Awesome projects the latest checkpoint
onto a closed `AgentState` channel set, permits only LangGraph's internal
`branch:to:` channels beyond it, then validates Thread, Turn, workspace,
Provider, model, budgets, continuation, tool progress, usage, and termination
fields.

Keeping checkpoints separate from Application tables isolates third-party saver
layout from the product's schema, while the Turn record provides the join. A
missing or corrupt checkpoint never causes Awesome to invent continuation
state; it produces a recovery error/decision.

## Change Journal blobs

`<HOME>/state/change-journal/blobs/<first-two-hex>/<sha256>` stores
content-addressed before/after bytes required for diff, undo, redo, and crash
reconciliation. Writes are temporary-file-plus-replace, and reads recompute the
digest before returning content. Metadata and pending intent live in
`application.db`; neither half is independently sufficient for complete undo
history.

Each ChangeSet is bounded to 1,000 nodes and 50 MiB. Shell execution is recorded
as an irreversible observation rather than a fictional filesystem snapshot.
See the [changes guide](../user-guide/changes.md).

## Leases and multiple sessions

Awesome uses non-blocking filesystem locks on a one-byte `.state.lock` file:

- `<HOME>/.state.lock` is shared by ordinary sessions and exclusive while
  initializing/resetting Application state;
- `<HOME>/.workspace-leases/<workspace_key>/.state.lock` prevents two live
  runtimes from owning the same canonical workspace path;
- `<HOME>/.workspace-entity-leases/<entity_key>/.state.lock` also binds the
  underlying directory identity, covering path aliases and replacement races.

Both workspace leases must be held. If the second acquisition fails, the first
is released. A competing process receives `operation_busy` rather than running
recovery or mutations concurrently. These lock directories are coordination
artifacts, not user configuration; do not delete them while Awesome is running.

## Schema compatibility and reset

Awesome performs a read-only preflight before normal database access:

| Observed state | Behavior |
| --- | --- |
| No database, or empty SQLite with version 0 | Initialize schema 7 under an exclusive lease, then downgrade to shared ownership. |
| Schema 7 | Open normally. |
| Schema 1–6 | Ask the user to reset local state; no automatic migration. |
| Schema greater than 7 | Refuse with `state_created_by_newer_version`. |
| Non-empty version 0, invalid SQLite, or unknown format | Refuse as unknown/unavailable state. |

The project deliberately has no in-place database migration layer in this
release. Reset is explicit because silently interpreting old recovery data can
be more dangerous than losing local conversation history.

After confirmation, reset validates that the exact `<HOME>/state` boundary is
not a symlink, renames it to a same-parent staging directory, initializes a new
`application.db`, then removes staging. Initialization failure restores the old
directory. Cleanup failure also attempts restoration and reports a bounded
diagnostic.

Reset removes:

- conversations, Threads, summaries, and usage;
- workspace trust and workspace MCP enablements;
- checkpoints;
- ChangeSets, undo/redo history, and blobs.

Reset keeps everything outside `<HOME>/state`: `config.yaml`, `.env`, the
Provider credential transaction journal and backup, `ui.json`, User Skills,
Local Memory documents/settings, and the installed release. Keeping the
credential recovery evidence outside the resettable namespace prevents a state
reset from erasing an unresolved cross-file transaction. Cloud Memory records
already stored by Mem0 are external and are not deleted by a local reset.

## Backup and restore

For a consistent offline backup:

1. exit every Awesome session using that `AWESOME_HOME`;
2. copy the entire `<HOME>` directory, including hidden files and the complete
   `state` directory;
3. record the Awesome product version used to create it;
4. protect the backup as a secret because it contains `.env`.

Copying only `application.db` can omit WAL content, checkpoints, or Change
Journal blobs and is not a supported consistent backup. For the same reason,
restore the whole stopped-state snapshot as a unit and use a product version
that accepts its Application schema. If only preferences or Skills are needed,
copy those user-owned files separately and deliberately exclude `.env`.
