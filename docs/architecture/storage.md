# Storage

Awesome stores product state under the resolved `AWESOME_HOME`; no separate
database service is required.

## Ownership

- `state/application.db`: workspace trust, Threads, transcript entries, Turns,
  summaries, bounded tool activity, ChangeSet metadata, pending mutations, and
  MCP enablement.
- `state/checkpoints.db`: LangGraph-owned checkpoints through its native SQLite
  saver.
- `state/change-journal/`: content-addressed before/after blobs used by diff,
  undo, and redo.
- `memory/USER.md` and `workspaces/<workspace_key>/MEMORY.md`: optional local
  memory documents.
- `config.yaml`, `.env`, `skills/`, and `ui.json`: user configuration, secrets,
  extensions, and presentation preferences.

Application and checkpoint databases remain separate because product lifecycle
and graph channels have different owners. Workspace files remain the primary
user-visible state.

## Durability boundary

Thread rows persist both the title and its `automatic` or `manual` provenance.
The first accepted message uses one transaction to update the automatic title,
append the user Entry, and create the Turn. A failure in any write rolls back
all three facts. Later model failure or cancellation does not undo an already
accepted first message or its title.

Token deltas, spinner state, raw provider payloads, unbounded shell output, and
credentials are not product history. Tool activity stores bounded summaries;
tool observations required for recovery remain in the unfinished Turn's
LangGraph checkpoint.

The Application database has one current bootstrap format: Schema 7. Schema
identity is independent from the Awesome product version and changes only when
persisted structure, interpretation, or cross-record invariants change. Schema
identities increase monotonically and are never reused.

Startup opens an existing Application database read-only before workspace
trust, checkpoints, Change Journal state, or writable database configuration.
It classifies the database as new, current, older, newer, unknown, or
unavailable:

- new and current state continue normally;
- older state opens one explicit reset-or-exit interaction;
- newer state asks the user to upgrade Awesome and never offers reset;
- unknown, corrupt, unreadable, or locked state stops with a diagnostic and is
  never silently deleted.

Awesome does not include historical adapters or a generic migration framework.
The current recovery flow is deliberately destructive only after confirmation:

```text
Ink startup prompt
    -> interaction.respond(reset_state)
    -> Application bootstrap
    -> exclusive state lease
    -> Storage atomic replacement
    -> fresh Schema 7
    -> workspace trust
```

Storage validates that the reset boundary is exactly `<AWESOME_HOME>/state`,
renames it atomically, creates and validates fresh state, and restores the
original directory if initialization fails. Normal Awesome processes retain a
shared state lease; reset requires exclusive ownership, so it cannot race an
active session.

Reset removes conversations, Threads, trust, checkpoints, and Change Journal
history. It preserves `config.yaml`, `.env`, `skills/`, `memory/`,
`workspaces/`, `ui.json`, and every workspace file because those paths are
outside the state boundary. Successful recovery continues to workspace trust
in the same Core process.
