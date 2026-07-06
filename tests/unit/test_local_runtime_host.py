from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from tests.type_helpers import test_settings

from awesome_agent.conversation.events import ConversationStreamEventKind
from awesome_agent.conversation.models import ThreadMessageRole
from awesome_agent.domain.enums import (
    AgentKind,
    AgentStatus,
    DispatchStatus,
    EventType,
    ExecutionKind,
    RunIntent,
    RunStatus,
)
from awesome_agent.domain.models import Agent, Run
from awesome_agent.memory.models import MemoryTarget
from awesome_agent.modeling.messages import AssistantMessage
from awesome_agent.modeling.provider import StructuredModelProvider
from awesome_agent.modeling.stream import ModelStreamEvent, TextDelta, TurnCompleted
from awesome_agent.modeling.tools import ToolCall
from awesome_agent.modeling.turns import ModelRequest, ModelTurn, ModelUsage, StopReason
from awesome_agent.runtime.graphs import CONVERSATION_TURN_ROUTE
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


class SequenceProvider(StructuredModelProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        self.calls += 1
        if self.calls <= 3:
            raise RuntimeError("model failed")
            yield TextDelta(text="unreachable")  # pragma: no cover
        yield TextDelta(text="recovered")
        yield TurnCompleted(
            turn=ModelTurn(
                assistant=AssistantMessage(content="recovered"),
                stop_reason=StopReason.COMPLETED,
                model="fake-model",
                provider="fake",
            )
        )


class SlowProvider(StructuredModelProvider):
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        yield TextDelta(text="first")
        await asyncio.sleep(0.35)
        yield TextDelta(text=" second")
        yield TurnCompleted(
            turn=ModelTurn(
                assistant=AssistantMessage(content="first second"),
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
            assert any(tool.name == "WriteFile" for tool in request.tools)
            yield TurnCompleted(
                turn=ModelTurn(
                    assistant=AssistantMessage(
                        content="",
                        tool_calls=[
                            ToolCall(
                                call_id="call-write",
                                name="WriteFile",
                                arguments_json=(
                                    '{"path":"calculate_1_plus_1.py",'
                                    '"content":"result = 1 + 1\\nprint(result)\\n"}'
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


class BlockingBeforeFirstEventProvider(StructuredModelProvider):
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        self.started.set()
        self.release.wait(timeout=5)
        yield TurnCompleted(
            turn=ModelTurn(
                assistant=AssistantMessage(content="late"),
                stop_reason=StopReason.COMPLETED,
                model="fake-model",
                provider="fake",
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


def test_local_runtime_host_default_path_uses_process_model_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test")
    monkeypatch.setenv("AWESOME_AGENT_MODEL_WORKER_FAKE", "echo")
    host = LocalRuntimeHost(
        settings=test_settings(
            local_state_dir=tmp_path / "state",
            deepseek_api_key="test-key",
        ),
        default_model="deepseek-v4-pro",
    )
    thread = host.create_thread(title="Chat", context_path=str(tmp_path))

    events = list(host.stream_turn(thread.id, "hi"))
    [run] = host.list_thread_runs(thread.id)
    host.close()

    assert any(
        event.event is ConversationStreamEventKind.MESSAGE_DELTA
        and event.payload == {"text": "hello"}
        for event in events
    )
    assert run["status"] == "completed"


def test_local_runtime_host_default_process_backend_accepts_consecutive_turns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "test")
    monkeypatch.setenv("AWESOME_AGENT_MODEL_WORKER_FAKE", "echo")
    host = LocalRuntimeHost(
        settings=test_settings(
            local_state_dir=tmp_path / "state",
            deepseek_api_key="test-key",
        ),
        default_model="deepseek-v4-pro",
    )
    thread = host.create_thread(title="Chat", context_path=str(tmp_path))
    second_events: list[ConversationStreamEventKind] = []
    errors: list[BaseException] = []
    collector: threading.Thread | None = None

    try:
        first_events = list(host.stream_turn(thread.id, "first"))

        def collect_second() -> None:
            try:
                second_events.extend(
                    event.event for event in host.stream_turn(thread.id, "second")
                )
            except BaseException as error:
                errors.append(error)

        collector = threading.Thread(target=collect_second, daemon=True)
        collector.start()
        collector.join(timeout=2)

        assert not collector.is_alive()
        assert errors == []
        assert ConversationStreamEventKind.MESSAGE_DELTA in [
            event.event for event in first_events
        ]
        assert ConversationStreamEventKind.MESSAGE_DELTA in second_events
        assert second_events[-1] is ConversationStreamEventKind.TURN_COMPLETED
        assert {
            run["status"] for run in host.list_thread_runs(thread.id)
        } == {"completed"}
    finally:
        for run in host.list_thread_runs(thread.id):
            if run["status"] not in {"completed", "failed", "cancelled"}:
                host.cancel(str(run["id"]))
        if collector is not None and collector.is_alive():
            collector.join(timeout=2)
        host.close()


def test_local_runtime_host_yields_delta_before_worker_finishes(
    tmp_path: Path,
) -> None:
    host = LocalRuntimeHost(
        settings=test_settings(local_state_dir=tmp_path / "state"),
        provider_factory=lambda _model: SlowProvider(),
        default_model="fake-model",
    )
    thread = host.create_thread("Live")

    started_at = time.monotonic()
    iterator = iter(host.stream_turn(thread.id, "hi"))
    first = next(iterator)
    second = next(iterator)
    third = next(iterator)
    elapsed = time.monotonic() - started_at

    assert first.event is ConversationStreamEventKind.TURN_STARTED
    assert second.event is ConversationStreamEventKind.MESSAGE_CREATED
    assert third.event is ConversationStreamEventKind.MESSAGE_DELTA
    assert third.payload["text"] == "first"
    assert elapsed < 0.25

    remaining = list(iterator)
    assert remaining[-1].event is ConversationStreamEventKind.TURN_COMPLETED


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


def test_local_runtime_host_accepts_next_message_after_failed_turn(
    tmp_path: Path,
) -> None:
    provider = SequenceProvider()
    host = LocalRuntimeHost(
        settings=test_settings(local_state_dir=tmp_path / "state"),
        provider_factory=lambda _model: provider,
        default_model="fake-model",
    )
    thread = host.create_thread(title="Chat", context_path=str(tmp_path))

    failed_events = list(host.stream_turn(thread.id, "first"))
    recovered_events = list(host.stream_turn(thread.id, "second"))

    assert failed_events[-1].event is ConversationStreamEventKind.ERROR
    assert recovered_events[-1].event is ConversationStreamEventKind.TURN_COMPLETED
    assert any(
        event.event is ConversationStreamEventKind.MESSAGE_DELTA
        and event.payload == {"text": "recovered"}
        for event in recovered_events
    )


def test_local_runtime_host_cancel_terminalizes_when_provider_blocks_loop(
    tmp_path: Path,
) -> None:
    provider = BlockingBeforeFirstEventProvider()
    host = LocalRuntimeHost(
        settings=test_settings(local_state_dir=tmp_path / "state"),
        provider_factory=lambda _model: provider,
        default_model="fake-model",
    )
    thread = host.create_thread(title="Chat", context_path=str(tmp_path))
    iterator = iter(host.stream_turn(thread.id, "hi"))
    started = next(iterator)
    next(iterator)
    run_id = str(started.payload["run_id"])
    assert provider.started.wait(timeout=1)

    host.cancel(run_id)

    events: list[ConversationStreamEventKind] = []
    errors: list[BaseException] = []

    def collect_remaining() -> None:
        try:
            events.extend(event.event for event in iterator)
        except BaseException as error:
            errors.append(error)

    collector = threading.Thread(target=collect_remaining, daemon=True)
    collector.start()
    collector.join(timeout=1)
    finished_before_provider_release = not collector.is_alive()
    provider.release.set()
    if collector.is_alive():
        collector.join(timeout=2)
    stored = asyncio.run(host.runtime_repository.get_run(UUID(run_id)))
    host.close()

    assert finished_before_provider_release
    assert errors == []
    assert events[-1] is ConversationStreamEventKind.TURN_COMPLETED
    assert stored.status is RunStatus.CANCELLED


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


def test_local_runtime_host_continue_turn_uses_after_sequence(tmp_path: Path) -> None:
    host = LocalRuntimeHost(
        settings=test_settings(local_state_dir=tmp_path / "state"),
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
    )
    thread = host.create_thread("Catchup")
    events = list(host.stream_turn(thread.id, "hi"))
    run_id = next(
        event.run_id for event in events if event.event.value == "turn.started"
    )
    first_delta = next(
        event for event in events if event.event.value == "message.delta"
    )

    catchup = list(
        host.continue_turn(
            thread.id,
            expected_run_id=str(run_id),
            after_sequence=first_delta.runtime_sequence or 0,
        )
    )

    assert all(
        event.runtime_sequence is None
        or event.runtime_sequence > (first_delta.runtime_sequence or 0)
        for event in catchup
    )


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
        "memory": {"local_enabled": False, "provider": None},
        "skill_ids": ["repository-inspection"],
    }


def test_local_runtime_host_scans_project_skills_once_at_startup(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    _write_skill(project, "repository-inspection", "Use bounded repository reads.")
    host = LocalRuntimeHost(
        settings=test_settings(local_state_dir=tmp_path / "state"),
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
        project_root=project,
    )

    skills = cast(list[dict[str, object]], host.list_skills()["items"])
    assert [skill["id"] for skill in skills] == ["repository-inspection"]

    _write_skill(project, "new-skill", "New instructions.")

    skills = cast(list[dict[str, object]], host.list_skills()["items"])
    assert [skill["id"] for skill in skills] == ["repository-inspection"]


def test_local_runtime_host_pins_catalog_and_injects_staged_skill_context(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    _write_skill(project, "repository-inspection", "Prefer repo.search first.")
    provider = CaptureRequestProvider()
    host = LocalRuntimeHost(
        settings=test_settings(local_state_dir=tmp_path / "state"),
        provider_factory=lambda _model: provider,
        default_model="fake-model",
        project_root=project,
    )
    thread = host.create_thread("Skills", context_path=str(project))

    list(
        host.stream_turn(
            thread.id,
            "inspect",
            skill_ids=("repository-inspection",),
        )
    )

    [run] = host.list_thread_runs(thread.id)
    assert run["extension_catalog_version"] == (
        host._container.extension_runtime.catalog.version
    )
    [user, _assistant] = host.list_thread_messages(thread.id)
    metadata = cast(dict[str, object], user["metadata"])
    assert metadata["extension_catalog_version"] == run["extension_catalog_version"]
    assert provider.requests
    assert any(
        "Prefer repo.search first." in message.content
        for message in provider.requests[0].messages
    )


def test_local_runtime_host_config_summary_matches_http_status_fields(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "awesome-agent.yaml").write_text("skills: []\n", encoding="utf-8")
    host = LocalRuntimeHost(
        settings=test_settings(local_state_dir=tmp_path / "state"),
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
        project_root=project,
    )

    summary = host.config_summary()

    assert summary["deepseek_base_url"] == "https://api.deepseek.com"
    assert summary["deepseek_api_key_env"] == "AWESOME_AGENT_DEEPSEEK_API_KEY"
    assert summary["project_config_path"] == str(project / "awesome-agent.yaml")
    assert summary["project_config_exists"] is True
    assert summary["project_env_path"] == str(project / ".env")
    assert summary["project_env_exists"] is False


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
    assert any(event.payload.get("tool") == "WriteFile" for event in tool_events)
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


def test_local_runtime_host_lists_real_memory_entries(tmp_path: Path) -> None:
    host = LocalRuntimeHost(
        settings=test_settings(
            local_state_dir=tmp_path / "state",
            builtin_memory_enabled=True,
        ),
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
    )

    added = asyncio.run(
        host._container.memory_service.add(
            target=MemoryTarget.USER,
            content="Prefer concise engineering updates.",
            source="explicit_user_request",
            run_id=None,
            agent_id=None,
        )
    )

    assert added.status == "added"
    assert added.entry is not None
    assert host.memory_entries("user") == [
        {
            "id": added.entry.id,
            "target": "user",
            "content": "Prefer concise engineering updates.",
            "created_at": None,
        }
    ]


def _write_skill(project: Path, skill_id: str, instructions: str) -> None:
    skill_dir = project / "skills" / skill_id
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                f"id: {skill_id}",
                'version: "1"',
                "risk_level: low",
                "requested_tools:",
                "  - repo.search",
                "---",
                instructions,
                "",
            ]
        ),
        encoding="utf-8",
    )


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


def test_local_runtime_host_reconciles_stale_cancel_requested_run_on_startup(
    tmp_path: Path,
) -> None:
    settings = test_settings(local_state_dir=tmp_path / "state")
    first = LocalRuntimeHost(
        settings=settings,
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
    )
    thread = first.create_thread("Stale", context_path=str(tmp_path))
    worker_id = uuid4()
    run = Run(
        goal="stale",
        intent=RunIntent.CONVERSATION,
        execution_kind=ExecutionKind.CONVERSATION,
        runtime_route=CONVERSATION_TURN_ROUTE,
        status=RunStatus.RUNNING,
        dispatch_status=DispatchStatus.EXECUTING,
        working_directory=tmp_path,
        current_worker_id=worker_id,
        current_worker_name="old-worker",
        fencing_token=1,
        attempt=1,
        lease_acquired_at=datetime.now(UTC) - timedelta(minutes=10),
        lease_expires_at=datetime.now(UTC) - timedelta(minutes=5),
        heartbeat_at=datetime.now(UTC) - timedelta(minutes=10),
        cancel_requested_at=datetime.now(UTC) - timedelta(minutes=5),
        cancel_requested_by="local-surface",
        cancel_reason="user_requested",
    )
    leader = Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="fake-model",
        status=AgentStatus.RUNNING,
    )
    asyncio.run(first.runtime_repository.create_run(run, leader))
    asyncio.run(
        first.runtime_repository.append_event(
            run_id=run.id,
            event_type=EventType.RUN_CREATED,
            payload={
                "thread_id": thread.id,
                "goal": "stale",
                "model": "fake-model",
                "runtime_route": CONVERSATION_TURN_ROUTE,
            },
            agent_id=leader.id,
        )
    )
    first.close()

    second = LocalRuntimeHost(
        settings=settings,
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
    )
    stored = asyncio.run(second.runtime_repository.get_run(run.id))
    second.close()

    assert stored.status is RunStatus.CANCELLED
    assert stored.dispatch_status is DispatchStatus.TERMINAL
    assert stored.current_worker_id is None
