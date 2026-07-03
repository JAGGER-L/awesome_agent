from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import pytest

from awesome_agent.conversation.models import ThreadMessageRole
from awesome_agent.modeling.messages import AssistantMessage
from awesome_agent.modeling.provider import StructuredModelProvider
from awesome_agent.modeling.stream import ModelStreamEvent, TextDelta, TurnCompleted
from awesome_agent.modeling.turns import ModelRequest, ModelTurn, StopReason
from awesome_agent.persistence.conversations import InMemoryConversationRepository
from awesome_agent.settings import Settings
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


def test_simple_question_uses_leader_turn() -> None:
    assert plan_execution_mode("What can you do?") is ExecutionMode.LEADER


def test_coding_request_uses_coding_execution_mode() -> None:
    assert plan_execution_mode("build a simple html snake game") is ExecutionMode.CODING


def test_continue_resumes_last_resumable_run() -> None:
    assert (
        plan_execution_mode("continue", resumable_run_id="run-1")
        is ExecutionMode.RESUME
    )
    assert plan_execution_mode("继续", resumable_run_id="run-1") is ExecutionMode.RESUME


@pytest.mark.parametrize("content", ["hi", "What can you do?"])
def test_local_runtime_host_streams_leader_turn(content: str) -> None:
    host = LocalRuntimeHost(
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
        repository=InMemoryConversationRepository(),
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
    assert "run_id" in events[0].payload
    assert events[2].payload == {
        "text": "hello",
        "run_id": events[0].payload["run_id"],
    }


def test_local_runtime_host_reports_coding_mode_boundary() -> None:
    host = LocalRuntimeHost(
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
        repository=InMemoryConversationRepository(),
    )
    thread = host.create_thread("Build")

    result = host.start_explicit_run(thread.id, "build a game")

    assert result["status"] == "planned"
    assert result["execution_mode"] == "coding"
    assert result["transport"] == "embedded"


def test_local_runtime_host_forwards_turn_options() -> None:
    host = LocalRuntimeHost(
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
        repository=InMemoryConversationRepository(),
    )
    thread = host.create_thread("Options")

    list(
        host.stream_turn(
            thread.id,
            "hi",
            model="alternate-model",
            thinking="off",
            memory={"local_enabled": True},
            skill_ids=("repository-inspection",),
        )
    )

    [user, _assistant] = host.list_thread_messages(thread.id)
    assert user["metadata"]["turn_options"] == {
        "model": "alternate-model",
        "thinking": "off",
        "memory": {"local_enabled": True},
        "skill_ids": ["repository-inspection"],
    }


def test_local_runtime_host_thread_summary_includes_changed_files() -> None:
    host = LocalRuntimeHost(
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
        repository=InMemoryConversationRepository(),
    )
    thread = host.create_thread("Snake")
    asyncio.run(
        host.repository.append_message(
            thread_id=UUID(thread.id),
            role=ThreadMessageRole.ASSISTANT,
            content="Done.",
            metadata={
                "changed_files": [
                    {
                        "path": "/mnt/user-data/workspace/snake.html",
                        "status": "created",
                    }
                ]
            },
        )
    )

    [summary] = host.list_threads()

    assert summary.changed_file_count == 1
    assert summary.latest_changed_files[0].visible_path == "snake.html"


def test_local_runtime_host_persists_threads_across_instances(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, local_state_dir=tmp_path / "state")
    first = LocalRuntimeHost(
        settings=settings,
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
    )
    thread = first.create_thread(
        "Durable",
        default_model="fake-model",
        thinking_mode="off",
        local_memory_enabled=True,
        provider_memory="mem0",
    )
    list(first.stream_turn(thread.id, "hi"))
    first.close()

    second = LocalRuntimeHost(
        settings=settings,
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
    )
    [restored] = second.list_threads()
    messages = second.list_thread_messages(restored.id)
    second.close()

    assert restored.id == thread.id
    assert restored.default_model == "fake-model"
    assert restored.thinking_mode == "off"
    assert restored.local_memory_enabled is True
    assert restored.provider_memory == "mem0"
    assert [message["content"] for message in messages] == ["hi", "hello world"]
