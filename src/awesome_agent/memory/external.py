from __future__ import annotations

from typing import Protocol

from awesome_agent.memory.models import MemoryAddRequest, MemoryEntry


class MemoryProvider(Protocol):
    async def initialize(self, session_id: str) -> bool: ...

    async def retrieve(
        self,
        query: str,
        *,
        thread_id: str,
        limit: int,
    ) -> list[MemoryEntry]: ...

    async def add(
        self,
        request: MemoryAddRequest,
        *,
        metadata: dict[str, str],
    ) -> bool: ...

    async def delete(self, memory_id: str) -> bool: ...

    async def sync_turn(
        self,
        *,
        user_message: str,
        assistant_message: str,
        metadata: dict[str, str],
    ) -> bool: ...

    async def close(self) -> None: ...


class NoopMemoryProvider:
    async def initialize(self, session_id: str) -> bool:
        return True

    async def retrieve(
        self,
        query: str,
        *,
        thread_id: str,
        limit: int,
    ) -> list[MemoryEntry]:
        return []

    async def add(
        self,
        request: MemoryAddRequest,
        *,
        metadata: dict[str, str],
    ) -> bool:
        return True

    async def delete(self, memory_id: str) -> bool:
        return True

    async def sync_turn(
        self,
        *,
        user_message: str,
        assistant_message: str,
        metadata: dict[str, str],
    ) -> bool:
        return True

    async def close(self) -> None:
        return None


class FailingMemoryProvider:
    def __init__(self, reason: str = "provider_failed") -> None:
        self.reason = reason

    async def initialize(self, session_id: str) -> bool:
        return False

    async def retrieve(
        self,
        query: str,
        *,
        thread_id: str,
        limit: int,
    ) -> list[MemoryEntry]:
        return []

    async def add(
        self,
        request: MemoryAddRequest,
        *,
        metadata: dict[str, str],
    ) -> bool:
        return False

    async def delete(self, memory_id: str) -> bool:
        return False

    async def sync_turn(
        self,
        *,
        user_message: str,
        assistant_message: str,
        metadata: dict[str, str],
    ) -> bool:
        return False

    async def close(self) -> None:
        return None
