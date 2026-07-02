from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from awesome_agent.cli.config_flow import ConfigFlowSummary
from awesome_agent.cli.repo_context import CliLaunchContext


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
    launch_context: CliLaunchContext | None = None
    first_run_summary: ConfigFlowSummary | None = None
    current_run_id: str | None = None
    status_label: str = "ready"
    details_enabled: bool = False
    messages: list[ChatMessage] = field(default_factory=list)

    @classmethod
    def new(
        cls,
        *,
        launch_context: CliLaunchContext | None = None,
        first_run_summary: ConfigFlowSummary | None = None,
    ) -> ChatSessionState:
        return cls(
            thread_id=uuid4(),
            launch_context=launch_context,
            first_run_summary=first_run_summary,
        )

    @property
    def context_label(self) -> str:
        if self.launch_context is None:
            return "workspace: -"
        return (
            f"{self.launch_context.context_kind}: "
            f"{self.launch_context.display_path}"
        )

    def append(self, message: ChatMessage) -> ChatSessionState:
        return replace(self, messages=[*self.messages, message])

    def toggle_details(self) -> ChatSessionState:
        return replace(self, details_enabled=not self.details_enabled)

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
