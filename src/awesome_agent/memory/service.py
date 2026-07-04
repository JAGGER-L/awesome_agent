from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from awesome_agent.memory.builtin import BuiltinMemoryStore
from awesome_agent.memory.external import MemoryProvider, NoopMemoryProvider
from awesome_agent.memory.models import (
    MemoryAddRequest,
    MemoryContextSnapshot,
    MemoryOperationResult,
    MemoryStatus,
    MemoryTarget,
)


@dataclass(frozen=True)
class EffectiveMemory:
    local_enabled: bool
    provider: str | None = None


class MemoryService:
    def __init__(
        self,
        *,
        builtin: BuiltinMemoryStore,
        provider: MemoryProvider | None = None,
        builtin_enabled: bool,
        provider_enabled: bool,
    ) -> None:
        self.builtin = builtin
        self._provider = provider or NoopMemoryProvider()
        self._builtin_enabled = builtin_enabled
        self._provider_enabled = provider_enabled

    @property
    def builtin_enabled(self) -> bool:
        return self._builtin_enabled

    @property
    def provider_enabled(self) -> bool:
        return self._provider_enabled

    def effective_from_payload(self, payload: dict[str, object]) -> EffectiveMemory:
        return EffectiveMemory(
            local_enabled=self._builtin_enabled
            and payload.get("local_enabled") is True,
            provider=(
                str(payload["provider"])
                if self._provider_enabled and isinstance(payload.get("provider"), str)
                else None
            ),
        )

    def add_request(
        self,
        *,
        target: MemoryTarget,
        content: str,
        source: str,
    ) -> MemoryAddRequest:
        return MemoryAddRequest(target=target, content=content, source=source)

    def status(self) -> MemoryStatus:
        self.builtin.ensure_files()
        counts, truncated = self.builtin.status()
        return MemoryStatus(
            enabled=self._builtin_enabled or self._provider_enabled,
            builtin_enabled=self._builtin_enabled,
            provider_enabled=self._provider_enabled,
            provider_status="enabled" if self._provider_enabled else "disabled",
            root=str(self.builtin.root),
            files={
                "user": str(self.builtin.path_for(MemoryTarget.USER)),
                "memory": str(self.builtin.path_for(MemoryTarget.MEMORY)),
            },
            counts=counts,
            truncated=truncated,
            hint=(
                None
                if self._builtin_enabled or self._provider_enabled
                else "Enable builtin_memory_enabled to use local file memory."
            ),
        )

    async def add(
        self,
        *,
        target: MemoryTarget,
        content: str,
        source: str,
        run_id: UUID | None,
        agent_id: UUID | None,
    ) -> MemoryOperationResult:
        request = self.add_request(target=target, content=content, source=source)
        result = (
            self.builtin.add(request)
            if self._builtin_enabled
            else MemoryOperationResult(
                operation="add",
                target=target,
                status="rejected_by_policy",
                source=source,
                reason="builtin_memory_disabled",
            )
        )
        if self._provider_enabled:
            provider_ok = await self._provider.add(
                request,
                metadata={
                    "run_id": str(run_id) if run_id is not None else "",
                    "agent_id": str(agent_id) if agent_id is not None else "",
                },
            )
            if not provider_ok and result.status == "added":
                result.provider_status = "failed"
        return result

    async def list_entries(
        self,
        *,
        target: MemoryTarget | None = None,
    ) -> MemoryOperationResult:
        return MemoryOperationResult(
            operation="list",
            target=target,
            status="listed",
            entries=self.builtin.list_entries(target),
        )

    async def delete(
        self,
        *,
        target: MemoryTarget,
        memory_id: str,
        run_id: UUID | None,
        agent_id: UUID | None,
    ) -> MemoryOperationResult:
        result = self.builtin.delete(target, memory_id)
        if self._provider_enabled:
            provider_ok = await self._provider.delete(memory_id)
            if not provider_ok and result.status == "deleted":
                result.provider_status = "failed"
        return result

    def context_snapshot(self, effective: EffectiveMemory) -> MemoryContextSnapshot:
        if not effective.local_enabled:
            return MemoryContextSnapshot(enabled=False)
        return self.builtin.context_snapshot()

    def render_context(self, snapshot: MemoryContextSnapshot) -> str:
        rendered = snapshot.render()
        if not rendered:
            return ""
        return f"<awesome_agent_memory>\n{rendered}\n</awesome_agent_memory>"
