from __future__ import annotations

import sqlite3
from pathlib import Path

from awesome_agent.storage.health import sqlite_database_health


def test_sqlite_database_health_reports_missing_and_valid_state(tmp_path: Path) -> None:
    database = tmp_path / "state.db"

    assert sqlite_database_health(database) is False

    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE example (value TEXT NOT NULL)")

    assert sqlite_database_health(database) is True


def test_sqlite_database_health_rejects_non_database_content(tmp_path: Path) -> None:
    database = tmp_path / "state.db"
    database.write_bytes(b"not a sqlite database")

    assert sqlite_database_health(database) is False
