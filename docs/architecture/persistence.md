# Persistence

All product state is embedded under resolved `AWESOME_HOME`; no service is
required.

## Ownership

- `state/application.db`: workspace trust, Threads, transcript entries, Turns,
  summaries, bounded tool activity, ChangeSet metadata, pending mutations, and
  MCP enablement.
- `state/checkpoints.db`: LangGraph-owned checkpoints, accessed through its
  native SQLite saver.
- `state/change-journal/`: content-addressed before/after blobs required for
  safe diff/undo/redo.
- `memory/USER.md` and `workspaces/<workspace_key>/MEMORY.md`: optional local
  memory documents.
- `config.yaml`, `.env`, `skills/`, and `ui.json`: user configuration,
  extensions, secrets, and presentation preferences.

SQLite transactions protect bounded product records. The two databases remain
separate because product lifecycle and graph checkpoints have different schema
owners. Workspace files remain the primary user-visible state.

## Deliberately not stored

Token deltas, spinner state, every rendered event, raw Provider payloads,
unbounded shell output, a second graph-state copy, distributed coordination
records, and credentials are not durable product records. Tool activity stores
bounded summaries, while tool observations needed by graph recovery stay in
LangGraph state.

The Phase 4 cutover treats earlier development data as disposable and provides
no data importer or compatibility adapter. Future schema evolution begins from
the V1 embedded schemas.
