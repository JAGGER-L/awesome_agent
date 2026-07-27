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
├── .skills.lock
├── .skills-transaction.json
├── config.yaml
├── ui.json
├── logs/
│   ├── .application.jsonl.lock
│   ├── application.jsonl
│   ├── application.jsonl.1
│   ├── application.jsonl.2
│   ├── application.jsonl.3
│   └── application.jsonl.4
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
│   ├── application.db.pre-migration.bak
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

## Application invocation logs

`<HOME>/logs/application.jsonl` is the current process/session-owned structured
diagnostic log. It is outside `WorkspaceRuntime`, Application database state,
and Thread history. Awesome retains at most the current file plus
`application.jsonl.1` through `.4`; each file is capped at 5 MiB.
`<HOME>/logs/.application.jsonl.lock` coordinates writers and is not one of
those five data files.

Every JSON line uses closed record version `1`: `version`, `timestamp`,
`session_id`, `correlation_id`, `operation`, `outcome`, `duration_ms`, and
optional `error_code` and bounded `usage`. Prompts, model or Tool bodies,
queries, URLs, paths, secrets, and arbitrary request/result payloads are never
logged. Writing is nonblocking and fail-open, so missing records can indicate a
full queue or local logging failure and do not change the Application result.
An invocation outcome describes the facade request only; it is not the later
terminal outcome of asynchronously admitted Agent work.

## User-owned files

### `<HOME>/config.yaml`

Strict user configuration schema version `2`: Provider defaults, credential
source selection, budgets, Web settings, Memory switches, disabled Skills, and
user MCP declarations. Version `1` remains readable and is atomically upgraded
by the first supported write. The document contains no secret values. See
[configuration](configuration.md).

### `<HOME>/.env`

The Awesome-managed credential store for `DEEPSEEK_API_KEY`,
`MOONSHOT_API_KEY`, `MEM0_API_KEY`, and optionally
`AWESOME_WEB_PROXY_URL`. `/auth` manages only the first three entries; supported
writers use a same-directory temporary file, flush it, and atomically replace
the destination. On POSIX, Awesome creates the directory for owner-only access
and the file with owner read/write mode.

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
resources. Bundled and User Skills use the same pinned package and `SKILL.md`
identity requirement; Workspace Skills additionally pin the complete trusted
workspace chain. Resource traversal for every source rejects escape, links,
junctions, and other reparse components.

`awesome skills install` validates a complete local directory or ZIP before it
publishes a User package. Local source traversal and cleanup of installed or
quarantined packages reject crossing a filesystem or mount boundary, including
POSIX mount and bind boundaries; Windows volume-mount traversal is covered by
the existing reparse-point rejection.

`<HOME>/.skills.lock` serializes list and mutation operations across processes.
`<HOME>/.skills-transaction.json` records an in-progress install, replacement,
or removal. A fresh install publishes its validated stage to an absent target
with one same-directory no-replace atomic rename. Replace is not one atomic
replacement: it records `prepared`, renames target to quarantine, records
`quarantined`, renames stage to target, and records `published` before cleanup.
Remove records the same phases around target-to-quarantine and deletes the
quarantine only after publication. Recovery rolls back replace/remove before
publication and rolls forward quarantine cleanup after publication.

Private `.skill-stage-*` and `.skill-quarantine-*` directories may exist under
`skills/` while such a transaction is running or awaiting recovery. Do not edit
or delete those artifacts while Awesome may be active. See
[Skills](../extensions/skills.md).

### Local Memory files

`<HOME>/memory/USER.md` stores user-scoped facts. A workspace-scoped document
lives at `<HOME>/workspaces/<workspace_key>/MEMORY.md`; it is intentionally
outside the repository so a remembered fact cannot become a commit by accident.
Both are bounded managed Markdown documents with stable entry IDs and a content
hash. See [Memory](../extensions/memory.md).

### User-state mutation locks

Core serializes read-modify-write transactions for `config.yaml`, `.env`,
`USER.md`, and each Workspace `MEMORY.md` across threads and processes. It uses
a persistent one-byte sibling named
`.<resource>.lock`; an already-hidden resource such as `.env` uses `.env.lock`,
not a second leading dot. Waiting for these locks is bounded and runs outside
the event-loop thread, so another process cannot freeze foreground cancellation
or status rendering. A cancelled mutation finishes its already-started
filesystem transaction within a bounded cleanup window before cancellation is
reported; a worker that misses that window cannot later publish an in-memory
state commit. Its filesystem outcome must be treated as uncertain until a later
process reloads the durable files.

A lock wait that reaches its deadline is reported as retryable
`operation_busy` for commands and credential RPCs, or retryable `timeout` for a
Memory tool call. An unsafe or unavailable sidecar/platform lock has two typed
envelopes: an Application command or RPC returns retryable
`state_unavailable` with bounded `state_directory` metadata, while a Memory
tool returns a non-retryable `state_unavailable` `ToolOutput`. These errors are
fixed and sanitized; they never expose the sidecar path or the operating-system
exception.

Skill package operations use their separate `.skills.lock`; lock waiting is
bounded and runs off the event-loop thread. Source size, entry count, and file
reads are also bounded. Once the owned package worker starts, however,
cancellation-safe convergence has no wall-clock cleanup deadline: if the caller
is cancelled, Core continues awaiting that worker until the transaction reaches
a recoverable terminal state, then re-raises the original cancellation. It does
not detach a worker whose later filesystem outcome would be unknown.

For Skill package RPCs, an unavailable or contended `.skills.lock` is a
retryable `operation_busy`; a package transaction that cannot complete safely
—including installed or quarantined cleanup that detects a boundary crossing—
is retryable `state_unavailable`. Source validation, source boundary-crossing,
size, existing-name, and missing-name failures are non-retryable
`invalid_arguments` with bounded diagnostic codes.

These lock files and package transaction markers are coordination artifacts,
not configuration, Memory, or Skill package content. Do not edit or delete them
while an Awesome process may be running. Their absence before the first
mutation is normal; Core creates them lazily and rejects a sidecar that is a
link, reparse point, non-regular file, or whose opened identity does not match
its path.

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
database. Current `PRAGMA user_version` is **8**. One process-level bounded FIFO
worker owns its long-lived connection. The connection enables foreign keys, a
five-second busy timeout, WAL journal mode, and normal synchronous mode.
Application-facing repositories expose async methods: reads use deferred
transactions and writes use `BEGIN IMMEDIATE`. A cancelled read may stop
waiting; admitted durable writes and lifecycle operations wait for a known
COMMIT, ROLLBACK, or close result before re-raising the first cancellation.
SQLite connections, cursors, and rows never cross the worker boundary.

Its logical ownership is:

| Records | Purpose |
| --- | --- |
| `trusted_workspaces` | Accepted workspace key, canonical path, and trust time |
| `threads` | Workspace association, title/source, selected model, Thinking and Skill mode, and optional immediate-parent fork/retry lineage |
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

Conversation search reads Thread titles and `thread_entries.content` inside the
active Workspace. It includes durable user, assistant, and direct-command
entries but excludes ToolActivity, summaries, checkpoints, and metadata. The
first implementation is a literal `LOWER`/substring SQLite query, not an FTS
index. Pages are stable in `updated_at DESC, id DESC` order, and cursor scope is
hash-bound to the Workspace and normalized query without publishing the
workspace key inside the cursor. Each page query and exact-result revalidation
has a 5,000,000 SQLite VM-op budget; exhaustion is surfaced as
`result_too_large`.

Do not edit this database manually. Row invariants, foreign keys, the Checkpoint
store, and Change Journal blobs form one recovery contract even though they use
separate files.

## Provider model transaction journal

`<HOME>/state/provider-model-transaction.json` closes the atomicity gap between
the default model in `config.yaml` and the selected model on a Thread in
`application.db`. Those resources cannot participate in one database
transaction. A model change therefore writes a durable `prepared` record with
one unique transaction identity plus the previous and target model identities,
replaces and reloads configuration, updates the Thread, verifies both resources,
changes the record to `committed`, and only then removes it. A failed callback
keeps its `prepared` evidence until SQLite has confirmed rollback and a fresh
transaction has re-verified both previous endpoints.

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

`/export` writes a deterministic public Thread projection to a
Workspace-relative Markdown or JSON file. Cited Markdown assistant entries keep
their own Sources section, while JSON assistant entries always carry a
`citations` list; workspace keys and internal entry metadata are excluded.
Output is capped at 5 MiB and rendering runs away from the event loop. The write
uses the shared identity-bound filesystem primitive; its normalized path must be
1–1,000 characters before mutation. Created and updated files produce Change
Journal evidence and support `/undo`; byte-identical exports are reported as
unchanged and produce no ChangeSet. A failed attempt with no reconciled evidence
publishes no empty ChangeSet, while recovery retains evidence for bytes that did
land.

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

## Schema compatibility, migration, and reset

Awesome performs a read-only preflight before normal database access:

| Observed state | Behavior |
| --- | --- |
| No database, or empty SQLite with version 0 | Initialize schema 8 under an exclusive lease, then downgrade to shared ownership. |
| Schema 8 | Open normally. |
| Schema 7 | Back up the database, then migrate it to schema 8 under an exclusive lease. |
| Schema 1–6 | Migration is unavailable; ask the user to reset local state or exit. |
| Schema greater than 8 | Refuse with `state_created_by_newer_version`. |
| Non-empty version 0, invalid SQLite, or unknown format | Refuse as unknown/unavailable state. |

The production migration registry has floor 7 and current 8. Its 7→8 step adds
the nullable Thread lineage field without rewriting existing conversation data.
Future supported upgrades must extend the adjacent linear chain. Startup first
performs a shared-lease preflight, acquires the exclusive state lease, rechecks
the schema, and creates `<HOME>/state/application.db.pre-migration.bak` with
SQLite's Backup API before applying the whole chain in one transaction. It
downgrades to shared ownership before initializing repositories.

The backup is independently reopened and checked before migration. A failed
step rolls back every schema and data change and leaves the backup available for
manual recovery. Startup never automatically restores that backup or resets
state. Newer, unknown, corrupt, unreadable, and locked states fail closed.

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

`application.db.pre-migration.bak` is a WAL-aware safety snapshot for manual
migration recovery, not a complete Awesome backup: it does not include the
checkpoint database, Change Journal blobs, configuration, or credentials.
