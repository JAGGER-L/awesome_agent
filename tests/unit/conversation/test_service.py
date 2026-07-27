from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import JsonValue

from awesome_agent.config import BudgetConfig, TurnConfig
from awesome_agent.conversation import (
    ConversationConflict,
    ConversationService,
    InvalidTurnTransition,
    ThreadEntryKind,
    ThreadTitleSource,
    TurnBusy,
    TurnStatus,
    UsageSummary,
)
from awesome_agent.core.citations import Citation
from awesome_agent.storage.application_sqlite import ApplicationSQLite
from awesome_agent.storage.conversations import SQLiteConversationRepositories

pytestmark = pytest.mark.asyncio


class DeterministicIds:
    def __init__(self) -> None:
        self._next = 0

    def __call__(self, prefix: str) -> str:
        self._next += 1
        return f"{prefix}_{self._next}"


class DeterministicClock:
    def __init__(self) -> None:
        self._now = datetime(2026, 7, 10, tzinfo=UTC)

    def __call__(self) -> datetime:
        result = self._now
        self._now += timedelta(seconds=1)
        return result


def _service(database: ApplicationSQLite) -> ConversationService:
    return ConversationService(
        store=SQLiteConversationRepositories(database),
        id_factory=DeterministicIds(),
        clock=DeterministicClock(),
    )


@pytest.fixture
async def application_database(tmp_path: Path) -> AsyncIterator[ApplicationSQLite]:
    database = ApplicationSQLite(tmp_path / "application.db")
    await database.initialize()
    try:
        yield database
    finally:
        await database.aclose()


def _turn_config() -> TurnConfig:
    return TurnConfig(
        provider="deepseek",
        model="deepseek/deepseek-v4-flash",
        thinking_enabled=True,
        skill_mode="debug",
        budgets=BudgetConfig(model_calls=12, tool_calls=24),
    )


async def test_create_list_and_read_threads_by_workspace(
    application_database: ApplicationSQLite,
) -> None:
    service = _service(application_database)
    first = await service.create_thread("workspace_1", "First")
    second = await service.create_thread("workspace_1")
    await service.create_thread("workspace_2", "Other")

    listed = await service.list_threads("workspace_1")
    view = await service.read_thread(first.id)

    assert {thread.id for thread in listed} == {first.id, second.id}
    assert second.title == "New conversation"
    assert second.title_source is ThreadTitleSource.AUTOMATIC
    assert second.thinking_enabled is True
    assert view.thread == first
    assert view.entries == ()
    assert view.turns == ()


async def test_begin_turn_atomically_appends_user_and_freezes_config(
    application_database: ApplicationSQLite,
) -> None:
    service = _service(application_database)
    thread = await service.create_thread("workspace_1", "Thread")
    config = _turn_config()

    turn = await service.begin_turn(
        thread.id,
        "Inspect repository",
        config,
        client_message_id="client_1",
    )
    view = await service.read_thread(thread.id)

    assert turn.checkpoint_key == turn.id
    assert turn.status is TurnStatus.IN_PROGRESS
    assert turn.provider == "deepseek"
    assert turn.model == "deepseek/deepseek-v4-flash"
    assert turn.thinking_enabled is True
    assert turn.skill_mode == "debug"
    assert turn.budgets == config.budgets
    assert view.turns[0].budgets == config.budgets
    assert view.entries[0].kind is ThreadEntryKind.USER_MESSAGE
    assert view.entries[0].content == "Inspect repository"
    assert view.entries[0].client_message_id == "client_1"
    assert view.turns == (turn,)


async def test_in_progress_turn_persists_context_snapshot_descriptor(
    application_database: ApplicationSQLite,
) -> None:
    service = _service(application_database)
    thread = await service.create_thread("workspace_1", "Thread")
    turn = await service.begin_turn(
        thread.id,
        "Inspect repository",
        _turn_config(),
        client_message_id="client_context",
    )
    manifest: tuple[dict[str, JsonValue], ...] = (
        {
            "kind": "product_instructions",
            "source_id": "product",
            "order": 0,
        },
        {
            "kind": "current_input",
            "source_id": turn.user_entry_id,
            "order": 1,
        },
    )

    recorded = await service.store_context_manifest(turn.id, manifest)
    repeated = await service.store_context_manifest(turn.id, manifest)

    assert recorded.context_manifest == manifest
    assert repeated == recorded
    assert (await service.read_thread(thread.id)).turns[0].context_manifest == manifest

    await service.cancel_turn(turn.id, context_manifest=manifest)
    with pytest.raises(ConversationConflict):
        await service.store_context_manifest(turn.id, manifest)


async def test_context_manifest_reconciliation_rejects_stale_compare_and_swap(
    application_database: ApplicationSQLite,
) -> None:
    service = _service(application_database)
    thread = await service.create_thread("workspace_1", "Thread")
    turn = await service.begin_turn(
        thread.id,
        "Inspect repository",
        _turn_config(),
        client_message_id="client_context_cas",
    )
    first: tuple[dict[str, JsonValue], ...] = ({"kind": "current_input", "order": 0},)
    stale: tuple[dict[str, JsonValue], ...] = (
        {"kind": "product_instructions", "order": 0},
    )

    await service.compare_and_swap_context_manifest(
        turn.id,
        first,
        expected_context_manifest=(),
    )

    with pytest.raises(ConversationConflict):
        await service.compare_and_swap_context_manifest(
            turn.id,
            stale,
            expected_context_manifest=(),
        )

    assert (await service.read_thread(thread.id)).turns[0].context_manifest == first


async def test_first_accepted_message_names_an_automatic_thread(
    application_database: ApplicationSQLite,
) -> None:
    service = _service(application_database)
    thread = await service.create_thread("workspace_1")

    await service.begin_turn(
        thread.id,
        "  calculate   cube  ",
        _turn_config(),
        client_message_id="client_first",
    )

    view = await service.read_thread(thread.id)
    assert view.thread.title == "calculate cube"
    assert view.thread.title_source is ThreadTitleSource.AUTOMATIC
    assert len(view.entries) == 1
    assert len(view.turns) == 1


async def test_rename_thread_normalizes_and_persists_manual_provenance(
    application_database: ApplicationSQLite,
) -> None:
    service = _service(application_database)
    thread = await service.create_thread("workspace_1")

    renamed = await service.rename_thread(thread.id, "  Cube   helper  ")

    assert renamed.title == "Cube helper"
    assert renamed.title_source is ThreadTitleSource.MANUAL
    assert (await service.read_thread(thread.id)).thread == renamed


async def test_rename_thread_rejects_more_than_100_visible_graphemes(
    application_database: ApplicationSQLite,
) -> None:
    service = _service(application_database)
    thread = await service.create_thread("workspace_1")

    with pytest.raises(ValueError, match="100 characters or fewer"):
        await service.rename_thread(thread.id, "👩‍💻" * 101)


async def test_one_in_progress_turn_per_thread_but_other_threads_are_independent(
    application_database: ApplicationSQLite,
) -> None:
    service = _service(application_database)
    first = await service.create_thread("workspace_1", "First")
    second = await service.create_thread("workspace_1", "Second")
    await service.begin_turn(
        first.id, "first", _turn_config(), client_message_id="client_first"
    )

    with pytest.raises(TurnBusy):
        await service.begin_turn(
            first.id,
            "duplicate",
            _turn_config(),
            client_message_id="client_duplicate",
        )

    other = await service.begin_turn(
        second.id, "second", _turn_config(), client_message_id="client_second"
    )
    assert other.thread_id == second.id


async def test_completion_appends_assistant_and_is_idempotent(
    application_database: ApplicationSQLite,
) -> None:
    service = _service(application_database)
    thread = await service.create_thread("workspace_1", "Thread")
    turn = await service.begin_turn(
        thread.id, "question", _turn_config(), client_message_id="client_1"
    )
    usage = UsageSummary(input_tokens=10, output_tokens=4, model_calls=1)
    citations = (
        Citation(
            id="S1",
            title="Source",
            url="https://example.com/source",
        ),
    )

    completed = await service.complete_turn(
        turn.id,
        "answer [[S1]]",
        usage,
        "completed",
        citations=citations,
    )
    repeated = await service.complete_turn(
        turn.id,
        "answer [[S1]]",
        usage,
        "completed",
        citations=citations,
    )
    view = await service.read_thread(thread.id)

    assert repeated == completed
    assert completed.status is TurnStatus.COMPLETED
    assert completed.assistant_entry_id is not None
    assert [entry.kind for entry in view.entries] == [
        ThreadEntryKind.USER_MESSAGE,
        ThreadEntryKind.ASSISTANT_MESSAGE,
    ]
    assert view.entries[1].content == "answer [[S1]]"
    assert view.entries[1].metadata == {
        "citations": [citations[0].model_dump(mode="json")]
    }

    with pytest.raises(ConversationConflict):
        await service.complete_turn(
            turn.id,
            "different",
            usage,
            "completed",
            citations=citations,
        )


@pytest.mark.parametrize(
    ("terminal", "code"),
    [(TurnStatus.FAILED, "model_failed"), (TurnStatus.CANCELLED, None)],
)
async def test_failure_and_cancellation_are_idempotent_terminal_updates(
    application_database: ApplicationSQLite,
    terminal: TurnStatus,
    code: str | None,
) -> None:
    service = _service(application_database)
    thread = await service.create_thread("workspace_1", "Thread")
    turn = await service.begin_turn(
        thread.id, "question", _turn_config(), client_message_id="client_1"
    )

    if terminal is TurnStatus.FAILED:
        result = await service.fail_turn(turn.id, code or "model_failed")
        repeated = await service.fail_turn(turn.id, code or "model_failed")
    else:
        result = await service.cancel_turn(turn.id)
        repeated = await service.cancel_turn(turn.id)

    assert result.status is terminal
    assert repeated == result
    with pytest.raises(InvalidTurnTransition):
        await service.complete_turn(turn.id, "late", UsageSummary(), "completed")


async def test_terminal_turns_persist_facts_and_derive_thread_totals(
    application_database: ApplicationSQLite,
) -> None:
    service = _service(application_database)
    thread = await service.create_thread("workspace_1", "Thread")
    completed = await service.begin_turn(
        thread.id, "complete", _turn_config(), client_message_id="client_complete"
    )
    complete_usage = UsageSummary(input_tokens=10, output_tokens=4, model_calls=1)
    failed_usage = UsageSummary(input_tokens=6, tool_calls=2)
    cancelled_usage = UsageSummary(input_tokens=3, active_execution_seconds=0.5)
    complete_manifest: tuple[dict[str, JsonValue], ...] = (
        {"kind": "history", "estimated_tokens": 10},
    )
    failed_manifest: tuple[dict[str, JsonValue], ...] = (
        {"kind": "summary", "estimated_tokens": 6},
    )
    cancelled_manifest: tuple[dict[str, JsonValue], ...] = (
        {"kind": "path", "estimated_tokens": 3},
    )

    await service.complete_turn(
        completed.id,
        "done",
        complete_usage,
        "completed",
        complete_manifest,
    )
    failed = await service.begin_turn(
        thread.id, "fail", _turn_config(), client_message_id="client_failed"
    )
    failed_result = await service.fail_turn(
        failed.id,
        "model_failed",
        usage=failed_usage,
        context_manifest=failed_manifest,
    )
    cancelled = await service.begin_turn(
        thread.id, "cancel", _turn_config(), client_message_id="client_cancelled"
    )
    cancelled_result = await service.cancel_turn(
        cancelled.id,
        usage=cancelled_usage,
        context_manifest=cancelled_manifest,
    )

    assert failed_result.usage == failed_usage
    assert failed_result.context_manifest == failed_manifest
    assert cancelled_result.usage == cancelled_usage
    assert cancelled_result.context_manifest == cancelled_manifest
    assert await service.thread_usage(thread.id) == (
        complete_usage + failed_usage + cancelled_usage
    )


@pytest.mark.parametrize("terminal", [TurnStatus.FAILED, TurnStatus.CANCELLED])
async def test_terminal_fact_repetition_is_idempotent_and_conflicts_on_change(
    application_database: ApplicationSQLite,
    terminal: TurnStatus,
) -> None:
    service = _service(application_database)
    thread = await service.create_thread("workspace_1", "Thread")
    turn = await service.begin_turn(
        thread.id, "question", _turn_config(), client_message_id="client_1"
    )
    usage = UsageSummary(input_tokens=5, model_calls=1)
    manifest: tuple[dict[str, JsonValue], ...] = (
        {"kind": "history", "estimated_tokens": 5},
    )

    if terminal is TurnStatus.FAILED:
        result = await service.fail_turn(
            turn.id, "model_failed", usage=usage, context_manifest=manifest
        )
        repeated = await service.fail_turn(
            turn.id, "model_failed", usage=usage, context_manifest=manifest
        )
        with pytest.raises(ConversationConflict):
            await service.fail_turn(
                turn.id,
                "model_failed",
                usage=UsageSummary(input_tokens=6),
                context_manifest=manifest,
            )
    else:
        result = await service.cancel_turn(
            turn.id, usage=usage, context_manifest=manifest
        )
        repeated = await service.cancel_turn(
            turn.id, usage=usage, context_manifest=manifest
        )
        with pytest.raises(ConversationConflict):
            await service.cancel_turn(
                turn.id,
                usage=UsageSummary(input_tokens=6),
                context_manifest=manifest,
            )

    assert repeated == result


async def test_latest_context_manifest_skips_newest_empty_terminal_turn(
    application_database: ApplicationSQLite,
) -> None:
    service = _service(application_database)
    thread = await service.create_thread("workspace_1", "Thread")
    first = await service.begin_turn(
        thread.id, "first", _turn_config(), client_message_id="client_first"
    )
    manifest: tuple[dict[str, JsonValue], ...] = (
        {"kind": "history", "estimated_tokens": 11},
    )
    await service.complete_turn(first.id, "done", UsageSummary(), "completed", manifest)
    latest = await service.begin_turn(
        thread.id, "latest", _turn_config(), client_message_id="client_latest"
    )
    await service.cancel_turn(latest.id)

    assert await service.latest_context_manifest(thread.id) == manifest


@pytest.mark.parametrize("terminal", ("completed", "failed", "cancelled"))
async def test_terminalization_preserves_durable_in_progress_context_snapshot(
    application_database: ApplicationSQLite,
    terminal: str,
) -> None:
    service = _service(application_database)
    thread = await service.create_thread("workspace_1", "Thread")
    turn = await service.begin_turn(
        thread.id,
        "question",
        _turn_config(),
        client_message_id=f"client_{terminal}",
    )
    manifest: tuple[dict[str, JsonValue], ...] = (
        {"kind": "current_input", "estimated_tokens": 3},
    )
    await service.store_context_manifest(turn.id, manifest)

    if terminal == "completed":
        result = await service.complete_turn(
            turn.id,
            "answer",
            UsageSummary(),
            "completed",
        )
    elif terminal == "failed":
        result = await service.fail_turn(turn.id, "model_failed")
    else:
        result = await service.cancel_turn(turn.id)

    assert result.context_manifest == manifest


async def test_append_direct_command_uses_next_durable_sequence(
    application_database: ApplicationSQLite,
) -> None:
    service = _service(application_database)
    thread = await service.create_thread("workspace_1", "Thread")
    turn = await service.begin_turn(
        thread.id, "question", _turn_config(), client_message_id="client_1"
    )
    await service.complete_turn(turn.id, "answer", UsageSummary(), "completed")

    direct = await service.append_direct_command(
        thread.id,
        "$ pytest\nexit=0\n2 passed",
        {"exit_code": 0},
    )
    view = await service.read_thread(thread.id)

    assert direct.sequence == 3
    assert direct.kind is ThreadEntryKind.DIRECT_COMMAND
    assert view.entries[-1] == direct


async def test_empty_user_message_is_rejected_without_durable_rows(
    application_database: ApplicationSQLite,
) -> None:
    service = _service(application_database)
    thread = await service.create_thread("workspace_1", "Thread")

    with pytest.raises(ValueError, match="empty"):
        await service.begin_turn(
            thread.id, "   ", _turn_config(), client_message_id="client_1"
        )

    view = await service.read_thread(thread.id)
    assert view.entries == ()
    assert view.turns == ()


async def test_service_exposes_bounded_thread_and_entry_pages(
    application_database: ApplicationSQLite,
) -> None:
    service = _service(application_database)
    first = await service.create_thread("workspace_1", "First")
    await service.create_thread("workspace_1", "Second")
    await service.begin_turn(
        first.id, "question", _turn_config(), client_message_id="client_1"
    )

    threads = await service.list_thread_page("workspace_1", cursor=None, limit=1)
    entries = await service.read_thread_page(
        first.id,
        before_sequence=None,
        limit=1,
    )

    assert len(threads.threads) == 1
    assert threads.has_more is True
    assert [entry.content for entry in entries.view.entries] == ["question"]
    assert entries.has_more is False
