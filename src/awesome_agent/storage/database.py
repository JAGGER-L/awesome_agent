from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

APPLICATION_SCHEMA_VERSION = 2

_MIGRATION_1 = """
CREATE TABLE trusted_workspaces (
    workspace_key TEXT PRIMARY KEY,
    canonical_path TEXT NOT NULL,
    trusted_at TEXT NOT NULL
)
"""

_MIGRATION_2 = """
CREATE TABLE change_sets (
    change_set_id TEXT PRIMARY KEY,
    workspace_key TEXT NOT NULL,
    session_id TEXT NOT NULL,
    turn_id TEXT,
    lifecycle TEXT NOT NULL,
    reversibility TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    sealed_at TEXT
);
CREATE INDEX idx_change_sets_workspace_created
ON change_sets (workspace_key, created_at DESC);

CREATE TABLE pending_mutations (
    pending_id TEXT PRIMARY KEY,
    change_set_id TEXT NOT NULL REFERENCES change_sets(change_set_id),
    relative_path TEXT NOT NULL,
    kind TEXT NOT NULL,
    node_type TEXT NOT NULL,
    before_hash TEXT,
    before_blob TEXT,
    before_mode INTEGER,
    intended_after_hash TEXT,
    intended_after_blob TEXT,
    intended_after_mode INTEGER,
    created_at TEXT NOT NULL
);
"""

_MIGRATIONS: dict[int, str] = {
    1: _MIGRATION_1,
    2: _MIGRATION_2,
}


class ApplicationSchemaTooNew(RuntimeError):
    def __init__(self, *, found: int, supported: int) -> None:
        super().__init__(
            f"Application state schema {found} is newer than supported {supported}."
        )
        self.found = found
        self.supported = supported


def _connect(path: Path) -> sqlite3.Connection:
    database_path = path.expanduser().resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def initialize_application_database(path: Path) -> None:
    connection = _connect(path)
    try:
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version > APPLICATION_SCHEMA_VERSION:
            raise ApplicationSchemaTooNew(
                found=version,
                supported=APPLICATION_SCHEMA_VERSION,
            )
        for target_version in range(version + 1, APPLICATION_SCHEMA_VERSION + 1):
            migration = _MIGRATIONS[target_version]
            with connection:
                connection.executescript(migration)
                connection.execute(f"PRAGMA user_version = {target_version}")
    finally:
        connection.close()


@contextmanager
def application_connection(path: Path) -> Iterator[sqlite3.Connection]:
    initialize_application_database(path)
    connection = _connect(path)
    try:
        yield connection
    finally:
        connection.close()
