from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
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
    def assistant(cls, content: str) -> ChatMessage:
        return cls(role="assistant", content=content, kind=ChatEventKind.MODEL)

    @classmethod
    def error(cls, content: str) -> ChatMessage:
        return cls(role="system", content=content, kind=ChatEventKind.ERROR)

    @classmethod
    def system(
        cls,
        content: str,
        *,
        kind: ChatEventKind = ChatEventKind.MESSAGE,
    ) -> ChatMessage:
        return cls(role="system", content=content, kind=kind)


@dataclass(frozen=True, slots=True)
class ThoughtBlock:
    text: str
    active: bool
    collapsed: bool
    elapsed_seconds: int | None
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class ChatSessionState:
    thread_id: UUID
    backend_thread_id: str | None = None
    launch_context: CliLaunchContext | None = None
    first_run_summary: ConfigFlowSummary | None = None
    thread_title: str = "New conversation"
    thread_context_label: str | None = None
    current_run_id: str | None = None
    last_resumable_run_id: str | None = None
    active_operation_id: str | None = None
    active_operation_label: str | None = None
    streaming_assistant_message_id: str | None = None
    streaming_buffer: str = ""
    thought_text: str = ""
    thought_active: bool = False
    thought_collapsed: bool = True
    thought_started_at: datetime | None = None
    thought_elapsed_seconds: int | None = None
    thought_truncated: bool = False
    status_label: str = "ready"
    details_enabled: bool = False
    last_failed_user_message: str | None = None
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
        return f"{self.launch_context.context_kind}: {self.launch_context.display_path}"

    def append(self, message: ChatMessage) -> ChatSessionState:
        return replace(self, messages=[*self.messages, message])

    def with_backend_thread(
        self,
        thread_id: str,
        *,
        title: str | None = None,
        context_label: str | None = None,
    ) -> ChatSessionState:
        return replace(
            self,
            backend_thread_id=thread_id,
            thread_title=title or self.thread_title,
            thread_context_label=context_label
            if context_label is not None
            else self.thread_context_label,
        )

    def switch_thread(
        self,
        *,
        backend_thread_id: str,
        title: str,
        context_label: str | None,
        messages: list[ChatMessage] | None = None,
    ) -> ChatSessionState:
        return replace(
            self,
            backend_thread_id=backend_thread_id,
            thread_title=title,
            thread_context_label=context_label,
            current_run_id=None,
            last_resumable_run_id=None,
            active_operation_id=None,
            active_operation_label=None,
            streaming_assistant_message_id=None,
            streaming_buffer="",
            thought_text="",
            thought_active=False,
            thought_collapsed=True,
            thought_started_at=None,
            thought_elapsed_seconds=None,
            thought_truncated=False,
            status_label="ready",
            last_failed_user_message=None,
            messages=messages or [],
        )

    def with_status(self, status_label: str) -> ChatSessionState:
        return replace(self, status_label=status_label)

    def with_last_failed_user_message(
        self,
        content: str | None,
    ) -> ChatSessionState:
        return replace(self, last_failed_user_message=content)

    def upsert_streaming_assistant(self, content: str) -> ChatSessionState:
        if self.messages and self.messages[-1].role == "assistant":
            return replace(
                self,
                messages=[
                    *self.messages[:-1],
                    ChatMessage.assistant(content),
                ],
            )
        return self.append(ChatMessage.assistant(content))

    def begin_operation(
        self,
        operation_id: str,
        label: str,
    ) -> ChatSessionState:
        return replace(
            self,
            active_operation_id=operation_id,
            active_operation_label=label,
            status_label=label,
        )

    def note_run_started(self, run_id: str) -> ChatSessionState:
        return replace(self, current_run_id=run_id)

    def append_stream_delta(self, text: str) -> ChatSessionState:
        buffer = f"{self.streaming_buffer}{text}"
        return replace(
            self.upsert_streaming_assistant(buffer),
            streaming_buffer=buffer,
        )

    def begin_thought(self, started_at: datetime) -> ChatSessionState:
        return replace(
            self,
            thought_text="",
            thought_active=True,
            thought_collapsed=False,
            thought_started_at=started_at,
            thought_elapsed_seconds=None,
            thought_truncated=False,
        )

    def append_thought_delta(
        self,
        text: str,
        *,
        max_chars: int = 16_000,
    ) -> ChatSessionState:
        if self.thought_truncated:
            return self
        combined = f"{self.thought_text}{text}"
        truncated = len(combined) > max_chars
        return replace(
            self,
            thought_text=combined[:max_chars],
            thought_truncated=truncated,
        )

    def complete_thought(self, ended_at: datetime) -> ChatSessionState:
        started_at = self.thought_started_at or ended_at
        elapsed = max(0, int((ended_at - started_at).total_seconds()))
        return replace(
            self,
            thought_active=False,
            thought_collapsed=True,
            thought_elapsed_seconds=elapsed,
        )

    def toggle_thought(self) -> ChatSessionState:
        if not self.thought_text and not self.thought_active:
            return self
        return replace(self, thought_collapsed=not self.thought_collapsed)

    def thought_block(self) -> ThoughtBlock | None:
        if not self.thought_active and not self.thought_text:
            return None
        return ThoughtBlock(
            text=self.thought_text,
            active=self.thought_active,
            collapsed=self.thought_collapsed,
            elapsed_seconds=self.thought_elapsed_seconds,
            truncated=self.thought_truncated,
        )

    def mark_operation_paused(self, run_id: str) -> ChatSessionState:
        return replace(
            self,
            current_run_id=run_id,
            last_resumable_run_id=run_id,
            active_operation_id=None,
            active_operation_label=None,
            status_label="paused",
        )

    def finish_operation(
        self,
        *,
        status_label: str = "ready",
    ) -> ChatSessionState:
        return replace(
            self,
            active_operation_id=None,
            active_operation_label=None,
            streaming_assistant_message_id=None,
            streaming_buffer="",
            thought_active=False,
            thought_collapsed=True,
            status_label=status_label,
        )

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


def should_resume_last_run(input_text: str) -> bool:
    return input_text.strip().casefold() in {"continue", "resume", "\u7ee7\u7eed"}


def chat_messages_from_thread_records(
    records: list[dict[str, Any]],
) -> list[ChatMessage]:
    return [_chat_message_from_record(record) for record in records]


def _chat_message_from_record(record: dict[str, Any]) -> ChatMessage:
    role = str(record.get("role") or "system")
    content = str(record.get("content") or "")
    raw_kind = str(record.get("kind") or ChatEventKind.MESSAGE)
    try:
        kind = ChatEventKind(raw_kind)
    except ValueError:
        kind = ChatEventKind.MESSAGE
    if role == "user":
        return ChatMessage.user(content)
    if role == "assistant":
        return ChatMessage.assistant(content)
    return ChatMessage.system(content, kind=kind)
