from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from awesome_agent.cli.config_flow import ConfigFlowSummary
from awesome_agent.cli.repo_context import CliLaunchContext
from awesome_agent.surfaces.client import (
    ChangedFileSummary,
    changed_file_summaries_from_payload,
)
from awesome_agent.tui.events import ApprovalPromptState, ToolTimelineEntry
from awesome_agent.tui.pickers import PickerState
from awesome_agent.tui.status_panel import StatusPanelTab


class ChatEventKind(StrEnum):
    MESSAGE = "message"
    COMMAND = "command"
    RUN = "run"
    TOOL = "tool"
    MODEL = "model"
    APPROVAL = "approval"
    ARTIFACT = "artifact"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ToolGroup:
    entries: tuple[ToolTimelineEntry, ...] = ()

    @property
    def total(self) -> int:
        return len(self.entries)

    @property
    def running(self) -> int:
        return sum(1 for entry in self.entries if entry.running)

    @property
    def completed(self) -> int:
        return sum(1 for entry in self.entries if entry.completed and not entry.failed)

    @property
    def failed(self) -> int:
        return sum(1 for entry in self.entries if entry.failed)


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: str
    content: str
    kind: ChatEventKind = ChatEventKind.MESSAGE
    attachments: tuple[dict[str, object], ...] = ()
    changed_files: tuple[ChangedFileSummary, ...] = ()
    tool_group: ToolGroup | None = None
    id: str = field(default_factory=lambda: uuid4().hex)
    turn_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def user(
        cls,
        content: str,
        *,
        turn_id: str | None = None,
        attachments: tuple[dict[str, object], ...] = (),
    ) -> ChatMessage:
        return cls(
            role="user",
            content=content,
            turn_id=turn_id,
            attachments=attachments,
        )

    @classmethod
    def assistant(
        cls,
        content: str,
        *,
        turn_id: str | None = None,
        changed_files: tuple[ChangedFileSummary, ...] = (),
    ) -> ChatMessage:
        return cls(
            role="assistant",
            content=content,
            kind=ChatEventKind.MODEL,
            turn_id=turn_id,
            changed_files=changed_files,
        )

    @classmethod
    def command(cls, content: str) -> ChatMessage:
        return cls(role="user", content=content, kind=ChatEventKind.COMMAND)

    @classmethod
    def error(cls, content: str) -> ChatMessage:
        return cls(role="system", content=content, kind=ChatEventKind.ERROR)

    @classmethod
    def system(
        cls,
        content: str,
        *,
        kind: ChatEventKind = ChatEventKind.MESSAGE,
        tool_group: ToolGroup | None = None,
    ) -> ChatMessage:
        return cls(role="system", content=content, kind=kind, tool_group=tool_group)


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
    active_turn_id: str | None = None
    active_thought_turn_id: str | None = None
    streaming_assistant_message_id: str | None = None
    streaming_buffer: str = ""
    thought_text: str = ""
    thought_active: bool = False
    thought_collapsed: bool = True
    thought_started_at: datetime | None = None
    thought_elapsed_seconds: int | None = None
    thought_truncated: bool = False
    thought_blocks: dict[str, ThoughtBlock] = field(default_factory=dict)
    current_model: str = "deepseek-v4-pro"
    thinking_mode: str = "on_high"
    local_memory_enabled: bool = False
    provider_memory: str | None = None
    staged_skill_ids: tuple[str, ...] = ()
    pending_attachments: tuple[dict[str, object], ...] = ()
    pending_model_provider_id: str | None = None
    active_picker: PickerState | None = None
    active_status_tab: StatusPanelTab | None = None
    pending_approval: ApprovalPromptState | None = None
    approval_decision_in_flight: bool = False
    approval_decision_run_id: str | None = None
    last_requested_model: str | None = None
    last_response_model: str | None = None
    last_model_provider: str | None = None
    last_model_response_id: str | None = None
    status_label: str = "ready"
    details_enabled: bool = False
    changed_files_expanded: bool = False
    tool_groups_expanded: bool = False
    last_failed_user_message: str | None = None
    messages: list[ChatMessage] = field(default_factory=list)

    @classmethod
    def new(
        cls,
        *,
        launch_context: CliLaunchContext | None = None,
        first_run_summary: ConfigFlowSummary | None = None,
    ) -> ChatSessionState:
        current_model = (
            first_run_summary.model_name
            if first_run_summary is not None
            else "deepseek-v4-pro"
        )
        return cls(
            thread_id=uuid4(),
            launch_context=launch_context,
            first_run_summary=first_run_summary,
            current_model=current_model,
        )

    @property
    def context_label(self) -> str:
        if self.launch_context is None:
            return "workspace: -"
        return f"{self.launch_context.context_kind}: {self.launch_context.display_path}"

    def append(self, message: ChatMessage) -> ChatSessionState:
        return replace(self, messages=[*self.messages, message])

    def append_tool_event(
        self,
        content: str,
        *,
        name: str = "tool",
        summary: str | None = None,
        details: dict[str, object] | None = None,
    ) -> ChatSessionState:
        return self.upsert_tool_event(
            ToolTimelineEntry(
                name=name,
                summary=summary or content,
                details=details or {},
            )
        )

    def upsert_tool_event(self, entry: ToolTimelineEntry) -> ChatSessionState:
        if self.messages and self.messages[-1].kind is ChatEventKind.TOOL:
            existing = self.messages[-1]
            entries = (
                existing.tool_group.entries if existing.tool_group is not None else ()
            )
            group = ToolGroup(entries=_upsert_tool_entry(entries, entry))
            messages = [
                *self.messages[:-1],
                ChatMessage.system(
                    _tool_group_summary(group),
                    kind=ChatEventKind.TOOL,
                    tool_group=group,
                ),
            ]
            return replace(self, messages=messages)
        group = ToolGroup(entries=(entry,))
        return self.append(
            ChatMessage.system(
                _tool_group_summary(group),
                kind=ChatEventKind.TOOL,
                tool_group=group,
            )
        )

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
            active_turn_id=None,
            active_thought_turn_id=None,
            streaming_assistant_message_id=None,
            streaming_buffer="",
            thought_text="",
            thought_active=False,
            thought_collapsed=True,
            thought_started_at=None,
            thought_elapsed_seconds=None,
            thought_truncated=False,
            thought_blocks={},
            staged_skill_ids=(),
            pending_attachments=(),
            pending_model_provider_id=None,
            active_picker=None,
            active_status_tab=None,
            pending_approval=None,
            approval_decision_in_flight=False,
            approval_decision_run_id=None,
            status_label="ready",
            last_failed_user_message=None,
            changed_files_expanded=False,
            tool_groups_expanded=False,
            messages=messages or [],
        )

    def with_status(self, status_label: str) -> ChatSessionState:
        return replace(self, status_label=status_label)

    def with_model(self, model_id: str) -> ChatSessionState:
        return replace(self, current_model=model_id)

    def with_pending_model_provider(
        self,
        provider_id: str | None,
    ) -> ChatSessionState:
        return replace(self, pending_model_provider_id=provider_id)

    def with_thinking(self, mode: str) -> ChatSessionState:
        if mode not in {"on_high", "on_max", "off"}:
            raise ValueError(f"Unsupported thinking mode: {mode}")
        return replace(self, thinking_mode=mode)

    def with_local_memory(self, enabled: bool) -> ChatSessionState:
        return replace(self, local_memory_enabled=enabled)

    def with_provider_memory(self, provider: str | None) -> ChatSessionState:
        return replace(self, provider_memory=provider)

    def stage_skill(self, skill_id: str) -> ChatSessionState:
        if skill_id in self.staged_skill_ids:
            return self
        return replace(self, staged_skill_ids=(*self.staged_skill_ids, skill_id))

    def unstage_skill(self, skill_id: str) -> ChatSessionState:
        return replace(
            self,
            staged_skill_ids=tuple(
                staged for staged in self.staged_skill_ids if staged != skill_id
            ),
        )

    def clear_staged_skills(self) -> ChatSessionState:
        return replace(self, staged_skill_ids=())

    def with_pending_attachment(
        self,
        attachment: dict[str, object],
    ) -> ChatSessionState:
        attachment_id = attachment.get("id")
        if attachment_id is not None and any(
            item.get("id") == attachment_id for item in self.pending_attachments
        ):
            return self
        return replace(
            self,
            pending_attachments=(*self.pending_attachments, dict(attachment)),
        )

    def without_pending_attachment(self, attachment_id: str) -> ChatSessionState:
        return replace(
            self,
            pending_attachments=tuple(
                item
                for item in self.pending_attachments
                if str(item.get("id") or "") != attachment_id
            ),
        )

    def clear_pending_attachments(self) -> ChatSessionState:
        return replace(self, pending_attachments=())

    def open_picker(self, picker: PickerState) -> ChatSessionState:
        return replace(self, active_picker=picker, active_status_tab=None)

    def close_picker(self) -> ChatSessionState:
        return replace(self, active_picker=None)

    def open_status_panel(
        self,
        tab: StatusPanelTab = StatusPanelTab.STATUS,
    ) -> ChatSessionState:
        return replace(self, active_status_tab=tab, active_picker=None)

    def close_status_panel(self) -> ChatSessionState:
        return replace(self, active_status_tab=None)

    def next_status_tab(self) -> ChatSessionState:
        if self.active_status_tab is None:
            return self
        tabs = StatusPanelTab.ordered()
        index = tabs.index(self.active_status_tab)
        return replace(self, active_status_tab=tabs[(index + 1) % len(tabs)])

    def previous_status_tab(self) -> ChatSessionState:
        if self.active_status_tab is None:
            return self
        tabs = StatusPanelTab.ordered()
        index = tabs.index(self.active_status_tab)
        return replace(self, active_status_tab=tabs[(index - 1) % len(tabs)])

    def with_approval_prompt(
        self,
        prompt: ApprovalPromptState | None,
    ) -> ChatSessionState:
        return replace(self, pending_approval=prompt)

    def with_approval_decision_in_flight(
        self,
        *,
        run_id: str | None,
        in_flight: bool,
    ) -> ChatSessionState:
        return replace(
            self,
            approval_decision_in_flight=in_flight,
            approval_decision_run_id=run_id if in_flight else None,
        )

    def with_last_failed_user_message(
        self,
        content: str | None,
    ) -> ChatSessionState:
        return replace(self, last_failed_user_message=content)

    def upsert_streaming_assistant(self, content: str) -> ChatSessionState:
        if self.messages and self.messages[-1].role == "assistant":
            existing = self.messages[-1]
            return replace(
                self,
                messages=[
                    *self.messages[:-1],
                    ChatMessage.assistant(
                        content,
                        turn_id=existing.turn_id,
                        changed_files=existing.changed_files,
                    ),
                ],
            )
        return self.append(ChatMessage.assistant(content, turn_id=self.active_turn_id))

    def with_latest_assistant_changed_files(
        self,
        value: object,
    ) -> ChatSessionState:
        changed_files = changed_file_summaries_from_payload(value)
        if not changed_files:
            return self
        for index in range(len(self.messages) - 1, -1, -1):
            existing = self.messages[index]
            if existing.role == "assistant":
                messages = list(self.messages)
                messages[index] = replace(existing, changed_files=changed_files)
                return replace(self, messages=messages)
        return self

    def begin_turn(self, turn_id: str) -> ChatSessionState:
        return replace(
            self,
            active_turn_id=turn_id,
            streaming_buffer="",
            streaming_assistant_message_id=None,
        )

    def finish_turn(self) -> ChatSessionState:
        return replace(self, active_turn_id=None, active_thought_turn_id=None)

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

    def note_run_terminal(self, run_id: str | None = None) -> ChatSessionState:
        return replace(
            self,
            current_run_id=(
                None
                if run_id is None or self.current_run_id == run_id
                else self.current_run_id
            ),
            last_resumable_run_id=(
                None
                if run_id is None or self.last_resumable_run_id == run_id
                else self.last_resumable_run_id
            ),
        )

    def note_model_metadata(self, payload: dict[str, object]) -> ChatSessionState:
        return replace(
            self,
            last_requested_model=_optional_payload_str(payload, "requested_model"),
            last_response_model=_optional_payload_str(payload, "response_model"),
            last_model_provider=_optional_payload_str(payload, "provider"),
            last_model_response_id=_optional_payload_str(payload, "response_id"),
        )

    def append_stream_delta(self, text: str) -> ChatSessionState:
        buffer = f"{self.streaming_buffer}{text}"
        return replace(
            self.upsert_streaming_assistant(buffer),
            streaming_buffer=buffer,
        )

    def begin_thought(self, started_at: datetime) -> ChatSessionState:
        turn_id = self.active_turn_id or self.active_operation_id or uuid4().hex
        thought = ThoughtBlock(
            text="",
            active=True,
            collapsed=False,
            elapsed_seconds=None,
            truncated=False,
        )
        return replace(
            self,
            active_thought_turn_id=turn_id,
            thought_text="",
            thought_active=True,
            thought_collapsed=False,
            thought_started_at=started_at,
            thought_elapsed_seconds=None,
            thought_truncated=False,
            thought_blocks={**self.thought_blocks, turn_id: thought},
        )

    def append_thought_delta(
        self,
        text: str,
        *,
        max_chars: int = 16_000,
    ) -> ChatSessionState:
        turn_id = self.active_thought_turn_id or self.active_turn_id
        if turn_id is None:
            return self
        current = self.thought_blocks.get(turn_id)
        if current is None:
            current = ThoughtBlock(
                text="",
                active=True,
                collapsed=False,
                elapsed_seconds=None,
                truncated=False,
            )
        if current.truncated:
            return self
        combined = f"{current.text}{text}"
        truncated = len(combined) > max_chars
        updated = ThoughtBlock(
            text=combined[:max_chars],
            active=current.active,
            collapsed=current.collapsed,
            elapsed_seconds=current.elapsed_seconds,
            truncated=truncated,
        )
        return replace(
            self,
            thought_text=updated.text,
            thought_truncated=truncated,
            thought_blocks={**self.thought_blocks, turn_id: updated},
        )

    def complete_thought(self, ended_at: datetime) -> ChatSessionState:
        started_at = self.thought_started_at or ended_at
        elapsed = max(0, int((ended_at - started_at).total_seconds()))
        turn_id = self.active_thought_turn_id or self.active_turn_id
        thought_blocks = self.thought_blocks
        if turn_id is not None:
            current = thought_blocks.get(turn_id)
            if current is not None:
                thought_blocks = {
                    **thought_blocks,
                    turn_id: ThoughtBlock(
                        text=current.text,
                        active=False,
                        collapsed=True,
                        elapsed_seconds=elapsed,
                        truncated=current.truncated,
                    ),
                }
        return replace(
            self,
            active_thought_turn_id=None,
            thought_active=False,
            thought_collapsed=True,
            thought_elapsed_seconds=elapsed,
            thought_blocks=thought_blocks,
        )

    def toggle_thought(self) -> ChatSessionState:
        turn_id = self.active_thought_turn_id or self.active_turn_id
        if turn_id is None:
            turn_id = next(reversed(self.thought_blocks), None)
        if turn_id is None:
            return self
        current = self.thought_blocks.get(turn_id)
        if current is None:
            return self
        updated = replace(current, collapsed=not current.collapsed)
        return replace(
            self,
            thought_collapsed=updated.collapsed,
            thought_blocks={**self.thought_blocks, turn_id: updated},
        )

    def thought_block(self) -> ThoughtBlock | None:
        return self.latest_thought()

    def thought_for_turn(self, turn_id: str) -> ThoughtBlock | None:
        return self.thought_blocks.get(turn_id)

    def latest_thought(self) -> ThoughtBlock | None:
        if self.active_thought_turn_id is not None:
            active = self.thought_blocks.get(self.active_thought_turn_id)
            if active is not None:
                return active
        return next(reversed(self.thought_blocks.values()), None)

    def mark_operation_paused(self, run_id: str) -> ChatSessionState:
        return replace(
            self,
            current_run_id=run_id,
            last_resumable_run_id=run_id,
            active_operation_id=None,
            active_operation_label=None,
            active_turn_id=None,
            active_thought_turn_id=None,
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
            active_turn_id=None,
            active_thought_turn_id=None,
            streaming_assistant_message_id=None,
            streaming_buffer="",
            thought_active=False,
            thought_collapsed=True,
            status_label=status_label,
        )

    def toggle_details(self) -> ChatSessionState:
        return replace(self, details_enabled=not self.details_enabled)

    def toggle_changed_files(self) -> ChatSessionState:
        return replace(self, changed_files_expanded=not self.changed_files_expanded)

    def toggle_tool_groups(self) -> ChatSessionState:
        return replace(self, tool_groups_expanded=not self.tool_groups_expanded)

    @property
    def has_running_tools(self) -> bool:
        return any(
            message.tool_group is not None
            and any(entry.running for entry in message.tool_group.entries)
            for message in self.messages
            if message.kind is ChatEventKind.TOOL
        )

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
        return ChatMessage.user(content, attachments=_record_attachments(record))
    if role == "assistant":
        metadata = record.get("metadata")
        changed_files: tuple[ChangedFileSummary, ...] = ()
        if isinstance(metadata, dict):
            changed_files = changed_file_summaries_from_payload(
                metadata.get("changed_files")
            )
        return ChatMessage.assistant(content, changed_files=changed_files)
    return ChatMessage.system(content, kind=kind)


def _optional_payload_str(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def _tool_group_summary(group: ToolGroup) -> str:
    pieces = [f"called {group.total}"]
    if group.running:
        pieces.append(f"{group.running} running")
    if group.completed:
        pieces.append(f"{group.completed} completed")
    if group.failed:
        pieces.append(f"{group.failed} failed")
    additions, deletions, files = _aggregate_change_stats(group)
    if files:
        suffix = "file" if files == 1 else "files"
        pieces.append(f"+{additions} -{deletions}")
        pieces.append(f"{files} {suffix}")
    return ", ".join(pieces)


def _upsert_tool_entry(
    entries: tuple[ToolTimelineEntry, ...],
    entry: ToolTimelineEntry,
) -> tuple[ToolTimelineEntry, ...]:
    identity = _tool_entry_identity(entry)
    if identity is None:
        return (*entries, entry)
    updated = list(entries)
    for index, existing in enumerate(updated):
        if _tool_entry_identity(existing) == identity:
            updated[index] = _merge_tool_entry(existing, entry)
            return tuple(updated)
    return (*entries, entry)


def _tool_entry_identity(entry: ToolTimelineEntry) -> tuple[str, str] | None:
    if entry.invocation_id:
        return ("invocation", entry.invocation_id)
    if entry.call_id:
        return ("call", entry.call_id)
    return None


def _merge_tool_entry(
    existing: ToolTimelineEntry,
    incoming: ToolTimelineEntry,
) -> ToolTimelineEntry:
    details = {**existing.details, **incoming.details}
    return replace(
        incoming,
        details=details,
        started_at=incoming.started_at or existing.started_at,
        change_stats=incoming.change_stats or existing.change_stats,
        changed_files=incoming.changed_files or existing.changed_files,
    )


def _aggregate_change_stats(group: ToolGroup) -> tuple[int, int, int]:
    additions = 0
    deletions = 0
    files = 0
    for entry in group.entries:
        stats = entry.change_stats
        if not isinstance(stats, dict):
            continue
        additions += _int_value(stats.get("additions"))
        deletions += _int_value(stats.get("deletions"))
        files += _int_value(stats.get("files"))
    return additions, deletions, files


def _int_value(value: object) -> int:
    return value if isinstance(value, int) and value > 0 else 0


def _record_attachments(record: dict[str, Any]) -> tuple[dict[str, object], ...]:
    metadata = record.get("metadata")
    if not isinstance(metadata, dict):
        return ()
    attachments = metadata.get("attachments")
    if not isinstance(attachments, list):
        return ()
    return tuple(dict(item) for item in attachments if isinstance(item, dict))
