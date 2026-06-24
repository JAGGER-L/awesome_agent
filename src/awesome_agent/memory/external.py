from __future__ import annotations

from typing import Any, Protocol, cast

from mem0 import AsyncMemoryClient
from mem0.client.types import SearchMemoryOptions

from awesome_agent.memory.models import MemoryCandidate, MemoryRecord


class ExternalMemory(Protocol):
    async def add(
        self, candidate: MemoryCandidate, *, user_id: str, project_id: str
    ) -> bool:
        """Store a memory candidate."""
        ...

    async def search(
        self, query: str, *, user_id: str, project_id: str
    ) -> list[MemoryRecord]:
        """Search external memories."""
        ...

    async def delete(self, memory_id: str) -> bool:
        """Delete one external memory."""
        ...


class Mem0PlatformMemory(ExternalMemory):
    def __init__(
        self,
        *,
        api_key: str,
        client: AsyncMemoryClient | None = None,
    ) -> None:
        self._client = client or AsyncMemoryClient(api_key=api_key)

    async def add(
        self, candidate: MemoryCandidate, *, user_id: str, project_id: str
    ) -> bool:
        try:
            await self._client.add(
                [{"role": "user", "content": candidate.content}],
                user_id=user_id,
                app_id=project_id,
                metadata={
                    "kind": candidate.kind.value,
                    "source": candidate.source.value,
                },
                infer=True,
            )
        except Exception:
            return False
        return True

    async def search(
        self, query: str, *, user_id: str, project_id: str
    ) -> list[MemoryRecord]:
        try:
            response = await self._client.search(
                query,
                options=SearchMemoryOptions(
                    filters={"user_id": user_id, "app_id": project_id},
                    top_k=10,
                ),
            )
        except Exception:
            return []

        raw_results = cast(list[dict[str, Any]], response.get("results", []))
        return [
            MemoryRecord(
                id=str(item.get("id", "")),
                content=str(item.get("memory", item.get("text", ""))),
                metadata={
                    str(key): str(value)
                    for key, value in cast(
                        dict[str, Any], item.get("metadata", {})
                    ).items()
                },
            )
            for item in raw_results
        ]

    async def delete(self, memory_id: str) -> bool:
        try:
            await self._client.delete(memory_id)
        except Exception:
            return False
        return True
