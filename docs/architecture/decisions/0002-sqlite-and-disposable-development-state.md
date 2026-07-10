# ADR 0002: SQLite and Disposable Development State

- Status: Accepted
- Date: 2026-07-10
- Scope: Persistence and migration

## Context

PostgreSQL, SQLAlchemy service adapters, migrations, and distributed runtime
records impose setup and maintenance costs that do not serve a local,
single-user coding agent. Existing records were created during development and
testing and have no preservation value.

## Decision

The target default persistence is SQLite plus ordinary files under the
resolved `AWESOME_HOME`.

Application state and LangGraph checkpoints have separate ownership. The
application persists bounded product records; LangGraph persists graph state
through its native SQLite checkpointer. Workspace files remain the primary
user-visible state.

All existing user, conversation, run, checkpoint, approval, artifact, memory,
and tool-execution data is considered disposable for this rewrite.

The migration will not provide:

- PostgreSQL-to-SQLite data import;
- dual reads or dual writes;
- compatibility adapters for old storage formats;
- preservation of existing development databases or state directories.

## Consequences

- A cutover starts with a fresh SQLite schema and fresh built-in memory files.
- Tests always create isolated fresh state.
- New target schemas still require normal forward schema versioning after they
  become product data.
- PostgreSQL adapters and migrations can be deleted when no target path
  references them.

## Rejected Alternatives

- Keep PostgreSQL as the local default: harms installation and first-run
  reliability.
- Store everything as JSON: simple initially, but weak for concurrent reads,
  indexes, atomic lifecycle updates, and schema evolution.
- Preserve old development data: creates code and test burden without user
  value.
