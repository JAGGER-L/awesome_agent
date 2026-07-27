from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from awesome_agent.config import BudgetConfig
from awesome_agent.conversation import (
    InvalidTurnTransition,
    Thread,
    ThreadEntry,
    ThreadEntryKind,
    ThreadSummary,
    ThreadTitleSource,
    ToolActivity,
    ToolActivityOrigin,
    ToolActivityOutcome,
    Turn,
    TurnStatus,
    UsageSummary,
    require_turn_transition,
)
from awesome_agent.core.citations import Citation


def _now() -> datetime:
    return datetime.now(UTC)


def _thread() -> Thread:
    now = _now()
    return Thread(
        id="thread_1",
        workspace_key="workspace_1",
        title="Local refactor",
        created_at=now,
        updated_at=now,
    )


def _turn(status: TurnStatus = TurnStatus.IN_PROGRESS) -> Turn:
    now = _now()
    terminal = status is not TurnStatus.IN_PROGRESS
    return Turn(
        id="turn_1",
        thread_id="thread_1",
        checkpoint_key="turn_1",
        status=status,
        provider="deepseek",
        model="deepseek/deepseek-v4-flash",
        thinking_enabled=False,
        skill_mode="auto",
        budgets=BudgetConfig(),
        user_entry_id="entry_user",
        assistant_entry_id=(
            "entry_assistant" if status is TurnStatus.COMPLETED else None
        ),
        usage=UsageSummary(),
        termination_reason=("completed" if status is TurnStatus.COMPLETED else None),
        error_code=("model_failed" if status is TurnStatus.FAILED else None),
        created_at=now,
        updated_at=now,
        completed_at=(now if terminal else None),
    )


def test_thread_and_turn_round_trip_as_frozen_boundary_models() -> None:
    thread = _thread()
    turn = _turn()

    assert Thread.model_validate_json(thread.model_dump_json()) == thread
    assert Turn.model_validate_json(turn.model_dump_json()) == turn
    with pytest.raises(ValidationError):
        thread.title = "changed"


def test_thread_defaults_to_automatic_title_provenance_and_thinking_on() -> None:
    thread = _thread()

    assert thread.title_source is ThreadTitleSource.AUTOMATIC
    assert thread.thinking_enabled is True


def test_checkpoint_key_must_equal_turn_id() -> None:
    payload = _turn().model_dump()
    payload["checkpoint_key"] = "thread_1"

    with pytest.raises(ValidationError, match="checkpoint_key"):
        Turn.model_validate(payload)


@pytest.mark.parametrize(
    "terminal",
    [TurnStatus.COMPLETED, TurnStatus.FAILED, TurnStatus.CANCELLED],
)
def test_in_progress_turn_can_reach_each_terminal_state(
    terminal: TurnStatus,
) -> None:
    require_turn_transition(TurnStatus.IN_PROGRESS, terminal)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (TurnStatus.IN_PROGRESS, TurnStatus.IN_PROGRESS),
        (TurnStatus.COMPLETED, TurnStatus.FAILED),
        (TurnStatus.COMPLETED, TurnStatus.CANCELLED),
        (TurnStatus.FAILED, TurnStatus.COMPLETED),
        (TurnStatus.CANCELLED, TurnStatus.IN_PROGRESS),
    ],
)
def test_no_other_durable_turn_transition_is_allowed(
    current: TurnStatus,
    target: TurnStatus,
) -> None:
    with pytest.raises(InvalidTurnTransition):
        require_turn_transition(current, target)


def test_completed_turn_requires_assistant_entry_and_completion_time() -> None:
    payload = _turn(TurnStatus.COMPLETED).model_dump()
    payload["assistant_entry_id"] = None

    with pytest.raises(ValidationError, match="assistant_entry_id"):
        Turn.model_validate(payload)


def test_failed_turn_requires_safe_error_code() -> None:
    payload = _turn(TurnStatus.FAILED).model_dump()
    payload["error_code"] = None

    with pytest.raises(ValidationError, match="error_code"):
        Turn.model_validate(payload)


def test_direct_thread_entry_is_bounded_to_30000_characters() -> None:
    with pytest.raises(ValidationError, match="direct_command"):
        ThreadEntry(
            id="entry_1",
            thread_id="thread_1",
            sequence=1,
            kind=ThreadEntryKind.DIRECT_COMMAND,
            content="x" * 30_001,
            created_at=_now(),
        )


def test_assistant_metadata_normalizes_legacy_empty_and_enforces_citations() -> None:
    entry = ThreadEntry(
        id="entry_1",
        thread_id="thread_1",
        sequence=1,
        kind=ThreadEntryKind.ASSISTANT_MESSAGE,
        content="answer",
        metadata={},
        created_at=_now(),
    )

    assert entry.metadata == {"citations": []}

    cited = ThreadEntry.model_validate(
        {
            **entry.model_dump(mode="python"),
            "metadata": {
                "citations": [
                    Citation(
                        id="S1",
                        title="Source",
                        url="https://example.com/source",
                    ).model_dump(mode="json")
                ]
            },
        }
    )
    assert cited.metadata == {
        "citations": [
            {
                "id": "S1",
                "title": "Source",
                "url": "https://example.com/source",
            }
        ]
    }

    with pytest.raises(ValidationError, match="extra_forbidden"):
        ThreadEntry.model_validate(
            {
                **entry.model_dump(mode="python"),
                "metadata": {"citations": [], "unknown": True},
            }
        )


def test_user_thread_entry_requires_client_message_identity() -> None:
    with pytest.raises(ValidationError, match="client_message_id"):
        ThreadEntry(
            id="entry_1",
            thread_id="thread_1",
            sequence=1,
            kind=ThreadEntryKind.USER_MESSAGE,
            content="inspect",
            created_at=_now(),
        )


def test_agent_tool_activity_requires_turn_and_direct_forbids_turn() -> None:
    common = {
        "id": "activity_1",
        "thread_id": "thread_1",
        "operation_id": "operation_1",
        "call_id": "call_1",
        "sequence": 1,
        "tool_name": "read_file",
        "outcome": ToolActivityOutcome.SUCCESS,
        "input_summary": "path=README.md",
        "result_summary": "read 20 lines",
        "duration_ms": 4,
        "created_at": _now(),
    }

    with pytest.raises(ValidationError, match="agent ToolActivity"):
        ToolActivity.model_validate(
            {**common, "origin": ToolActivityOrigin.AGENT, "turn_id": None}
        )
    with pytest.raises(ValidationError, match="direct ToolActivity"):
        ToolActivity.model_validate(
            {
                **common,
                "origin": ToolActivityOrigin.DIRECT,
                "turn_id": "turn_1",
            }
        )

    direct = ToolActivity.model_validate(
        {**common, "origin": ToolActivityOrigin.DIRECT, "turn_id": None}
    )
    assert direct.turn_id is None


def test_tool_activity_rejects_raw_sized_input_and_result_bodies() -> None:
    common = {
        "id": "activity_1",
        "thread_id": "thread_1",
        "turn_id": "turn_1",
        "operation_id": "operation_1",
        "call_id": "call_1",
        "sequence": 1,
        "origin": ToolActivityOrigin.AGENT,
        "tool_name": "read_file",
        "outcome": ToolActivityOutcome.SUCCESS,
        "duration_ms": 4,
        "created_at": _now(),
    }

    with pytest.raises(ValidationError):
        ToolActivity.model_validate(
            {**common, "input_summary": "x" * 2_001, "result_summary": "ok"}
        )
    with pytest.raises(ValidationError):
        ToolActivity.model_validate(
            {**common, "input_summary": "ok", "result_summary": "x" * 4_001}
        )


def test_thread_summary_tracks_covered_completed_history() -> None:
    summary = ThreadSummary(
        thread_id="thread_1",
        content="Goal: simplify runtime.",
        content_hash="a" * 64,
        covered_entry_sequence=8,
        covered_turn_count=4,
        estimated_tokens=120,
        provider="deepseek",
        model="deepseek/deepseek-v4-flash",
        updated_at=_now(),
    )

    assert summary.covered_entry_sequence == 8
    assert summary.covered_turn_count == 4


def test_usage_summary_adds_non_negative_counters() -> None:
    usage = UsageSummary(input_tokens=10, output_tokens=5, model_calls=1)
    combined = usage + UsageSummary(
        input_tokens=2,
        reasoning_tokens=3,
        tool_calls=1,
    )

    assert combined.input_tokens == 12
    assert combined.output_tokens == 5
    assert combined.reasoning_tokens == 3
    assert combined.model_calls == 1
    assert combined.tool_calls == 1

    with pytest.raises(ValidationError):
        UsageSummary(input_tokens=-1)


@pytest.mark.parametrize(
    "value",
    [9_007_199_254_740_992, float("inf"), float("nan")],
)
def test_usage_summary_rejects_non_interoperable_numbers(value: int | float) -> None:
    with pytest.raises(ValidationError):
        UsageSummary(
            input_tokens=value if isinstance(value, int) else 0,
            active_execution_seconds=(value if isinstance(value, float) else 0.0),
        )
