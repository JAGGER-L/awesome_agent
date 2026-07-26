from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from pathlib import Path
from threading import Event, Thread, current_thread, get_ident
from typing import Any, cast

import pytest

from awesome_agent.storage.application_sqlite import (
    ApplicationSQLite,
    ApplicationSQLiteBusy,
    ApplicationSQLiteClosed,
    ApplicationSQLiteResultError,
    ApplicationSQLiteUnavailable,
)
from awesome_agent.storage.compatibility import StateCompatibility
from awesome_agent.storage.database import initialize_application_database
from awesome_agent.storage.state_lease import StateLease, StateLeaseMode
from awesome_agent.storage.state_recovery import StateResetError, reset_local_state


async def _wait_for(event: Event) -> None:
    for _ in range(1_000):
        if event.is_set():
            return
        await asyncio.sleep(0.001)
    pytest.fail("worker operation did not start")


def _execute(connection: sqlite3.Connection, statement: str) -> None:
    connection.execute(statement)


@pytest.mark.asyncio
async def test_worker_owns_one_daemon_thread_and_reuses_one_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = ApplicationSQLite(tmp_path / "state" / "application.db")
    event_loop_thread = get_ident()
    try:
        assert (await database.preflight()).compatibility is StateCompatibility.NEW

        await database.initialize()

        def inspect(connection: sqlite3.Connection) -> tuple[int, bool, str, int, int]:
            row = connection.execute("PRAGMA user_version").fetchone()
            assert row is not None
            return (
                get_ident(),
                current_thread().daemon,
                current_thread().name,
                id(connection),
                int(row[0]),
            )

        first = await database.read(inspect)
        second = await database.read(inspect)

        assert first == second
        assert first[0] != event_loop_thread
        assert first[1] is True
        assert first[2] == "awesome-application-sqlite"
        assert first[4] == 7

        def reject_path_preflight(_path: Path) -> None:
            raise AssertionError("owned connection was not used")

        monkeypatch.setattr(
            "awesome_agent.storage.application_sqlite.inspect_application_state",
            reject_path_preflight,
        )
        assert (await database.preflight()).compatibility is StateCompatibility.CURRENT
        assert await database.quick_check() is True
    finally:
        await database.aclose()


@pytest.mark.asyncio
async def test_read_and_write_transactions_commit_and_rollback(tmp_path: Path) -> None:
    database = ApplicationSQLite(tmp_path / "application.db")
    try:
        await database.initialize()
        await database.write(
            lambda connection: _execute(
                connection, "CREATE TABLE example (value TEXT NOT NULL)"
            )
        )
        await database.write(
            lambda connection: _execute(
                connection, "INSERT INTO example (value) VALUES ('kept')"
            )
        )

        def insert_then_fail(connection: sqlite3.Connection) -> None:
            connection.execute("INSERT INTO example (value) VALUES ('rolled-back')")
            raise ValueError("stop")

        with pytest.raises(ValueError, match="stop"):
            await database.write(insert_then_fail)

        values = await database.read(
            lambda connection: [
                str(row[0])
                for row in connection.execute(
                    "SELECT value FROM example ORDER BY rowid"
                ).fetchall()
            ]
        )
        assert values == ["kept"]
    finally:
        await database.aclose()


@pytest.mark.asyncio
async def test_rollback_outcome_error_fails_worker_closed(
    tmp_path: Path,
) -> None:
    path = tmp_path / "application.db"
    database = ApplicationSQLite(path)
    await database.initialize()
    await database.write(
        lambda connection: _execute(
            connection, "CREATE TABLE example (value TEXT NOT NULL)"
        )
    )
    owned_connection = database._connection
    assert isinstance(owned_connection, sqlite3.Connection)
    delegate: sqlite3.Connection = owned_connection
    rollback_error = sqlite3.OperationalError("injected ROLLBACK outcome error")

    class RollbackOutcomeConnection:
        def execute(self, sql: str, parameters: Any = ()) -> sqlite3.Cursor:
            cursor = delegate.execute(sql, parameters)
            if sql.strip().upper() == "ROLLBACK":
                raise rollback_error
            return cursor

        def __getattr__(self, name: str) -> Any:
            return getattr(delegate, name)

    cast(Any, database)._connection = cast(
        sqlite3.Connection,
        RollbackOutcomeConnection(),
    )

    def insert_then_fail(connection: sqlite3.Connection) -> None:
        connection.execute("INSERT INTO example (value) VALUES ('rolled-back')")
        raise ValueError("stop")

    with pytest.raises(ApplicationSQLiteUnavailable) as captured:
        await database.write(insert_then_fail)
    assert captured.value.__cause__ is not None
    assert captured.value.__cause__.__cause__ is rollback_error
    with pytest.raises(ApplicationSQLiteUnavailable):
        await database.read(lambda _connection: None)
    await database.aclose()
    await database.aclose()

    reopened = ApplicationSQLite(path)
    try:
        await reopened.initialize()
        assert (
            await reopened.read(
                lambda connection: int(
                    connection.execute("SELECT COUNT(*) FROM example").fetchone()[0]
                )
            )
            == 0
        )
    finally:
        await reopened.aclose()


@pytest.mark.asyncio
async def test_sqlite_owned_results_are_rejected_before_commit(tmp_path: Path) -> None:
    database = ApplicationSQLite(tmp_path / "application.db")
    try:
        await database.initialize()
        await database.write(
            lambda connection: _execute(
                connection, "CREATE TABLE example (value TEXT NOT NULL)"
            )
        )

        with pytest.raises(ApplicationSQLiteResultError):
            await database.write(
                lambda connection: connection.execute(
                    "INSERT INTO example (value) VALUES ('rolled-back')"
                )
            )
        with pytest.raises(ApplicationSQLiteResultError):
            await database.read(lambda connection: connection)
        with pytest.raises(ApplicationSQLiteResultError):
            await database.read(
                lambda connection: {"row": connection.execute("SELECT 1").fetchone()}
            )

        assert (
            await database.read(
                lambda connection: int(
                    connection.execute("SELECT COUNT(*) FROM example").fetchone()[0]
                )
            )
            == 0
        )
    finally:
        await database.aclose()


@pytest.mark.asyncio
async def test_operation_error_does_not_stop_the_worker(tmp_path: Path) -> None:
    database = ApplicationSQLite(tmp_path / "application.db")
    try:
        with pytest.raises(ApplicationSQLiteUnavailable):
            await database.read(lambda _connection: None)

        await database.initialize()

        def fail(_connection: sqlite3.Connection) -> None:
            raise LookupError("operation failed")

        with pytest.raises(LookupError, match="operation failed"):
            await database.read(fail)

        assert await database.read(lambda _connection: 41 + 1) == 42
    finally:
        await database.aclose()


@pytest.mark.asyncio
async def test_cancelled_initialize_finishes_before_reraising(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = ApplicationSQLite(tmp_path / "application.db")
    started = Event()
    release = Event()

    def blocked_initialize(path: Path) -> None:
        started.set()
        release.wait()
        initialize_application_database(path)

    try:
        monkeypatch.setattr(
            "awesome_agent.storage.application_sqlite.initialize_application_database",
            blocked_initialize,
        )
        initialize = asyncio.create_task(database.initialize())
        await _wait_for(started)
        initialize.cancel("first cancellation")
        await asyncio.sleep(0)
        initialize.cancel("second cancellation")
        await asyncio.sleep(0)
        assert not initialize.done()

        release.set()
        with pytest.raises(asyncio.CancelledError) as raised:
            await initialize
        assert raised.value.args == ("first cancellation",)
        assert await database.quick_check() is True
    finally:
        release.set()
        await database.aclose()


@pytest.mark.asyncio
async def test_bounded_queue_rejects_excess_work_without_blocking(
    tmp_path: Path,
) -> None:
    database = ApplicationSQLite(
        tmp_path / "application.db",
        queue_capacity=1,
    )
    started = Event()
    release = Event()

    def block(_connection: sqlite3.Connection) -> None:
        started.set()
        release.wait()

    try:
        await database.initialize()
        active = asyncio.create_task(database.write(block))
        await _wait_for(started)
        queued = asyncio.create_task(database.read(lambda _connection: None))
        await asyncio.sleep(0)

        with pytest.raises(ApplicationSQLiteBusy):
            await database.read(lambda _connection: None)

        release.set()
        await active
        await queued
    finally:
        release.set()
        await database.aclose()


@pytest.mark.asyncio
async def test_worker_executes_admitted_operations_in_fifo_order(
    tmp_path: Path,
) -> None:
    database = ApplicationSQLite(
        tmp_path / "application.db",
        queue_capacity=4,
    )
    started = Event()
    release = Event()
    order: list[int] = []

    def block(_connection: sqlite3.Connection) -> None:
        started.set()
        release.wait()
        order.append(0)

    def append(value: int) -> Callable[[sqlite3.Connection], None]:
        def operation(_connection: sqlite3.Connection) -> None:
            order.append(value)

        return operation

    try:
        await database.initialize()
        active = asyncio.create_task(database.read(block))
        await _wait_for(started)
        queued = []
        for value in (1, 2, 3):
            queued.append(asyncio.create_task(database.write(append(value))))
            await asyncio.sleep(0)

        release.set()
        await active
        await asyncio.gather(*queued)

        assert order == [0, 1, 2, 3]
    finally:
        release.set()
        await database.aclose()


@pytest.mark.asyncio
async def test_caller_cancellation_does_not_cancel_an_admitted_write(
    tmp_path: Path,
) -> None:
    database = ApplicationSQLite(tmp_path / "application.db")
    started = Event()
    release = Event()

    def insert_after_release(connection: sqlite3.Connection) -> None:
        started.set()
        release.wait()
        connection.execute("INSERT INTO example (value) VALUES ('committed')")

    try:
        await database.initialize()
        await database.write(
            lambda connection: _execute(
                connection, "CREATE TABLE example (value TEXT NOT NULL)"
            )
        )
        write = asyncio.create_task(database.write(insert_after_release))
        await _wait_for(started)

        write.cancel("first cancellation")
        await asyncio.sleep(0)
        assert not write.done()
        write.cancel("second cancellation")
        await asyncio.sleep(0)
        assert not write.done()

        release.set()
        with pytest.raises(asyncio.CancelledError) as raised:
            await write
        assert raised.value.args == ("first cancellation",)

        count = await database.read(
            lambda connection: int(
                connection.execute("SELECT COUNT(*) FROM example").fetchone()[0]
            )
        )
        assert count == 1
    finally:
        release.set()
        await database.aclose()


@pytest.mark.asyncio
async def test_read_cancellation_stops_waiting_without_stopping_worker(
    tmp_path: Path,
) -> None:
    database = ApplicationSQLite(tmp_path / "application.db")
    started = Event()
    release = Event()

    def block(_connection: sqlite3.Connection) -> int:
        started.set()
        release.wait()
        return 7

    try:
        await database.initialize()
        read = asyncio.create_task(database.read(block))
        await _wait_for(started)
        read.cancel()

        with pytest.raises(asyncio.CancelledError):
            await read

        release.set()
        assert await database.read(lambda _connection: 8) == 8
    finally:
        release.set()
        await database.aclose()


@pytest.mark.asyncio
async def test_close_drains_accepted_work_rejects_new_work_and_is_idempotent(
    tmp_path: Path,
) -> None:
    database = ApplicationSQLite(tmp_path / "application.db")
    started = Event()
    release = Event()
    order: list[str] = []

    def block(_connection: sqlite3.Connection) -> None:
        started.set()
        release.wait()
        order.append("active")

    try:
        await database.initialize()
        active = asyncio.create_task(database.read(block))
        await _wait_for(started)
        queued = asyncio.create_task(
            database.write(lambda _connection: order.append("queued"))
        )
        await asyncio.sleep(0)
        close = asyncio.create_task(database.aclose())
        await asyncio.sleep(0)

        with pytest.raises(ApplicationSQLiteClosed):
            await database.read(lambda _connection: None)

        release.set()
        await asyncio.gather(active, queued, close)
        assert order == ["active", "queued"]

        await database.aclose()
        with pytest.raises(ApplicationSQLiteClosed):
            await database.read(lambda _connection: None)
    finally:
        release.set()
        await database.aclose()


@pytest.mark.asyncio
async def test_cancelled_close_still_drains_before_reraising(tmp_path: Path) -> None:
    database = ApplicationSQLite(tmp_path / "application.db")
    started = Event()
    release = Event()

    def block(_connection: sqlite3.Connection) -> None:
        started.set()
        release.wait()

    try:
        await database.initialize()
        active = asyncio.create_task(database.read(block))
        await _wait_for(started)
        close = asyncio.create_task(database.aclose())
        await asyncio.sleep(0)
        close.cancel("close cancellation")
        await asyncio.sleep(0)
        assert not close.done()

        release.set()
        await active
        with pytest.raises(asyncio.CancelledError):
            await close
        await database.aclose()
    finally:
        release.set()
        await database.aclose()


@pytest.mark.asyncio
async def test_reset_replaces_state_and_reopens_the_owned_connection(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    lease = StateLease.acquire(home, StateLeaseMode.EXCLUSIVE)
    database = ApplicationSQLite(home / "state" / "application.db")
    try:
        await database.initialize()
        await database.write(
            lambda connection: _execute(
                connection, "CREATE TABLE reset_marker (value TEXT NOT NULL)"
            )
        )

        await database.reset(lease)

        marker_exists = await database.read(
            lambda connection: (
                connection.execute(
                    "SELECT 1 FROM sqlite_schema WHERE name = 'reset_marker'"
                ).fetchone()
                is not None
            )
        )
        assert marker_exists is False
        assert await database.quick_check() is True
    finally:
        await database.aclose()
        lease.close()


@pytest.mark.asyncio
async def test_reset_rejects_nonexclusive_lease_without_closing_connection(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    lease = StateLease.acquire(home, StateLeaseMode.SHARED)
    database = ApplicationSQLite(home / "state" / "application.db")
    try:
        await database.initialize()

        with pytest.raises(StateResetError, match="exclusive_lease_required"):
            await database.reset(lease)
        assert await database.quick_check() is True
        assert await database.read(lambda _connection: 7) == 7

        lease.close()
        with pytest.raises(StateResetError, match="exclusive_lease_required"):
            await database.reset(lease)
        assert await database.quick_check() is True
        assert await database.read(lambda _connection: 8) == 8
    finally:
        await database.aclose()
        lease.close()


@pytest.mark.asyncio
async def test_cancelled_reset_keeps_exclusive_lease_until_worker_finishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    lease = StateLease.acquire(home, StateLeaseMode.EXCLUSIVE)
    database = ApplicationSQLite(home / "state" / "application.db")
    started = Event()
    release = Event()

    def blocked_reset(active_lease: StateLease) -> None:
        started.set()
        release.wait()
        reset_local_state(active_lease)

    try:
        await database.initialize()
        monkeypatch.setattr(
            "awesome_agent.storage.application_sqlite.reset_local_state",
            blocked_reset,
        )
        reset = asyncio.create_task(database.reset(lease))
        await _wait_for(started)
        reset.cancel("first cancellation")
        await asyncio.sleep(0)
        reset.cancel("second cancellation")
        await asyncio.sleep(0)
        assert not reset.done()
        assert lease.active is True

        release.set()
        with pytest.raises(asyncio.CancelledError) as raised:
            await reset
        assert raised.value.args == ("first cancellation",)
        assert await database.quick_check() is True
    finally:
        release.set()
        await database.aclose()
        lease.close()


def test_worker_start_failure_is_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_start(_thread: Thread) -> None:
        raise RuntimeError("thread unavailable")

    monkeypatch.setattr(Thread, "start", fail_start)

    with pytest.raises(ApplicationSQLiteUnavailable) as raised:
        ApplicationSQLite(tmp_path / "application.db")

    assert isinstance(raised.value.__cause__, RuntimeError)
