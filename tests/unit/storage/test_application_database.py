from pathlib import Path

import pytest

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
    assert version == APPLICATION_SCHEMA_VERSION == 1
    assert str(journal_mode).lower() == "wal"
    assert foreign_keys == 1
    assert table is not None


def test_noncurrent_schema_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "application.db"
    initialize_application_database(path)
    with application_connection(path) as connection:
        connection.execute("PRAGMA user_version = 99")

    with pytest.raises(ApplicationSchemaMismatch):
        initialize_application_database(path)
