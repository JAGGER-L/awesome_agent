from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

APPLICATION_SCHEMA_VERSION = 1

_MIGRATION_1 = """
CREATE TABLE trusted_workspaces (
    workspace_key TEXT PRIMARY KEY,
    canonical_path TEXT NOT NULL,
    trusted_at TEXT NOT NULL
)
"""


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
        if version == 0:
            with connection:
                connection.execute(_MIGRATION_1)
                connection.execute("PRAGMA user_version = 1")
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
