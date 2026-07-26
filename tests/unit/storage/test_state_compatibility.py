from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from awesome_agent.storage.compatibility import (
    APPLICATION_SCHEMA_VERSION,
    ApplicationStateUnavailable,
    StateCompatibility,
    inspect_application_state,
)


def _inventory(root: Path) -> tuple[tuple[str, bytes], ...]:
    if not root.exists():
        return ()
    return tuple(
        (item.relative_to(root).as_posix(), item.read_bytes())
        for item in sorted(root.rglob("*"))
        if item.is_file()
    )


def _missing(_: Path) -> None:
    return


def _empty_sqlite(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path):
        pass


def _schema(version: int) -> Callable[[Path], None]:
    def create(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as connection:
            connection.execute(f"PRAGMA user_version = {version}")

    return create


def _schema_zero_with_tables(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE legacy_state (value TEXT NOT NULL)")
        connection.execute("INSERT INTO legacy_state VALUES ('preserve me')")


@pytest.mark.parametrize(
    ("setup", "expected", "found_schema"),
    [
        (_missing, StateCompatibility.NEW, None),
        (_empty_sqlite, StateCompatibility.NEW, 0),
        (_schema(7), StateCompatibility.CURRENT, 7),
        (_schema(2), StateCompatibility.MIGRATION_UNAVAILABLE, 2),
        (_schema(6), StateCompatibility.MIGRATION_UNAVAILABLE, 6),
        (_schema(8), StateCompatibility.NEWER, 8),
        (_schema(999), StateCompatibility.NEWER, 999),
        (_schema(-1), StateCompatibility.UNKNOWN, -1),
        (_schema_zero_with_tables, StateCompatibility.UNKNOWN, 0),
    ],
)
def test_inspect_application_state_classifies_without_writing(
    tmp_path: Path,
    setup: Callable[[Path], None],
    expected: StateCompatibility,
    found_schema: int | None,
) -> None:
    path = tmp_path / "state" / "application.db"
    setup(path)
    before = _inventory(tmp_path)

    result = inspect_application_state(path)

    assert result.compatibility is expected
    assert result.found_schema == found_schema
    assert result.expected_schema == APPLICATION_SCHEMA_VERSION == 7
    assert _inventory(tmp_path) == before


def test_inspect_missing_database_does_not_create_parent_or_sidecars(
    tmp_path: Path,
) -> None:
    state = tmp_path / "state"
    path = state / "application.db"

    result = inspect_application_state(path)

    assert result.compatibility is StateCompatibility.NEW
    assert not state.exists()


def test_inspect_corrupt_database_is_unavailable_without_mutation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "state" / "application.db"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not a sqlite database")
    before = _inventory(tmp_path)

    with pytest.raises(ApplicationStateUnavailable) as raised:
        inspect_application_state(path)

    assert raised.value.path == path.resolve()
    assert _inventory(tmp_path) == before
    assert not path.with_name("application.db-wal").exists()
    assert not path.with_name("application.db-shm").exists()
