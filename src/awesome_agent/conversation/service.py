from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import JsonValue

from awesome_agent.config.models import TurnConfig
from awesome_agent.conversation.materialization import (
    RetryPreparation,
    ThreadMaterializationPlan,
    build_thread_materialization,
    materialization_source_fingerprint,
    terminal_materialization_target,
)
from awesome_agent.conversation.models import (
    AssistantEntryMetadata,
    Thread,
    ThreadEntry,
    ThreadEntryKind,
    ThreadListPage,
    ThreadPage,
    ThreadSummary,
    ThreadTitleSource,
    ThreadView,
    Turn,
    TurnStatus,
    UsageSummary,
)
from awesome_agent.conversation.repository import (
    ConversationConflict,
    ConversationStore,
    ThreadNotFound,
    TurnNotFound,
    require_turn_transition,
)
from awesome_agent.conversation.titles import (
    automatic_title,
    normalize_title,
    visible_graphemes,
)
from awesome_agent.core.citations import Citation


class ConversationService:
    def __init__(
        self,
        *,
        store: ConversationStore,
        id_factory: Callable[[str], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._id_factory = id_factory or _new_identifier
        self._clock = clock or (lambda: datetime.now(UTC))

    async def create_thread(
        self,
        workspace_key: str,
        title: str | None = None,
        *,
        current_model: str | None = None,
    ) -> Thread:
        normalized_title = "New conversation" if title is None else title.strip()
        if not normalized_title:
            raise ValueError("Thread title cannot be empty.")
        now = self._clock()
        return await self._store.create_thread(
            Thread(
                id=self._id_factory("thread"),
                workspace_key=workspace_key,
                title=normalized_title,
                title_source=(
                    ThreadTitleSource.AUTOMATIC
                    if title is None
                    else ThreadTitleSource.MANUAL
                ),
                current_model=current_model,
                created_at=now,
                updated_at=now,
            )
        )

    async def list_threads(self, workspace_key: str) -> tuple[Thread, ...]:
        return tuple(await self._store.list_threads(workspace_key))

    async def match_thread_prefix(
        self,
        workspace_key: str,
        *,
        prefix: str,
        limit: int = 200,
    ) -> tuple[Thread, ...]:
        return tuple(
            await self._store.match_threads(
                workspace_key,
                prefix=prefix,
                limit=limit,
            )
        )

    async def list_thread_page(
        self,
        workspace_key: str,
        *,
        cursor: tuple[datetime, str] | None,
        limit: int,
    ) -> ThreadListPage:
        return await self._store.list_threads_page(
            workspace_key,
            cursor=cursor,
            limit=limit,
        )

    async def search_thread_page(
        self,
        workspace_key: str,
        *,
        query: str,
        cursor: tuple[datetime, str] | None,
        limit: int,
    ) -> ThreadListPage:
        normalized = query.strip()
        if not 1 <= len(normalized) <= 200:
            raise ValueError("Thread search query must be 1 to 200 characters.")
        if not 1 <= limit <= 50:
            raise ValueError("Thread search limit must be 1 to 50.")
        return await self._store.search_threads_page(
            workspace_key,
            query=normalized,
            cursor=cursor,
            limit=limit,
        )

    async def thread_matches_search(
        self,
        workspace_key: str,
        *,
        query: str,
        thread_id: str,
    ) -> bool:
        normalized = query.strip()
        if not 1 <= len(normalized) <= 200:
            raise ValueError("Thread search query must be 1 to 200 characters.")
        return await self._store.thread_matches_search(
            workspace_key,
            query=normalized,
            thread_id=thread_id,
        )

    async def fork_thread(
        self,
        workspace_key: str,
        source_thread_id: str,
        source_turn_id: str | None = None,
    ) -> ThreadView:
        source, target = await self._materialization_source(
            workspace_key,
            source_thread_id,
            source_turn_id,
        )
        view, preparation = build_thread_materialization(
            source,
            target,
            kind="fork",
            id_factory=self._id_factory,
            now=self._clock(),
        )
        assert preparation is None
        plan = ThreadMaterializationPlan(
            kind="fork",
            source_workspace_key=workspace_key,
            source_thread_id=source.thread.id,
            source_turn_id=target.id,
            source_fingerprint=materialization_source_fingerprint(source),
            view=view,
        )
        return await self._store.materialize_fork(plan)

    async def prepare_retry(
        self,
        workspace_key: str,
        source_thread_id: str,
        source_turn_id: str | None = None,
    ) -> RetryPreparation:
        source, target = await self._materialization_source(
            workspace_key,
            source_thread_id,
            source_turn_id,
        )
        view, preparation = build_thread_materialization(
            source,
            target,
            kind="retry",
            id_factory=self._id_factory,
            now=self._clock(),
        )
        assert preparation is not None
        plan = ThreadMaterializationPlan(
            kind="retry",
            source_workspace_key=workspace_key,
            source_thread_id=source.thread.id,
            source_turn_id=target.id,
            source_fingerprint=materialization_source_fingerprint(source),
            view=view,
        )
        return await self._store.materialize_retry(plan, preparation)

    async def set_skill_mode(self, thread_id: str, skill_mode: str) -> Thread:
        if re.fullmatch(r"(?:auto|off|[a-z][a-z0-9-]{0,63})", skill_mode) is None:
            raise ValueError("Skill mode is invalid.")
        return await self._store.set_thread_skill_mode(
            thread_id,
            skill_mode,
            updated_at=self._clock(),
        )

    async def rename_thread(self, thread_id: str, title: str) -> Thread:
        normalized = normalize_title(title)
        if not normalized:
            raise ValueError("Title required · /rename <title>")
        if len(visible_graphemes(normalized)) > 100:
            raise ValueError("Thread title must be 100 characters or fewer.")
        return await self._store.rename_thread(
            thread_id,
            normalized,
            updated_at=self._clock(),
        )

    async def set_model(self, thread_id: str, model: str | None) -> Thread:
        return await self._store.set_thread_model(
            thread_id,
            model,
            updated_at=self._clock(),
        )

    async def set_thinking(self, thread_id: str, enabled: bool) -> Thread:
        return await self._store.set_thread_thinking(
            thread_id,
            enabled,
            updated_at=self._clock(),
        )

    async def read_thread(self, thread_id: str) -> ThreadView:
        return await self._store.read_thread(thread_id)

    async def read_thread_page(
        self,
        thread_id: str,
        *,
        before_sequence: int | None,
        limit: int,
    ) -> ThreadPage:
        return await self._store.read_thread_page(
            thread_id,
            before_sequence=before_sequence,
            limit=limit,
        )

    async def begin_turn(
        self,
        thread_id: str,
        user_content: str,
        config: TurnConfig,
        *,
        client_message_id: str,
    ) -> Turn:
        if not user_content.strip():
            raise ValueError("User message cannot be empty.")
        view = await self._store.read_thread(thread_id)
        now = self._clock()
        entry = ThreadEntry(
            id=self._id_factory("entry"),
            thread_id=thread_id,
            sequence=_next_sequence(view),
            kind=ThreadEntryKind.USER_MESSAGE,
            content=user_content,
            client_message_id=client_message_id,
            created_at=now,
        )
        turn_id = self._id_factory("turn")
        turn = Turn(
            id=turn_id,
            thread_id=thread_id,
            checkpoint_key=turn_id,
            status=TurnStatus.IN_PROGRESS,
            provider=config.provider,
            model=config.model,
            thinking_enabled=config.thinking_enabled,
            skill_mode=config.skill_mode,
            budgets=config.budgets,
            user_entry_id=entry.id,
            created_at=now,
            updated_at=now,
        )
        suggested_title = (
            automatic_title(user_content)
            if view.thread.title_source is ThreadTitleSource.AUTOMATIC
            and not view.entries
            else None
        )
        return await self._store.begin_turn(
            entry,
            turn,
            automatic_title=suggested_title,
            updated_at=now,
        )

    async def complete_turn(
        self,
        turn_id: str,
        assistant_content: str,
        usage: UsageSummary,
        termination_reason: str,
        context_manifest: tuple[dict[str, JsonValue], ...] = (),
        citations: tuple[Citation, ...] = (),
    ) -> Turn:
        view, current = await self._turn_view(turn_id)
        recorded_manifest = context_manifest or current.context_manifest
        if current.status is TurnStatus.COMPLETED:
            entry = _entry_by_id(view, current.assistant_entry_id)
            if (
                entry is not None
                and entry.content == assistant_content
                and entry.metadata
                == AssistantEntryMetadata(citations=citations).model_dump(mode="json")
                and current.usage == usage
                and current.termination_reason == termination_reason
                and current.context_manifest == recorded_manifest
            ):
                return current
            raise ConversationConflict("Completed Turn finalization differs.")
        require_turn_transition(current.status, TurnStatus.COMPLETED)
        now = self._clock()
        assistant = ThreadEntry(
            id=self._id_factory("entry"),
            thread_id=current.thread_id,
            sequence=_next_sequence(view),
            kind=ThreadEntryKind.ASSISTANT_MESSAGE,
            content=assistant_content,
            metadata=AssistantEntryMetadata(citations=citations).model_dump(
                mode="json"
            ),
            created_at=now,
        )
        completed = current.model_copy(
            update={
                "status": TurnStatus.COMPLETED,
                "assistant_entry_id": assistant.id,
                "usage": usage,
                "termination_reason": termination_reason,
                "context_manifest": recorded_manifest,
                "updated_at": now,
                "completed_at": now,
            }
        )
        completed = Turn.model_validate(completed.model_dump())
        return await self._store.complete_turn(assistant, completed)

    async def store_context_manifest(
        self,
        turn_id: str,
        context_manifest: tuple[dict[str, JsonValue], ...],
    ) -> Turn:
        _, current = await self._turn_view(turn_id)
        if current.status is not TurnStatus.IN_PROGRESS:
            raise ConversationConflict(
                "Only an in-progress Turn can record a context snapshot."
            )
        if current.context_manifest == context_manifest:
            return current
        updated = current.model_copy(
            update={
                "context_manifest": context_manifest,
                "updated_at": self._clock(),
            }
        )
        return await self._store.update_in_progress_turn(
            updated,
            expected_context_manifest=current.context_manifest,
        )

    async def compare_and_swap_context_manifest(
        self,
        turn_id: str,
        context_manifest: tuple[dict[str, JsonValue], ...],
        *,
        expected_context_manifest: tuple[dict[str, JsonValue], ...],
    ) -> Turn:
        _, current = await self._turn_view(turn_id)
        if current.status is not TurnStatus.IN_PROGRESS:
            raise ConversationConflict(
                "Only an in-progress Turn can reconcile a context snapshot."
            )
        if current.context_manifest != expected_context_manifest:
            raise ConversationConflict(
                "Turn context changed before the snapshot was reconciled."
            )
        if current.context_manifest == context_manifest:
            return current
        updated = Turn.model_validate(
            current.model_copy(
                update={
                    "context_manifest": context_manifest,
                    "updated_at": self._clock(),
                }
            ).model_dump()
        )
        return await self._store.update_in_progress_turn(
            updated,
            expected_context_manifest=expected_context_manifest,
        )

    async def fail_turn(
        self,
        turn_id: str,
        error_code: str,
        *,
        usage: UsageSummary | None = None,
        context_manifest: tuple[dict[str, JsonValue], ...] = (),
    ) -> Turn:
        if not error_code.strip():
            raise ValueError("error_code cannot be empty.")
        observed_usage = usage or UsageSummary()
        _, current = await self._turn_view(turn_id)
        recorded_manifest = context_manifest or current.context_manifest
        if current.status is TurnStatus.FAILED:
            if (
                current.error_code == error_code
                and current.usage == observed_usage
                and current.context_manifest == recorded_manifest
            ):
                return current
            raise ConversationConflict("Failed Turn finalization differs.")
        require_turn_transition(current.status, TurnStatus.FAILED)
        now = self._clock()
        failed = current.model_copy(
            update={
                "status": TurnStatus.FAILED,
                "error_code": error_code,
                "usage": observed_usage,
                "context_manifest": recorded_manifest,
                "updated_at": now,
                "completed_at": now,
            }
        )
        failed = Turn.model_validate(failed.model_dump())
        return await self._store.update_terminal_turn(failed)

    async def cancel_turn(
        self,
        turn_id: str,
        *,
        usage: UsageSummary | None = None,
        context_manifest: tuple[dict[str, JsonValue], ...] = (),
    ) -> Turn:
        observed_usage = usage or UsageSummary()
        _, current = await self._turn_view(turn_id)
        recorded_manifest = context_manifest or current.context_manifest
        if current.status is TurnStatus.CANCELLED:
            if (
                current.usage == observed_usage
                and current.context_manifest == recorded_manifest
            ):
                return current
            raise ConversationConflict("Cancelled Turn finalization differs.")
        require_turn_transition(current.status, TurnStatus.CANCELLED)
        now = self._clock()
        cancelled = current.model_copy(
            update={
                "status": TurnStatus.CANCELLED,
                "termination_reason": "cancelled",
                "usage": observed_usage,
                "context_manifest": recorded_manifest,
                "updated_at": now,
                "completed_at": now,
            }
        )
        cancelled = Turn.model_validate(cancelled.model_dump())
        return await self._store.update_terminal_turn(cancelled)

    async def thread_usage(self, thread_id: str) -> UsageSummary:
        total = UsageSummary()
        for turn in (await self._store.read_thread(thread_id)).turns:
            total += turn.usage
        return total

    async def latest_context_manifest(
        self,
        thread_id: str,
    ) -> tuple[dict[str, JsonValue], ...]:
        turns = (await self._store.read_thread(thread_id)).turns
        return next(
            (
                turn.context_manifest
                for turn in reversed(turns)
                if turn.context_manifest
            ),
            (),
        )

    async def append_direct_command(
        self,
        thread_id: str,
        content: str,
        metadata: dict[str, JsonValue],
    ) -> ThreadEntry:
        view = await self._store.read_thread(thread_id)
        entry = ThreadEntry(
            id=self._id_factory("entry"),
            thread_id=thread_id,
            sequence=_next_sequence(view),
            kind=ThreadEntryKind.DIRECT_COMMAND,
            content=content,
            metadata=metadata,
            created_at=self._clock(),
        )
        return await self._store.append_direct_command(entry)

    async def store_summary(
        self,
        summary: ThreadSummary,
        *,
        expected: ThreadSummary | None,
    ) -> ThreadSummary:
        return await self._store.compare_and_swap_summary(summary, expected=expected)

    async def _turn_view(self, turn_id: str) -> tuple[ThreadView, Turn]:
        thread_id = await self._thread_id_for_turn(turn_id)
        view = await self._store.read_thread(thread_id)
        current = next((turn for turn in view.turns if turn.id == turn_id), None)
        if current is None:
            raise TurnNotFound(turn_id)
        return view, current

    async def _materialization_source(
        self,
        workspace_key: str,
        source_thread_id: str,
        source_turn_id: str | None,
    ) -> tuple[ThreadView, Turn]:
        _require_identifier(workspace_key, label="Workspace")
        _require_identifier(source_thread_id, label="source Thread")
        if source_turn_id is not None:
            _require_identifier(source_turn_id, label="source Turn")
        source = await self._store.read_thread(source_thread_id)
        if source.thread.workspace_key != workspace_key:
            raise ThreadNotFound(source_thread_id)
        target = terminal_materialization_target(source, source_turn_id)
        return source, target

    async def _thread_id_for_turn(self, turn_id: str) -> str:
        thread_id = await self._store.thread_id_for_turn(turn_id)
        if thread_id is None:
            raise TurnNotFound(turn_id)
        return thread_id


def _next_sequence(view: ThreadView) -> int:
    return 1 if not view.entries else view.entries[-1].sequence + 1


def _entry_by_id(view: ThreadView, entry_id: str | None) -> ThreadEntry | None:
    if entry_id is None:
        return None
    return next((entry for entry in view.entries if entry.id == entry_id), None)


def _new_identifier(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _require_identifier(value: str, *, label: str) -> None:
    if not 1 <= len(value) <= 128:
        raise ValueError(f"{label} identity must be 1 to 128 characters.")
