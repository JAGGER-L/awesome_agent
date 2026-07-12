from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Literal, TypeVar, cast

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


class OperationController:
    def __init__(self, emitter: EventEmitter) -> None:
        self._emitter = emitter
        self._active_id: str | None = None
        self._active_task: asyncio.Task[object] | None = None

    @property
    def active_operation_id(self) -> str | None:
        return self._active_id

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
        if self._active_id is not None:
            raise OperationBusy("Another operation is active.")
        operation_id = new_identifier("operation")

        self._active_id = operation_id
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

        task: asyncio.Task[T] = asyncio.create_task(invoke())
        self._active_task = cast(asyncio.Task[object], task)
        await ready.wait()
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
        task = self._active_task
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
