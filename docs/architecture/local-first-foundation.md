# Local-first Foundation Detailed Design

> Status: Accepted Phase 1 design
>
> Decision date: 2026-07-10
>
> Implementation status: PR1 is complete. PR2 through PR6 remain pending.

## Purpose

Phase 1 establishes the local application foundations that Agent Core and all
future surfaces consume. It does not implement a reasoning loop, model calls,
Ink, an HTTP API, or a compatibility layer for the current platform runtime.

The phase is complete only when a headless Python application can establish
workspace trust, execute the eight fixed tools, capture controlled file
changes, perform safe diff/undo/redo, emit ordered live events, cancel work,
and reopen local state without PostgreSQL, Worker, HTTP, or Docker.

## Permanent Target Packages

```text
awesome_agent/
├─ core/
│  ├─ workspace/     # canonical identity, trust models, and policy
│  ├─ tools/         # target contracts, registry, executor, and eight tools
│  ├─ changes/       # ChangeSet, diff, undo, and redo
│  ├─ events.py      # typed event envelope and EventSink
│  └─ contracts.py   # only shared opaque identifiers
├─ application/
│  ├─ commands.py    # application command intents and dispatch
│  └─ headless.py    # local composition and Phase 1 acceptance path
└─ storage/
   ├─ database.py    # application SQLite lifecycle and schema versions
   ├─ trust.py       # workspace trust SQLite adapter
   ├─ changes.py     # ChangeSet SQLite adapter
   └─ checkpoints.py # native LangGraph SQLite adapter
```

These are permanent names, not `v2`, `next`, or compatibility namespaces.
Phase 2 extends `core` and `application`; Phase 4 deletes the superseded
modules without renaming this target path.

Dependency direction is fixed:

```text
application -> core
application -> storage
storage     -> core protocols and models
core        -> Python and narrow third-party data-model libraries only
```

Target packages must not import the current `runtime`, `persistence`, `tools`,
`surfaces`, `api`, approval, Artifact, Worker, team, or Docker-service paths.
There is no repository base class, ORM, dependency-injection container,
command bus, event bus, or general recovery framework.

## PR1: SQLite Checkpoint Foundation — Complete

PR1 established separate `application.db` and `checkpoints.db` paths and a
narrow adapter around LangGraph's official asynchronous SQLite checkpointer.
The application does not copy or interpret LangGraph checkpoint tables.

## PR2: Application SQLite and Workspace Trust

### Application database

Use Python's standard `sqlite3` module. The application database boundary owns
parent-directory creation, WAL mode, foreign keys, a busy timeout, explicit
transactions, and ordered schema versions through `PRAGMA user_version`.
It rejects a schema newer than the running application.

PR2 creates only the trust schema. Thread, message, and ChangeSet tables are
added by the PR that owns each capability. No complete future schema is
reserved in advance.

### Workspace identity

The startup directory itself is the workspace; discovery must not silently
promote it to a Git root. The directory must exist and be a directory.
Identity resolves symlinks strictly and normalizes host case behavior. A
symlink and its real directory share one identity. A moved directory is a new
identity and requires trust again.

The identity contains only a canonical key, canonical path, and display path.
It does not contain a repository ID, Git remote, inode, or device-specific
identity.

### Trust

Persist only accepted trust with the canonical key, canonical path, and
acceptance timestamp. `accept` writes the record, `status` returns `trusted` or
`unknown`, and `revoke` deletes the record. Declining does not persist a denial;
it ends the current launch and the next launch remains unknown.

An unknown workspace cannot load project configuration, instructions, skills,
MCP declarations, or tools. Invalid and inaccessible workspaces fail before
any project content is read.

## PR3: Foundation Contracts

PR3 defines JSON-round-trippable Pydantic models and narrow internal
`Protocol` boundaries. It does not access SQLite, execute tools, or start a
runtime.

### Events

The versioned event envelope contains session identity, optional turn
identity, monotonically increasing sequence, UTC timestamp, event type, and a
typed payload. Phase 1 event types are interaction required, tool started,
tool result, workspace changed, operation completed, operation failed, and
operation cancelled.

An `EventSink` accepts events and a session-scoped emitter assigns sequence.
Events are live projections. There is no persistence, replay, subscriber
registry, queue, or event store.

### Tools

A tool request contains call identity, stable tool name, and JSON-safe
arguments. A result contains call identity, tool name, success or error status,
bounded model-facing content, typed common metadata, and an optional
structured error.

Expected error categories are invalid arguments, not found, workspace escape,
permission denied, conflict, timeout, and execution failed. Cancellation is
control flow rather than a normal tool observation.

### Changes

A ChangeSet contains identity, session and optional turn identity, workspace
identity, `open | applied | undone` lifecycle, `full | partial | none`
reversibility, ordered controlled file changes, unmanaged execution
observations, and timestamps. Conflict is an undo/redo result, not a persisted
lifecycle state.

Controlled file entries contain a relative path, create/update/delete kind,
before and after hashes, and internal backup references. Unmanaged execute
observations never invent a before-image.

### Commands

PR3 freezes command name and ownership metadata for application, skill-backed,
and Ink-local commands. It defines command intent and result envelopes. Handler
registration and argument validation belong to PR6.

## PR4: Tool Kernel and Read-only Tools

The registry is a small stable-name mapping with duplicate rejection,
deterministic listing, and provider-neutral JSON Schema export. It is not a
dependency-injection or plugin framework.

Every tool call passes through one executor boundary:

```text
lookup -> trusted workspace -> argument validation -> timeout/cancellation
       -> handler -> redaction/bounds -> normalized result -> ordered events
```

Tool arguments use workspace-relative paths. Absolute paths, traversal,
prefix collisions, workspace escapes, and unsafe symlink resolution fail
closed. Directory symlinks are not followed, `.git` is not traversed, and
sensitive or binary content is not exposed.

PR4 implements:

- `ls`: deterministic direct-child listing with an entry bound;
- `read_file`: bounded UTF-8 line ranges with total-line metadata;
- `glob`: deterministic bounded workspace-relative matches;
- `grep`: bounded regex or text matches with path and line metadata.

Expected failures are recoverable results. Unknown exceptions are executor
invariant failures and never expose tracebacks as model content.

## PR5: Modifying Tools and Change Journal

### Controlled tools

- `write_file` creates or atomically replaces one bounded text file.
- `edit_file` performs exact old/new replacement and rejects missing or
  ambiguous text unless `replace_all` is explicit.
- `delete` removes a file or directory recursively without following symlinks.
  It cannot delete the workspace root, `.git`, or sensitive paths.

All safety, size, file-count, and journal-capacity checks complete before a
workspace mutation. A recursive delete that cannot be backed up within bounds
is rejected rather than downgraded to an irreversible controlled operation.

### Journal storage and crash safety

SQLite stores metadata. Before and after content lives under
`AWESOME_HOME/state/change-journal/` as SHA-256-addressed blobs. Source content
is not stored as large SQLite rows and is never written back into the project
as journal metadata.

Each controlled mutation uses a small pending protocol: persist preimage and a
pending record, atomically mutate the workspace file, persist postimage, then
finalize. Startup reconciles only incomplete journal mutations; this is not a
general recovery engine.

Undo first verifies every current hash against the recorded after hash. Redo
verifies every current hash against the before hash. Any conflict prevents the
operation from starting, leaves the ChangeSet lifecycle unchanged, and never
overwrites later user edits.

### Host execution

`execute` runs through the local host shell with a workspace-contained working
directory, bounded timeout and output, process-tree cancellation, output
redaction, and a sanitized child environment. Provider API keys, tokens, and
secret-like variables are not inherited by default.

Host execution is not a sandbox and cannot guarantee filesystem containment.
Privilege elevation, host shutdown or reboot, disk formatting or partitioning,
and destructive commands explicitly targeting a filesystem root are hard
denials. A detected path boundary crossing becomes `interaction_required` in
PR6 and is denied before that interaction path exists. The policy does not
claim that shell text inspection can detect every indirect side effect or
external path access, and it is not described as OS isolation.

ChangeSet reversibility is `full` for controlled file tools, `partial` when a
turn also invokes `execute`, and `none` when it contains only unmanaged shell
effects. Partial undo restores only controlled changes and reports what it did
not restore; none is not undoable. Phase 1 does not take a full workspace
snapshot to pretend otherwise.

## PR6: Commands, Events, and the Headless Slice

A permanent `LocalApplication` composes trust, tools, changes, commands,
cancellation, storage, and event emission. It serializes one active operation
per session with an in-process lock and task reference. It is not a durable Run
or Worker runtime.

PR6 implements handlers only for capabilities that exist in Phase 1:

- `/workspace`
- `/tools`
- `/diff`
- `/undo`
- `/redo`
- `/status`
- `/doctor`

Other accepted command names remain metadata until their real Phase 2 or Phase
3 capability exists; the product does not return fabricated stub data.
`!command` routes through the same `execute` tool path. `@path` ownership is
frozen, but actual context injection belongs to Phase 2.

Workspace trust interactions use `trust | deny`: trust persists and deny ends
the current launch without a denial record. Exceptional execute boundary
interactions use `allow_once | deny`, remain in memory for one operation, and
never create an approval table or reusable grant.

Events are forwarded live. Restart reconstructs truth from trust records,
ChangeSet metadata and blobs, and current workspace files. Event sequence
restarts with a new session and no replay is required.

## Validation and Phase Exit

Every PR runs target formatting, lint, affected strict type checking, targeted
unit and structural tests, and only the local integration tests required by
its boundary. All tests use a fresh temporary `AWESOME_HOME` and workspace.

Phase 1 closes only when a headless test proves this sequence:

```text
start -> trust required -> trust -> read -> controlled modifications
      -> execute -> diff -> undo -> redo -> cancel a long execute
      -> close -> reopen -> reconstruct trust and ChangeSet state
```

The acceptance path must prove that project content cannot load before trust,
event sequence and terminal semantics are deterministic, later user edits are
never overwritten, partial reversibility is explicit, and no PostgreSQL,
Worker, HTTP server, model, Docker service, or legacy runtime is contacted.

Each PR begins from and merges back into `codex/local-first-architecture`.
Nothing in Phase 1 merges directly into `main`.
