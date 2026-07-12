from datetime import UTC, datetime
from typing import Any, cast

import pytest

from awesome_agent.config import BudgetConfig
from awesome_agent.context import (
    CompressionRequest,
    CompressionStatus,
    ThreadCompressor,
    plan_compression,
)
from awesome_agent.conversation import (
    Thread,
    ThreadEntry,
    ThreadEntryKind,
    ThreadSummary,
    ThreadView,
    Turn,
    TurnStatus,
)
from awesome_agent.modeling import (
    AssistantMessage,
    ModelRequest,
    ModelTurn,
    ModelUsage,
    SelectedModel,
    StopReason,
)


def _view(turn_count: int, summary: ThreadSummary | None = None) -> ThreadView:
    now = datetime.now(UTC)
    thread = Thread(
        id="thread_1",
        workspace_key="workspace_1",
        title="Thread",
        created_at=now,
        updated_at=now,
    )
    entries: list[ThreadEntry] = []
    turns: list[Turn] = []
    sequence = 1
    for index in range(turn_count):
        user = ThreadEntry(
            id=f"user_{index}",
            thread_id=thread.id,
            sequence=sequence,
            kind=ThreadEntryKind.USER_MESSAGE,
            content=f"question {index}",
            client_message_id=f"client_{index}",
            created_at=now,
        )
        assistant = ThreadEntry(
            id=f"assistant_{index}",
            thread_id=thread.id,
            sequence=sequence + 1,
            kind=ThreadEntryKind.ASSISTANT_MESSAGE,
            content=f"answer {index}",
            created_at=now,
        )
        entries.extend((user, assistant))
        turns.append(
            Turn(
                id=f"turn_{index}",
                thread_id=thread.id,
                checkpoint_key=f"turn_{index}",
                status=TurnStatus.COMPLETED,
                provider="deepseek",
                model="deepseek/deepseek-v4-flash",
                budgets=BudgetConfig(),
                user_entry_id=user.id,
                assistant_entry_id=assistant.id,
                completed_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        sequence += 2
    return ThreadView(
        thread=thread,
        entries=tuple(entries),
        turns=tuple(turns),
        summary=summary,
    )


@pytest.mark.parametrize("turn_count", range(9))
def test_plan_keeps_latest_four_completed_turns(turn_count: int) -> None:
    plan = plan_compression(_view(turn_count))

    expected = max(0, turn_count - 4)
    assert plan.candidate_turn_count == expected
    assert plan.covered_entry_sequence == expected * 2
    assert len(plan.entries) == expected * 2


def test_second_plan_uses_previous_summary_and_only_new_entries() -> None:
    summary = ThreadSummary(
        thread_id="thread_1",
        content="prior summary",
        content_hash="a" * 64,
        covered_entry_sequence=2,
        covered_turn_count=1,
        estimated_tokens=10,
        provider="deepseek",
        model="deepseek/deepseek-v4-flash",
        updated_at=datetime.now(UTC),
    )

    plan = plan_compression(_view(6, summary))

    assert plan.previous_summary == "prior summary"
    assert [entry.sequence for entry in plan.entries] == [3, 4]
    assert plan.covered_entry_sequence == 4
    assert plan.candidate_turn_count == 2


class FakeGateway:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.requests: list[ModelRequest] = []

    async def complete(
        self, selected: SelectedModel, request: ModelRequest
    ) -> ModelTurn:
        del selected
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("provider failed")
        return ModelTurn(
            provider="deepseek",
            model="deepseek/deepseek-v4-flash",
            assistant=AssistantMessage(content="new summary"),
            stop_reason=StopReason.COMPLETED,
            usage=ModelUsage(input_tokens=20, output_tokens=5),
        )


@pytest.mark.asyncio
async def test_compressor_uses_tools_disabled_and_returns_usage() -> None:
    gateway = FakeGateway()
    compressor = ThreadCompressor(cast(Any, gateway))

    result = await compressor.compact(
        CompressionRequest(
            view=_view(5),
            provider="deepseek",
            model="deepseek/deepseek-v4-flash",
        )
    )

    assert result.status is CompressionStatus.COMPLETED
    assert result.summary is not None
    assert result.summary.covered_turn_count == 1
    assert result.usage.input_tokens == 20
    request = gateway.requests[0]
    assert request.tools == ()


@pytest.mark.asyncio
async def test_failure_returns_no_summary_and_does_not_advance_coverage() -> None:
    result = await ThreadCompressor(cast(Any, FakeGateway(fail=True))).compact(
        CompressionRequest(
            view=_view(5),
            provider="deepseek",
            model="deepseek/deepseek-v4-flash",
        )
    )

    assert result.status is CompressionStatus.FAILED
    assert result.summary is None
    assert result.error_code == "compression_failed"
