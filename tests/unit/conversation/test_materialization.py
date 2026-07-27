from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from awesome_agent.config import BudgetConfig, TurnConfig
from awesome_agent.conversation import (
    AssistantEntryMetadata,
    ConversationConflict,
    ConversationService,
    InvalidTurnTransition,
    ThreadEntry,
    ThreadEntryKind,
    ThreadNotFound,
    ThreadSummary,
    ThreadTitleSource,
    ThreadView,
    Turn,
    TurnNotFound,
    TurnStatus,
    UsageSummary,
)
from awesome_agent.conversation.materialization import (
    ThreadMaterializationPlan,
    build_thread_materialization,
)
from awesome_agent.core.citations import Citation
from awesome_agent.core.tools import ToolActivityDraft, ToolExecutionOrigin
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
        self._now = datetime(2026, 7, 28, tzinfo=UTC)

    def __call__(self) -> datetime:
        result = self._now
        self._now += timedelta(seconds=1)
        return result


@pytest.fixture
async def application_database(tmp_path: Path) -> AsyncIterator[ApplicationSQLite]:
    database = ApplicationSQLite(tmp_path / "application.db")
    await database.initialize()
    try:
        yield database
    finally:
        await database.aclose()


def _service(
    database: ApplicationSQLite,
) -> tuple[ConversationService, SQLiteConversationRepositories]:
    repositories = SQLiteConversationRepositories(database)
    return (
        ConversationService(
            store=repositories,
            id_factory=DeterministicIds(),
            clock=DeterministicClock(),
        ),
        repositories,
    )


def _config(
    model: str,
    *,
    thinking: bool,
    skill_mode: str,
    model_calls: int,
) -> TurnConfig:
    return TurnConfig(
        provider="deepseek",
        model=model,
        thinking_enabled=thinking,
        skill_mode=skill_mode,
        budgets=BudgetConfig(model_calls=model_calls, tool_calls=24),
    )


async def _seed_history(
    service: ConversationService,
    repositories: SQLiteConversationRepositories,
) -> tuple[str, str, str, str]:
    thread = await service.create_thread(
        "workspace_1",
        "Materialization source",
        current_model="deepseek/current",
    )
    await service.set_thinking(thread.id, False)
    await service.set_skill_mode(thread.id, "source-skill")
    first = await service.begin_turn(
        thread.id,
        "first question",
        _config(
            "deepseek/first",
            thinking=True,
            skill_mode="first-skill",
            model_calls=11,
        ),
        client_message_id="client_source_first",
    )
    await service.complete_turn(
        first.id,
        "first answer [[S1]]",
        UsageSummary(input_tokens=10, output_tokens=5, model_calls=1),
        "completed",
        ({"kind": "history", "order": 1},),
        (
            Citation(
                id="S1",
                title="Source",
                url="https://example.com/source",
            ),
        ),
    )
    await service.append_direct_command(
        thread.id,
        "before target",
        {
            "operation_id": "operation_old_before",
            "status": "success",
            "exit_code": 0,
            "truncated": False,
            "managed_side_effects": True,
        },
    )
    second = await service.begin_turn(
        thread.id,
        "failed question",
        _config(
            "deepseek/failed",
            thinking=False,
            skill_mode="failed-skill",
            model_calls=12,
        ),
        client_message_id="client_source_failed",
    )
    await service.fail_turn(
        second.id,
        "model_failed",
        usage=UsageSummary(input_tokens=6, model_calls=1),
        context_manifest=({"kind": "current_input", "order": 2},),
    )
    await service.append_direct_command(
        thread.id,
        "after failed target",
        {
            "operation_id": "operation_old_after",
            "status": "success",
        },
    )
    third = await service.begin_turn(
        thread.id,
        "retry this question",
        _config(
            "deepseek/frozen-retry",
            thinking=True,
            skill_mode="retry-skill",
            model_calls=13,
        ),
        client_message_id="client_source_retry",
    )
    await service.complete_turn(
        third.id,
        "old answer",
        UsageSummary(input_tokens=7, output_tokens=3, model_calls=1),
        "completed",
        ({"kind": "current_input", "order": 3},),
    )
    await service.store_summary(
        ThreadSummary(
            thread_id=thread.id,
            content="source-only summary",
            content_hash="a" * 64,
            covered_entry_sequence=2,
            covered_turn_count=1,
            estimated_tokens=10,
            provider="deepseek",
            model="deepseek/first",
            updated_at=datetime(2026, 7, 28, 1, tzinfo=UTC),
        ),
        expected=None,
    )
    await repositories.finalize(
        ToolActivityDraft(
            thread_id=thread.id,
            turn_id=first.id,
            operation_id="operation_source_tool",
            call_id="call_source_tool",
            origin=ToolExecutionOrigin.AGENT,
            tool_name="read_file",
            outcome="success",
            input_summary="source input",
            result_summary="source result",
            duration_ms=1,
        )
    )
    return thread.id, first.id, second.id, third.id


async def test_fork_materializes_terminal_prefix_with_independent_identities(
    application_database: ApplicationSQLite,
) -> None:
    service, repositories = _service(application_database)
    source_id, first_id, failed_id, _ = await _seed_history(service, repositories)
    source = await service.read_thread(source_id)

    fork = await service.fork_thread("workspace_1", source_id, failed_id)

    assert fork.thread.id != source_id
    assert fork.thread.title == "Fork of Materialization source"
    assert fork.thread.title_source is ThreadTitleSource.MANUAL
    assert fork.thread.current_model == "deepseek/current"
    assert fork.thread.thinking_enabled is False
    assert fork.thread.skill_mode == "source-skill"
    assert fork.thread.lineage is not None
    assert fork.thread.lineage.model_dump() == {
        "kind": "fork",
        "source_thread_id": source_id,
        "source_turn_id": failed_id,
    }
    assert [entry.content for entry in fork.entries] == [
        "first question",
        "first answer [[S1]]",
        "before target",
        "failed question",
    ]
    assert [entry.sequence for entry in fork.entries] == [1, 2, 3, 4]
    assert [turn.status for turn in fork.turns] == [
        TurnStatus.COMPLETED,
        TurnStatus.FAILED,
    ]
    assert all(turn.context_manifest == () for turn in fork.turns)
    assert all(turn.checkpoint_key == turn.id for turn in fork.turns)
    assert fork.summary is None
    assert fork.tool_activities == ()
    source_entry_ids = {entry.id for entry in source.entries}
    source_turn_ids = {turn.id for turn in source.turns}
    source_client_ids = {
        entry.client_message_id
        for entry in source.entries
        if entry.client_message_id is not None
    }
    assert not source_entry_ids & {entry.id for entry in fork.entries}
    assert not source_turn_ids & {turn.id for turn in fork.turns}
    assert not source_client_ids & {
        entry.client_message_id
        for entry in fork.entries
        if entry.client_message_id is not None
    }
    assistant = next(
        entry
        for entry in fork.entries
        if entry.kind is ThreadEntryKind.ASSISTANT_MESSAGE
    )
    assistant_metadata = AssistantEntryMetadata.model_validate(assistant.metadata)
    assert assistant_metadata.citations[0].id == "S1"
    direct = next(
        entry for entry in fork.entries if entry.kind is ThreadEntryKind.DIRECT_COMMAND
    )
    assert direct.metadata == {
        "status": "success",
        "exit_code": 0,
        "truncated": False,
        "managed_side_effects": True,
    }
    source_statuses = [
        turn.status for turn in source.turns if turn.id in {first_id, failed_id}
    ]
    assert source_statuses == [
        TurnStatus.COMPLETED,
        TurnStatus.FAILED,
    ]


async def test_retry_excludes_target_result_and_freezes_target_turn_config(
    application_database: ApplicationSQLite,
) -> None:
    service, repositories = _service(application_database)
    source_id, _, _, retry_target_id = await _seed_history(service, repositories)
    source_before = await service.read_thread(source_id)
    target = next(turn for turn in source_before.turns if turn.id == retry_target_id)
    await service.set_model(source_id, "deepseek/current-after-target")
    await service.set_thinking(source_id, False)
    await service.set_skill_mode(source_id, "current-skill")

    prepared = await service.prepare_retry(
        "workspace_1",
        source_id,
        retry_target_id,
    )

    assert prepared.view.thread.title == "Retry of Materialization source"
    assert prepared.view.thread.current_model == "deepseek/current-after-target"
    assert prepared.view.thread.thinking_enabled is False
    assert prepared.view.thread.skill_mode == "current-skill"
    assert prepared.view.thread.lineage is not None
    assert prepared.view.thread.lineage.kind == "retry"
    assert prepared.view.thread.lineage.source_turn_id == retry_target_id
    assert prepared.content == "retry this question"
    assert prepared.turn.status is TurnStatus.IN_PROGRESS
    assert prepared.turn.provider == target.provider
    assert prepared.turn.model == target.model
    assert prepared.turn.thinking_enabled == target.thinking_enabled
    assert prepared.turn.skill_mode == target.skill_mode
    assert prepared.turn.budgets == target.budgets
    assert prepared.turn.usage == UsageSummary()
    assert prepared.turn.context_manifest == ()
    assert prepared.turn.assistant_entry_id is None
    assert prepared.turn.completed_at is None
    assert prepared.view.entries[-1].content == "retry this question"
    assert prepared.view.entries[-1].client_message_id == prepared.client_message_id
    assert "old answer" not in [entry.content for entry in prepared.view.entries]
    assert [turn.status for turn in prepared.view.turns] == [
        TurnStatus.COMPLETED,
        TurnStatus.FAILED,
        TurnStatus.IN_PROGRESS,
    ]
    assert prepared.view.summary is None
    assert prepared.view.tool_activities == ()
    direct_entries = tuple(
        entry
        for entry in prepared.view.entries
        if entry.kind is ThreadEntryKind.DIRECT_COMMAND
    )
    assert [entry.content for entry in direct_entries] == [
        "before target",
        "after failed target",
    ]
    assert all("operation_id" not in entry.metadata for entry in direct_entries)


async def test_latest_terminal_is_used_but_in_progress_target_is_rejected(
    application_database: ApplicationSQLite,
) -> None:
    service, repositories = _service(application_database)
    source_id, _, _, latest_terminal_id = await _seed_history(service, repositories)
    active = await service.begin_turn(
        source_id,
        "still running",
        _config(
            "deepseek/active",
            thinking=False,
            skill_mode="auto",
            model_calls=10,
        ),
        client_message_id="client_active_source",
    )

    fork = await service.fork_thread("workspace_1", source_id)

    assert fork.thread.lineage is not None
    assert fork.thread.lineage.source_turn_id == latest_terminal_id
    with pytest.raises(InvalidTurnTransition):
        await service.fork_thread("workspace_1", source_id, active.id)
    with pytest.raises(InvalidTurnTransition):
        await service.prepare_retry("workspace_1", source_id, active.id)
    with pytest.raises(ThreadNotFound):
        await service.fork_thread("workspace_other", source_id, latest_terminal_id)
    with pytest.raises(TurnNotFound):
        await service.fork_thread("workspace_1", source_id, "turn_missing")


async def test_cancelled_fork_includes_its_user_without_an_assistant(
    application_database: ApplicationSQLite,
) -> None:
    service, _ = _service(application_database)
    source = await service.create_thread("workspace_1", "Cancelled source")
    completed = await service.begin_turn(
        source.id,
        "completed question",
        _config(
            "deepseek/completed",
            thinking=True,
            skill_mode="auto",
            model_calls=10,
        ),
        client_message_id="client_completed_source",
    )
    await service.complete_turn(
        completed.id,
        "completed answer",
        UsageSummary(),
        "completed",
    )
    cancelled = await service.begin_turn(
        source.id,
        "cancelled question",
        _config(
            "deepseek/cancelled",
            thinking=False,
            skill_mode="auto",
            model_calls=10,
        ),
        client_message_id="client_cancelled_source",
    )
    cancelled_terminal = await service.cancel_turn(cancelled.id)

    fork = await service.fork_thread("workspace_1", source.id, cancelled.id)

    assert [entry.content for entry in fork.entries] == [
        "completed question",
        "completed answer",
        "cancelled question",
    ]
    assert [turn.status for turn in fork.turns] == [
        TurnStatus.COMPLETED,
        TurnStatus.CANCELLED,
    ]
    assert fork.turns[-1].assistant_entry_id is None

    source_view = await service.read_thread(source.id)
    assert cancelled_terminal.completed_at is not None
    partial = ThreadEntry(
        id="entry_cancelled_partial",
        thread_id=source.id,
        sequence=len(source_view.entries) + 1,
        kind=ThreadEntryKind.ASSISTANT_MESSAGE,
        content="partial answer must not be cloned",
        created_at=cancelled_terminal.completed_at,
    )
    malformed_target = cancelled_terminal.model_copy(
        update={"assistant_entry_id": partial.id}
    )
    malformed = source_view.model_copy(
        update={
            "entries": (*source_view.entries, partial),
            "turns": (*source_view.turns[:-1], malformed_target),
        }
    )
    materialized, _ = build_thread_materialization(
        malformed,
        malformed_target,
        kind="fork",
        id_factory=DeterministicIds(),
        now=datetime(2026, 7, 29, tzinfo=UTC),
    )
    assert partial.content not in {entry.content for entry in materialized.entries}


async def test_materialization_rejects_terminal_content_beyond_target_boundary(
    application_database: ApplicationSQLite,
) -> None:
    service, repositories = _service(application_database)
    source_id, _, target_id, _ = await _seed_history(service, repositories)
    source = await service.read_thread(source_id)
    target = next(turn for turn in source.turns if turn.id == target_id)
    entries = tuple(
        entry.model_copy(update={"sequence": 100})
        if entry.kind is ThreadEntryKind.ASSISTANT_MESSAGE
        and entry.content == "first answer [[S1]]"
        else entry
        for entry in source.entries
    )
    malformed = source.model_copy(update={"entries": entries})

    with pytest.raises(ConversationConflict, match="crosses"):
        build_thread_materialization(
            malformed,
            target,
            kind="fork",
            id_factory=DeterministicIds(),
            now=datetime(2026, 7, 29, tzinfo=UTC),
        )


@pytest.mark.parametrize("mutation", ["config", "entry"])
async def test_materialization_rejects_same_timestamp_source_mutation(
    application_database: ApplicationSQLite,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    service, repositories = _service(application_database)
    source_id, _, target_id, _ = await _seed_history(service, repositories)
    original = repositories.materialize_fork

    async def mutate_then_materialize(
        plan: ThreadMaterializationPlan,
    ) -> ThreadView:
        source = await repositories.read_thread(source_id)
        if mutation == "config":
            await repositories.set_thread_model(
                source_id,
                "deepseek/concurrent",
                updated_at=source.thread.updated_at,
            )
        else:
            await repositories.append_direct_command(
                ThreadEntry(
                    id="entry_concurrent",
                    thread_id=source_id,
                    sequence=source.entries[-1].sequence + 1,
                    kind=ThreadEntryKind.DIRECT_COMMAND,
                    content="concurrent",
                    created_at=source.thread.updated_at,
                )
            )
        return await original(plan)

    monkeypatch.setattr(repositories, "materialize_fork", mutate_then_materialize)

    with pytest.raises(ConversationConflict, match="changed"):
        await service.fork_thread("workspace_1", source_id, target_id)

    assert [thread.id for thread in await repositories.list_threads("workspace_1")] == [
        source_id
    ]


async def test_materialization_insert_failure_rolls_back_every_destination_row(
    application_database: ApplicationSQLite,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repositories = _service(application_database)
    source_id, _, target_id, _ = await _seed_history(service, repositories)
    create_turn = repositories._turns.create

    def fail_destination_turn(
        turn: Turn,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> Turn:
        if turn.thread_id != source_id:
            raise RuntimeError("injected destination Turn failure")
        return create_turn(turn, connection=connection)

    monkeypatch.setattr(repositories._turns, "create", fail_destination_turn)

    with pytest.raises(RuntimeError, match="injected destination"):
        await service.fork_thread("workspace_1", source_id, target_id)

    assert [thread.id for thread in await repositories.list_threads("workspace_1")] == [
        source_id
    ]
    assert (await repositories.read_thread(source_id)).thread.id == source_id
