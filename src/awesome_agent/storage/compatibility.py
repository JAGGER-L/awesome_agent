from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

APPLICATION_SCHEMA_VERSION = 7


class StateCompatibility(StrEnum):
    NEW = "new"
    CURRENT = "current"
    OLDER = "older"
    NEWER = "newer"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class StatePreflight:
    compatibility: StateCompatibility
    found_schema: int | None
    expected_schema: int


class ApplicationStateUnavailable(RuntimeError):
    def __init__(self, path: Path) -> None:
        resolved = path.expanduser().resolve()
        super().__init__(f"Application state is unavailable: {resolved}")
        self.path = resolved


def inspect_application_state(path: Path) -> StatePreflight:
    database_path = path.expanduser().resolve()
    if not database_path.exists():
        return StatePreflight(
            compatibility=StateCompatibility.NEW,
            found_schema=None,
            expected_schema=APPLICATION_SCHEMA_VERSION,
        )

    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"{database_path.as_uri()}?mode=ro&immutable=1",
            uri=True,
            timeout=5.0,
        )
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        user_objects = int(
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_schema "
                "WHERE name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]
        )
    except (OSError, sqlite3.Error, TypeError, ValueError) as error:
        raise ApplicationStateUnavailable(database_path) from error
    finally:
        if connection is not None:
            connection.close()

    if version == 0:
        compatibility = (
            StateCompatibility.NEW
            if user_objects == 0
            else StateCompatibility.UNKNOWN
        )
    elif version == APPLICATION_SCHEMA_VERSION:
        compatibility = StateCompatibility.CURRENT
    elif 0 < version < APPLICATION_SCHEMA_VERSION:
        compatibility = StateCompatibility.OLDER
    elif version > APPLICATION_SCHEMA_VERSION:
        compatibility = StateCompatibility.NEWER
    else:
        compatibility = StateCompatibility.UNKNOWN
    return StatePreflight(
        compatibility=compatibility,
        found_schema=version,
        expected_schema=APPLICATION_SCHEMA_VERSION,
    )
