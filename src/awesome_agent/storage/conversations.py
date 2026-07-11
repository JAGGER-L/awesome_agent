from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from awesome_agent.conversation.models import (
    Thread,
    ThreadEntry,
    ThreadEntryKind,
    ThreadListPage,
    ThreadPage,
    ThreadSummary,
    ThreadView,
    ToolActivity,
    ToolActivityOrigin,
    ToolActivityOutcome,
    Turn,
    TurnStatus,
    UsageSummary,
)
from awesome_agent.conversation.repository import (
    ConversationConflict,
    ThreadNotFound,
    TurnBusy,
    TurnNotFound,
    require_turn_transition,
)
from awesome_agent.core.tools import ToolActivityDraft
from awesome_agent.storage.database import application_connection


class SQLiteConversationRepositories:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.threads = SQLiteThreadRepository(path)
        self.entries = SQLiteThreadEntryRepository(path)
        self.turns = SQLiteTurnRepository(path)
        self.summaries = SQLiteThreadSummaryRepository(path)
        self.tool_activities = SQLiteToolActivityRepository(path)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with application_connection(self.path) as connection, connection:
            yield connection

    def create_thread(self, thread: Thread) -> Thread:
        return self.threads.create(thread)

    def update_thread(self, thread: Thread) -> Thread:
        return self.threads.update(thread)

    def list_threads(self, workspace_key: str) -> Sequence[Thread]:
        return self.threads.list(workspace_key)

    def list_threads_page(
        self,
        workspace_key: str,
        *,
        cursor: tuple[datetime, str] | None,
        limit: int,
    ) -> ThreadListPage:
        threads, has_more = self.threads.list_page(
            workspace_key,
            cursor=cursor,
            limit=limit,
        )
        return ThreadListPage(threads=threads, has_more=has_more)

    def read_thread(self, thread_id: str) -> ThreadView:
        with self.transaction() as connection:
            thread = self.threads.get(thread_id, connection=connection)
            if thread is None:
                raise ThreadNotFound(thread_id)
            return ThreadView(
                thread=thread,
                entries=tuple(self.entries.list(thread_id, connection=connection)),
                turns=tuple(self.turns.list(thread_id, connection=connection)),
                summary=self.summaries.get(thread_id, connection=connection),
                tool_activities=tuple(
                    self.tool_activities.list(thread_id, connection=connection)
                ),
            )

    def read_thread_page(
        self,
        thread_id: str,
        *,
        before_sequence: int | None,
        limit: int,
    ) -> ThreadPage:
        with self.transaction() as connection:
            thread = self.threads.get(thread_id, connection=connection)
            if thread is None:
                raise ThreadNotFound(thread_id)
            entries, has_more = self.entries.list_page(
                thread_id,
                before_sequence=before_sequence,
                limit=limit,
                connection=connection,
            )
            entry_ids = tuple(entry.id for entry in entries)
            turns = self.turns.list_for_entries(
                thread_id,
                entry_ids=entry_ids,
                connection=connection,
            )
            direct_operation_ids = tuple(
                operation_id
                for entry in entries
                if entry.kind is ThreadEntryKind.DIRECT_COMMAND
                and isinstance(
                    operation_id := entry.metadata.get("operation_id"),
                    str,
                )
            )
            activities = self.tool_activities.list_associated(
                thread_id,
                turn_ids=tuple(turn.id for turn in turns),
                operation_ids=direct_operation_ids,
                connection=connection,
            )
            view = ThreadView(
                thread=thread,
                entries=entries,
                turns=turns,
                summary=self.summaries.get(thread_id, connection=connection),
                tool_activities=activities,
            )
        return ThreadPage(
            view=view,
            has_more=has_more,
            next_before_sequence=(
                entries[0].sequence if has_more and entries else None
            ),
        )

    def thread_id_for_turn(self, turn_id: str) -> str | None:
        turn = self.turns.get(turn_id)
        return None if turn is None else turn.thread_id

    def begin_turn(self, user_entry: ThreadEntry, turn: Turn) -> Turn:
        with self.transaction() as connection:
            thread = self.threads.get(turn.thread_id, connection=connection)
            if thread is None:
                raise ThreadNotFound(turn.thread_id)
            if self.turns.in_progress(turn.thread_id, connection=connection):
                raise TurnBusy(turn.thread_id)
            self._require_next_sequence(user_entry, connection)
            self.entries.append(user_entry, connection=connection)
            self.turns.create(turn, connection=connection)
            self.threads.update(
                thread.model_copy(update={"updated_at": user_entry.created_at}),
                connection=connection,
            )
        return turn

    def complete_turn(self, assistant_entry: ThreadEntry, turn: Turn) -> Turn:
        with self.transaction() as connection:
            current = self.turns.get(turn.id, connection=connection)
            if current is None:
                raise TurnNotFound(turn.id)
            require_turn_transition(current.status, TurnStatus.COMPLETED)
            if current.thread_id != assistant_entry.thread_id:
                raise ConversationConflict("Assistant Entry belongs to another Thread.")
            self._require_next_sequence(assistant_entry, connection)
            self.entries.append(assistant_entry, connection=connection)
            self.turns.update(turn, connection=connection)
            thread = self.threads.get(turn.thread_id, connection=connection)
            if thread is None:
                raise ThreadNotFound(turn.thread_id)
            self.threads.update(
                thread.model_copy(update={"updated_at": turn.updated_at}),
                connection=connection,
            )
        return turn

    def update_terminal_turn(self, turn: Turn) -> Turn:
        with self.transaction() as connection:
            current = self.turns.get(turn.id, connection=connection)
            if current is None:
                raise TurnNotFound(turn.id)
            require_turn_transition(current.status, turn.status)
            self.turns.update(turn, connection=connection)
            thread = self.threads.get(turn.thread_id, connection=connection)
            if thread is None:
                raise ThreadNotFound(turn.thread_id)
            self.threads.update(
                thread.model_copy(update={"updated_at": turn.updated_at}),
                connection=connection,
            )
        return turn

    def append_direct_command(self, entry: ThreadEntry) -> ThreadEntry:
        with self.transaction() as connection:
            thread = self.threads.get(entry.thread_id, connection=connection)
            if thread is None:
                raise ThreadNotFound(entry.thread_id)
            self._require_next_sequence(entry, connection)
            self.entries.append(entry, connection=connection)
            self.threads.update(
                thread.model_copy(update={"updated_at": entry.created_at}),
                connection=connection,
            )
        return entry

    def compare_and_swap_summary(
        self,
        summary: ThreadSummary,
        *,
        expected: ThreadSummary | None,
    ) -> ThreadSummary:
        return self.summaries.compare_and_swap(summary, expected=expected)

    def _require_next_sequence(
        self,
        entry: ThreadEntry,
        connection: sqlite3.Connection,
    ) -> None:
        row = connection.execute(
            """
            SELECT COALESCE(MAX(sequence), 0) + 1
            FROM thread_entries WHERE thread_id = ?
            """,
            (entry.thread_id,),
        ).fetchone()
        expected = int(row[0])
        if entry.sequence != expected:
            raise ConversationConflict("Thread Entry sequence changed concurrently.")


class _SQLiteRepository:
    def __init__(self, path: Path) -> None:
        self._path = path

    @contextmanager
    def _connection(
        self,
        connection: sqlite3.Connection | None,
    ) -> Iterator[sqlite3.Connection]:
        if connection is not None:
            yield connection
            return
        with application_connection(self._path) as opened, opened:
            yield opened


class SQLiteThreadRepository(_SQLiteRepository):
    def create(
        self,
        thread: Thread,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> Thread:
        try:
            with self._connection(connection) as active:
                active.execute(
                    """
                    INSERT INTO threads (
                        thread_id, workspace_key, title, current_model,
                        thinking_enabled, skill_mode, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        thread.id,
                        thread.workspace_key,
                        thread.title,
                        thread.current_model,
                        int(thread.thinking_enabled),
                        thread.skill_mode,
                        _time(thread.created_at),
                        _time(thread.updated_at),
                    ),
                )
        except sqlite3.IntegrityError:
            raise ConversationConflict("Thread already exists or is invalid.") from None
        return thread

    def get(
        self,
        thread_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> Thread | None:
        with self._connection(connection) as active:
            row = active.execute(
                "SELECT * FROM threads WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
        return None if row is None else _thread_from_row(row)

    def list(
        self,
        workspace_key: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> Sequence[Thread]:
        with self._connection(connection) as active:
            rows = active.execute(
                """
                SELECT * FROM threads
                WHERE workspace_key = ?
                ORDER BY updated_at DESC, thread_id
                """,
                (workspace_key,),
            ).fetchall()
        return tuple(_thread_from_row(row) for row in rows)

    def list_page(
        self,
        workspace_key: str,
        *,
        cursor: tuple[datetime, str] | None,
        limit: int,
        connection: sqlite3.Connection | None = None,
    ) -> tuple[tuple[Thread, ...], bool]:
        with self._connection(connection) as active:
            if cursor is None:
                rows = active.execute(
                    """
                    SELECT * FROM threads
                    WHERE workspace_key = ?
                    ORDER BY updated_at DESC, thread_id
                    LIMIT ?
                    """,
                    (workspace_key, limit + 1),
                ).fetchall()
            else:
                updated_at, thread_id = cursor
                encoded_time = _time(updated_at)
                rows = active.execute(
                    """
                    SELECT * FROM threads
                    WHERE workspace_key = ?
                      AND (updated_at < ? OR (updated_at = ? AND thread_id > ?))
                    ORDER BY updated_at DESC, thread_id
                    LIMIT ?
                    """,
                    (
                        workspace_key,
                        encoded_time,
                        encoded_time,
                        thread_id,
                        limit + 1,
                    ),
                ).fetchall()
        return (
            tuple(_thread_from_row(row) for row in rows[:limit]),
            len(rows) > limit,
        )

    def update(
        self,
        thread: Thread,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> Thread:
        with self._connection(connection) as active:
            cursor = active.execute(
                """
                UPDATE threads SET
                    workspace_key = ?, title = ?, current_model = ?,
                    thinking_enabled = ?, skill_mode = ?, updated_at = ?
                WHERE thread_id = ?
                """,
                (
                    thread.workspace_key,
                    thread.title,
                    thread.current_model,
                    int(thread.thinking_enabled),
                    thread.skill_mode,
                    _time(thread.updated_at),
                    thread.id,
                ),
            )
            if cursor.rowcount != 1:
                raise ThreadNotFound(thread.id)
        return thread


class SQLiteThreadEntryRepository(_SQLiteRepository):
    def append(
        self,
        entry: ThreadEntry,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> ThreadEntry:
        try:
            with self._connection(connection) as active:
                active.execute(
                    """
                    INSERT INTO thread_entries (
                        entry_id, thread_id, sequence, kind, content,
                        metadata_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.id,
                        entry.thread_id,
                        entry.sequence,
                        entry.kind.value,
                        entry.content,
                        _json(entry.metadata),
                        _time(entry.created_at),
                    ),
                )
        except sqlite3.IntegrityError:
            raise ConversationConflict(
                "Thread Entry conflicts with stored state."
            ) from None
        return entry

    def list(
        self,
        thread_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> Sequence[ThreadEntry]:
        with self._connection(connection) as active:
            rows = active.execute(
                """
                SELECT * FROM thread_entries
                WHERE thread_id = ? ORDER BY sequence
                """,
                (thread_id,),
            ).fetchall()
        return tuple(_entry_from_row(row) for row in rows)

    def list_page(
        self,
        thread_id: str,
        *,
        before_sequence: int | None,
        limit: int,
        connection: sqlite3.Connection | None = None,
    ) -> tuple[tuple[ThreadEntry, ...], bool]:
        with self._connection(connection) as active:
            if before_sequence is None:
                rows = active.execute(
                    """
                    SELECT * FROM thread_entries
                    WHERE thread_id = ?
                    ORDER BY sequence DESC
                    LIMIT ?
                    """,
                    (thread_id, limit + 1),
                ).fetchall()
            else:
                rows = active.execute(
                    """
                    SELECT * FROM thread_entries
                    WHERE thread_id = ? AND sequence < ?
                    ORDER BY sequence DESC
                    LIMIT ?
                    """,
                    (thread_id, before_sequence, limit + 1),
                ).fetchall()
        selected = tuple(_entry_from_row(row) for row in rows[:limit])
        return tuple(reversed(selected)), len(rows) > limit


class SQLiteTurnRepository(_SQLiteRepository):
    def create(
        self,
        turn: Turn,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> Turn:
        try:
            with self._connection(connection) as active:
                active.execute(
                    """
                    INSERT INTO turns (
                        turn_id, thread_id, checkpoint_key, status, provider,
                        model, thinking_enabled, skill_mode, budgets_json,
                        user_entry_id, assistant_entry_id, usage_json,
                        termination_reason,
                        error_code, context_manifest_json, created_at,
                        updated_at, completed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    _turn_values(turn),
                )
        except sqlite3.IntegrityError:
            if self.in_progress(turn.thread_id, connection=connection) is not None:
                raise TurnBusy(turn.thread_id) from None
            raise ConversationConflict("Turn conflicts with stored state.") from None
        return turn

    def get(
        self,
        turn_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> Turn | None:
        with self._connection(connection) as active:
            row = active.execute(
                "SELECT * FROM turns WHERE turn_id = ?",
                (turn_id,),
            ).fetchone()
        return None if row is None else _turn_from_row(row)

    def list(
        self,
        thread_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> Sequence[Turn]:
        with self._connection(connection) as active:
            rows = active.execute(
                """
                SELECT * FROM turns
                WHERE thread_id = ? ORDER BY created_at, turn_id
                """,
                (thread_id,),
            ).fetchall()
        return tuple(_turn_from_row(row) for row in rows)

    def list_for_entries(
        self,
        thread_id: str,
        *,
        entry_ids: tuple[str, ...],
        connection: sqlite3.Connection | None = None,
    ) -> tuple[Turn, ...]:
        if not entry_ids:
            return ()
        placeholders = ",".join("?" for _ in entry_ids)
        with self._connection(connection) as active:
            rows = active.execute(
                f"""
                SELECT * FROM turns
                WHERE thread_id = ?
                  AND (user_entry_id IN ({placeholders})
                       OR assistant_entry_id IN ({placeholders}))
                ORDER BY created_at, turn_id
                """,
                (thread_id, *entry_ids, *entry_ids),
            ).fetchall()
        return tuple(_turn_from_row(row) for row in rows)

    def update(
        self,
        turn: Turn,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> Turn:
        with self._connection(connection) as active:
            cursor = active.execute(
                """
                UPDATE turns SET
                    status = ?, assistant_entry_id = ?, usage_json = ?,
                    termination_reason = ?, error_code = ?,
                    context_manifest_json = ?, updated_at = ?, completed_at = ?
                WHERE turn_id = ?
                """,
                (
                    turn.status.value,
                    turn.assistant_entry_id,
                    _json(turn.usage.model_dump(mode="json")),
                    turn.termination_reason,
                    turn.error_code,
                    _json(turn.context_manifest),
                    _time(turn.updated_at),
                    _optional_time(turn.completed_at),
                    turn.id,
                ),
            )
            if cursor.rowcount != 1:
                raise TurnNotFound(turn.id)
        return turn

    def in_progress(
        self,
        thread_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> Turn | None:
        with self._connection(connection) as active:
            row = active.execute(
                """
                SELECT * FROM turns
                WHERE thread_id = ? AND status = 'in_progress'
                """,
                (thread_id,),
            ).fetchone()
        return None if row is None else _turn_from_row(row)


class SQLiteThreadSummaryRepository(_SQLiteRepository):
    def compare_and_swap(
        self,
        summary: ThreadSummary,
        *,
        expected: ThreadSummary | None,
    ) -> ThreadSummary:
        with self._connection(None) as active:
            current = self.get(summary.thread_id, connection=active)
            if current != expected:
                raise ConversationConflict("Thread Summary changed concurrently.")
            return self.upsert(summary, connection=active)

    def get(
        self,
        thread_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> ThreadSummary | None:
        with self._connection(connection) as active:
            row = active.execute(
                "SELECT * FROM thread_summaries WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()
        return None if row is None else _summary_from_row(row)

    def upsert(
        self,
        summary: ThreadSummary,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> ThreadSummary:
        try:
            with self._connection(connection) as active:
                active.execute(
                    """
                    INSERT INTO thread_summaries (
                        thread_id, content, content_hash,
                        covered_entry_sequence, covered_turn_count,
                        estimated_tokens, provider, model, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(thread_id) DO UPDATE SET
                        content = excluded.content,
                        content_hash = excluded.content_hash,
                        covered_entry_sequence = excluded.covered_entry_sequence,
                        covered_turn_count = excluded.covered_turn_count,
                        estimated_tokens = excluded.estimated_tokens,
                        provider = excluded.provider,
                        model = excluded.model,
                        updated_at = excluded.updated_at
                    """,
                    (
                        summary.thread_id,
                        summary.content,
                        summary.content_hash,
                        summary.covered_entry_sequence,
                        summary.covered_turn_count,
                        summary.estimated_tokens,
                        summary.provider,
                        summary.model,
                        _time(summary.updated_at),
                    ),
                )
        except sqlite3.IntegrityError:
            raise ConversationConflict("Thread Summary is invalid.") from None
        return summary


class SQLiteToolActivityRepository(_SQLiteRepository):
    def finalize(self, draft: ToolActivityDraft) -> None:
        with self._connection(None) as active:
            existing = self._by_operation_call(
                draft.operation_id,
                draft.call_id,
                connection=active,
            )
            if existing is not None:
                return
            row = active.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) + 1
                FROM tool_activities WHERE thread_id = ?
                """,
                (draft.thread_id,),
            ).fetchone()
            self.append(
                ToolActivity(
                    id=f"activity_{uuid4().hex}",
                    thread_id=draft.thread_id,
                    turn_id=draft.turn_id,
                    operation_id=draft.operation_id,
                    call_id=draft.call_id,
                    sequence=int(row[0]),
                    origin=ToolActivityOrigin(draft.origin.value),
                    tool_name=draft.tool_name,
                    outcome=ToolActivityOutcome(draft.outcome),
                    input_summary=draft.input_summary,
                    result_summary=draft.result_summary,
                    error_code=draft.error_code,
                    duration_ms=draft.duration_ms,
                    change_set_id=draft.change_set_id,
                    created_at=datetime.now(UTC),
                ),
                connection=active,
            )

    def append(
        self,
        activity: ToolActivity,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> ToolActivity:
        try:
            with self._connection(connection) as active:
                active.execute(
                    """
                    INSERT INTO tool_activities (
                        activity_id, thread_id, turn_id, operation_id,
                        call_id, sequence, origin, tool_name, outcome,
                        input_summary, result_summary, error_code,
                        duration_ms, change_set_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        activity.id,
                        activity.thread_id,
                        activity.turn_id,
                        activity.operation_id,
                        activity.call_id,
                        activity.sequence,
                        activity.origin.value,
                        activity.tool_name,
                        activity.outcome.value,
                        activity.input_summary,
                        activity.result_summary,
                        activity.error_code,
                        activity.duration_ms,
                        activity.change_set_id,
                        _time(activity.created_at),
                    ),
                )
        except sqlite3.IntegrityError:
            existing = self._by_operation_call(
                activity.operation_id,
                activity.call_id,
                connection=connection,
            )
            if existing == activity:
                return existing
            raise ConversationConflict(
                "Tool Activity conflicts with stored state."
            ) from None
        return activity

    def list(
        self,
        thread_id: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> Sequence[ToolActivity]:
        with self._connection(connection) as active:
            rows = active.execute(
                """
                SELECT * FROM tool_activities
                WHERE thread_id = ? ORDER BY created_at, sequence, activity_id
                """,
                (thread_id,),
            ).fetchall()
        return tuple(_activity_from_row(row) for row in rows)

    def list_associated(
        self,
        thread_id: str,
        *,
        turn_ids: tuple[str, ...],
        operation_ids: tuple[str, ...],
        connection: sqlite3.Connection | None = None,
    ) -> tuple[ToolActivity, ...]:
        clauses: list[str] = []
        parameters: list[object] = [thread_id]
        if turn_ids:
            placeholders = ",".join("?" for _ in turn_ids)
            clauses.append(f"turn_id IN ({placeholders})")
            parameters.extend(turn_ids)
        if operation_ids:
            placeholders = ",".join("?" for _ in operation_ids)
            clauses.append(f"operation_id IN ({placeholders})")
            parameters.extend(operation_ids)
        if not clauses:
            return ()
        predicate = " OR ".join(clauses)
        with self._connection(connection) as active:
            rows = active.execute(
                f"""
                SELECT * FROM tool_activities
                WHERE thread_id = ? AND ({predicate})
                ORDER BY created_at, sequence, activity_id
                """,
                parameters,
            ).fetchall()
        return tuple(_activity_from_row(row) for row in rows)

    def _by_operation_call(
        self,
        operation_id: str,
        call_id: str,
        *,
        connection: sqlite3.Connection | None,
    ) -> ToolActivity | None:
        with self._connection(connection) as active:
            row = active.execute(
                """
                SELECT * FROM tool_activities
                WHERE operation_id = ? AND call_id = ?
                """,
                (operation_id, call_id),
            ).fetchone()
        return None if row is None else _activity_from_row(row)


def _thread_from_row(row: sqlite3.Row) -> Thread:
    return Thread(
        id=row["thread_id"],
        workspace_key=row["workspace_key"],
        title=row["title"],
        current_model=row["current_model"],
        thinking_enabled=bool(row["thinking_enabled"]),
        skill_mode=row["skill_mode"],
        created_at=_parse_time(row["created_at"]),
        updated_at=_parse_time(row["updated_at"]),
    )


def _entry_from_row(row: sqlite3.Row) -> ThreadEntry:
    return ThreadEntry(
        id=row["entry_id"],
        thread_id=row["thread_id"],
        sequence=row["sequence"],
        kind=row["kind"],
        content=row["content"],
        metadata=json.loads(row["metadata_json"]),
        created_at=_parse_time(row["created_at"]),
    )


def _turn_values(turn: Turn) -> tuple[object, ...]:
    return (
        turn.id,
        turn.thread_id,
        turn.checkpoint_key,
        turn.status.value,
        turn.provider,
        turn.model,
        int(turn.thinking_enabled),
        turn.skill_mode,
        _json(turn.budgets.model_dump(mode="json")),
        turn.user_entry_id,
        turn.assistant_entry_id,
        _json(turn.usage.model_dump(mode="json")),
        turn.termination_reason,
        turn.error_code,
        _json(turn.context_manifest),
        _time(turn.created_at),
        _time(turn.updated_at),
        _optional_time(turn.completed_at),
    )


def _turn_from_row(row: sqlite3.Row) -> Turn:
    return Turn(
        id=row["turn_id"],
        thread_id=row["thread_id"],
        checkpoint_key=row["checkpoint_key"],
        status=TurnStatus(row["status"]),
        provider=row["provider"],
        model=row["model"],
        thinking_enabled=bool(row["thinking_enabled"]),
        skill_mode=row["skill_mode"],
        budgets=json.loads(row["budgets_json"]),
        user_entry_id=row["user_entry_id"],
        assistant_entry_id=row["assistant_entry_id"],
        usage=UsageSummary.model_validate(json.loads(row["usage_json"])),
        termination_reason=row["termination_reason"],
        error_code=row["error_code"],
        context_manifest=tuple(json.loads(row["context_manifest_json"])),
        created_at=_parse_time(row["created_at"]),
        updated_at=_parse_time(row["updated_at"]),
        completed_at=_parse_optional_time(row["completed_at"]),
    )


def _summary_from_row(row: sqlite3.Row) -> ThreadSummary:
    return ThreadSummary(
        thread_id=row["thread_id"],
        content=row["content"],
        content_hash=row["content_hash"],
        covered_entry_sequence=row["covered_entry_sequence"],
        covered_turn_count=row["covered_turn_count"],
        estimated_tokens=row["estimated_tokens"],
        provider=row["provider"],
        model=row["model"],
        updated_at=_parse_time(row["updated_at"]),
    )


def _activity_from_row(row: sqlite3.Row) -> ToolActivity:
    return ToolActivity(
        id=row["activity_id"],
        thread_id=row["thread_id"],
        turn_id=row["turn_id"],
        operation_id=row["operation_id"],
        call_id=row["call_id"],
        sequence=row["sequence"],
        origin=row["origin"],
        tool_name=row["tool_name"],
        outcome=row["outcome"],
        input_summary=row["input_summary"],
        result_summary=row["result_summary"],
        error_code=row["error_code"],
        duration_ms=row["duration_ms"],
        change_set_id=row["change_set_id"],
        created_at=_parse_time(row["created_at"]),
    )


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _optional_time(value: datetime | None) -> str | None:
    return None if value is None else _time(value)


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _parse_optional_time(value: str | None) -> datetime | None:
    return None if value is None else _parse_time(value)
