from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from awesome_agent.storage.migrations import (
    APPLICATION_MIGRATIONS,
    ApplicationMigrationRegistry,
)

APPLICATION_SCHEMA_VERSION = APPLICATION_MIGRATIONS.current


class StateCompatibility(StrEnum):
    NEW = "new"
    CURRENT = "current"
    MIGRATION_REQUIRED = "migration_required"
    MIGRATION_UNAVAILABLE = "migration_unavailable"
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


def inspect_application_state(
    path: Path,
    *,
    registry: ApplicationMigrationRegistry = APPLICATION_MIGRATIONS,
) -> StatePreflight:
    database_path = path.expanduser().resolve()
    if not database_path.exists():
        return StatePreflight(
            compatibility=StateCompatibility.NEW,
            found_schema=None,
            expected_schema=registry.current,
        )

    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(
            f"{database_path.as_uri()}?mode=ro",
            uri=True,
            timeout=5.0,
        )
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        user_objects = int(
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]
        )
    except (OSError, sqlite3.Error, TypeError, ValueError) as error:
        raise ApplicationStateUnavailable(database_path) from error
    finally:
        if connection is not None:
            connection.close()

    compatibility = classify_application_schema(
        version,
        user_objects=user_objects,
        registry=registry,
    )
    return StatePreflight(
        compatibility=compatibility,
        found_schema=version,
        expected_schema=registry.current,
    )


def classify_application_schema(
    version: int,
    *,
    user_objects: int,
    registry: ApplicationMigrationRegistry = APPLICATION_MIGRATIONS,
) -> StateCompatibility:
    if version == 0:
        return (
            StateCompatibility.NEW if user_objects == 0 else StateCompatibility.UNKNOWN
        )
    if version == registry.current:
        return StateCompatibility.CURRENT
    if 0 < version < registry.current:
        path = registry.path_from(version)
        return (
            StateCompatibility.MIGRATION_REQUIRED
            if path
            else StateCompatibility.MIGRATION_UNAVAILABLE
        )
    if version > registry.current:
        return StateCompatibility.NEWER
    return StateCompatibility.UNKNOWN
