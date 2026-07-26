from __future__ import annotations

import asyncio
import os
import sqlite3
import stat
from collections.abc import Callable
from pathlib import Path
from threading import Event
from typing import Any, cast

import pytest

from awesome_agent.storage import migrations as migrations_module
from awesome_agent.storage.application_sqlite import (
    ApplicationSQLite,
    ApplicationSQLiteUnavailable,
)
from awesome_agent.storage.compatibility import (
    StateCompatibility,
    inspect_application_state,
)
from awesome_agent.storage.migrations import (
    APPLICATION_MIGRATIONS,
    APPLICATION_SCHEMA_FLOOR,
    ApplicationMigration,
    ApplicationMigrationBackupError,
    ApplicationMigrationBoundaryError,
    ApplicationMigrationConnection,
    ApplicationMigrationError,
    ApplicationMigrationRegistry,
    ApplicationMigrationStepError,
)
from awesome_agent.storage.state_lease import StateLease, StateLeaseMode


def _seed_schema_seven(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE parent (
                id INTEGER PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE child (
                id INTEGER PRIMARY KEY,
                parent_id INTEGER NOT NULL REFERENCES parent(id)
            );
            CREATE INDEX child_parent_id_idx ON child(parent_id);
            INSERT INTO parent(id, value) VALUES (1, 'preserve me');
            INSERT INTO child(id, parent_id) VALUES (1, 1);
            PRAGMA user_version = 7;
            """
        )


def _seven_to_eight(connection: ApplicationMigrationConnection) -> None:
    connection.execute(
        "ALTER TABLE parent ADD COLUMN migrated_value TEXT NOT NULL DEFAULT ''"
    )
    connection.execute("UPDATE parent SET migrated_value = value || '-schema-8'")


def _eight_to_nine(connection: ApplicationMigrationConnection) -> None:
    connection.execute("CREATE TABLE migration_marker (value TEXT PRIMARY KEY)")
    connection.execute("INSERT INTO migration_marker VALUES ('schema-9')")


def _synthetic_registry(
    final_step: Callable[[ApplicationMigrationConnection], None] = _eight_to_nine,
) -> ApplicationMigrationRegistry:
    return ApplicationMigrationRegistry(
        floor=7,
        current=9,
        migrations=(
            ApplicationMigration(7, 8, _seven_to_eight),
            ApplicationMigration(8, 9, final_step),
        ),
    )


def _schema(path: Path) -> int:
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        row = connection.execute("PRAGMA user_version").fetchone()
    finally:
        connection.close()
    assert row is not None
    return int(row[0])


def test_production_registry_stays_at_schema_seven_without_steps() -> None:
    assert APPLICATION_SCHEMA_FLOOR == 7
    assert APPLICATION_MIGRATIONS.floor == 7
    assert APPLICATION_MIGRATIONS.current == 7
    assert APPLICATION_MIGRATIONS.migrations == ()
    assert APPLICATION_MIGRATIONS.path_from(7) == ()
    assert APPLICATION_MIGRATIONS.path_from(6) is None


@pytest.mark.parametrize(
    "migrations",
    [
        (ApplicationMigration(8, 9, _eight_to_nine),),
        (ApplicationMigration(7, 8, _seven_to_eight),),
        (
            ApplicationMigration(8, 9, _eight_to_nine),
            ApplicationMigration(7, 8, _seven_to_eight),
        ),
    ],
)
def test_registry_rejects_incomplete_or_unordered_chains(
    migrations: tuple[ApplicationMigration, ...],
) -> None:
    with pytest.raises(ValueError):
        ApplicationMigrationRegistry(floor=7, current=9, migrations=migrations)


def test_migration_definition_rejects_skip_and_reverse_steps() -> None:
    with pytest.raises(ValueError):
        ApplicationMigration(7, 9, _seven_to_eight)
    with pytest.raises(ValueError):
        ApplicationMigration(8, 7, _seven_to_eight)


def test_registry_rejects_duplicate_source_steps() -> None:
    with pytest.raises(ValueError, match="source schemas must be unique"):
        ApplicationMigrationRegistry(
            floor=7,
            current=8,
            migrations=(
                ApplicationMigration(7, 8, _seven_to_eight),
                ApplicationMigration(7, 8, _seven_to_eight),
            ),
        )


@pytest.mark.asyncio
async def test_worker_migrates_entire_chain_and_publishes_independent_backup(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    path = home / "state" / "application.db"
    backup = path.with_name("application.db.pre-migration.bak")
    _seed_schema_seven(path)
    wal_writer = sqlite3.connect(path)
    assert wal_writer.execute("PRAGMA journal_mode = WAL").fetchone() == ("wal",)
    wal_writer.execute("PRAGMA wal_autocheckpoint = 0")
    wal_writer.execute("INSERT INTO parent(id, value) VALUES (2, 'from wal')")
    wal_writer.execute("INSERT INTO child(id, parent_id) VALUES (2, 2)")
    wal_writer.commit()
    assert path.with_name("application.db-wal").exists()
    immutable = sqlite3.connect(
        f"{path.as_uri()}?mode=ro&immutable=1",
        uri=True,
    )
    try:
        assert immutable.execute("SELECT COUNT(*) FROM parent").fetchone() == (1,)
    finally:
        immutable.close()
    database = ApplicationSQLite(path, migration_registry=_synthetic_registry())
    lease = StateLease.acquire(home, StateLeaseMode.EXCLUSIVE)
    try:
        preflight = await database.preflight()
        assert preflight.compatibility is StateCompatibility.MIGRATION_REQUIRED

        assert await database.migrate(lease) == backup

        assert _schema(path) == 9
        assert _schema(backup) == 7
        with sqlite3.connect(path) as connection:
            assert connection.execute("PRAGMA quick_check(1)").fetchone() == ("ok",)
            assert connection.execute(
                "SELECT value, migrated_value FROM parent ORDER BY id"
            ).fetchall() == [
                ("preserve me", "preserve me-schema-8"),
                ("from wal", "from wal-schema-8"),
            ]
            assert connection.execute(
                "SELECT value FROM migration_marker"
            ).fetchone() == ("schema-9",)
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
            assert connection.execute(
                "SELECT name FROM sqlite_schema WHERE name = 'child_parent_id_idx'"
            ).fetchone() == ("child_parent_id_idx",)
        with sqlite3.connect(
            f"{backup.as_uri()}?mode=ro",
            uri=True,
        ) as connection:
            assert connection.execute("PRAGMA quick_check(1)").fetchone() == ("ok",)
            assert connection.execute(
                "SELECT value FROM parent ORDER BY id"
            ).fetchall() == [("preserve me",), ("from wal",)]
            assert connection.execute(
                "SELECT COUNT(*) FROM pragma_table_info('parent')"
            ).fetchone() == (2,)
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        if os.name != "nt":
            assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    finally:
        wal_writer.close()
        lease.close()
        await database.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ["validation", "fsync"])
async def test_backup_failure_leaves_source_schema_and_data_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_point: str,
) -> None:
    home = tmp_path / "home"
    path = home / "state" / "application.db"
    backup = path.with_name("application.db.pre-migration.bak")
    _seed_schema_seven(path)
    step_called = False

    def observe_first_step(connection: ApplicationMigrationConnection) -> None:
        nonlocal step_called
        step_called = True
        _seven_to_eight(connection)

    registry = ApplicationMigrationRegistry(
        floor=7,
        current=9,
        migrations=(
            ApplicationMigration(7, 8, observe_first_step),
            ApplicationMigration(8, 9, _eight_to_nine),
        ),
    )
    if failure_point == "validation":
        require_quick_check = migrations_module._require_quick_check

        def fail_backup_validation(
            connection: sqlite3.Connection,
            *,
            label: str,
            error_type: type[ApplicationMigrationError],
        ) -> None:
            if label == "pre-migration backup":
                raise ApplicationMigrationBackupError("injected validation failure")
            require_quick_check(
                connection,
                label=label,
                error_type=error_type,
            )

        monkeypatch.setattr(
            migrations_module,
            "_require_quick_check",
            fail_backup_validation,
        )
    else:
        monkeypatch.setattr(
            migrations_module,
            "_sync_file",
            lambda _path: (_ for _ in ()).throw(OSError("injected fsync failure")),
        )
    database = ApplicationSQLite(path, migration_registry=registry)
    lease = StateLease.acquire(home, StateLeaseMode.EXCLUSIVE)
    try:
        with pytest.raises(ApplicationMigrationBackupError):
            await database.migrate(lease)
        assert step_called is False
        assert _schema(path) == 7
        assert not backup.exists()
        with sqlite3.connect(path) as connection:
            assert connection.execute("SELECT value FROM parent").fetchall() == [
                ("preserve me",)
            ]
            assert connection.execute(
                "SELECT COUNT(*) FROM pragma_table_info('parent')"
            ).fetchone() == (2,)
    finally:
        lease.close()
        await database.aclose()


@pytest.mark.asyncio
async def test_failed_second_step_rolls_back_the_whole_chain_and_keeps_backup(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    path = home / "state" / "application.db"
    backup = path.with_name("application.db.pre-migration.bak")
    _seed_schema_seven(path)

    def fail_second_step(connection: ApplicationMigrationConnection) -> None:
        connection.execute("CREATE TABLE must_roll_back (value TEXT)")
        raise RuntimeError("injected migration failure")

    database = ApplicationSQLite(
        path,
        migration_registry=_synthetic_registry(fail_second_step),
    )
    lease = StateLease.acquire(home, StateLeaseMode.EXCLUSIVE)
    try:
        with pytest.raises(ApplicationMigrationStepError) as raised:
            await database.migrate(lease)
        assert (raised.value.from_schema, raised.value.to_schema) == (8, 9)
        assert _schema(path) == 7
        assert _schema(backup) == 7
        with sqlite3.connect(path) as connection:
            assert connection.execute("SELECT value FROM parent").fetchone() == (
                "preserve me",
            )
            assert (
                connection.execute(
                    "SELECT name FROM sqlite_schema WHERE name = 'must_roll_back'"
                ).fetchone()
                is None
            )
            assert connection.execute(
                "SELECT COUNT(*) FROM pragma_table_info('parent')"
            ).fetchone() == (2,)
        assert (
            await database.preflight()
        ).compatibility is StateCompatibility.MIGRATION_REQUIRED
    finally:
        lease.close()
        await database.aclose()


@pytest.mark.asyncio
async def test_commit_outcome_unknown_fails_migration_worker_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    path = home / "state" / "application.db"
    _seed_schema_seven(path)
    database = ApplicationSQLite(path, migration_registry=_synthetic_registry())
    delegate = sqlite3.connect(
        path,
        isolation_level=None,
        check_same_thread=False,
    )

    class CommitOutcomeConnection:
        def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
            cursor = delegate.execute(sql, parameters)
            if sql.strip().upper() == "COMMIT":
                raise sqlite3.OperationalError("injected COMMIT outcome error")
            return cursor

        def __getattr__(self, name: str) -> Any:
            return getattr(delegate, name)

    monkeypatch.setattr(
        database,
        "_open_migration_connection_on_worker",
        lambda: cast(sqlite3.Connection, CommitOutcomeConnection()),
    )
    lease = StateLease.acquire(home, StateLeaseMode.EXCLUSIVE)
    try:
        with pytest.raises(ApplicationSQLiteUnavailable):
            await database.migrate(lease)
        with pytest.raises(ApplicationSQLiteUnavailable):
            await database.preflight()
    finally:
        delegate.close()
        lease.close()
        await database.aclose()


@pytest.mark.asyncio
async def test_unknown_outcome_close_failure_still_fails_worker_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    path = home / "state" / "application.db"
    _seed_schema_seven(path)
    database = ApplicationSQLite(path, migration_registry=_synthetic_registry())
    delegate = sqlite3.connect(
        path,
        isolation_level=None,
        check_same_thread=False,
    )
    close_attempted = False

    class CommitAndCloseOutcomeConnection:
        def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
            cursor = delegate.execute(sql, parameters)
            if sql.strip().upper() == "COMMIT":
                raise sqlite3.OperationalError("injected COMMIT outcome error")
            return cursor

        def close(self) -> None:
            nonlocal close_attempted
            close_attempted = True
            raise sqlite3.OperationalError("injected close failure")

        def __getattr__(self, name: str) -> Any:
            return getattr(delegate, name)

    monkeypatch.setattr(
        database,
        "_open_migration_connection_on_worker",
        lambda: cast(sqlite3.Connection, CommitAndCloseOutcomeConnection()),
    )
    lease = StateLease.acquire(home, StateLeaseMode.EXCLUSIVE)
    try:
        with pytest.raises(ApplicationSQLiteUnavailable):
            await database.migrate(lease)
        assert close_attempted is True
        with pytest.raises(ApplicationSQLiteUnavailable):
            await database.preflight()
    finally:
        delegate.close()
        lease.close()
        await database.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("escape", ["transaction", "attach"])
async def test_migration_step_cannot_escape_owned_transaction_or_database(
    tmp_path: Path,
    escape: str,
) -> None:
    home = tmp_path / "home"
    path = home / "state" / "application.db"
    outside = tmp_path / "outside.db"
    _seed_schema_seven(path)
    with sqlite3.connect(outside) as connection:
        connection.execute("CREATE TABLE protected (value TEXT NOT NULL)")
        connection.execute("INSERT INTO protected VALUES ('unchanged')")

    def attempt_escape(connection: ApplicationMigrationConnection) -> None:
        assert not hasattr(connection, "commit")
        assert not hasattr(connection, "rollback")
        assert not hasattr(connection, "executescript")
        connection.execute("CREATE TABLE must_roll_back (value TEXT)")
        if escape == "transaction":
            connection.execute("COMMIT")
            connection.execute("BEGIN")
        else:
            connection.execute("ATTACH DATABASE ? AS escaped", (str(outside),))

    database = ApplicationSQLite(
        path,
        migration_registry=_synthetic_registry(attempt_escape),
    )
    lease = StateLease.acquire(home, StateLeaseMode.EXCLUSIVE)
    try:
        with pytest.raises(ApplicationMigrationStepError):
            await database.migrate(lease)
        assert _schema(path) == 7
        with sqlite3.connect(path) as connection:
            assert (
                connection.execute(
                    "SELECT name FROM sqlite_schema WHERE name = 'must_roll_back'"
                ).fetchone()
                is None
            )
        with sqlite3.connect(outside) as connection:
            assert connection.execute("SELECT value FROM protected").fetchall() == [
                ("unchanged",)
            ]
        assert (
            await database.preflight()
        ).compatibility is StateCompatibility.MIGRATION_REQUIRED
    finally:
        lease.close()
        await database.aclose()


@pytest.mark.asyncio
async def test_rollback_failure_fails_migration_worker_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    path = home / "state" / "application.db"
    _seed_schema_seven(path)

    def fail_final_step(connection: ApplicationMigrationConnection) -> None:
        connection.execute("CREATE TABLE rollback_probe (value TEXT)")
        raise RuntimeError("injected step failure")

    database = ApplicationSQLite(
        path,
        migration_registry=_synthetic_registry(fail_final_step),
    )
    delegate = sqlite3.connect(
        path,
        isolation_level=None,
        check_same_thread=False,
    )

    class RollbackOutcomeConnection:
        def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
            if sql.strip().upper() == "ROLLBACK":
                raise sqlite3.OperationalError("injected ROLLBACK outcome error")
            return delegate.execute(sql, parameters)

        def __getattr__(self, name: str) -> Any:
            return getattr(delegate, name)

    monkeypatch.setattr(
        database,
        "_open_migration_connection_on_worker",
        lambda: cast(sqlite3.Connection, RollbackOutcomeConnection()),
    )
    lease = StateLease.acquire(home, StateLeaseMode.EXCLUSIVE)
    try:
        with pytest.raises(ApplicationSQLiteUnavailable):
            await database.migrate(lease)
        with pytest.raises(ApplicationSQLiteUnavailable):
            await database.preflight()
        assert _schema(path) == 7
    finally:
        delegate.close()
        lease.close()
        await database.aclose()


@pytest.mark.asyncio
async def test_cancelled_migration_finishes_before_reraising(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    path = home / "state" / "application.db"
    _seed_schema_seven(path)
    started = Event()
    release = Event()

    def blocked_final_step(connection: ApplicationMigrationConnection) -> None:
        started.set()
        release.wait(timeout=2)
        _eight_to_nine(connection)

    database = ApplicationSQLite(
        path,
        migration_registry=_synthetic_registry(blocked_final_step),
    )
    lease = StateLease.acquire(home, StateLeaseMode.EXCLUSIVE)
    migration = asyncio.create_task(database.migrate(lease))
    try:
        while not started.is_set():
            await asyncio.sleep(0)
        migration.cancel("first cancellation")
        await asyncio.sleep(0)
        migration.cancel("second cancellation")
        await asyncio.sleep(0)
        assert not migration.done()

        release.set()
        with pytest.raises(asyncio.CancelledError) as raised:
            await migration
        assert raised.value.args == ("first cancellation",)
        assert _schema(path) == 9
        assert _schema(path.with_name("application.db.pre-migration.bak")) == 7
    finally:
        release.set()
        lease.close()
        await database.aclose()


@pytest.mark.asyncio
async def test_migration_rejects_shared_or_wrong_lease_without_mutation(
    tmp_path: Path,
) -> None:
    first_home = tmp_path / "first"
    second_home = tmp_path / "second"
    path = first_home / "state" / "application.db"
    _seed_schema_seven(path)
    database = ApplicationSQLite(path, migration_registry=_synthetic_registry())
    shared = StateLease.acquire(first_home, StateLeaseMode.SHARED)
    wrong = StateLease.acquire(second_home, StateLeaseMode.EXCLUSIVE)
    try:
        with pytest.raises(ApplicationMigrationBoundaryError):
            await database.migrate(shared)
        with pytest.raises(ApplicationMigrationBoundaryError):
            await database.migrate(wrong)
        assert _schema(path) == 7
        assert not path.with_name("application.db.pre-migration.bak").exists()
    finally:
        shared.close()
        wrong.close()
        await database.aclose()


@pytest.mark.asyncio
async def test_migration_rejects_multilink_database_without_backup(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    path = home / "state" / "application.db"
    linked = tmp_path / "application-linked.db"
    _seed_schema_seven(path)
    try:
        os.link(path, linked)
    except OSError as error:
        pytest.skip(f"hard links unavailable: {error}")
    database = ApplicationSQLite(path, migration_registry=_synthetic_registry())
    lease = StateLease.acquire(home, StateLeaseMode.EXCLUSIVE)
    try:
        with pytest.raises(ApplicationMigrationBoundaryError):
            await database.migrate(lease)
        assert _schema(path) == 7
        assert not path.with_name("application.db.pre-migration.bak").exists()
    finally:
        lease.close()
        await database.aclose()


@pytest.mark.asyncio
async def test_migration_rejects_symlink_database_without_backup(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    path = home / "state" / "application.db"
    target = tmp_path / "outside.db"
    _seed_schema_seven(target)
    path.parent.mkdir(parents=True)
    try:
        path.symlink_to(target)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")
    database = ApplicationSQLite(path, migration_registry=_synthetic_registry())
    lease = StateLease.acquire(home, StateLeaseMode.EXCLUSIVE)
    try:
        with pytest.raises(ApplicationMigrationBoundaryError):
            await database.migrate(lease)
        assert _schema(target) == 7
        assert not path.with_name("application.db.pre-migration.bak").exists()
    finally:
        lease.close()
        await database.aclose()


def test_preflight_reads_schema_identity_from_live_wal(tmp_path: Path) -> None:
    path = tmp_path / "state" / "application.db"
    path.parent.mkdir(parents=True)
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA journal_mode = WAL").fetchone() == ("wal",)
        connection.execute("CREATE TABLE wal_only (value TEXT NOT NULL)")
        connection.execute("INSERT INTO wal_only VALUES ('visible')")
        connection.execute("PRAGMA user_version = 7")
        connection.commit()
        assert path.with_name("application.db-wal").exists()

        result = inspect_application_state(path)

        assert result.compatibility is StateCompatibility.CURRENT
        assert result.found_schema == 7
    finally:
        connection.close()
