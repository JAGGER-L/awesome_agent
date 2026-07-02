from __future__ import annotations

from typing import Protocol
from uuid import UUID

from awesome_agent.conversation.models import (
    ThreadMessage,
    ThreadMessageKind,
    ThreadMessageRole,
)
from awesome_agent.domain.threads import Thread


class ConversationRepository(Protocol):
    async def create_thread(
        self,
        *,
        title: str,
        context_kind: str = "workspace",
        context_path: str | None = None,
        repository_id: UUID | None = None,
        default_model: str | None = None,
        sandbox_profile: str | None = None,
    ) -> Thread:
        pass

    async def list_threads(self) -> list[Thread]:
        pass

    async def get_thread(self, thread_id: UUID) -> Thread:
        pass

    async def bind_repository(self, thread_id: UUID, repository_id: UUID) -> Thread:
        pass

    async def resolve_thread(self, query: str) -> Thread:
        pass

    async def append_message(
        self,
        *,
        thread_id: UUID,
        role: ThreadMessageRole,
        content: str,
        kind: ThreadMessageKind = ThreadMessageKind.MESSAGE,
        run_id: UUID | None = None,
        metadata: dict[str, object] | None = None,
    ) -> ThreadMessage:
        pass

    async def list_messages(self, thread_id: UUID) -> list[ThreadMessage]:
        pass
