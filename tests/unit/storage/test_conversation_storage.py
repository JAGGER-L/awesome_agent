from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

import awesome_agent.storage.conversations as conversation_storage_module
from awesome_agent.config import BudgetConfig
from awesome_agent.conversation import (
    ConversationConflict,
    Thread,
    ThreadEntry,
    ThreadEntryKind,
    ThreadLineage,
    ThreadSearchLimitExceeded,
    ThreadSummary,
    ThreadTitleSource,
    ToolActivity,
    ToolActivityOrigin,
    ToolActivityOutcome,
    Turn,
    TurnBusy,
    TurnStatus,
    UsageSummary,
)
from awesome_agent.core.tools import ToolActivityDraft, ToolExecutionOrigin
from awesome_agent.storage.application_sqlite import ApplicationSQLite
from awesome_agent.storage.conversations import (
    SQLiteConversationRepositories,
    SQLiteThreadEntryRepository,
    SQLiteThreadRepository,
    SQLiteThreadSummaryRepository,
    SQLiteToolActivityRepository,
    SQLiteTurnRepository,
)
from awesome_agent.storage.database import (
    APPLICATION_SCHEMA_VERSION,
)

pytestmark = pytest.mark.asyncio

_THREADS = SQLiteThreadRepository()
_ENTRIES = SQLiteThreadEntryRepository()
_TURNS = SQLiteTurnRepository()
_SUMMARIES = SQLiteThreadSummaryRepository()
_ACTIVITIES = SQLiteToolActivityRepository()


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
        metadata=(
            {} if kind is ThreadEntryKind.ASSISTANT_MESSAGE else {"z": 1, "a": True}
        ),
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


@pytest.fixture
async def application_database(tmp_path: Path) -> AsyncIterator[ApplicationSQLite]:
    database = ApplicationSQLite(tmp_path / "application.db")
    await database.initialize()
    try:
        yield database
    finally:
        await database.aclose()


async def _wait_for(event: threading.Event) -> None:
    for _ in range(1_000):
        if event.is_set():
            return
        await asyncio.sleep(0.001)
    pytest.fail("worker operation did not start")


async def _create_thread(database: ApplicationSQLite, thread: Thread) -> Thread:
    return await database.write(
        lambda connection: _THREADS.create(thread, connection=connection)
    )


async def _append_entry(
    database: ApplicationSQLite,
    entry: ThreadEntry,
) -> ThreadEntry:
    return await database.write(
        lambda connection: _ENTRIES.append(entry, connection=connection)
    )


async def _create_turn(database: ApplicationSQLite, turn: Turn) -> Turn:
    return await database.write(
        lambda connection: _TURNS.create(turn, connection=connection)
    )


async def _get_thread(
    database: ApplicationSQLite,
    thread_id: str,
) -> Thread | None:
    return await database.read(
        lambda connection: _THREADS.get(thread_id, connection=connection)
    )


async def _list_entries(
    database: ApplicationSQLite,
    thread_id: str,
) -> Sequence[ThreadEntry]:
    return await database.read(
        lambda connection: _ENTRIES.list(thread_id, connection=connection)
    )


async def _get_turn(database: ApplicationSQLite, turn_id: str) -> Turn | None:
    return await database.read(
        lambda connection: _TURNS.get(turn_id, connection=connection)
    )


async def _append_activity(
    database: ApplicationSQLite,
    activity: ToolActivity,
) -> ToolActivity:
    return await database.write(
        lambda connection: _ACTIVITIES.append(activity, connection=connection)
    )


async def test_application_schema_creates_only_product_state_tables(
    application_database: ApplicationSQLite,
) -> None:
    def inspect(
        connection: sqlite3.Connection,
    ) -> tuple[int, set[str], set[str], list[str]]:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        indexes = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        thread_index_columns = [
            str(row[2])
            for row in connection.execute(
                "PRAGMA index_info(idx_threads_workspace_updated)"
            ).fetchall()
        ]
        return int(version), tables, indexes, thread_index_columns

    version, tables, indexes, thread_index_columns = await application_database.read(
        inspect
    )

    assert version == APPLICATION_SCHEMA_VERSION == 8
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
    assert thread_index_columns == ["workspace_key", "updated_at", "thread_id"]


async def test_current_schema_accepts_new_identity_and_rejects_duplicate(
    application_database: ApplicationSQLite,
) -> None:
    await _create_thread(application_database, _thread())

    await _append_entry(application_database, _entry("entry_new", sequence=1))

    with pytest.raises(ConversationConflict):
        await _append_entry(
            application_database,
            _entry(
                "entry_duplicate",
                sequence=2,
            ).model_copy(update={"client_message_id": "client_entry_new"}),
        )


async def test_thread_lineage_round_trips_and_survives_updates(
    application_database: ApplicationSQLite,
) -> None:
    repositories = SQLiteConversationRepositories(application_database)
    lineage = ThreadLineage(
        kind="fork",
        source_thread_id="thread_source",
        source_turn_id="turn_source",
    )
    thread = _thread().model_copy(update={"lineage": lineage})

    await repositories.create_thread(thread)
    updated = await repositories.set_thread_model(
        thread.id,
        "deepseek/updated",
        updated_at=thread.updated_at + timedelta(seconds=1),
    )
    reopened = await repositories.read_thread(thread.id)

    assert updated.lineage == lineage
    assert reopened.thread.lineage == lineage


async def test_repositories_persist_and_reopen_ordered_thread_state(
    application_database: ApplicationSQLite,
) -> None:
    repositories = SQLiteConversationRepositories(application_database)
    expected_thread = _thread()
    await _create_thread(application_database, expected_thread)
    await _append_entry(application_database, _entry("entry_2", sequence=2))
    await _append_entry(application_database, _entry("entry_1", sequence=1))
    await _create_turn(application_database, _turn())

    reopened = SQLiteConversationRepositories(application_database)
    reopened_view = await reopened.read_thread("thread_1")
    original_view = await repositories.read_thread("thread_1")
    reopened_thread = reopened_view.thread

    assert reopened_thread == original_view.thread == expected_thread
    assert reopened_thread.title_source is ThreadTitleSource.AUTOMATIC
    assert [entry.id for entry in reopened_view.entries] == [
        "entry_1",
        "entry_2",
    ]
    assert reopened_view.turns == original_view.turns


async def test_entry_sequence_is_unique_per_thread(
    application_database: ApplicationSQLite,
) -> None:
    await _create_thread(application_database, _thread())
    await _append_entry(application_database, _entry("entry_1", sequence=1))

    with pytest.raises(ConversationConflict):
        await _append_entry(application_database, _entry("entry_2", sequence=1))


async def test_only_one_in_progress_turn_exists_per_thread(
    application_database: ApplicationSQLite,
) -> None:
    await _create_thread(application_database, _thread())
    await _append_entry(application_database, _entry("entry_1", sequence=1))
    await _append_entry(application_database, _entry("entry_2", sequence=2))
    await _create_turn(application_database, _turn())

    with pytest.raises(TurnBusy):
        await _create_turn(
            application_database,
            _turn("turn_2", user_entry_id="entry_2"),
        )

    await _create_thread(application_database, _thread("thread_2"))
    await _append_entry(
        application_database,
        _entry("entry_other", thread_id="thread_2", sequence=1),
    )
    other = await _create_turn(
        application_database,
        _turn(
            "turn_other",
            thread_id="thread_2",
            user_entry_id="entry_other",
        ),
    )
    assert other.thread_id == "thread_2"


async def test_foreign_keys_reject_cross_thread_turn_entry(
    application_database: ApplicationSQLite,
) -> None:
    await _create_thread(application_database, _thread())
    await _create_thread(application_database, _thread("thread_2"))
    await _append_entry(
        application_database,
        _entry("entry_other", thread_id="thread_2", sequence=1),
    )

    with pytest.raises(ConversationConflict):
        await _create_turn(application_database, _turn(user_entry_id="entry_other"))


async def test_repository_transaction_rolls_back_multirow_failure(
    application_database: ApplicationSQLite,
) -> None:
    def fail_transaction(connection: sqlite3.Connection) -> None:
        _THREADS.create(_thread(), connection=connection)
        _ENTRIES.append(
            _entry("entry_1", sequence=1),
            connection=connection,
        )
        raise RuntimeError("rollback")

    with pytest.raises(RuntimeError, match="rollback"):
        await application_database.write(fail_transaction)

    assert await _get_thread(application_database, "thread_1") is None


@pytest.mark.parametrize("reader", ("full", "page"))
async def test_thread_snapshot_reads_queue_without_blocking_the_event_loop(
    application_database: ApplicationSQLite,
    reader: str,
) -> None:
    snapshot = SQLiteConversationRepositories(application_database)
    await _create_thread(application_database, _thread())
    writer_started = threading.Event()
    release_writer = threading.Event()

    def reserve_writer(_connection: sqlite3.Connection) -> None:
        writer_started.set()
        release_writer.wait()

    writer = asyncio.create_task(application_database.write(reserve_writer))
    await _wait_for(writer_started)

    async def read_snapshot() -> object:
        if reader == "full":
            return await snapshot.read_thread("thread_1")
        return await snapshot.read_thread_page(
            "thread_1",
            before_sequence=None,
            limit=10,
        )

    read = asyncio.create_task(read_snapshot())
    await asyncio.sleep(0.01)

    assert read.done() is False

    release_writer.set()
    await writer
    observed = await read
    assert observed is not None


@pytest.mark.parametrize("winner", ("manifest", "cancel"))
async def test_turn_manifest_and_terminal_writes_are_monotonic_in_worker_order(
    application_database: ApplicationSQLite,
    winner: str,
) -> None:
    second = SQLiteConversationRepositories(application_database)
    await _create_thread(application_database, _thread())
    await _append_entry(application_database, _entry("entry_1", sequence=1))
    original = await _create_turn(application_database, _turn())
    updated_manifest = ({"kind": "current_input", "order": 2},)
    manifest_update = original.model_copy(
        update={
            "context_manifest": updated_manifest,
            "updated_at": original.updated_at + timedelta(seconds=1),
        }
    )
    cancelled = Turn.model_validate(
        original.model_copy(
            update={
                "status": TurnStatus.CANCELLED,
                "termination_reason": "cancelled",
                "updated_at": original.updated_at + timedelta(seconds=2),
                "completed_at": original.updated_at + timedelta(seconds=2),
            }
        ).model_dump()
    )
    winner_started = threading.Event()
    release_winner = threading.Event()

    def commit_winner(connection: sqlite3.Connection) -> None:
        winner_started.set()
        assert release_winner.wait(timeout=5)
        _TURNS.update(
            manifest_update if winner == "manifest" else cancelled,
            connection=connection,
        )

    winner_task = asyncio.create_task(application_database.write(commit_winner))
    await _wait_for(winner_started)
    if winner == "manifest":
        loser_task = asyncio.create_task(second.update_terminal_turn(cancelled))
    else:
        loser_task = asyncio.create_task(
            second.update_in_progress_turn(
                manifest_update,
                expected_context_manifest=original.context_manifest,
            )
        )
    await asyncio.sleep(0.01)
    assert loser_task.done() is False
    release_winner.set()
    await winner_task
    with pytest.raises(ConversationConflict):
        await loser_task

    stored = await _get_turn(application_database, original.id)
    assert stored is not None
    if winner == "manifest":
        assert stored.status is TurnStatus.IN_PROGRESS
        assert stored.context_manifest == updated_manifest
    else:
        assert stored.status is TurnStatus.CANCELLED
        assert stored.context_manifest == original.context_manifest


async def test_begin_turn_rolls_back_title_and_entry_when_turn_write_fails(
    application_database: ApplicationSQLite,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repositories = SQLiteConversationRepositories(application_database)
    thread = _thread().model_copy(
        update={
            "title": "New conversation",
            "title_source": ThreadTitleSource.AUTOMATIC,
        }
    )
    await repositories.create_thread(thread)
    updated = thread.model_copy(
        update={"title": "calculate cube", "updated_at": _now()}
    )

    def fail_turn_write(
        _repository: SQLiteTurnRepository,
        *args: object,
        **kwargs: object,
    ) -> None:
        raise RuntimeError("turn write failed")

    monkeypatch.setattr(SQLiteTurnRepository, "create", fail_turn_write)

    with pytest.raises(RuntimeError, match="turn write failed"):
        await repositories.begin_turn(
            _entry("entry_1", sequence=1),
            _turn(),
            automatic_title=updated.title,
            updated_at=updated.updated_at,
        )

    view = await repositories.read_thread(thread.id)
    assert view.thread.title == "New conversation"
    assert view.entries == ()
    assert view.turns == ()


async def test_begin_turn_preserves_a_manual_title_selected_after_stale_read(
    application_database: ApplicationSQLite,
) -> None:
    repositories = SQLiteConversationRepositories(application_database)
    thread = _thread().model_copy(
        update={
            "title": "New conversation",
            "title_source": ThreadTitleSource.AUTOMATIC,
        }
    )
    await repositories.create_thread(thread)
    await repositories.rename_thread(
        thread.id,
        "Manual title",
        updated_at=_now(),
    )

    await repositories.begin_turn(
        _entry("entry_1", sequence=1),
        _turn(),
        automatic_title="stale automatic suggestion",
        updated_at=_now(),
    )

    stored = (await repositories.read_thread(thread.id)).thread
    assert stored.title == "Manual title"
    assert stored.title_source is ThreadTitleSource.MANUAL


async def test_begin_turn_only_applies_automatic_title_without_prior_entries(
    application_database: ApplicationSQLite,
) -> None:
    repositories = SQLiteConversationRepositories(application_database)
    thread = _thread().model_copy(
        update={
            "title": "Existing automatic title",
            "title_source": ThreadTitleSource.AUTOMATIC,
        }
    )
    await repositories.create_thread(thread)
    await _append_entry(application_database, _entry("entry_1", sequence=1))

    await repositories.begin_turn(
        _entry("entry_2", sequence=2),
        _turn(user_entry_id="entry_2"),
        automatic_title="stale first-turn suggestion",
        updated_at=_now(),
    )

    stored = (await repositories.read_thread(thread.id)).thread
    assert stored.title == "Existing automatic title"
    assert stored.title_source is ThreadTitleSource.AUTOMATIC


@pytest.mark.parametrize(
    ("first_offset", "second_offset"),
    ((20, 10), (10, 20)),
    ids=("newer-commits-first", "older-commits-first"),
)
@pytest.mark.parametrize(
    ("mutation", "first_value", "second_value", "field"),
    (
        ("model", "model-a", "model-b", "current_model"),
        ("rename", "Title A", "Title B", "title"),
        ("thinking", False, True, "thinking_enabled"),
        ("skill", "off", "debug", "skill_mode"),
    ),
)
async def test_thread_field_commits_never_move_updated_at_backwards(
    application_database: ApplicationSQLite,
    first_offset: int,
    second_offset: int,
    mutation: str,
    first_value: str | bool,
    second_value: str | bool,
    field: str,
) -> None:
    first = SQLiteConversationRepositories(application_database)
    second = SQLiteConversationRepositories(application_database)
    base = datetime(2026, 7, 26, 1, 0, tzinfo=UTC)
    thread = _thread().model_copy(update={"created_at": base, "updated_at": base})
    await first.create_thread(thread)
    first_time = base + timedelta(seconds=first_offset)
    second_time = base + timedelta(seconds=second_offset)

    async def mutate(
        repositories: SQLiteConversationRepositories,
        value: str | bool,
        updated_at: datetime,
    ) -> Thread:
        if mutation == "model":
            assert isinstance(value, str)
            return await repositories.set_thread_model(
                thread.id,
                value,
                updated_at=updated_at,
            )
        if mutation == "rename":
            assert isinstance(value, str)
            return await repositories.rename_thread(
                thread.id,
                value,
                updated_at=updated_at,
            )
        if mutation == "thinking":
            assert isinstance(value, bool)
            return await repositories.set_thread_thinking(
                thread.id,
                value,
                updated_at=updated_at,
            )
        assert mutation == "skill"
        assert isinstance(value, str)
        return await repositories.set_thread_skill_mode(
            thread.id,
            value,
            updated_at=updated_at,
        )

    first_result = await mutate(first, first_value, first_time)
    second_result = await mutate(second, second_value, second_time)

    expected_time = max(first_time, second_time)
    stored = (await first.read_thread(thread.id)).thread
    assert first_result.updated_at == first_time
    assert second_result.updated_at == expected_time
    assert stored.updated_at == expected_time
    assert getattr(stored, field) == second_value
    if mutation == "rename":
        assert stored.title_source is ThreadTitleSource.MANUAL


@pytest.mark.parametrize(
    ("field_offset", "turn_offset"),
    ((20, 10), (10, 20)),
    ids=("newer-field-commits-first", "older-field-commits-first"),
)
async def test_begin_turn_never_moves_thread_updated_at_backwards(
    application_database: ApplicationSQLite,
    field_offset: int,
    turn_offset: int,
) -> None:
    field_writer = SQLiteConversationRepositories(application_database)
    turn_writer = SQLiteConversationRepositories(application_database)
    base = datetime(2026, 7, 26, 2, 0, tzinfo=UTC)
    thread = _thread().model_copy(
        update={
            "title": "New conversation",
            "title_source": ThreadTitleSource.AUTOMATIC,
            "created_at": base,
            "updated_at": base,
        }
    )
    await field_writer.create_thread(thread)
    field_time = base + timedelta(seconds=field_offset)
    turn_time = base + timedelta(seconds=turn_offset)
    await field_writer.set_thread_model(
        thread.id,
        "deepseek/newer-selection",
        updated_at=field_time,
    )

    await turn_writer.begin_turn(
        _entry("entry_1", sequence=1),
        _turn(),
        automatic_title="First accepted request",
        updated_at=turn_time,
    )

    view = await field_writer.read_thread(thread.id)
    assert view.thread.updated_at == max(field_time, turn_time)
    assert view.thread.current_model == "deepseek/newer-selection"
    assert view.thread.title == "First accepted request"
    assert view.thread.title_source is ThreadTitleSource.AUTOMATIC
    assert len(view.entries) == 1
    assert len(view.turns) == 1


async def test_summary_upsert_and_tool_activity_idempotency(
    application_database: ApplicationSQLite,
) -> None:
    await _create_thread(application_database, _thread())
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

    assert (
        await application_database.write(
            lambda connection: _SUMMARIES.upsert(summary, connection=connection)
        )
        == summary
    )
    assert (
        await application_database.read(
            lambda connection: _SUMMARIES.get("thread_1", connection=connection)
        )
        == summary
    )
    assert await _append_activity(application_database, activity) == activity
    assert await _append_activity(application_database, activity) == activity

    changed = activity.model_copy(update={"result_summary": "different"})
    with pytest.raises(ConversationConflict):
        await _append_activity(application_database, changed)


async def test_tool_activity_writer_finalizes_one_terminal_record(
    application_database: ApplicationSQLite,
) -> None:
    repositories = SQLiteConversationRepositories(application_database)
    await _create_thread(application_database, _thread())
    await _append_entry(application_database, _entry("entry_1", sequence=1))
    await _create_turn(application_database, _turn())
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

    await repositories.finalize(draft)
    await repositories.finalize(draft.model_copy(update={"duration_ms": 99}))

    activities = (await repositories.read_thread("thread_1")).tool_activities
    assert len(activities) == 1
    assert activities[0].duration_ms == 12


async def test_summary_compare_and_swap_rejects_stale_coverage(
    application_database: ApplicationSQLite,
) -> None:
    repositories = SQLiteConversationRepositories(application_database)
    await repositories.create_thread(_thread())
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

    assert await repositories.compare_and_swap_summary(first, expected=None) == first
    assert await repositories.compare_and_swap_summary(second, expected=first) == second
    with pytest.raises(ConversationConflict):
        await repositories.compare_and_swap_summary(first, expected=None)


async def test_json_columns_use_canonical_compact_encoding(
    application_database: ApplicationSQLite,
) -> None:
    await _create_thread(application_database, _thread())
    await _append_entry(application_database, _entry("entry_1", sequence=1))
    await _create_turn(application_database, _turn())

    def read_json(connection: sqlite3.Connection) -> tuple[str, str, str]:
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
        return str(metadata), str(usage), str(budgets)

    metadata, usage, budgets = await application_database.read(read_json)

    assert metadata == '{"a":true,"z":1}'
    assert json.loads(usage)["model_calls"] == 1
    assert json.loads(budgets)["model_calls"] == 12
    assert ": " not in usage
    assert ": " not in budgets


async def test_timestamps_are_normalized_to_utc_iso_8601(
    application_database: ApplicationSQLite,
) -> None:
    offset_time = datetime(2026, 7, 10, 16, 0, tzinfo=timezone(timedelta(hours=8)))
    await _create_thread(
        application_database,
        Thread(
            id="thread_utc",
            workspace_key="workspace_1",
            title="UTC",
            created_at=offset_time,
            updated_at=offset_time,
        ),
    )

    stored = await application_database.read(
        lambda connection: str(
            connection.execute(
                "SELECT created_at FROM threads WHERE thread_id = ?",
                ("thread_utc",),
            ).fetchone()[0]
        )
    )

    assert stored == "2026-07-10T08:00:00+00:00"


async def test_repositories_translate_sqlite_integrity_errors(
    application_database: ApplicationSQLite,
) -> None:
    with pytest.raises(ConversationConflict) as raised:
        await _append_entry(
            application_database,
            _entry("missing_parent", sequence=1),
        )

    assert not isinstance(raised.value.__cause__, sqlite3.IntegrityError)


async def test_thread_pages_are_bounded_stable_and_workspace_scoped(
    application_database: ApplicationSQLite,
) -> None:
    repositories = SQLiteConversationRepositories(application_database)
    timestamp = datetime(2026, 7, 11, 8, 0, tzinfo=UTC)

    def seed(connection: sqlite3.Connection) -> None:
        for index in range(205):
            _THREADS.create(
                Thread(
                    id=f"thread_{index:03d}",
                    workspace_key="workspace_1",
                    title=f"Thread {index}",
                    created_at=timestamp,
                    updated_at=timestamp,
                ),
                connection=connection,
            )
        _THREADS.create(
            Thread(
                id="thread_foreign",
                workspace_key="workspace_2",
                title="Foreign",
                created_at=timestamp,
                updated_at=timestamp,
            ),
            connection=connection,
        )

    await application_database.write(seed)
    first = await repositories.list_threads_page("workspace_1", cursor=None, limit=200)
    second = await repositories.list_threads_page(
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


async def test_thread_search_is_literal_scoped_and_keyset_paginated(
    application_database: ApplicationSQLite,
) -> None:
    repositories = SQLiteConversationRepositories(application_database)
    timestamp = datetime(2026, 7, 11, 8, 0, tzinfo=UTC)

    def seed(connection: sqlite3.Connection) -> None:
        threads = (
            Thread(
                id="thread_title",
                workspace_key="workspace_1",
                title="Needle in title",
                created_at=timestamp,
                updated_at=timestamp + timedelta(seconds=3),
            ),
            Thread(
                id="thread_entry",
                workspace_key="workspace_1",
                title="Entry match",
                created_at=timestamp,
                updated_at=timestamp + timedelta(seconds=2),
            ),
            Thread(
                id="thread_hidden",
                workspace_key="workspace_1",
                title="Hidden fields only",
                created_at=timestamp,
                updated_at=timestamp + timedelta(seconds=1),
            ),
            Thread(
                id="thread_percent",
                workspace_key="workspace_1",
                title="100% complete",
                created_at=timestamp,
                updated_at=timestamp,
            ),
            Thread(
                id="thread_foreign",
                workspace_key="workspace_2",
                title="Needle in another workspace",
                created_at=timestamp,
                updated_at=timestamp + timedelta(seconds=4),
            ),
        )
        for thread in threads:
            _THREADS.create(thread, connection=connection)
        _ENTRIES.append(
            _entry(
                "entry_direct",
                thread_id="thread_entry",
                sequence=1,
                kind=ThreadEntryKind.DIRECT_COMMAND,
            ).model_copy(update={"content": "echo NEEDLE"}),
            connection=connection,
        )
        _ENTRIES.append(
            _entry(
                "entry_hidden",
                thread_id="thread_hidden",
                sequence=1,
                kind=ThreadEntryKind.DIRECT_COMMAND,
            ).model_copy(
                update={
                    "content": "irrelevant",
                    "metadata": {"hidden": "needle"},
                }
            ),
            connection=connection,
        )
        _SUMMARIES.upsert(
            ThreadSummary(
                thread_id="thread_hidden",
                content="needle appears only in the summary",
                content_hash="a" * 64,
                covered_entry_sequence=1,
                covered_turn_count=0,
                estimated_tokens=1,
                provider="deepseek",
                model="deepseek/deepseek-v4-flash",
                updated_at=timestamp,
            ),
            connection=connection,
        )

    await application_database.write(seed)
    first = await repositories.search_threads_page(
        "workspace_1",
        query="needle",
        cursor=None,
        limit=1,
    )
    second = await repositories.search_threads_page(
        "workspace_1",
        query="needle",
        cursor=(first.threads[-1].updated_at, first.threads[-1].id),
        limit=1,
    )
    literal_percent = await repositories.search_threads_page(
        "workspace_1",
        query="%",
        cursor=None,
        limit=50,
    )

    assert [thread.id for thread in first.threads] == ["thread_title"]
    assert first.has_more is True
    assert [thread.id for thread in second.threads] == ["thread_entry"]
    assert second.has_more is False
    assert [thread.id for thread in literal_percent.threads] == ["thread_percent"]
    assert await repositories.thread_matches_search(
        "workspace_1",
        query="needle",
        thread_id="thread_entry",
    )
    assert not await repositories.thread_matches_search(
        "workspace_1",
        query="needle",
        thread_id="thread_hidden",
    )
    assert not await repositories.thread_matches_search(
        "workspace_1",
        query="needle",
        thread_id="thread_foreign",
    )


@pytest.mark.parametrize("operation", ["page", "match"])
async def test_thread_search_opcode_limit_is_typed_and_handler_is_reset(
    application_database: ApplicationSQLite,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    repositories = SQLiteConversationRepositories(application_database)
    await _create_thread(application_database, _thread())
    monkeypatch.setattr(
        conversation_storage_module,
        "_THREAD_SEARCH_OPCODE_BUDGET",
        1,
    )

    with pytest.raises(ThreadSearchLimitExceeded):
        if operation == "page":
            await repositories.search_threads_page(
                "workspace_1",
                query="missing",
                cursor=None,
                limit=50,
            )
        else:
            await repositories.thread_matches_search(
                "workspace_1",
                query="missing",
                thread_id="thread_1",
            )

    # A failed bounded scan must not leave its progress handler on the owner.
    assert [thread.id for thread in await repositories.list_threads("workspace_1")] == [
        "thread_1"
    ]


async def test_thread_entry_pages_read_tail_and_traverse_without_overlap(
    application_database: ApplicationSQLite,
) -> None:
    repositories = SQLiteConversationRepositories(application_database)

    def seed(connection: sqlite3.Connection) -> None:
        _THREADS.create(_thread(), connection=connection)
        for sequence in range(1, 506):
            _ENTRIES.append(
                _entry(f"entry_{sequence}", sequence=sequence),
                connection=connection,
            )

    await application_database.write(seed)
    tail = await repositories.read_thread_page(
        "thread_1", before_sequence=None, limit=500
    )
    older = await repositories.read_thread_page(
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


async def test_thread_entry_page_contains_only_associated_turns_and_tools(
    application_database: ApplicationSQLite,
) -> None:
    repositories = SQLiteConversationRepositories(application_database)
    now = _now()
    direct_entry = _entry(
        "entry_2",
        sequence=2,
        kind=ThreadEntryKind.DIRECT_COMMAND,
    ).model_copy(update={"metadata": {"operation_id": "operation_direct"}})

    def seed(connection: sqlite3.Connection) -> None:
        _THREADS.create(_thread(), connection=connection)
        _ENTRIES.append(_entry("entry_1", sequence=1), connection=connection)
        _ENTRIES.append(direct_entry, connection=connection)
        _ENTRIES.append(_entry("entry_3", sequence=3), connection=connection)
        _ENTRIES.append(
            _entry(
                "entry_4",
                sequence=4,
                kind=ThreadEntryKind.ASSISTANT_MESSAGE,
            ),
            connection=connection,
        )
        _TURNS.create(
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
        _ACTIVITIES.append(
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
        _ACTIVITIES.append(
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

    await application_database.write(seed)
    recent = await repositories.read_thread_page(
        "thread_1", before_sequence=None, limit=2
    )
    older = await repositories.read_thread_page("thread_1", before_sequence=3, limit=2)

    assert [turn.id for turn in recent.view.turns] == ["turn_1"]
    assert [item.id for item in recent.view.tool_activities] == ["activity_agent"]
    assert older.view.turns == ()
    assert [item.id for item in older.view.tool_activities] == ["activity_direct"]
