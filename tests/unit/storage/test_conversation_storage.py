from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from awesome_agent.config import BudgetConfig
from awesome_agent.conversation import (
    ConversationConflict,
    Thread,
    ThreadEntry,
    ThreadEntryKind,
    ThreadSummary,
    ToolActivity,
    ToolActivityOrigin,
    ToolActivityOutcome,
    Turn,
    TurnBusy,
    TurnStatus,
    UsageSummary,
)
from awesome_agent.core.tools import ToolActivityDraft, ToolExecutionOrigin
from awesome_agent.storage.conversations import SQLiteConversationRepositories
from awesome_agent.storage.database import (
    APPLICATION_SCHEMA_VERSION,
    application_connection,
    initialize_application_database,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _thread(identifier: str = "thread_1") -> Thread:
    now = _now()
    return Thread(
        id=identifier,
        workspace_key="workspace_1",
        title=f"Thread {identifier}",
        created_at=now,
        updated_at=now,
    )


def _entry(
    identifier: str,
    *,
    thread_id: str = "thread_1",
    sequence: int,
    kind: ThreadEntryKind = ThreadEntryKind.USER_MESSAGE,
) -> ThreadEntry:
    return ThreadEntry(
        id=identifier,
        thread_id=thread_id,
        sequence=sequence,
        kind=kind,
        content=f"content {identifier}",
        client_message_id=(
            f"client_{identifier}" if kind is ThreadEntryKind.USER_MESSAGE else None
        ),
        metadata={"z": 1, "a": True},
        created_at=_now(),
    )


def _turn(
    identifier: str = "turn_1",
    *,
    thread_id: str = "thread_1",
    user_entry_id: str = "entry_1",
) -> Turn:
    now = _now()
    return Turn(
        id=identifier,
        thread_id=thread_id,
        checkpoint_key=identifier,
        status=TurnStatus.IN_PROGRESS,
        provider="deepseek",
        model="deepseek/deepseek-v4-flash",
        budgets=BudgetConfig(model_calls=12, tool_calls=24),
        user_entry_id=user_entry_id,
        usage=UsageSummary(model_calls=1),
        context_manifest=({"kind": "current_input", "order": 1},),
        created_at=now,
        updated_at=now,
    )


def test_application_schema_creates_only_product_state_tables(
    tmp_path: Path,
) -> None:
    path = tmp_path / "application.db"

    initialize_application_database(path)

    with application_connection(path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }

    assert version == APPLICATION_SCHEMA_VERSION == 1
    assert {
        "trusted_workspaces",
        "change_sets",
        "pending_mutations",
        "threads",
        "thread_entries",
        "turns",
        "thread_summaries",
        "tool_activities",
        "mcp_enablements",
    } <= tables
    assert {
        "idx_turns_one_in_progress",
        "idx_thread_entries_sequence",
        "idx_tool_activities_operation_call",
        "idx_tool_activities_thread_operation",
        "idx_tool_activities_thread_turn",
    } <= indexes
    assert not {"runs", "jobs", "leases", "attempts", "event_store"} & tables
    with application_connection(path) as connection:
        thread_index_columns = [
            row[2]
            for row in connection.execute(
                "PRAGMA index_info(idx_threads_workspace_updated)"
            ).fetchall()
        ]
    assert thread_index_columns == ["workspace_key", "updated_at", "thread_id"]


def test_current_schema_accepts_new_identity_and_rejects_duplicate(
    tmp_path: Path,
) -> None:
    path = tmp_path / "application.db"
    repositories = SQLiteConversationRepositories(path)
    repositories.threads.create(_thread())

    repositories.entries.append(_entry("entry_new", sequence=1))

    with pytest.raises(ConversationConflict):
        repositories.entries.append(
            _entry(
                "entry_duplicate",
                sequence=2,
            ).model_copy(update={"client_message_id": "client_entry_new"})
        )


def test_repositories_persist_and_reopen_ordered_thread_state(tmp_path: Path) -> None:
    path = tmp_path / "application.db"
    repositories = SQLiteConversationRepositories(path)
    repositories.threads.create(_thread())
    repositories.entries.append(_entry("entry_2", sequence=2))
    repositories.entries.append(_entry("entry_1", sequence=1))
    repositories.turns.create(_turn())

    reopened = SQLiteConversationRepositories(path)

    assert reopened.threads.get("thread_1") == _thread_from_store(repositories)
    assert [entry.id for entry in reopened.entries.list("thread_1")] == [
        "entry_1",
        "entry_2",
    ]
    assert reopened.turns.get("turn_1") == repositories.turns.get("turn_1")


def _thread_from_store(repositories: SQLiteConversationRepositories) -> Thread:
    thread = repositories.threads.get("thread_1")
    assert thread is not None
    return thread


def test_entry_sequence_is_unique_per_thread(tmp_path: Path) -> None:
    repositories = SQLiteConversationRepositories(tmp_path / "application.db")
    repositories.threads.create(_thread())
    repositories.entries.append(_entry("entry_1", sequence=1))

    with pytest.raises(ConversationConflict):
        repositories.entries.append(_entry("entry_2", sequence=1))


def test_only_one_in_progress_turn_exists_per_thread(tmp_path: Path) -> None:
    repositories = SQLiteConversationRepositories(tmp_path / "application.db")
    repositories.threads.create(_thread())
    repositories.entries.append(_entry("entry_1", sequence=1))
    repositories.entries.append(_entry("entry_2", sequence=2))
    repositories.turns.create(_turn())

    with pytest.raises(TurnBusy):
        repositories.turns.create(_turn("turn_2", user_entry_id="entry_2"))

    repositories.threads.create(_thread("thread_2"))
    repositories.entries.append(_entry("entry_other", thread_id="thread_2", sequence=1))
    other = repositories.turns.create(
        _turn(
            "turn_other",
            thread_id="thread_2",
            user_entry_id="entry_other",
        )
    )
    assert other.thread_id == "thread_2"


def test_foreign_keys_reject_cross_thread_turn_entry(tmp_path: Path) -> None:
    repositories = SQLiteConversationRepositories(tmp_path / "application.db")
    repositories.threads.create(_thread())
    repositories.threads.create(_thread("thread_2"))
    repositories.entries.append(_entry("entry_other", thread_id="thread_2", sequence=1))

    with pytest.raises(ConversationConflict):
        repositories.turns.create(_turn(user_entry_id="entry_other"))


def test_repository_transaction_rolls_back_multirow_failure(tmp_path: Path) -> None:
    repositories = SQLiteConversationRepositories(tmp_path / "application.db")

    with (
        pytest.raises(RuntimeError, match="rollback"),
        repositories.transaction() as connection,
    ):
        repositories.threads.create(_thread(), connection=connection)
        repositories.entries.append(
            _entry("entry_1", sequence=1),
            connection=connection,
        )
        raise RuntimeError("rollback")

    assert repositories.threads.get("thread_1") is None


def test_summary_upsert_and_tool_activity_idempotency(tmp_path: Path) -> None:
    repositories = SQLiteConversationRepositories(tmp_path / "application.db")
    repositories.threads.create(_thread())
    summary = ThreadSummary(
        thread_id="thread_1",
        content="summary",
        content_hash="a" * 64,
        covered_entry_sequence=2,
        covered_turn_count=1,
        estimated_tokens=10,
        provider="deepseek",
        model="deepseek/deepseek-v4-flash",
        updated_at=_now(),
    )
    activity = ToolActivity(
        id="activity_1",
        thread_id="thread_1",
        turn_id=None,
        operation_id="operation_1",
        call_id="call_1",
        sequence=1,
        origin=ToolActivityOrigin.DIRECT,
        tool_name="execute",
        outcome=ToolActivityOutcome.SUCCESS,
        input_summary="command summary",
        result_summary="result summary",
        duration_ms=10,
        created_at=_now(),
    )

    assert repositories.summaries.upsert(summary) == summary
    assert repositories.summaries.get("thread_1") == summary
    assert repositories.tool_activities.append(activity) == activity
    assert repositories.tool_activities.append(activity) == activity

    changed = activity.model_copy(update={"result_summary": "different"})
    with pytest.raises(ConversationConflict):
        repositories.tool_activities.append(changed)


def test_tool_activity_writer_finalizes_one_terminal_record(tmp_path: Path) -> None:
    repositories = SQLiteConversationRepositories(tmp_path / "application.db")
    repositories.threads.create(_thread())
    repositories.entries.append(_entry("entry_1", sequence=1))
    repositories.turns.create(_turn())
    draft = ToolActivityDraft(
        thread_id="thread_1",
        turn_id="turn_1",
        operation_id="operation_1",
        call_id="call_1",
        origin=ToolExecutionOrigin.AGENT,
        tool_name="read_file",
        outcome="success",
        input_summary="arguments: path",
        result_summary="Tool execution completed.",
        duration_ms=12,
        change_set_id=None,
    )

    repositories.tool_activities.finalize(draft)
    repositories.tool_activities.finalize(draft.model_copy(update={"duration_ms": 99}))

    activities = repositories.tool_activities.list("thread_1")
    assert len(activities) == 1
    assert activities[0].duration_ms == 12


def test_summary_compare_and_swap_rejects_stale_coverage(tmp_path: Path) -> None:
    repositories = SQLiteConversationRepositories(tmp_path / "application.db")
    repositories.threads.create(_thread())
    first = ThreadSummary(
        thread_id="thread_1",
        content="first",
        content_hash="a" * 64,
        covered_entry_sequence=2,
        covered_turn_count=1,
        estimated_tokens=10,
        provider="deepseek",
        model="deepseek/deepseek-v4-flash",
        updated_at=_now(),
    )
    second = first.model_copy(
        update={
            "content": "second",
            "content_hash": "b" * 64,
            "covered_entry_sequence": 4,
            "covered_turn_count": 2,
        }
    )

    assert repositories.compare_and_swap_summary(first, expected=None) == first
    assert repositories.compare_and_swap_summary(second, expected=first) == second
    with pytest.raises(ConversationConflict):
        repositories.compare_and_swap_summary(first, expected=None)


def test_json_columns_use_canonical_compact_encoding(tmp_path: Path) -> None:
    path = tmp_path / "application.db"
    repositories = SQLiteConversationRepositories(path)
    repositories.threads.create(_thread())
    repositories.entries.append(_entry("entry_1", sequence=1))
    repositories.turns.create(_turn())

    with application_connection(path) as connection:
        metadata = connection.execute(
            "SELECT metadata_json FROM thread_entries WHERE entry_id = ?",
            ("entry_1",),
        ).fetchone()[0]
        usage = connection.execute(
            "SELECT usage_json FROM turns WHERE turn_id = ?",
            ("turn_1",),
        ).fetchone()[0]
        budgets = connection.execute(
            "SELECT budgets_json FROM turns WHERE turn_id = ?",
            ("turn_1",),
        ).fetchone()[0]

    assert metadata == '{"a":true,"z":1}'
    assert json.loads(usage)["model_calls"] == 1
    assert json.loads(budgets)["model_calls"] == 12
    assert ": " not in usage
    assert ": " not in budgets


def test_timestamps_are_normalized_to_utc_iso_8601(tmp_path: Path) -> None:
    path = tmp_path / "application.db"
    repositories = SQLiteConversationRepositories(path)
    offset_time = datetime(2026, 7, 10, 16, 0, tzinfo=timezone(timedelta(hours=8)))
    repositories.threads.create(
        Thread(
            id="thread_utc",
            workspace_key="workspace_1",
            title="UTC",
            created_at=offset_time,
            updated_at=offset_time,
        )
    )

    with application_connection(path) as connection:
        stored = connection.execute(
            "SELECT created_at FROM threads WHERE thread_id = ?",
            ("thread_utc",),
        ).fetchone()[0]

    assert stored == "2026-07-10T08:00:00+00:00"


def test_repositories_translate_sqlite_integrity_errors(tmp_path: Path) -> None:
    repositories = SQLiteConversationRepositories(tmp_path / "application.db")

    with pytest.raises(ConversationConflict) as raised:
        repositories.entries.append(_entry("missing_parent", sequence=1))

    assert not isinstance(raised.value.__cause__, sqlite3.IntegrityError)


def test_thread_pages_are_bounded_stable_and_workspace_scoped(tmp_path: Path) -> None:
    repositories = SQLiteConversationRepositories(tmp_path / "application.db")
    timestamp = datetime(2026, 7, 11, 8, 0, tzinfo=UTC)
    with repositories.transaction() as connection:
        for index in range(205):
            repositories.threads.create(
                Thread(
                    id=f"thread_{index:03d}",
                    workspace_key="workspace_1",
                    title=f"Thread {index}",
                    created_at=timestamp,
                    updated_at=timestamp,
                ),
                connection=connection,
            )
        repositories.threads.create(
            Thread(
                id="thread_foreign",
                workspace_key="workspace_2",
                title="Foreign",
                created_at=timestamp,
                updated_at=timestamp,
            ),
            connection=connection,
        )

    first = repositories.list_threads_page("workspace_1", cursor=None, limit=200)
    second = repositories.list_threads_page(
        "workspace_1",
        cursor=(first.threads[-1].updated_at, first.threads[-1].id),
        limit=200,
    )

    assert len(first.threads) == 200
    assert first.has_more is True
    assert [thread.id for thread in first.threads[:3]] == [
        "thread_000",
        "thread_001",
        "thread_002",
    ]
    assert [thread.id for thread in second.threads] == [
        "thread_200",
        "thread_201",
        "thread_202",
        "thread_203",
        "thread_204",
    ]
    assert second.has_more is False
    assert not {thread.id for thread in first.threads + second.threads} & {
        "thread_foreign"
    }


def test_thread_entry_pages_read_tail_and_traverse_without_overlap(
    tmp_path: Path,
) -> None:
    repositories = SQLiteConversationRepositories(tmp_path / "application.db")
    with repositories.transaction() as connection:
        repositories.threads.create(_thread(), connection=connection)
        for sequence in range(1, 506):
            repositories.entries.append(
                _entry(f"entry_{sequence}", sequence=sequence),
                connection=connection,
            )

    tail = repositories.read_thread_page("thread_1", before_sequence=None, limit=500)
    older = repositories.read_thread_page(
        "thread_1",
        before_sequence=tail.next_before_sequence,
        limit=500,
    )

    assert [entry.sequence for entry in tail.view.entries] == list(range(6, 506))
    assert tail.has_more is True
    assert tail.next_before_sequence == 6
    assert [entry.sequence for entry in older.view.entries] == [1, 2, 3, 4, 5]
    assert older.has_more is False
    assert older.next_before_sequence is None


def test_thread_entry_page_contains_only_associated_turns_and_tools(
    tmp_path: Path,
) -> None:
    repositories = SQLiteConversationRepositories(tmp_path / "application.db")
    now = _now()
    direct_entry = _entry(
        "entry_2",
        sequence=2,
        kind=ThreadEntryKind.DIRECT_COMMAND,
    ).model_copy(update={"metadata": {"operation_id": "operation_direct"}})
    with repositories.transaction() as connection:
        repositories.threads.create(_thread(), connection=connection)
        repositories.entries.append(
            _entry("entry_1", sequence=1), connection=connection
        )
        repositories.entries.append(direct_entry, connection=connection)
        repositories.entries.append(
            _entry("entry_3", sequence=3), connection=connection
        )
        repositories.entries.append(
            _entry(
                "entry_4",
                sequence=4,
                kind=ThreadEntryKind.ASSISTANT_MESSAGE,
            ),
            connection=connection,
        )
        repositories.turns.create(
            Turn(
                id="turn_1",
                thread_id="thread_1",
                checkpoint_key="turn_1",
                status=TurnStatus.COMPLETED,
                provider="deepseek",
                model="deepseek/deepseek-v4-flash",
                budgets=BudgetConfig(),
                user_entry_id="entry_3",
                assistant_entry_id="entry_4",
                completed_at=now,
                created_at=now,
                updated_at=now,
            ),
            connection=connection,
        )
        repositories.tool_activities.append(
            ToolActivity(
                id="activity_agent",
                thread_id="thread_1",
                turn_id="turn_1",
                operation_id="operation_agent",
                call_id="call_agent",
                sequence=1,
                origin=ToolActivityOrigin.AGENT,
                tool_name="read_file",
                outcome=ToolActivityOutcome.SUCCESS,
                duration_ms=1,
                created_at=now,
            ),
            connection=connection,
        )
        repositories.tool_activities.append(
            ToolActivity(
                id="activity_direct",
                thread_id="thread_1",
                turn_id=None,
                operation_id="operation_direct",
                call_id="call_direct",
                sequence=2,
                origin=ToolActivityOrigin.DIRECT,
                tool_name="execute",
                outcome=ToolActivityOutcome.SUCCESS,
                duration_ms=1,
                created_at=now,
            ),
            connection=connection,
        )

    recent = repositories.read_thread_page("thread_1", before_sequence=None, limit=2)
    older = repositories.read_thread_page("thread_1", before_sequence=3, limit=2)

    assert [turn.id for turn in recent.view.turns] == ["turn_1"]
    assert [item.id for item in recent.view.tool_activities] == ["activity_agent"]
    assert older.view.turns == ()
    assert [item.id for item in older.view.tool_activities] == ["activity_direct"]
