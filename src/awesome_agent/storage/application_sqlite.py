from __future__ import annotations

import asyncio
import os
import sqlite3
from collections.abc import Callable, Mapping
from concurrent.futures import Future as ConcurrentFuture
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from queue import Empty, Queue
from threading import BoundedSemaphore, Lock, Thread
from typing import cast

from awesome_agent.storage.compatibility import (
    ApplicationStateUnavailable,
    StatePreflight,
    classify_application_schema,
    inspect_application_state,
)
from awesome_agent.storage.database import _connect, initialize_application_database
from awesome_agent.storage.migrations import (
    APPLICATION_MIGRATIONS,
    ApplicationMigrationError,
    ApplicationMigrationOutcomeUnknown,
    ApplicationMigrationRegistry,
    migrate_application_database,
    validate_application_migration_boundary,
)
from awesome_agent.storage.state_lease import StateLease, StateLeaseMode
from awesome_agent.storage.state_recovery import StateResetError, reset_local_state


class ApplicationSQLiteError(RuntimeError):
    """Base error for the process-owned Application SQLite worker."""


class ApplicationSQLiteBusy(ApplicationSQLiteError):
    """Raised when the bounded worker queue cannot admit another operation."""


class ApplicationSQLiteClosed(ApplicationSQLiteError):
    """Raised when work is submitted after shutdown has started."""


class ApplicationSQLiteUnavailable(
    ApplicationStateUnavailable,
    ApplicationSQLiteError,
):
    """Raised when the dedicated worker cannot accept or execute work."""

    def __init__(self, path: Path) -> None:
        resolved = path.expanduser().resolve()
        ApplicationSQLiteError.__init__(
            self,
            f"Application SQLite worker is unavailable: {resolved}",
        )
        self.path = resolved


class ApplicationSQLiteResultError(ApplicationSQLiteError):
    """Raised when an operation tries to expose a SQLite-owned object."""


class _ApplicationSQLiteFatal(RuntimeError):
    """Stops the worker when transaction outcome can no longer be proven."""

    def __init__(
        self,
        operation_error: BaseException,
        rollback_error: BaseException,
    ) -> None:
        super().__init__("Application SQLite rollback outcome is unknown.")
        self.operation_error = operation_error
        self.rollback_error = rollback_error


class _WorkerState(StrEnum):
    OPEN = "open"
    CLOSING = "closing"
    CLOSED = "closed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class _WorkItem:
    operation: Callable[[], object]
    future: ConcurrentFuture[object]


@dataclass(frozen=True, slots=True)
class _CloseItem:
    future: ConcurrentFuture[None]


type _QueueItem = _WorkItem | _CloseItem


class ApplicationSQLite:
    """Serialize Application SQLite access on one connection-owning thread.

    The queue is intentionally bounded. Reads may stop waiting when their caller
    is cancelled. Durable writes and lifecycle controls instead finish on the
    worker, observe their result, and then re-raise the first cancellation, so an
    in-flight SQLite transaction never has an unknowable commit state.
    """

    def __init__(
        self,
        path: Path,
        *,
        queue_capacity: int = 256,
        migration_registry: ApplicationMigrationRegistry = APPLICATION_MIGRATIONS,
    ) -> None:
        if queue_capacity < 1:
            raise ValueError("queue_capacity must be at least 1")
        self._lexical_path = Path(os.path.abspath(path.expanduser()))
        self._path = self._lexical_path.resolve()
        self._migration_registry = migration_registry
        # One extra queue position is reserved for the close sentinel. The
        # semaphore enforces the advertised bound for normal queued operations;
        # the operation currently executing on the worker does not consume a
        # queue position.
        self._queue: Queue[_QueueItem] = Queue(maxsize=queue_capacity + 1)
        self._capacity = BoundedSemaphore(queue_capacity)
        self._state_lock = Lock()
        self._state = _WorkerState.OPEN
        self._close_future: ConcurrentFuture[None] | None = None
        self._connection: sqlite3.Connection | None = None
        self._thread = Thread(
            target=self._worker_main,
            name="awesome-application-sqlite",
            daemon=True,
        )
        try:
            self._thread.start()
        except BaseException as error:
            with self._state_lock:
                self._state = _WorkerState.FAILED
            raise ApplicationSQLiteUnavailable(self._path) from error

    async def preflight(self) -> StatePreflight:
        """Inspect state compatibility without mutating the database."""

        return await self._submit(self._preflight_on_worker)

    async def initialize(self) -> None:
        """Initialize schema 7 when new, then retain one owned connection."""

        await self._submit(self._initialize_on_worker, durable=True)

    async def reset(self, lease: StateLease) -> None:
        """Replace local state under an exclusive lease and reopen the connection."""

        if not lease.active or lease.mode is not StateLeaseMode.EXCLUSIVE:
            raise StateResetError("exclusive_lease_required", self._path.parent)
        expected_path = (lease.home / "state" / "application.db").resolve()
        if expected_path != self._path:
            raise ValueError("State lease does not own this Application database.")

        def reset_on_worker() -> None:
            self._close_connection_on_worker()
            reset_local_state(lease)
            self._open_connection_on_worker()

        await self._submit(reset_on_worker, durable=True)

    async def migrate(self, lease: StateLease) -> Path | None:
        """Back up and migrate state under one validated exclusive lease."""

        return await self._submit(
            lambda: self._migrate_on_worker(lease),
            durable=True,
        )

    async def read[T](
        self,
        operation: Callable[[sqlite3.Connection], T],
    ) -> T:
        """Run a read transaction and publish its value only after commit."""

        return await self._submit(
            lambda: self._run_transaction(operation, immediate=False)
        )

    async def write[T](
        self,
        operation: Callable[[sqlite3.Connection], T],
    ) -> T:
        """Run an immediate write transaction and publish only after commit."""

        return await self._submit(
            lambda: self._run_transaction(operation, immediate=True),
            durable=True,
        )

    async def quick_check(self) -> bool | None:
        """Return a bounded health result using the owned connection."""

        def check(connection: sqlite3.Connection) -> bool:
            row = connection.execute("PRAGMA quick_check(1)").fetchone()
            return row is not None and len(row) > 0 and row[0] == "ok"

        try:
            return await self.read(check)
        except sqlite3.OperationalError:
            return None
        except sqlite3.DatabaseError:
            return False
        except ApplicationSQLiteUnavailable:
            return None

    async def suspend(self) -> None:
        """Close the owned connection while keeping the worker reusable."""

        await self._submit(self._close_connection_on_worker, durable=True)

    async def aclose(self) -> None:
        """Drain admitted work and close the owned connection exactly once."""

        with self._state_lock:
            if self._close_future is not None:
                close_future = self._close_future
            elif self._state is _WorkerState.OPEN:
                close_future = ConcurrentFuture()
                self._close_future = close_future
                self._state = _WorkerState.CLOSING
                self._queue.put_nowait(_CloseItem(close_future))
            elif self._state in {_WorkerState.CLOSED, _WorkerState.FAILED}:
                return
            else:
                close_future = ConcurrentFuture()
                close_future.set_exception(self._unavailable())
                self._close_future = close_future
        try:
            await _await_durable_future(close_future)
        except ApplicationSQLiteUnavailable:
            with self._state_lock:
                if self._state is _WorkerState.FAILED:
                    return
            raise

    async def _submit[T](
        self,
        operation: Callable[[], T],
        *,
        durable: bool = False,
    ) -> T:
        admitted = False
        with self._state_lock:
            if self._state is _WorkerState.FAILED:
                raise self._unavailable()
            if self._state is not _WorkerState.OPEN:
                raise ApplicationSQLiteClosed(
                    "Application SQLite worker is closing or closed."
                )
            if not self._capacity.acquire(blocking=False):
                raise ApplicationSQLiteBusy("Application SQLite worker queue is full.")
            admitted = True
            future: ConcurrentFuture[object] = ConcurrentFuture()
            try:
                self._queue.put_nowait(
                    _WorkItem(
                        operation=cast("Callable[[], object]", operation),
                        future=future,
                    )
                )
            except BaseException:
                self._capacity.release()
                raise
        assert admitted
        if durable:
            return cast("T", await _await_durable_future(future))
        return cast("T", await _await_interruptible_future(future))

    def _worker_main(self) -> None:
        close_item: _CloseItem | None = None
        fatal_error: BaseException | None = None
        try:
            while True:
                item = self._queue.get()
                try:
                    if isinstance(item, _CloseItem):
                        close_item = item
                        break
                    self._capacity.release()
                    self._execute_work_item(item)
                finally:
                    self._queue.task_done()
        except BaseException as error:
            fatal_error = error
        finally:
            try:
                self._close_connection_on_worker()
            except BaseException as error:
                if fatal_error is None:
                    fatal_error = error
            if fatal_error is None:
                with self._state_lock:
                    self._state = _WorkerState.CLOSED
                if close_item is not None and not close_item.future.done():
                    close_item.future.set_result(None)
            else:
                self._fail_worker(fatal_error, close_item)

    def _execute_work_item(self, item: _WorkItem) -> None:
        try:
            result = item.operation()
        except _ApplicationSQLiteFatal as error:
            if not item.future.done():
                item.future.set_exception(self._unavailable(error))
            raise
        except BaseException as error:
            if not item.future.done():
                item.future.set_exception(error)
        else:
            if not item.future.done():
                item.future.set_result(result)

    def _fail_worker(
        self,
        cause: BaseException,
        close_item: _CloseItem | None,
    ) -> None:
        with self._state_lock:
            self._state = _WorkerState.FAILED
        if close_item is not None and not close_item.future.done():
            close_item.future.set_exception(self._unavailable(cause))
        while True:
            try:
                item = self._queue.get_nowait()
            except Empty:
                break
            try:
                if isinstance(item, _CloseItem):
                    if not item.future.done():
                        item.future.set_exception(self._unavailable(cause))
                else:
                    if not item.future.done():
                        item.future.set_exception(self._unavailable(cause))
                    self._capacity.release()
            finally:
                self._queue.task_done()

    def _initialize_on_worker(self) -> None:
        if self._connection is not None:
            return
        initialize_application_database(self._path)
        self._open_connection_on_worker()

    def _preflight_on_worker(self) -> StatePreflight:
        connection = self._connection
        if connection is None:
            return inspect_application_state(
                self._lexical_path,
                registry=self._migration_registry,
            )
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        user_objects = int(
            connection.execute(
                "SELECT COUNT(*) FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]
        )
        compatibility = classify_application_schema(
            version,
            user_objects=user_objects,
            registry=self._migration_registry,
        )
        return StatePreflight(
            compatibility=compatibility,
            found_schema=version,
            expected_schema=self._migration_registry.current,
        )

    def _migrate_on_worker(self, lease: StateLease) -> Path | None:
        validated = validate_application_migration_boundary(
            lease,
            self._lexical_path,
        )
        if validated != self._path:
            raise RuntimeError("Application migration path identity changed.")
        if self._connection is not None:
            raise ApplicationMigrationError(
                "Application migration requires an unopened runtime connection."
            )
        connection = self._open_migration_connection_on_worker()
        try:
            backup = migrate_application_database(
                connection,
                self._path,
                registry=self._migration_registry,
            )
        except ApplicationMigrationOutcomeUnknown as error:
            secondary_error = error.rollback_error
            try:
                connection.close()
            except BaseException as close_error:
                secondary_error = BaseExceptionGroup(
                    "Migration rollback and connection close both failed.",
                    [secondary_error, close_error],
                )
            raise _ApplicationSQLiteFatal(
                error.operation_error,
                secondary_error,
            ) from error
        except BaseException as operation_error:
            try:
                connection.close()
            except BaseException as close_error:
                raise _ApplicationSQLiteFatal(
                    operation_error,
                    close_error,
                ) from close_error
            raise
        try:
            connection.close()
        except BaseException as close_error:
            raise _ApplicationSQLiteFatal(
                ApplicationMigrationError(
                    "Application migration connection could not close safely."
                ),
                close_error,
            ) from close_error
        return backup

    def _open_migration_connection_on_worker(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            f"{self._path.as_uri()}?mode=rw",
            uri=True,
            timeout=5.0,
            check_same_thread=True,
            isolation_level=None,
        )
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
        except BaseException:
            connection.close()
            raise
        return connection

    def _open_connection_on_worker(self) -> None:
        if self._connection is not None:
            raise RuntimeError("Application SQLite connection is already open.")
        connection = _connect(self._path)
        connection.isolation_level = None
        self._connection = connection

    def _close_connection_on_worker(self) -> None:
        connection = self._connection
        if connection is None:
            return
        self._connection = None
        connection.close()

    def _run_transaction[T](
        self,
        operation: Callable[[sqlite3.Connection], T],
        *,
        immediate: bool,
    ) -> T:
        connection = self._connection
        if connection is None:
            raise ApplicationSQLiteUnavailable(self._path)
        connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        try:
            result = operation(connection)
            if _contains_sqlite_owned_value(result):
                raise ApplicationSQLiteResultError(
                    "SQLite Connection, Cursor, and Row values cannot leave the worker."
                )
            connection.execute("COMMIT")
        except BaseException as operation_error:
            if connection.in_transaction:
                try:
                    connection.execute("ROLLBACK")
                except BaseException as rollback_error:
                    raise _ApplicationSQLiteFatal(
                        operation_error,
                        rollback_error,
                    ) from rollback_error
            raise
        return result

    def _unavailable(
        self,
        cause: BaseException | None = None,
    ) -> ApplicationSQLiteUnavailable:
        error = ApplicationSQLiteUnavailable(self._path)
        if cause is not None:
            error.__cause__ = cause
        return error


async def _await_interruptible_future[T](future: ConcurrentFuture[T]) -> T:
    wrapped = asyncio.wrap_future(future)
    try:
        return await asyncio.shield(wrapped)
    except asyncio.CancelledError:
        wrapped.add_done_callback(_consume_background_result)
        raise


async def _await_durable_future[T](future: ConcurrentFuture[T]) -> T:
    wrapped = asyncio.wrap_future(future)
    cancellation: asyncio.CancelledError | None = None
    while True:
        try:
            result = await asyncio.shield(wrapped)
        except asyncio.CancelledError as error:
            if cancellation is None:
                cancellation = error
            continue
        except BaseException:
            if cancellation is not None:
                raise cancellation from None
            raise
        if cancellation is not None:
            raise cancellation
        return result


def _consume_background_result[T](future: asyncio.Future[T]) -> None:
    if not future.cancelled():
        future.exception()


def _contains_sqlite_owned_value(value: object) -> bool:
    return _contains_sqlite_owned_value_inner(value, seen=set())


def _contains_sqlite_owned_value_inner(value: object, *, seen: set[int]) -> bool:
    if isinstance(value, (sqlite3.Connection, sqlite3.Cursor, sqlite3.Row)):
        return True
    if isinstance(value, Mapping):
        identity = id(value)
        if identity in seen:
            return False
        seen.add(identity)
        return any(
            _contains_sqlite_owned_value_inner(item, seen=seen)
            for pair in value.items()
            for item in pair
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        identity = id(value)
        if identity in seen:
            return False
        seen.add(identity)
        return any(
            _contains_sqlite_owned_value_inner(item, seen=seen) for item in value
        )
    return False
