from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from awesome_agent.conversation.models import (
    Thread,
    ThreadEntry,
    ThreadEntryKind,
    ThreadLineage,
    ThreadTitleSource,
    ThreadView,
    Turn,
    TurnStatus,
    UsageSummary,
)
from awesome_agent.conversation.repository import (
    ConversationConflict,
    InvalidTurnTransition,
    TurnNotFound,
)
from awesome_agent.conversation.titles import normalize_title, visible_graphemes

type MaterializationKind = Literal["fork", "retry"]


class ThreadMaterializationPlan(BaseModel):
    """Immutable, source-bound destination prepared by ConversationService."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: Literal["fork", "retry"]
    source_workspace_key: str = Field(min_length=1, max_length=128)
    source_thread_id: str = Field(min_length=1, max_length=128)
    source_turn_id: str = Field(min_length=1, max_length=128)
    source_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    view: ThreadView

    @model_validator(mode="after")
    def validate_destination(self) -> Self:
        thread = self.view.thread
        lineage = thread.lineage
        if thread.workspace_key != self.source_workspace_key:
            raise ValueError("Materialized Thread must remain in the source Workspace.")
        if thread.id == self.source_thread_id:
            raise ValueError("Materialized Thread must use a new identity.")
        if (
            lineage is None
            or lineage.kind != self.kind
            or lineage.source_thread_id != self.source_thread_id
            or lineage.source_turn_id != self.source_turn_id
        ):
            raise ValueError("Materialized Thread lineage does not match its source.")
        if self.view.summary is not None or self.view.tool_activities:
            raise ValueError(
                "Materialized Threads cannot copy summaries or ToolActivity."
            )
        if tuple(entry.sequence for entry in self.view.entries) != tuple(
            range(1, len(self.view.entries) + 1)
        ):
            raise ValueError("Materialized Thread Entries must be contiguous.")
        if any(entry.thread_id != thread.id for entry in self.view.entries):
            raise ValueError("Materialized Thread Entry identity is inconsistent.")
        entry_by_id = {entry.id: entry for entry in self.view.entries}
        if len(entry_by_id) != len(self.view.entries):
            raise ValueError("Materialized Thread Entry identities must be unique.")
        if len({turn.id for turn in self.view.turns}) != len(self.view.turns):
            raise ValueError("Materialized Turn identities must be unique.")
        user_sequences: list[int] = []
        for turn in self.view.turns:
            if turn.thread_id != thread.id or turn.checkpoint_key != turn.id:
                raise ValueError("Materialized Turn identity is inconsistent.")
            user = entry_by_id.get(turn.user_entry_id)
            if user is None or user.kind is not ThreadEntryKind.USER_MESSAGE:
                raise ValueError("Materialized Turn user Entry is unavailable.")
            user_sequences.append(user.sequence)
            if turn.status is not TurnStatus.IN_PROGRESS and turn.context_manifest:
                raise ValueError("Cloned terminal Turns must clear context manifests.")
            if turn.assistant_entry_id is not None:
                assistant = entry_by_id.get(turn.assistant_entry_id)
                if (
                    assistant is None
                    or assistant.kind is not ThreadEntryKind.ASSISTANT_MESSAGE
                    or assistant.sequence <= user.sequence
                ):
                    raise ValueError("Materialized Turn assistant Entry is invalid.")
        if user_sequences != sorted(user_sequences):
            raise ValueError("Materialized Turns must follow transcript order.")
        in_progress = tuple(
            turn for turn in self.view.turns if turn.status is TurnStatus.IN_PROGRESS
        )
        if self.kind == "fork" and in_progress:
            raise ValueError("Fork materialization may contain only terminal Turns.")
        if self.kind == "retry" and (
            len(in_progress) != 1 or self.view.turns[-1] != in_progress[0]
        ):
            raise ValueError(
                "Retry materialization requires one final in-progress Turn."
            )
        if in_progress:
            retry = in_progress[0]
            if (
                retry.context_manifest
                or retry.usage != UsageSummary()
                or retry.assistant_entry_id is not None
                or retry.termination_reason is not None
                or retry.error_code is not None
                or retry.completed_at is not None
            ):
                raise ValueError(
                    "Retry Turn must begin without terminal state or usage."
                )
        return self


class RetryPreparation(BaseModel):
    """A materialized retry ready for the existing Turn execution path."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    view: ThreadView
    turn: Turn
    content: str = Field(min_length=1, max_length=200_000)
    client_message_id: str = Field(
        pattern=r"^client_[A-Za-z0-9_-]+$",
        max_length=128,
    )

    @model_validator(mode="after")
    def validate_preparation(self) -> Self:
        if (
            self.view.thread.lineage is None
            or self.view.thread.lineage.kind != "retry"
            or self.turn.status is not TurnStatus.IN_PROGRESS
            or self.turn.thread_id != self.view.thread.id
            or not self.view.turns
            or self.view.turns[-1] != self.turn
        ):
            raise ValueError("Retry preparation identities are inconsistent.")
        if not self.view.entries:
            raise ValueError("Retry preparation requires one user Entry.")
        user = self.view.entries[-1]
        if (
            user.kind is not ThreadEntryKind.USER_MESSAGE
            or user.id != self.turn.user_entry_id
            or user.content != self.content
            or user.client_message_id != self.client_message_id
        ):
            raise ValueError("Retry preparation user input is inconsistent.")
        return self


def materialization_source_fingerprint(view: ThreadView) -> str:
    """Hash all source state that can affect fork/retry materialization."""

    entries = tuple(sorted(view.entries, key=lambda entry: (entry.sequence, entry.id)))
    turns = tuple(sorted(view.turns, key=lambda turn: (turn.created_at, turn.id)))
    payload = {
        "entries": [entry.model_dump(mode="json") for entry in entries],
        "thread": view.thread.model_dump(mode="json"),
        "turns": [turn.model_dump(mode="json") for turn in turns],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def terminal_materialization_target(
    view: ThreadView,
    turn_id: str | None,
) -> Turn:
    entry_by_id = {entry.id: entry for entry in view.entries}
    if turn_id is not None:
        target = next((turn for turn in view.turns if turn.id == turn_id), None)
        if target is None:
            raise TurnNotFound(turn_id)
        if target.status is TurnStatus.IN_PROGRESS:
            raise InvalidTurnTransition("Fork and retry require a terminal Turn.")
        _turn_user_sequence(target, entry_by_id)
        return target
    terminal = tuple(
        turn for turn in view.turns if turn.status is not TurnStatus.IN_PROGRESS
    )
    if not terminal:
        raise TurnNotFound("latest_terminal")
    return max(
        terminal,
        key=lambda turn: (_turn_user_sequence(turn, entry_by_id), turn.id),
    )


def build_thread_materialization(
    source: ThreadView,
    target: Turn,
    *,
    kind: MaterializationKind,
    id_factory: Callable[[str], str],
    now: datetime,
) -> tuple[ThreadView, RetryPreparation | None]:
    entry_by_id = {entry.id: entry for entry in source.entries}
    target_user = entry_by_id.get(target.user_entry_id)
    if target_user is None or target_user.kind is not ThreadEntryKind.USER_MESSAGE:
        raise ConversationConflict("Target Turn user Entry is unavailable.")
    target_user_sequence = target_user.sequence
    include_target = kind == "fork"
    source_turns = tuple(
        sorted(
            (
                turn
                for turn in source.turns
                if turn.status is not TurnStatus.IN_PROGRESS
                and (
                    _turn_user_sequence(turn, entry_by_id) <= target_user_sequence
                    if include_target
                    else _turn_user_sequence(turn, entry_by_id) < target_user_sequence
                )
            ),
            key=lambda turn: (_turn_user_sequence(turn, entry_by_id), turn.id),
        )
    )
    if include_target and target not in source_turns:
        raise ConversationConflict("Target Turn is outside the materialized prefix.")
    target_boundary = _turn_boundary_sequence(target, entry_by_id)
    for turn in source_turns:
        boundary = _turn_boundary_sequence(turn, entry_by_id)
        if (
            (include_target and boundary > target_boundary)
            or (not include_target and boundary >= target_user_sequence)
        ):
            raise ConversationConflict(
                "Terminal Turn content crosses the materialized prefix boundary."
            )
    included_entry_ids: set[str] = set()
    for turn in source_turns:
        included_entry_ids.add(turn.user_entry_id)
        if (
            turn.status is TurnStatus.COMPLETED
            and turn.assistant_entry_id is not None
        ):
            included_entry_ids.add(turn.assistant_entry_id)
    direct_boundary = (
        target_boundary if include_target else target_user_sequence - 1
    )
    source_entries = tuple(
        entry
        for entry in sorted(
            source.entries,
            key=lambda entry: (entry.sequence, entry.id),
        )
        if entry.id in included_entry_ids
        or (
            entry.kind is ThreadEntryKind.DIRECT_COMMAND
            and entry.sequence <= direct_boundary
        )
    )
    new_thread_id = id_factory("thread")
    cloned_entries: list[ThreadEntry] = []
    entry_id_map: dict[str, str] = {}
    for sequence, entry in enumerate(source_entries, start=1):
        cloned_id = id_factory("entry")
        entry_id_map[entry.id] = cloned_id
        metadata = dict(entry.metadata)
        if entry.kind is ThreadEntryKind.DIRECT_COMMAND:
            metadata.pop("operation_id", None)
        cloned_entries.append(
            ThreadEntry(
                id=cloned_id,
                thread_id=new_thread_id,
                sequence=sequence,
                kind=entry.kind,
                content=entry.content,
                client_message_id=(
                    id_factory("client")
                    if entry.kind is ThreadEntryKind.USER_MESSAGE
                    else None
                ),
                metadata=metadata,
                created_at=_materialized_time(now, sequence * 4),
            )
        )
    cloned_entry_by_id = {entry.id: entry for entry in cloned_entries}
    cloned_turns: list[Turn] = []
    for turn in source_turns:
        cloned_id = id_factory("turn")
        cloned_user_id = entry_id_map.get(turn.user_entry_id)
        cloned_assistant_id = (
            entry_id_map.get(turn.assistant_entry_id)
            if turn.status is TurnStatus.COMPLETED
            and turn.assistant_entry_id is not None
            else None
        )
        if cloned_user_id is None or (
            turn.status is TurnStatus.COMPLETED
            and turn.assistant_entry_id is not None
            and cloned_assistant_id is None
        ):
            raise ConversationConflict("Turn prefix Entry mapping is incomplete.")
        user_sequence = cloned_entry_by_id[cloned_user_id].sequence
        terminal_sequence = (
            cloned_entry_by_id[cloned_assistant_id].sequence
            if cloned_assistant_id is not None
            else user_sequence
        )
        created_at = _materialized_time(now, user_sequence * 4 + 1)
        completed_at = _materialized_time(now, terminal_sequence * 4 + 2)
        cloned_turns.append(
            Turn(
                id=cloned_id,
                thread_id=new_thread_id,
                checkpoint_key=cloned_id,
                status=turn.status,
                provider=turn.provider,
                model=turn.model,
                thinking_enabled=turn.thinking_enabled,
                skill_mode=turn.skill_mode,
                budgets=turn.budgets,
                user_entry_id=cloned_user_id,
                assistant_entry_id=cloned_assistant_id,
                usage=turn.usage,
                termination_reason=turn.termination_reason,
                error_code=turn.error_code,
                context_manifest=(),
                created_at=created_at,
                updated_at=completed_at,
                completed_at=completed_at,
            )
        )
    retry_turn: Turn | None = None
    retry_content: str | None = None
    retry_client_message_id: str | None = None
    if kind == "retry":
        retry_content = target_user.content
        retry_client_message_id = id_factory("client")
        retry_entry = ThreadEntry(
            id=id_factory("entry"),
            thread_id=new_thread_id,
            sequence=len(cloned_entries) + 1,
            kind=ThreadEntryKind.USER_MESSAGE,
            content=retry_content,
            client_message_id=retry_client_message_id,
            metadata=dict(target_user.metadata),
            created_at=_materialized_time(now, (len(cloned_entries) + 1) * 4),
        )
        cloned_entries.append(retry_entry)
        retry_turn_id = id_factory("turn")
        retry_created_at = _materialized_time(now, retry_entry.sequence * 4 + 1)
        retry_turn = Turn(
            id=retry_turn_id,
            thread_id=new_thread_id,
            checkpoint_key=retry_turn_id,
            status=TurnStatus.IN_PROGRESS,
            provider=target.provider,
            model=target.model,
            thinking_enabled=target.thinking_enabled,
            skill_mode=target.skill_mode,
            budgets=target.budgets,
            user_entry_id=retry_entry.id,
            context_manifest=(),
            created_at=retry_created_at,
            updated_at=retry_created_at,
        )
        cloned_turns.append(retry_turn)
    latest = max(
        (
            now,
            *(entry.created_at for entry in cloned_entries),
            *(turn.updated_at for turn in cloned_turns),
        )
    )
    title_prefix = "Fork of " if kind == "fork" else "Retry of "
    thread = Thread(
        id=new_thread_id,
        workspace_key=source.thread.workspace_key,
        title=_materialized_title(title_prefix, source.thread.title),
        title_source=ThreadTitleSource.MANUAL,
        current_model=source.thread.current_model,
        thinking_enabled=source.thread.thinking_enabled,
        skill_mode=source.thread.skill_mode,
        lineage=ThreadLineage(
            kind=kind,
            source_thread_id=source.thread.id,
            source_turn_id=target.id,
        ),
        created_at=now,
        updated_at=latest,
    )
    view = ThreadView(
        thread=thread,
        entries=tuple(cloned_entries),
        turns=tuple(cloned_turns),
    )
    if retry_turn is None:
        return view, None
    assert retry_content is not None
    assert retry_client_message_id is not None
    return (
        view,
        RetryPreparation(
            view=view,
            turn=retry_turn,
            content=retry_content,
            client_message_id=retry_client_message_id,
        ),
    )


def _turn_user_sequence(turn: Turn, entries: dict[str, ThreadEntry]) -> int:
    user = entries.get(turn.user_entry_id)
    if user is None or user.kind is not ThreadEntryKind.USER_MESSAGE:
        raise ConversationConflict("Turn user Entry is unavailable.")
    return user.sequence


def _turn_boundary_sequence(turn: Turn, entries: dict[str, ThreadEntry]) -> int:
    user_sequence = _turn_user_sequence(turn, entries)
    if turn.status is not TurnStatus.COMPLETED:
        return user_sequence
    if turn.assistant_entry_id is None:
        raise ConversationConflict("Completed Turn assistant Entry is unavailable.")
    assistant = entries.get(turn.assistant_entry_id)
    if assistant is None or assistant.kind is not ThreadEntryKind.ASSISTANT_MESSAGE:
        raise ConversationConflict("Completed Turn assistant Entry is unavailable.")
    if assistant.sequence <= user_sequence:
        raise ConversationConflict("Completed Turn transcript order is invalid.")
    return assistant.sequence


def _materialized_time(now: datetime, ordinal: int) -> datetime:
    return now + timedelta(microseconds=ordinal)


def _materialized_title(prefix: str, source_title: str) -> str:
    normalized = normalize_title(f"{prefix}{source_title}")
    clusters = visible_graphemes(normalized)
    if len(clusters) <= 100:
        return normalized
    return "".join(clusters[:99]) + "…"


__all__ = [
    "MaterializationKind",
    "RetryPreparation",
    "ThreadMaterializationPlan",
    "build_thread_materialization",
    "materialization_source_fingerprint",
    "terminal_materialization_target",
]
