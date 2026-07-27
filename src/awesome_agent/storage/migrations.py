from __future__ import annotations

import os
import sqlite3
import stat
import tempfile
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from awesome_agent.contract_versions import (
    APPLICATION_SCHEMA_CURRENT,
    APPLICATION_SCHEMA_MIGRATION_FLOOR,
)
from awesome_agent.storage.state_lease import StateLease, StateLeaseMode

type MigrationParameters = Sequence[object] | Mapping[str, object]
type MigrationRow = tuple[object, ...]


class ApplicationMigrationCursor:
    """A migration cursor that does not expose its underlying connection."""

    __slots__ = ("__cursor",)

    def __init__(self, cursor: sqlite3.Cursor) -> None:
        self.__cursor = cursor

    @property
    def rowcount(self) -> int:
        return self.__cursor.rowcount

    @property
    def lastrowid(self) -> int | None:
        return self.__cursor.lastrowid

    def fetchone(self) -> MigrationRow | None:
        return cast("MigrationRow | None", self.__cursor.fetchone())

    def fetchmany(self, size: int | None = None) -> list[MigrationRow]:
        if size is None:
            return cast("list[MigrationRow]", self.__cursor.fetchmany())
        return cast("list[MigrationRow]", self.__cursor.fetchmany(size))

    def fetchall(self) -> list[MigrationRow]:
        return cast("list[MigrationRow]", self.__cursor.fetchall())

    def __iter__(self) -> Iterator[MigrationRow]:
        return cast("Iterator[MigrationRow]", iter(self.__cursor))


class ApplicationMigrationConnection:
    """Restricted schema/data interface for one framework-owned transaction."""

    __slots__ = ("__connection",)

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.__connection = connection

    def execute(
        self,
        sql: str,
        parameters: MigrationParameters = (),
    ) -> ApplicationMigrationCursor:
        return ApplicationMigrationCursor(self.__connection.execute(sql, parameters))

    def executemany(
        self,
        sql: str,
        parameters: Iterable[MigrationParameters],
    ) -> ApplicationMigrationCursor:
        return ApplicationMigrationCursor(
            self.__connection.executemany(sql, parameters)
        )


type MigrationOperation = Callable[[ApplicationMigrationConnection], None]


class ApplicationMigrationError(RuntimeError):
    """Base error for non-destructive Application schema migration."""


class ApplicationMigrationUnavailable(ApplicationMigrationError):
    """Raised when no complete migration chain reaches the current schema."""


class ApplicationMigrationBackupError(ApplicationMigrationError):
    """Raised when the pre-migration SQLite backup cannot be published safely."""


class ApplicationMigrationBoundaryError(ApplicationMigrationError):
    """Raised when the exclusive lease does not own a safe database boundary."""


class ApplicationMigrationStepError(ApplicationMigrationError):
    """Raised after one failed migration step was rolled back."""

    def __init__(self, from_schema: int, to_schema: int) -> None:
        super().__init__(
            f"Application schema migration {from_schema}->{to_schema} failed."
        )
        self.from_schema = from_schema
        self.to_schema = to_schema


class ApplicationMigrationOutcomeUnknown(ApplicationMigrationError):
    """Raised when a failed migration step cannot prove transaction rollback."""

    def __init__(
        self,
        from_schema: int,
        to_schema: int,
        operation_error: BaseException,
        rollback_error: BaseException,
    ) -> None:
        super().__init__(
            f"Application schema migration {from_schema}->{to_schema} "
            "has an unknown transaction outcome."
        )
        self.from_schema = from_schema
        self.to_schema = to_schema
        self.operation_error = operation_error
        self.rollback_error = rollback_error


@dataclass(frozen=True, slots=True)
class ApplicationMigration:
    """One adjacent, forward-only Application schema transition."""

    from_schema: int
    to_schema: int
    apply: MigrationOperation

    def __post_init__(self) -> None:
        if self.from_schema < 1 or self.to_schema != self.from_schema + 1:
            raise ValueError("Application migrations must advance one positive schema.")


@dataclass(frozen=True, slots=True)
class ApplicationMigrationRegistry:
    """A bounded linear migration catalog with an explicit support floor."""

    floor: int
    current: int
    migrations: tuple[ApplicationMigration, ...] = ()

    def __post_init__(self) -> None:
        if self.floor < 1 or self.current < self.floor:
            raise ValueError("Application migration floor/current are invalid.")
        ordered = tuple(sorted(self.migrations, key=lambda item: item.from_schema))
        if ordered != self.migrations:
            raise ValueError("Application migrations must be ordered by source schema.")
        sources = [item.from_schema for item in ordered]
        if len(sources) != len(set(sources)):
            raise ValueError("Application migration source schemas must be unique.")
        if any(
            item.from_schema < self.floor or item.to_schema > self.current
            for item in ordered
        ):
            raise ValueError("Application migration lies outside the supported range.")
        expected_sources = tuple(range(self.floor, self.current))
        if tuple(sources) != expected_sources:
            raise ValueError(
                "Application migrations must form one complete linear chain."
            )

    def path_from(self, found_schema: int) -> tuple[ApplicationMigration, ...] | None:
        """Return the exact forward chain, or None when migration is unavailable."""

        if found_schema == self.current:
            return ()
        if found_schema < self.floor or found_schema > self.current:
            return None
        by_source = {item.from_schema: item for item in self.migrations}
        path: list[ApplicationMigration] = []
        version = found_schema
        while version < self.current:
            migration = by_source.get(version)
            if migration is None:
                return None
            path.append(migration)
            version = migration.to_schema
        return tuple(path)

    @property
    def complete(self) -> bool:
        return self.path_from(self.floor) is not None


APPLICATION_SCHEMA_FLOOR = APPLICATION_SCHEMA_MIGRATION_FLOOR


def _migrate_schema_7_to_8(connection: ApplicationMigrationConnection) -> None:
    connection.execute("ALTER TABLE threads ADD COLUMN lineage_json TEXT")


APPLICATION_MIGRATIONS = ApplicationMigrationRegistry(
    floor=APPLICATION_SCHEMA_FLOOR,
    current=APPLICATION_SCHEMA_CURRENT,
    migrations=(ApplicationMigration(7, 8, _migrate_schema_7_to_8),),
)


def validate_application_migration_boundary(
    lease: StateLease,
    database_path: Path,
) -> Path:
    """Pin migration to the exclusive lease's lexical, non-linked database."""

    lexical = Path(os.path.abspath(database_path.expanduser()))
    expected = Path(os.path.abspath(lease.home / "state" / "application.db"))
    if not lease.active or lease.mode is not StateLeaseMode.EXCLUSIVE:
        raise ApplicationMigrationBoundaryError(
            "Application migration requires an active exclusive state lease."
        )
    if lexical != expected:
        raise ApplicationMigrationBoundaryError(
            "State lease does not own this Application database."
        )
    try:
        state_status = os.lstat(expected.parent)
        database_status = os.lstat(expected)
    except OSError as error:
        raise ApplicationMigrationBoundaryError(
            "Application migration boundary is unavailable."
        ) from error
    if _is_link_or_reparse(state_status) or not stat.S_ISDIR(state_status.st_mode):
        raise ApplicationMigrationBoundaryError(
            "Application state directory is not a stable local directory."
        )
    if expected.parent.resolve().parent != lease.home:
        raise ApplicationMigrationBoundaryError(
            "Application state directory escaped the leased home."
        )
    if (
        _is_link_or_reparse(database_status)
        or not stat.S_ISREG(database_status.st_mode)
        or int(database_status.st_nlink) != 1
    ):
        raise ApplicationMigrationBoundaryError(
            "Application database is not one private regular file."
        )
    resolved = expected.resolve()
    if resolved.parent != lease.home / "state":
        raise ApplicationMigrationBoundaryError(
            "Application database escaped the leased state directory."
        )
    return resolved


def migrate_application_database(
    connection: sqlite3.Connection,
    database_path: Path,
    *,
    registry: ApplicationMigrationRegistry = APPLICATION_MIGRATIONS,
) -> Path | None:
    """Back up and migrate one live WAL-aware Application connection.

    The complete adjacent chain owns one transaction, so any failed step rolls
    schema and data back to the pre-migration version. The validated fixed backup
    remains available for manual recovery; this function never restores or resets
    Application state automatically.
    """

    if connection.in_transaction:
        raise ApplicationMigrationError(
            "Application migration requires an idle SQLite connection."
        )
    found_schema = _schema_version(connection)
    path = registry.path_from(found_schema)
    if path is None:
        raise ApplicationMigrationUnavailable(
            f"No migration path from schema {found_schema} to {registry.current}."
        )
    if not path:
        return None

    backup_path = create_application_migration_backup(connection, database_path)
    _apply_migration_chain(connection, path)
    return backup_path


def create_application_migration_backup(
    source: sqlite3.Connection,
    database_path: Path,
) -> Path:
    """Publish a validated SQLite Backup API snapshot at the fixed backup path."""

    resolved = database_path.expanduser().resolve()
    backup_path = resolved.with_name(f"{resolved.name}.pre-migration.bak")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    _require_quick_check(
        source,
        label="source Application database",
        error_type=ApplicationMigrationBackupError,
    )
    if backup_path.exists() or backup_path.is_symlink():
        status = os.lstat(backup_path)
        if (
            _is_link_or_reparse(status)
            or not stat.S_ISREG(status.st_mode)
            or int(status.st_nlink) != 1
        ):
            raise ApplicationMigrationBackupError(
                "Existing pre-migration backup is not one private regular file."
            )
    descriptor, raw_temporary = tempfile.mkstemp(
        dir=backup_path.parent,
        prefix=f".{backup_path.name}.",
        suffix=".tmp",
    )
    os.close(descriptor)
    temporary = Path(raw_temporary)
    destination: sqlite3.Connection | None = None
    try:
        destination = sqlite3.connect(temporary, timeout=5.0, check_same_thread=True)
        source.backup(destination)
        destination.close()
        destination = None
        validation = sqlite3.connect(
            f"{temporary.as_uri()}?mode=ro",
            uri=True,
            timeout=5.0,
            check_same_thread=True,
        )
        try:
            _require_quick_check(
                validation,
                label="pre-migration backup",
                error_type=ApplicationMigrationBackupError,
            )
            backup_schema = _schema_version(validation)
        finally:
            validation.close()
        if backup_schema != _schema_version(source):
            raise ApplicationMigrationBackupError(
                "Pre-migration backup schema does not match the source database."
            )
        os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR)
        _sync_file(temporary)
        os.replace(temporary, backup_path)
        if os.name != "nt":
            os.chmod(backup_path, stat.S_IRUSR | stat.S_IWUSR)
        _sync_directory(backup_path.parent)
    except ApplicationMigrationBackupError:
        raise
    except (OSError, sqlite3.Error) as error:
        raise ApplicationMigrationBackupError(
            "Pre-migration backup could not be created safely."
        ) from error
    finally:
        if destination is not None:
            destination.close()
        for artifact in (
            temporary,
            temporary.with_name(f"{temporary.name}-journal"),
            temporary.with_name(f"{temporary.name}-wal"),
            temporary.with_name(f"{temporary.name}-shm"),
        ):
            with suppress(OSError):
                artifact.unlink(missing_ok=True)
    return backup_path


def _apply_migration_chain(
    connection: sqlite3.Connection,
    migrations: tuple[ApplicationMigration, ...],
) -> None:
    first = migrations[0]
    observed = _schema_version(connection)
    if observed != first.from_schema:
        raise ApplicationMigrationUnavailable(
            f"Migration expected schema {first.from_schema}, found {observed}."
        )
    connection.execute("BEGIN IMMEDIATE")
    active = first
    try:
        for migration in migrations:
            active = migration
            if _schema_version(connection) != migration.from_schema:
                raise ApplicationMigrationUnavailable(
                    "Application migration chain lost its schema boundary."
                )
            guarded = ApplicationMigrationConnection(connection)
            connection.set_authorizer(_migration_authorizer)
            try:
                migration.apply(guarded)
            finally:
                connection.set_authorizer(None)
            if not connection.in_transaction:
                raise ApplicationMigrationError(
                    "Migration operation escaped its transaction boundary."
                )
            connection.execute(f"PRAGMA user_version = {migration.to_schema}")
            if _schema_version(connection) != migration.to_schema:
                raise ApplicationMigrationError(
                    "Migration did not publish its target schema identity."
                )
        _require_quick_check(
            connection,
            label="migration chain",
            error_type=ApplicationMigrationError,
        )
        connection.execute("COMMIT")
    except BaseException as operation_error:
        try:
            in_transaction = connection.in_transaction
        except BaseException as boundary_error:
            raise ApplicationMigrationOutcomeUnknown(
                active.from_schema,
                active.to_schema,
                operation_error,
                boundary_error,
            ) from boundary_error
        if not in_transaction:
            lost_transaction_error = RuntimeError(
                "Migration transaction closed before rollback could be proven."
            )
            raise ApplicationMigrationOutcomeUnknown(
                active.from_schema,
                active.to_schema,
                operation_error,
                lost_transaction_error,
            ) from operation_error
        try:
            connection.execute("ROLLBACK")
        except BaseException as rollback_error:
            raise ApplicationMigrationOutcomeUnknown(
                active.from_schema,
                active.to_schema,
                operation_error,
                rollback_error,
            ) from rollback_error
        raise ApplicationMigrationStepError(
            active.from_schema,
            active.to_schema,
        ) from operation_error


def _migration_authorizer(
    action: int,
    _argument_1: str | None,
    _argument_2: str | None,
    _database: str | None,
    _trigger: str | None,
) -> int:
    if action in {
        sqlite3.SQLITE_ATTACH,
        sqlite3.SQLITE_DETACH,
        sqlite3.SQLITE_SAVEPOINT,
        sqlite3.SQLITE_TRANSACTION,
    }:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("PRAGMA user_version").fetchone()
    if row is None:
        raise ApplicationMigrationError("Application schema identity is unavailable.")
    return int(row[0])


def _require_quick_check(
    connection: sqlite3.Connection,
    *,
    label: str,
    error_type: type[ApplicationMigrationError],
) -> None:
    row = connection.execute("PRAGMA quick_check(1)").fetchone()
    if row is None or len(row) == 0 or str(row[0]).lower() != "ok":
        raise error_type(f"{label} failed SQLite quick_check.")


def _sync_file(path: Path) -> None:
    flags = (os.O_RDWR if os.name == "nt" else os.O_RDONLY) | getattr(
        os,
        "O_BINARY",
        0,
    )
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _is_link_or_reparse(status: os.stat_result) -> bool:
    attributes = int(getattr(status, "st_file_attributes", 0))
    reparse = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(status.st_mode) or bool(attributes & reparse)


__all__ = [
    "APPLICATION_MIGRATIONS",
    "APPLICATION_SCHEMA_FLOOR",
    "ApplicationMigration",
    "ApplicationMigrationBackupError",
    "ApplicationMigrationBoundaryError",
    "ApplicationMigrationConnection",
    "ApplicationMigrationCursor",
    "ApplicationMigrationError",
    "ApplicationMigrationOutcomeUnknown",
    "ApplicationMigrationRegistry",
    "ApplicationMigrationStepError",
    "ApplicationMigrationUnavailable",
    "create_application_migration_backup",
    "migrate_application_database",
    "validate_application_migration_boundary",
]
