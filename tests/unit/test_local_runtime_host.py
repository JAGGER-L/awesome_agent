from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from awesome_agent.modeling.messages import AssistantMessage
from awesome_agent.modeling.provider import StructuredModelProvider
from awesome_agent.modeling.stream import ModelStreamEvent, TextDelta, TurnCompleted
from awesome_agent.modeling.turns import ModelRequest, ModelTurn, StopReason
from awesome_agent.surfaces.local_runtime_host import (
    ExecutionMode,
    LocalRuntimeHost,
    plan_execution_mode,
)


class FakeProvider(StructuredModelProvider):
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        yield TextDelta(text="hello")
        yield TextDelta(text=" world")
        yield TurnCompleted(
            turn=ModelTurn(
                assistant=AssistantMessage(content="hello world"),
                stop_reason=StopReason.COMPLETED,
                model="fake-model",
                provider="fake",
            )
        )


def test_simple_question_uses_lightweight_run() -> None:
    assert plan_execution_mode("What can you do?") is ExecutionMode.LIGHTWEIGHT


def test_coding_request_uses_coding_execution_mode() -> None:
    assert plan_execution_mode("build a simple html snake game") is ExecutionMode.CODING


def test_continue_resumes_last_resumable_run() -> None:
    assert (
        plan_execution_mode("continue", resumable_run_id="run-1")
        is ExecutionMode.RESUME
    )
    assert plan_execution_mode("继续", resumable_run_id="run-1") is ExecutionMode.RESUME


@pytest.mark.parametrize("content", ["hi", "What can you do?"])
def test_local_runtime_host_streams_lightweight_turn(content: str) -> None:
    host = LocalRuntimeHost(
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
    )
    thread = host.create_thread("Test")

    events = list(host.stream_turn(thread.id, content))

    assert [event.event.value for event in events] == [
        "turn.started",
        "message.created",
        "message.delta",
        "message.delta",
        "message.completed",
        "turn.completed",
    ]
    assert events[2].payload == {"text": "hello"}


def test_local_runtime_host_reports_coding_mode_boundary() -> None:
    host = LocalRuntimeHost(
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
    )
    thread = host.create_thread("Build")

    result = host.start_explicit_run(thread.id, "build a game")

    assert result["status"] == "planned"
    assert result["execution_mode"] == "coding"
    assert result["transport"] == "embedded"
