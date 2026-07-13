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

Token deltas, spinner state, raw provider payloads, unbounded shell output, and
credentials are not product history. Tool activity stores bounded summaries;
tool observations required for recovery remain in the unfinished Turn's
LangGraph checkpoint.

The Application database has one current schema bootstrap. This development
line does not carry historical schema migrations or data adapters: a database
whose `PRAGMA user_version` differs from the current schema is rejected. Tests
always create isolated current state instead of depending on developer data.
