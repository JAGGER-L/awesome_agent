from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Literal, TypeVar, cast

from awesome_agent.application.foreground import (
    ForegroundArbiter,
    ForegroundBusy,
    ForegroundLease,
)
from awesome_agent.core.contracts import new_identifier
from awesome_agent.core.events import (
    EventEmitter,
    EventType,
    OperationLifecyclePayload,
)

T = TypeVar("T")
type TerminalEventType = Literal[
    EventType.OPERATION_COMPLETED,
    EventType.OPERATION_FAILED,
    EventType.OPERATION_CANCELLED,
]


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


class OperationController:
    def __init__(
        self,
        emitter: EventEmitter,
        foreground: ForegroundArbiter | None = None,
    ) -> None:
        self._emitter = emitter
        self._foreground = foreground or ForegroundArbiter()
        self._active_id: str | None = None
        self._active_task: asyncio.Task[object] | None = None
        self._active_reservation: OperationReservation | None = None
        self._starter_task: asyncio.Task[object] | None = None
        self._active_thread_id: str | None = None
        self._active_turn_id: str | None = None

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

    def reserve(self) -> OperationReservation:
        if self._active_id is not None:
            raise OperationBusy("Another operation is active.")
        try:
            lease = self._foreground.acquire_operation()
        except ForegroundBusy as error:
            raise OperationBusy("Another foreground action is active.") from error
        operation_id = new_identifier("operation")
        self._active_id = operation_id
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
            self._active_reservation = None
            self._starter_task = None
            self._active_thread_id = None
            self._active_turn_id = None
            reservation.lease.release()
            raise

        ready = asyncio.Event()

        async def invoke() -> T:
            ready.set()
            try:
                result = await factory(operation_id)
            except asyncio.CancelledError:
                await self._terminal(
                    EventType.OPERATION_CANCELLED,
                    operation_id,
                    thread_id,
                    turn_id,
                    client_message_id,
                )
                raise
            except Exception:
                await self._terminal(
                    EventType.OPERATION_FAILED,
                    operation_id,
                    thread_id,
                    turn_id,
                    client_message_id,
                )
                raise
            else:
                await self._terminal(
                    EventType.OPERATION_COMPLETED,
                    operation_id,
                    thread_id,
                    turn_id,
                    client_message_id,
                )
                return result
            finally:
                self._active_id = None
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
            task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await task
            if self._active_id == operation_id:
                with suppress(Exception):
                    await self._terminal(
                        EventType.OPERATION_CANCELLED,
                        operation_id,
                        thread_id,
                        turn_id,
                        client_message_id,
                    )
                self._active_id = None
                self._active_task = None
                self._active_thread_id = None
                self._active_turn_id = None
                reservation.lease.release()
            self._starter_task = None
            raise
        self._starter_task = None
        return OperationHandle(operation_id=operation_id, task=task)

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
        if self._active_id != operation_id or self._active_task is None:
            return False
        self._active_task.cancel()
        return True

    async def shutdown(self) -> None:
        starter = self._starter_task
        if starter is not None and starter is not asyncio.current_task():
            starter.cancel()
            with suppress(asyncio.CancelledError):
                await starter
        elif starter is asyncio.current_task() and self._active_reservation is not None:
            self.abort(self._active_reservation)
        task = self._active_task
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        reservation = self._active_reservation
        if reservation is not None:
            self.abort(reservation)
