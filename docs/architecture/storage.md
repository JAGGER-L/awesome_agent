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

The Application database has one current schema bootstrap. A database whose
`PRAGMA user_version` differs from the current schema is rejected; no historical
data adapter is present. Tests always create isolated current state instead of
depending on developer data.

The current Application schema is version 2. During source development, a
schema mismatch is resolved by stopping Awesome and removing the disposable
repository-local `.awesome-dev` directory before running `uv run awesome-dev`
again. Awesome intentionally does not migrate or reinterpret Schema v1 test
data.
