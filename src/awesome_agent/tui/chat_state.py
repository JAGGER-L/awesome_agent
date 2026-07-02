from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4


class ChatEventKind(StrEnum):
    MESSAGE = "message"
    RUN = "run"
    TOOL = "tool"
    MODEL = "model"
    APPROVAL = "approval"
    ARTIFACT = "artifact"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    content: str
    kind: ChatEventKind = ChatEventKind.MESSAGE
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def user(cls, content: str) -> ChatMessage:
        return cls(role="user", content=content)

    @classmethod
    def system(
        cls,
        content: str,
        *,
        kind: ChatEventKind = ChatEventKind.MESSAGE,
    ) -> ChatMessage:
        return cls(role="system", content=content, kind=kind)


@dataclass(frozen=True, slots=True)
class ChatSessionState:
    thread_id: UUID
    current_run_id: str | None = None
    status_label: str = "ready"
    messages: list[ChatMessage] = field(default_factory=list)

    @classmethod
    def new(cls) -> ChatSessionState:
        return cls(thread_id=uuid4())

    def append(self, message: ChatMessage) -> ChatSessionState:
        return replace(self, messages=[*self.messages, message])

    def with_run(
        self,
        run_id: str,
        *,
        status_label: str = "running",
    ) -> ChatSessionState:
        return replace(
            self,
            current_run_id=run_id,
            status_label=status_label,
        )
