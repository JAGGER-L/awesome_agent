import sqlite3
from pathlib import Path
from typing import cast

import pytest

from awesome_agent.storage import database as database_module
from awesome_agent.storage.database import (
    APPLICATION_SCHEMA_VERSION,
    ApplicationSchemaMismatch,
    application_connection,
    initialize_application_database,
)


def test_initialize_creates_versioned_wal_database(tmp_path: Path) -> None:
    path = tmp_path / "state" / "application.db"

    initialize_application_database(path)

    assert path.is_file()
    with application_connection(path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'trusted_workspaces'"
        ).fetchone()
    assert version == APPLICATION_SCHEMA_VERSION == 7
    assert str(journal_mode).lower() == "wal"
    assert foreign_keys == 1
    assert table is not None


def test_noncurrent_schema_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "application.db"
    initialize_application_database(path)
    with application_connection(path) as connection:
        connection.execute("PRAGMA user_version = 99")

    with pytest.raises(ApplicationSchemaMismatch) as raised:
        initialize_application_database(path)

    assert raised.value.direction.value == "newer"


def test_noncurrent_schema_is_rejected_without_mutation(tmp_path: Path) -> None:
    path = tmp_path / "state" / "application.db"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 1")
    before = path.read_bytes()
    before_entries = tuple(sorted(item.name for item in path.parent.iterdir()))

    with pytest.raises(ApplicationSchemaMismatch) as raised:
        initialize_application_database(path)

    assert raised.value.found == 1
    assert raised.value.expected == APPLICATION_SCHEMA_VERSION
    assert raised.value.direction.value == "migration_unavailable"
    assert path.read_bytes() == before
    assert tuple(sorted(item.name for item in path.parent.iterdir())) == before_entries
    assert not path.with_name("application.db-wal").exists()
    assert not path.with_name("application.db-shm").exists()


def test_connect_closes_partial_connection_when_pragma_setup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingConnection:
        row_factory: object | None = None

        def __init__(self) -> None:
            self.closed = False

        def execute(self, statement: str) -> None:
            raise sqlite3.OperationalError(f"injected failure: {statement}")

        def close(self) -> None:
            self.closed = True

    connection = FailingConnection()
    monkeypatch.setattr(
        "awesome_agent.storage.database.sqlite3.connect",
        lambda *args, **kwargs: cast(sqlite3.Connection, connection),
    )

    with pytest.raises(sqlite3.OperationalError, match="injected failure"):
        database_module._connect(tmp_path / "application.db")

    assert connection.closed is True
