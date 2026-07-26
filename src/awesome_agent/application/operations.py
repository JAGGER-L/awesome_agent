from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, TypeVar, cast

from awesome_agent.application.foreground import (
    ForegroundArbiter,
    ForegroundBusy,
    ForegroundLease,
)
from awesome_agent.core.cancellation import finish_cancellation_safe
from awesome_agent.core.contracts import new_identifier
from awesome_agent.core.events import (
    EventEmitter,
    EventType,
    OperationLifecyclePayload,
)

T = TypeVar("T")
logger = logging.getLogger(__name__)
_COMMITTED_PUBLICATION_TIMEOUT_SECONDS = 10.0
type TerminalEventType = Literal[
    EventType.OPERATION_COMPLETED,
    EventType.OPERATION_FAILED,
    EventType.OPERATION_CANCELLED,
]


class _OperationPhase(StrEnum):
    RESERVED = "reserved"
    STARTING = "starting"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMMITTING = "committing"
    COMMITTED = "committed"


class OperationBusy(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class OperationHandle[T]:
    operation_id: str
    task: asyncio.Task[T]


@dataclass(slots=True)
class OperationReservation:
    operation_id: str
    lease: ForegroundLease
    consumed: bool = False


@dataclass(frozen=True, slots=True)
class OperationContinuation:
    interaction_id: str
    interaction_generation: int
    thread_id: str
    turn_id: str


class OperationController:
    def __init__(
        self,
        emitter: EventEmitter,
        foreground: ForegroundArbiter | None = None,
        admission_gate: Callable[[OperationContinuation | None], bool] | None = None,
    ) -> None:
        self._emitter = emitter
        self._foreground = foreground or ForegroundArbiter()
        self._admission_gate = admission_gate
        self._active_id: str | None = None
        self._active_task: asyncio.Task[object] | None = None
        self._active_reservation: OperationReservation | None = None
        self._starter_task: asyncio.Task[object] | None = None
        self._active_thread_id: str | None = None
        self._active_turn_id: str | None = None
        self._active_phase: _OperationPhase | None = None
        self._committed_terminal: TerminalEventType | None = None

    @property
    def active_operation_id(self) -> str | None:
        return self._active_id

    @property
    def active_thread_id(self) -> str | None:
        return self._active_thread_id

    @property
    def active_turn_id(self) -> str | None:
        return self._active_turn_id

    async def run(
        self,
        factory: Callable[[str], Awaitable[T]],
        *,
        thread_id: str | None = None,
        turn_id: str | None = None,
        client_message_id: str | None = None,
    ) -> T:
        handle = await self.start(
            factory,
            thread_id=thread_id,
            turn_id=turn_id,
            client_message_id=client_message_id,
        )
        return await handle.task

    async def start(
        self,
        factory: Callable[[str], Awaitable[T]],
        *,
        thread_id: str | None = None,
        turn_id: str | None = None,
        client_message_id: str | None = None,
    ) -> OperationHandle[T]:
        reservation = self.reserve()
        return await self.start_reserved(
            reservation,
            factory,
            thread_id=thread_id,
            turn_id=turn_id,
            client_message_id=client_message_id,
        )

    def reserve(
        self,
        *,
        continuation: OperationContinuation | None = None,
    ) -> OperationReservation:
        if self._active_id is not None:
            raise OperationBusy("Another operation is active.")
        try:
            lease = self._foreground.acquire_operation()
        except ForegroundBusy as error:
            raise OperationBusy("Another foreground action is active.") from error
        try:
            if self._admission_gate is not None and not self._admission_gate(
                continuation
            ):
                raise OperationBusy("A pending interaction blocks new operations.")
        except BaseException:
            lease.release()
            raise
        operation_id = new_identifier("operation")
        self._active_id = operation_id
        self._active_phase = _OperationPhase.RESERVED
        reservation = OperationReservation(operation_id=operation_id, lease=lease)
        self._active_reservation = reservation
        self._starter_task = cast(asyncio.Task[object] | None, asyncio.current_task())
        return reservation

    def abort(self, reservation: OperationReservation) -> None:
        if reservation.consumed:
            return
        reservation.consumed = True
        if self._active_id == reservation.operation_id:
            self._active_id = None
            self._active_phase = None
            self._committed_terminal = None
            self._active_reservation = None
            self._starter_task = None
        reservation.lease.release()

    async def start_reserved(
        self,
        reservation: OperationReservation,
        factory: Callable[[str], Awaitable[T]],
        *,
        thread_id: str | None = None,
        turn_id: str | None = None,
        client_message_id: str | None = None,
    ) -> OperationHandle[T]:
        if reservation.consumed or self._active_id != reservation.operation_id:
            raise RuntimeError("Operation reservation is not active.")
        reservation.consumed = True
        operation_id = reservation.operation_id
        self._active_phase = _OperationPhase.STARTING
        self._active_thread_id = thread_id
        self._active_turn_id = turn_id
        try:
            await self._emitter.emit(
                OperationLifecyclePayload(kind=EventType.OPERATION_STARTED),
                thread_id=thread_id,
                turn_id=turn_id,
                operation_id=operation_id,
                client_message_id=client_message_id,
            )
        except BaseException:
            self._active_id = None
            self._active_phase = None
            self._committed_terminal = None
            self._active_reservation = None
            self._starter_task = None
            self._active_thread_id = None
            self._active_turn_id = None
            reservation.lease.release()
            raise

        ready = asyncio.Event()
        if self._active_phase is _OperationPhase.STARTING:
            self._active_phase = _OperationPhase.RUNNING

        async def invoke() -> T:
            ready.set()
            if self._active_phase is _OperationPhase.CANCELLING:
                with suppress(Exception, asyncio.CancelledError):
                    await self._publish_cancelling(
                        operation_id,
                        lambda: self._terminal(
                            EventType.OPERATION_CANCELLED,
                            operation_id,
                            thread_id,
                            turn_id,
                            client_message_id,
                        ),
                    )
                raise asyncio.CancelledError
            try:
                result = await factory(operation_id)
            except asyncio.CancelledError as cancellation:
                if self._active_phase is _OperationPhase.COMMITTED:
                    await self._publish_committed_terminal(
                        operation_id,
                        thread_id,
                        turn_id,
                        client_message_id,
                    )
                    raise cancellation
                self._active_phase = _OperationPhase.CANCELLING
                with suppress(Exception, asyncio.CancelledError):
                    await self._publish_cancelling(
                        operation_id,
                        lambda: self._terminal(
                            EventType.OPERATION_CANCELLED,
                            operation_id,
                            thread_id,
                            turn_id,
                            client_message_id,
                        ),
                    )
                raise cancellation
            except Exception as error:
                if self._current_phase() is _OperationPhase.CANCELLING:
                    with suppress(Exception, asyncio.CancelledError):
                        await self._publish_cancelling(
                            operation_id,
                            lambda: self._terminal(
                                EventType.OPERATION_CANCELLED,
                                operation_id,
                                thread_id,
                                turn_id,
                                client_message_id,
                            ),
                        )
                    raise asyncio.CancelledError from error
                if self._active_phase is _OperationPhase.RUNNING:
                    self._commit_terminal(EventType.OPERATION_FAILED)
                await self._publish_committed_terminal(
                    operation_id,
                    thread_id,
                    turn_id,
                    client_message_id,
                )
                if self._committed_terminal is not EventType.OPERATION_FAILED:
                    raise RuntimeError(
                        "Operation failed after completion was committed."
                    ) from error
                raise error
            else:
                if self._current_phase() is _OperationPhase.CANCELLING:
                    with suppress(Exception, asyncio.CancelledError):
                        await self._publish_cancelling(
                            operation_id,
                            lambda: self._terminal(
                                EventType.OPERATION_CANCELLED,
                                operation_id,
                                thread_id,
                                turn_id,
                                client_message_id,
                            ),
                        )
                    raise asyncio.CancelledError
                if self._active_phase is _OperationPhase.RUNNING:
                    self._commit_terminal(EventType.OPERATION_COMPLETED)
                await self._publish_committed_terminal(
                    operation_id,
                    thread_id,
                    turn_id,
                    client_message_id,
                )
                if self._committed_terminal is not EventType.OPERATION_COMPLETED:
                    raise RuntimeError(
                        "Operation completed after failure was committed."
                    )
                return result
            finally:
                self._active_id = None
                self._active_phase = None
                self._committed_terminal = None
                self._active_task = None
                self._active_thread_id = None
                self._active_turn_id = None
                reservation.lease.release()

        task: asyncio.Task[T] = asyncio.create_task(invoke())
        self._active_task = cast(asyncio.Task[object], task)
        self._active_reservation = None
        try:
            await ready.wait()
        except asyncio.CancelledError:
            if self._active_phase is _OperationPhase.RUNNING:
                self._active_phase = _OperationPhase.CANCELLING
                task.cancel()
            if self._active_phase is _OperationPhase.CANCELLING:
                with suppress(asyncio.CancelledError, Exception):
                    await task
            else:
                with suppress(asyncio.CancelledError, Exception):
                    await asyncio.shield(task)
            if self._active_id == operation_id:
                with suppress(Exception):
                    await self._publish_cancelling(
                        operation_id,
                        lambda: self._terminal(
                            EventType.OPERATION_CANCELLED,
                            operation_id,
                            thread_id,
                            turn_id,
                            client_message_id,
                        ),
                    )
                self._active_id = None
                self._active_phase = None
                self._committed_terminal = None
                self._active_task = None
                self._active_thread_id = None
                self._active_turn_id = None
                reservation.lease.release()
            self._starter_task = None
            raise
        self._starter_task = None
        return OperationHandle(operation_id=operation_id, task=task)

    async def commit_completed[T](
        self,
        operation_id: str,
        action: Callable[[], Awaitable[T]],
    ) -> T:
        """Finish one durable success before exposing caller cancellation."""

        self._require_running(operation_id)
        self._active_phase = _OperationPhase.COMMITTING
        try:
            result, cancellation = await finish_cancellation_safe(action())
        except BaseException:
            self._commit_terminal(EventType.OPERATION_FAILED)
            raise
        self._commit_terminal(EventType.OPERATION_COMPLETED)
        if cancellation is not None:
            raise cancellation
        return result

    async def commit_failed[T](
        self,
        operation_id: str,
        action: Callable[[], Awaitable[T]],
    ) -> T:
        """Finish one durable failure before exposing caller cancellation."""

        self._require_running(operation_id)
        self._active_phase = _OperationPhase.COMMITTING
        try:
            result, cancellation = await finish_cancellation_safe(action())
        finally:
            self._commit_terminal(EventType.OPERATION_FAILED)
        if cancellation is not None:
            raise cancellation
        return result

    async def publish_committed(
        self,
        operation_id: str,
        publication: Callable[[], Awaitable[None]],
    ) -> None:
        """Finish a committed Turn publication despite later task cancellation."""

        await self._publish_committed(operation_id, publication)

    def _require_running(self, operation_id: str) -> None:
        if self._active_id != operation_id:
            raise RuntimeError("Operation is not active.")
        if self._active_phase is _OperationPhase.CANCELLING:
            raise asyncio.CancelledError
        if self._active_phase is not _OperationPhase.RUNNING:
            raise RuntimeError("Operation outcome is already committed.")

    def _current_phase(self) -> _OperationPhase | None:
        """Read phase after an await where another task may have cancelled."""

        return self._active_phase

    def _commit_terminal(self, terminal: TerminalEventType) -> None:
        if terminal is EventType.OPERATION_CANCELLED:
            raise RuntimeError("Cancellation is not a committed outcome.")
        self._active_phase = _OperationPhase.COMMITTED
        self._committed_terminal = terminal

    async def _publish_committed(
        self,
        operation_id: str,
        publication: Callable[[], Awaitable[None]],
    ) -> None:
        if (
            self._active_id != operation_id
            or self._active_phase is not _OperationPhase.COMMITTED
        ):
            raise RuntimeError("Operation outcome is not committed.")
        await self._publish_bounded(operation_id, publication)

    async def _publish_cancelling(
        self,
        operation_id: str,
        publication: Callable[[], Awaitable[None]],
    ) -> None:
        if (
            self._active_id != operation_id
            or self._active_phase is not _OperationPhase.CANCELLING
        ):
            raise RuntimeError("Operation cancellation is not active.")
        await self._publish_bounded(operation_id, publication)

    async def _publish_committed_terminal(
        self,
        operation_id: str,
        thread_id: str | None,
        turn_id: str | None,
        client_message_id: str | None,
    ) -> None:
        terminal = self._committed_terminal
        if terminal is None:
            raise RuntimeError("Operation has no committed terminal outcome.")
        try:
            await self._publish_committed(
                operation_id,
                lambda: self._terminal(
                    terminal,
                    operation_id,
                    thread_id,
                    turn_id,
                    client_message_id,
                ),
            )
        except Exception:
            logger.warning(
                "Committed Operation event delivery failed.",
                exc_info=True,
            )

    @staticmethod
    async def _publish_bounded(
        operation_id: str,
        publication: Callable[[], Awaitable[None]],
    ) -> None:
        task: asyncio.Future[None] = asyncio.ensure_future(publication())
        deadline = (
            asyncio.get_running_loop().time() + _COMMITTED_PUBLICATION_TIMEOUT_SECONDS
        )
        while not task.done():
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                await asyncio.wait((task,), timeout=remaining)
            except asyncio.CancelledError:
                continue
        if not task.done():
            task.cancel()
            task.add_done_callback(_consume_task)
            raise TimeoutError("Committed operation publication timed out.")
        if task.cancelled():
            raise RuntimeError("Operation publication was cancelled internally.")
        task.result()

    async def _terminal(
        self,
        event_type: TerminalEventType,
        operation_id: str,
        thread_id: str | None,
        turn_id: str | None,
        client_message_id: str | None,
    ) -> None:
        await self._emitter.emit(
            OperationLifecyclePayload(
                kind=event_type,
            ),
            thread_id=thread_id,
            turn_id=turn_id,
            operation_id=operation_id,
            client_message_id=client_message_id,
        )

    async def cancel(self, operation_id: str) -> bool:
        if (
            self._active_id == operation_id
            and self._active_phase is _OperationPhase.STARTING
        ):
            self._active_phase = _OperationPhase.CANCELLING
            return True
        if (
            self._active_id != operation_id
            or self._active_task is None
            or self._active_phase is not _OperationPhase.RUNNING
        ):
            return False
        self._active_phase = _OperationPhase.CANCELLING
        self._active_task.cancel()
        return True

    async def shutdown(self) -> None:
        self._foreground.begin_closing()
        starter = self._starter_task
        if starter is not None and starter is not asyncio.current_task():
            starter.cancel()
            with suppress(asyncio.CancelledError):
                await starter
        elif starter is asyncio.current_task() and self._active_reservation is not None:
            self.abort(self._active_reservation)
        task = self._active_task
        if task is not None:
            if self._active_phase is _OperationPhase.RUNNING:
                self._active_phase = _OperationPhase.CANCELLING
                task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await asyncio.shield(task)
        reservation = self._active_reservation
        if reservation is not None:
            self.abort(reservation)


def _consume_task(task: asyncio.Future[None]) -> None:
    if task.cancelled():
        return
    with suppress(Exception):
        task.exception()
