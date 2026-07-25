from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum


class ForegroundKind(StrEnum):
    OPERATION = "operation"
    EXCLUSIVE = "exclusive"
    RESOLVING_INTERACTION = "resolving_interaction"


class ForegroundBusy(RuntimeError):
    pass


@dataclass(slots=True)
class ForegroundLease:
    _arbiter: ForegroundArbiter
    kind: ForegroundKind
    _released: bool = False

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        self._arbiter._release(self)

    async def __aenter__(self) -> ForegroundLease:
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        traceback: object,
    ) -> None:
        del exc_type, exc, traceback
        self.release()


class ForegroundArbiter:
    """Atomic, session-local authority for foreground mutation ownership."""

    def __init__(self) -> None:
        self._lease: ForegroundLease | None = None
        self._owner: asyncio.Task[object] | None = None
        self._closing = False
        self._idle = asyncio.Event()
        self._idle.set()

    @property
    def active_kind(self) -> ForegroundKind | None:
        return self._lease.kind if self._lease is not None else None

    @property
    def operation_active(self) -> bool:
        return self.active_kind is ForegroundKind.OPERATION

    @property
    def exclusive_active(self) -> bool:
        return self.active_kind in {
            ForegroundKind.EXCLUSIVE,
            ForegroundKind.RESOLVING_INTERACTION,
        }

    @property
    def closing(self) -> bool:
        return self._closing

    def acquire_operation(self) -> ForegroundLease:
        return self._acquire(ForegroundKind.OPERATION)

    def acquire_exclusive(self) -> ForegroundLease:
        return self._acquire(ForegroundKind.EXCLUSIVE)

    def acquire_interaction_resolution(self) -> ForegroundLease:
        return self._acquire(ForegroundKind.RESOLVING_INTERACTION)

    def begin_closing(self) -> None:
        self._closing = True

    def cancel_exclusive(self) -> None:
        lease = self._lease
        owner = self._owner
        if (
            lease is None
            or lease.kind is ForegroundKind.OPERATION
            or owner is None
            or owner is asyncio.current_task()
            or owner.done()
        ):
            return
        owner.cancel()

    async def wait_idle(self) -> None:
        await self._idle.wait()

    def _acquire(self, kind: ForegroundKind) -> ForegroundLease:
        if self._closing or self._lease is not None:
            raise ForegroundBusy("Another foreground action is active.")
        lease = ForegroundLease(self, kind)
        self._lease = lease
        self._owner = asyncio.current_task()
        self._idle.clear()
        return lease

    def _release(self, lease: ForegroundLease) -> None:
        if self._lease is not lease:
            raise RuntimeError("Foreground lease ownership was lost.")
        self._lease = None
        self._owner = None
        self._idle.set()


__all__ = [
    "ForegroundArbiter",
    "ForegroundBusy",
    "ForegroundKind",
    "ForegroundLease",
]
