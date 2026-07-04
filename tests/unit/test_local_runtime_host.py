from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast
from uuid import UUID

import pytest
from tests.type_helpers import test_settings

from awesome_agent.conversation.events import ConversationStreamEventKind
from awesome_agent.conversation.models import ThreadMessageRole
from awesome_agent.domain.enums import EventType, RunStatus
from awesome_agent.modeling.messages import AssistantMessage
from awesome_agent.modeling.provider import StructuredModelProvider
from awesome_agent.modeling.stream import ModelStreamEvent, TextDelta, TurnCompleted
from awesome_agent.modeling.tools import ToolCall
from awesome_agent.modeling.turns import ModelRequest, ModelTurn, ModelUsage, StopReason
from awesome_agent.surfaces.local_runtime_host import LocalRuntimeHost


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


class CaptureRequestProvider(StructuredModelProvider):
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        self.requests.append(request)
        yield TurnCompleted(
            turn=ModelTurn(
                assistant=AssistantMessage(content="done"),
                stop_reason=StopReason.COMPLETED,
                model="fake-model",
                provider="fake",
            )
        )


class FailingProvider(StructuredModelProvider):
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        raise RuntimeError("model failed")
        yield TurnCompleted(  # pragma: no cover
            turn=ModelTurn(
                assistant=AssistantMessage(content="unreachable"),
                stop_reason=StopReason.COMPLETED,
                model="fake-model",
                provider="fake",
            )
        )


class PatchToolProvider(StructuredModelProvider):
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        self.requests.append(request)
        if len(self.requests) == 1:
            assert any(tool.name == "repo.apply_patch" for tool in request.tools)
            yield TurnCompleted(
                turn=ModelTurn(
                    assistant=AssistantMessage(
                        content="",
                        tool_calls=[
                            ToolCall(
                                call_id="call-write",
                                name="repo.apply_patch",
                                arguments_json=(
                                    '{"patch":"--- /dev/null\\n'
                                    "+++ b/calculate_1_plus_1.py\\n"
                                    "@@ -0,0 +1,2 @@\\n"
                                    "+result = 1 + 1\\n"
                                    '+print(result)\\n"}'
                                ),
                            )
                        ],
                    ),
                    stop_reason=StopReason.TOOL_CALLS,
                    model="fake-model",
                    provider="fake",
                )
            )
            return
        assert any(message.role == "tool" for message in request.messages)
        yield TextDelta(text="Created calculate_1_plus_1.py.")
        yield TurnCompleted(
            turn=ModelTurn(
                assistant=AssistantMessage(content="Created calculate_1_plus_1.py."),
                stop_reason=StopReason.COMPLETED,
                model="fake-model",
                provider="fake",
                usage=ModelUsage(input_tokens=10, output_tokens=5, reasoning_tokens=1),
            )
        )


@pytest.mark.parametrize("content", ["hi", "What can you do?"])
def test_local_runtime_host_streams_leader_turn(
    tmp_path: Path,
    content: str,
) -> None:
    host = LocalRuntimeHost(
        settings=test_settings(local_state_dir=tmp_path / "state"),
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
    assert "run_id" in events[0].payload
    assert str(events[2].run_id) == events[0].payload["run_id"]
    assert events[2].runtime_sequence is not None
    assert events[2].payload == {"text": "hello"}


def test_local_runtime_host_stream_turn_creates_durable_conversation_run(
    tmp_path: Path,
) -> None:
    host = LocalRuntimeHost(
        settings=test_settings(local_state_dir=tmp_path / "state"),
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
    )
    thread = host.create_thread(title="Chat", context_path=str(tmp_path))

    events = list(host.stream_turn(thread.id, "hi"))

    assert any(event.event.value == "turn.started" for event in events)
    runs = host.list_thread_runs(thread.id)
    assert runs
    assert runs[0]["runtime_route"] == "conversation-turn"
    assert runs[0]["status"] == "completed"


def test_local_runtime_host_uses_worker_pump_for_user_message_turn(
    tmp_path: Path,
) -> None:
    host = LocalRuntimeHost(
        settings=test_settings(local_state_dir=tmp_path / "state"),
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
    )
    thread = host.create_thread(title="Chat", context_path=str(tmp_path))

    list(host.stream_turn(thread.id, "hi"))

    [run] = host.list_thread_runs(thread.id)
    assert run["status"] == "completed"
    assert run["runtime_route"] == "conversation-turn"
    runtime_events = asyncio.run(
        host.runtime_repository.list_events(UUID(str(run["id"])))
    )
    runtime_event_types = [event.event_type for event in runtime_events]
    assert EventType.DISPATCH_CLAIMED in runtime_event_types
    assert EventType.GRAPH_COMPLETED in runtime_event_types


def test_local_runtime_host_stream_turn_returns_error_after_retry_exhaustion(
    tmp_path: Path,
) -> None:
    host = LocalRuntimeHost(
        settings=test_settings(local_state_dir=tmp_path / "state"),
        provider_factory=lambda _model: FailingProvider(),
        default_model="fake-model",
    )
    thread = host.create_thread(title="Chat", context_path=str(tmp_path))

    events = list(host.stream_turn(thread.id, "hi"))

    assert events[-1].event.value == "error"
    assert events[-1].payload["message"] == "model failed"
    [run] = host.list_thread_runs(thread.id)
    assert run["status"] == "recovery_required"
    runtime_events = asyncio.run(
        host.runtime_repository.list_events(UUID(str(run["id"])))
    )
    runtime_event_types = [event.event_type for event in runtime_events]
    assert EventType.RUN_STATUS_CHANGED in runtime_event_types
    assert EventType.DISPATCH_RECOVERY_REQUIRED in runtime_event_types


def test_local_runtime_host_rejects_conversation_repository_injection(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="no longer accepts"):
        LocalRuntimeHost(
            settings=test_settings(local_state_dir=tmp_path / "state"),
            provider_factory=lambda _model: FakeProvider(),
            default_model="fake-model",
            repository=object(),
        )


def test_local_runtime_host_last_resumable_run_uses_persisted_runtime_status(
    tmp_path: Path,
) -> None:
    host = LocalRuntimeHost(
        settings=test_settings(local_state_dir=tmp_path / "state"),
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
    )
    thread = host.create_thread("Resume")
    list(host.stream_turn(thread.id, "hi"))
    [created] = host.list_thread_runs(thread.id)
    run = asyncio.run(host.runtime_repository.get_run(UUID(str(created["id"]))))

    asyncio.run(
        host.runtime_repository.update_run(
            run.model_copy(update={"status": RunStatus.WAITING})
        )
    )

    resumable = host.last_resumable_run(thread.id)
    assert resumable is not None
    assert resumable["id"] == created["id"]
    assert resumable["status"] == "waiting"


def test_local_runtime_host_forwards_turn_options(tmp_path: Path) -> None:
    host = LocalRuntimeHost(
        settings=test_settings(local_state_dir=tmp_path / "state"),
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
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
    metadata = cast(dict[str, object], user["metadata"])
    assert metadata["turn_options"] == {
        "model": "alternate-model",
        "thinking": "off",
        "memory": {"local_enabled": True},
        "skill_ids": ["repository-inspection"],
    }


def test_local_runtime_host_passes_thinking_mode_into_model_request(
    tmp_path: Path,
) -> None:
    provider = CaptureRequestProvider()
    host = LocalRuntimeHost(
        settings=test_settings(local_state_dir=tmp_path / "state"),
        provider_factory=lambda _model: provider,
        default_model="fake-model",
    )
    thread = host.create_thread("Options")

    list(host.stream_turn(thread.id, "hi", thinking="off"))

    assert provider.requests
    assert provider.requests[0].thinking == "off"


def test_local_runtime_host_executes_leader_tools_in_thread_workspace(
    tmp_path: Path,
) -> None:
    provider = PatchToolProvider()
    host = LocalRuntimeHost(
        settings=test_settings(local_state_dir=tmp_path / "state"),
        provider_factory=lambda _model: provider,
        default_model="fake-model",
    )
    thread = host.create_thread("Workspace", context_path=str(tmp_path))

    events = list(
        host.stream_turn(
            thread.id,
            "\u521b\u5efa\u4e00\u4e2a\u7528\u4e8e\u8ba1\u7b971+1\u7684python\u6587\u4ef6",
        )
    )

    target = tmp_path / "calculate_1_plus_1.py"
    assert target.read_text(encoding="utf-8") == "result = 1 + 1\nprint(result)\n"
    assert len(provider.requests) == 2
    tool_events = [
        event
        for event in events
        if event.event is ConversationStreamEventKind.TOOL_COMPLETED
    ]
    assert any(event.payload.get("tool") == "repo.apply_patch" for event in tool_events)
    assert any(
        event.payload.get("changed_files")
        == [{"path": "calculate_1_plus_1.py", "status": "created"}]
        for event in events
    )


def test_local_runtime_host_usage_summary_reads_persisted_turn_usage(
    tmp_path: Path,
) -> None:
    provider = PatchToolProvider()
    host = LocalRuntimeHost(
        settings=test_settings(local_state_dir=tmp_path / "state"),
        provider_factory=lambda _model: provider,
        default_model="fake-model",
    )
    thread = host.create_thread("Usage", context_path=str(tmp_path))

    list(host.stream_turn(thread.id, "create file"))

    assert host.usage_summary(thread.id, None) == {
        "thread_id": thread.id,
        "run_id": None,
        "input_tokens": 10,
        "output_tokens": 5,
        "reasoning_tokens": 1,
        "total_tokens": 15,
        "budget": "-",
    }


def test_local_runtime_host_extracts_local_memory_facts(tmp_path: Path) -> None:
    host = LocalRuntimeHost(
        settings=test_settings(local_state_dir=tmp_path / "state"),
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
    )
    thread = host.create_thread("Memory")

    list(
        host.stream_turn(
            thread.id,
            "\u6211\u76ee\u524d\u5728\u5b66\u4e60python",
            memory={"local_enabled": True},
        )
    )

    assert host.local_memory_facts(thread.id) == [
        "\u7528\u6237\u76ee\u524d\u5728\u5b66\u4e60python\u3002"
    ]


def test_local_runtime_host_thread_summary_includes_changed_files(
    tmp_path: Path,
) -> None:
    host = LocalRuntimeHost(
        settings=test_settings(local_state_dir=tmp_path / "state"),
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
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
    settings = test_settings(local_state_dir=tmp_path / "state")
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


def test_local_runtime_host_persists_runtime_runs_across_instances(
    tmp_path: Path,
) -> None:
    settings = test_settings(local_state_dir=tmp_path / "state")
    first = LocalRuntimeHost(
        settings=settings,
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
    )
    thread = first.create_thread("Durable", context_path=str(tmp_path))
    list(first.stream_turn(thread.id, "hi"))
    [created] = first.list_thread_runs(thread.id)
    first.close()

    second = LocalRuntimeHost(
        settings=settings,
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
    )
    runs = second.list_thread_runs(thread.id)
    second.close()

    assert runs == [created]
