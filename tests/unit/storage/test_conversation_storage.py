from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

import awesome_agent.storage.database as database
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


def test_migration_three_creates_only_product_conversation_tables(
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

    assert version == APPLICATION_SCHEMA_VERSION == 3
    assert {
        "trusted_workspaces",
        "change_sets",
        "pending_mutations",
        "threads",
        "thread_entries",
        "turns",
        "thread_summaries",
        "tool_activities",
    } <= tables
    assert {
        "idx_turns_one_in_progress",
        "idx_thread_entries_sequence",
        "idx_tool_activities_operation_call",
    } <= indexes
    assert not {"runs", "jobs", "leases", "attempts", "event_store"} & tables


def test_migration_three_rolls_back_the_entire_script_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "application.db"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 2")
    monkeypatch.setitem(
        database._MIGRATIONS,
        3,
        "CREATE TABLE partial_migration (id TEXT); INVALID SQL;",
    )

    with pytest.raises(sqlite3.OperationalError):
        initialize_application_database(path)

    with sqlite3.connect(path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        partial = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("partial_migration",),
        ).fetchone()

    assert version == 2
    assert partial is None


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
