from __future__ import annotations

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
from awesome_agent.storage.conversations import SQLiteConversationRepositories


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


def _service(path: Path) -> ConversationService:
    return ConversationService(
        store=SQLiteConversationRepositories(path),
        id_factory=DeterministicIds(),
        clock=DeterministicClock(),
    )


def _turn_config() -> TurnConfig:
    return TurnConfig(
        provider="deepseek",
        model="deepseek/deepseek-v4-flash",
        thinking_enabled=True,
        skill_mode="debug",
        budgets=BudgetConfig(model_calls=12, tool_calls=24),
    )


def test_create_list_and_read_threads_by_workspace(tmp_path: Path) -> None:
    service = _service(tmp_path / "application.db")
    first = service.create_thread("workspace_1", "First")
    second = service.create_thread("workspace_1")
    service.create_thread("workspace_2", "Other")

    listed = service.list_threads("workspace_1")
    view = service.read_thread(first.id)

    assert {thread.id for thread in listed} == {first.id, second.id}
    assert second.title == "New conversation"
    assert second.title_source is ThreadTitleSource.AUTOMATIC
    assert second.thinking_enabled is True
    assert view.thread == first
    assert view.entries == ()
    assert view.turns == ()


def test_begin_turn_atomically_appends_user_and_freezes_config(tmp_path: Path) -> None:
    service = _service(tmp_path / "application.db")
    thread = service.create_thread("workspace_1", "Thread")
    config = _turn_config()

    turn = service.begin_turn(
        thread.id,
        "Inspect repository",
        config,
        client_message_id="client_1",
    )
    view = service.read_thread(thread.id)

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


def test_one_in_progress_turn_per_thread_but_other_threads_are_independent(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path / "application.db")
    first = service.create_thread("workspace_1", "First")
    second = service.create_thread("workspace_1", "Second")
    service.begin_turn(
        first.id, "first", _turn_config(), client_message_id="client_first"
    )

    with pytest.raises(TurnBusy):
        service.begin_turn(
            first.id,
            "duplicate",
            _turn_config(),
            client_message_id="client_duplicate",
        )

    other = service.begin_turn(
        second.id, "second", _turn_config(), client_message_id="client_second"
    )
    assert other.thread_id == second.id


def test_completion_appends_assistant_and_is_idempotent(tmp_path: Path) -> None:
    service = _service(tmp_path / "application.db")
    thread = service.create_thread("workspace_1", "Thread")
    turn = service.begin_turn(
        thread.id, "question", _turn_config(), client_message_id="client_1"
    )
    usage = UsageSummary(input_tokens=10, output_tokens=4, model_calls=1)

    completed = service.complete_turn(turn.id, "answer", usage, "completed")
    repeated = service.complete_turn(turn.id, "answer", usage, "completed")
    view = service.read_thread(thread.id)

    assert repeated == completed
    assert completed.status is TurnStatus.COMPLETED
    assert completed.assistant_entry_id is not None
    assert [entry.kind for entry in view.entries] == [
        ThreadEntryKind.USER_MESSAGE,
        ThreadEntryKind.ASSISTANT_MESSAGE,
    ]
    assert view.entries[1].content == "answer"

    with pytest.raises(ConversationConflict):
        service.complete_turn(turn.id, "different", usage, "completed")


@pytest.mark.parametrize(
    ("terminal", "code"),
    [(TurnStatus.FAILED, "model_failed"), (TurnStatus.CANCELLED, None)],
)
def test_failure_and_cancellation_are_idempotent_terminal_updates(
    tmp_path: Path,
    terminal: TurnStatus,
    code: str | None,
) -> None:
    service = _service(tmp_path / f"{terminal}.db")
    thread = service.create_thread("workspace_1", "Thread")
    turn = service.begin_turn(
        thread.id, "question", _turn_config(), client_message_id="client_1"
    )

    if terminal is TurnStatus.FAILED:
        result = service.fail_turn(turn.id, code or "model_failed")
        repeated = service.fail_turn(turn.id, code or "model_failed")
    else:
        result = service.cancel_turn(turn.id)
        repeated = service.cancel_turn(turn.id)

    assert result.status is terminal
    assert repeated == result
    with pytest.raises(InvalidTurnTransition):
        service.complete_turn(turn.id, "late", UsageSummary(), "completed")


def test_terminal_turns_persist_facts_and_derive_thread_totals(tmp_path: Path) -> None:
    service = _service(tmp_path / "application.db")
    thread = service.create_thread("workspace_1", "Thread")
    completed = service.begin_turn(
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

    service.complete_turn(
        completed.id,
        "done",
        complete_usage,
        "completed",
        complete_manifest,
    )
    failed = service.begin_turn(
        thread.id, "fail", _turn_config(), client_message_id="client_failed"
    )
    failed_result = service.fail_turn(
        failed.id,
        "model_failed",
        usage=failed_usage,
        context_manifest=failed_manifest,
    )
    cancelled = service.begin_turn(
        thread.id, "cancel", _turn_config(), client_message_id="client_cancelled"
    )
    cancelled_result = service.cancel_turn(
        cancelled.id,
        usage=cancelled_usage,
        context_manifest=cancelled_manifest,
    )

    assert failed_result.usage == failed_usage
    assert failed_result.context_manifest == failed_manifest
    assert cancelled_result.usage == cancelled_usage
    assert cancelled_result.context_manifest == cancelled_manifest
    assert service.thread_usage(thread.id) == (
        complete_usage + failed_usage + cancelled_usage
    )


@pytest.mark.parametrize("terminal", [TurnStatus.FAILED, TurnStatus.CANCELLED])
def test_terminal_fact_repetition_is_idempotent_and_conflicts_on_change(
    tmp_path: Path,
    terminal: TurnStatus,
) -> None:
    service = _service(tmp_path / f"{terminal}-facts.db")
    thread = service.create_thread("workspace_1", "Thread")
    turn = service.begin_turn(
        thread.id, "question", _turn_config(), client_message_id="client_1"
    )
    usage = UsageSummary(input_tokens=5, model_calls=1)
    manifest: tuple[dict[str, JsonValue], ...] = (
        {"kind": "history", "estimated_tokens": 5},
    )

    if terminal is TurnStatus.FAILED:
        result = service.fail_turn(
            turn.id, "model_failed", usage=usage, context_manifest=manifest
        )
        repeated = service.fail_turn(
            turn.id, "model_failed", usage=usage, context_manifest=manifest
        )
        with pytest.raises(ConversationConflict):
            service.fail_turn(
                turn.id,
                "model_failed",
                usage=UsageSummary(input_tokens=6),
                context_manifest=manifest,
            )
    else:
        result = service.cancel_turn(turn.id, usage=usage, context_manifest=manifest)
        repeated = service.cancel_turn(turn.id, usage=usage, context_manifest=manifest)
        with pytest.raises(ConversationConflict):
            service.cancel_turn(
                turn.id,
                usage=UsageSummary(input_tokens=6),
                context_manifest=manifest,
            )

    assert repeated == result


def test_latest_context_manifest_skips_newest_empty_terminal_turn(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path / "application.db")
    thread = service.create_thread("workspace_1", "Thread")
    first = service.begin_turn(
        thread.id, "first", _turn_config(), client_message_id="client_first"
    )
    manifest: tuple[dict[str, JsonValue], ...] = (
        {"kind": "history", "estimated_tokens": 11},
    )
    service.complete_turn(first.id, "done", UsageSummary(), "completed", manifest)
    latest = service.begin_turn(
        thread.id, "latest", _turn_config(), client_message_id="client_latest"
    )
    service.cancel_turn(latest.id)

    assert service.latest_context_manifest(thread.id) == manifest


def test_append_direct_command_uses_next_durable_sequence(tmp_path: Path) -> None:
    service = _service(tmp_path / "application.db")
    thread = service.create_thread("workspace_1", "Thread")
    turn = service.begin_turn(
        thread.id, "question", _turn_config(), client_message_id="client_1"
    )
    service.complete_turn(turn.id, "answer", UsageSummary(), "completed")

    direct = service.append_direct_command(
        thread.id,
        "$ pytest\nexit=0\n2 passed",
        {"exit_code": 0},
    )
    view = service.read_thread(thread.id)

    assert direct.sequence == 3
    assert direct.kind is ThreadEntryKind.DIRECT_COMMAND
    assert view.entries[-1] == direct


def test_empty_user_message_is_rejected_without_durable_rows(tmp_path: Path) -> None:
    service = _service(tmp_path / "application.db")
    thread = service.create_thread("workspace_1", "Thread")

    with pytest.raises(ValueError, match="empty"):
        service.begin_turn(
            thread.id, "   ", _turn_config(), client_message_id="client_1"
        )

    view = service.read_thread(thread.id)
    assert view.entries == ()
    assert view.turns == ()


def test_service_exposes_bounded_thread_and_entry_pages(tmp_path: Path) -> None:
    service = _service(tmp_path / "application.db")
    first = service.create_thread("workspace_1", "First")
    service.create_thread("workspace_1", "Second")
    service.begin_turn(
        first.id, "question", _turn_config(), client_message_id="client_1"
    )

    threads = service.list_thread_page("workspace_1", cursor=None, limit=1)
    entries = service.read_thread_page(
        first.id,
        before_sequence=None,
        limit=1,
    )

    assert len(threads.threads) == 1
    assert threads.has_more is True
    assert [entry.content for entry in entries.view.entries] == ["question"]
    assert entries.has_more is False
