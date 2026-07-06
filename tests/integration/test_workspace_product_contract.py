from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from tests.type_helpers import test_settings

import awesome_agent.extensions.assembly as assembly_module
from awesome_agent.conversation.events import ConversationStreamEventKind
from awesome_agent.domain.enums import (
    ApprovalStatus,
    DispatchStatus,
    EventType,
    RunStatus,
)
from awesome_agent.modeling import (
    AssistantMessage,
    ModelRequest,
    ModelStreamEvent,
    ModelTurn,
    StopReason,
    StructuredModelProvider,
    TextDelta,
    ToolCall,
    ToolResultMessage,
    TurnCompleted,
)
from awesome_agent.sandbox.base import CommandRequest, CommandResult
from awesome_agent.sandbox.local import LocalSandbox
from awesome_agent.settings import Settings
from awesome_agent.surfaces.local_runtime_container import LocalRuntimeContainer

pytestmark = pytest.mark.integration

CUBE_SOURCE = """def cube(value: int) -> int:
    return value ** 3


if __name__ == "__main__":
    print(cube(3))
"""

TEST_SOURCE = """from cube import cube


def test_cube() -> None:
    assert cube(3) == 27
"""

PUBLIC_WORKSPACE_TOOLS = {"ReadFile", "WriteFile", "EditFile", "Bash", "Glob", "Grep"}
INTERNAL_COMPATIBILITY_TOOLS = {
    "repo.read",
    "repo.diff",
    "repo.apply_patch",
    "shell.execute",
}


def _settings(tmp_path: Path) -> Settings:
    return test_settings(local_state_dir=tmp_path / "state")


class RecordingLocalSandbox(LocalSandbox):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[CommandRequest] = []
        self.results: list[CommandResult] = []

    async def execute(self, request: CommandRequest) -> CommandResult:
        self.requests.append(request)
        result = await super().execute(request)
        self.results.append(result)
        return result


class CubeProductProvider(StructuredModelProvider):
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        async def events() -> AsyncIterator[ModelStreamEvent]:
            self.requests.append(request)
            tool_names = {tool.name for tool in request.tools}
            assert tool_names & PUBLIC_WORKSPACE_TOOLS == PUBLIC_WORKSPACE_TOOLS
            assert not tool_names & INTERNAL_COMPATIBILITY_TOOLS
            tool_results = [
                message
                for message in request.messages
                if isinstance(message, ToolResultMessage)
            ]
            if len(tool_results) == 0:
                yield TurnCompleted(
                    turn=ModelTurn(
                        assistant=AssistantMessage(
                            content="",
                            tool_calls=[
                                ToolCall(
                                    call_id="write-cube",
                                    name="WriteFile",
                                    arguments_json=json.dumps(
                                        {
                                            "path": "cube.py",
                                            "content": CUBE_SOURCE,
                                            "overwrite": False,
                                        }
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
            if len(tool_results) == 1:
                yield TurnCompleted(
                    turn=ModelTurn(
                        assistant=AssistantMessage(
                            content="",
                            tool_calls=[
                                ToolCall(
                                    call_id="write-test",
                                    name="WriteFile",
                                    arguments_json=json.dumps(
                                        {
                                            "path": "test_cube.py",
                                            "content": TEST_SOURCE,
                                            "overwrite": False,
                                        }
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
            if len(tool_results) == 2:
                yield TurnCompleted(
                    turn=ModelTurn(
                        assistant=AssistantMessage(
                            content="",
                            tool_calls=[
                                ToolCall(
                                    call_id="run-pytest",
                                    name="Bash",
                                    arguments_json=json.dumps(
                                        {
                                            "command": "pytest -q",
                                            "timeout_seconds": 30,
                                            "max_output_chars": 20000,
                                        }
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
            assert len(tool_results) == 3
            assert "passed" in tool_results[-1].content.lower()
            final = "Created cube.py and verified it with pytest."
            yield TextDelta(text=final)
            yield TurnCompleted(
                turn=ModelTurn(
                    assistant=AssistantMessage(content=final),
                    stop_reason=StopReason.COMPLETED,
                    model="fake-model",
                    provider="fake",
                )
            )

        return events()


class SensitiveWriteRetryProvider(StructuredModelProvider):
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        async def events() -> AsyncIterator[ModelStreamEvent]:
            self.requests.append(request)
            tool_results = [
                message
                for message in request.messages
                if isinstance(message, ToolResultMessage)
            ]
            if len(tool_results) == 0:
                yield TurnCompleted(
                    turn=ModelTurn(
                        assistant=AssistantMessage(
                            content="",
                            tool_calls=[
                                ToolCall(
                                    call_id="write-env-one",
                                    name="WriteFile",
                                    arguments_json=json.dumps(
                                        {
                                            "path": ".env",
                                            "content": "TOKEN=one\n",
                                            "overwrite": False,
                                        }
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
            if len(tool_results) == 1:
                yield TurnCompleted(
                    turn=ModelTurn(
                        assistant=AssistantMessage(
                            content="",
                            tool_calls=[
                                ToolCall(
                                    call_id="write-env-two",
                                    name="WriteFile",
                                    arguments_json=json.dumps(
                                        {
                                            "path": ".env",
                                            "content": "TOKEN=two\n",
                                            "overwrite": True,
                                        }
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
            assert len(tool_results) == 2
            yield TurnCompleted(
                turn=ModelTurn(
                    assistant=AssistantMessage(
                        content="Updated .env after approval reuse."
                    ),
                    stop_reason=StopReason.COMPLETED,
                    model="fake-model",
                    provider="fake",
                )
            )

        return events()


@pytest.mark.asyncio
async def test_local_runtime_creates_non_empty_python_file_and_runs_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = CubeProductProvider()
    sandbox = RecordingLocalSandbox()
    monkeypatch.setattr(
        assembly_module,
        "create_sandbox",
        lambda **_: sandbox,
    )
    container = LocalRuntimeContainer(
        settings=_settings(tmp_path),
        provider_factory=lambda _model: provider,
        default_model="fake-model",
        project_root=tmp_path,
    )
    try:
        thread = await container.conversations.create_thread(
            title="Cube",
            context_path=str(tmp_path),
        )
        stream = container.conversation_service.start_turn(
            thread_id=thread.id,
            content="write a Python file that calculates a cube and test it",
        )
        first = await anext(stream)
        run_id = UUID(str(first.payload["run_id"]))
        await container.worker_pump.drain_until_run_terminal_or_waiting(str(run_id))
        streamed = [first, *[event async for event in stream]]

        run = await container.runtime.get_run(run_id)
        approvals = await container.approvals.list_for_run(run_id)
        runtime_events = await container.runtime.list_events(run_id)
        messages = await container.conversations.list_messages(thread.id)

        assert run.status is RunStatus.COMPLETED
        assert run.dispatch_status is DispatchStatus.TERMINAL
        assert approvals == []
        assert (tmp_path / "cube.py").read_text(encoding="utf-8") == CUBE_SOURCE
        assert (tmp_path / "test_cube.py").read_text(encoding="utf-8") == TEST_SOURCE
        assert len(provider.requests) == 4
        assert len(sandbox.requests) == 1
        assert sandbox.requests[0].argv == ["pytest", "-q"]
        assert sandbox.results[0].exit_code == 0
        assert "passed" in sandbox.results[0].stdout

        tool_events = [
            event
            for event in runtime_events
            if event.event_type is EventType.TOOL_CALL_CREATED
        ]
        assert [event.payload["tool"] for event in tool_events] == [
            "WriteFile",
            "WriteFile",
            "Bash",
        ]
        assert all(event.payload["status"] == "completed" for event in tool_events)
        assert any(
            event.event is ConversationStreamEventKind.TOOL_COMPLETED
            and event.payload.get("tool") == "WriteFile"
            for event in streamed
        )
        assert not [
            event
            for event in streamed
            if event.event is ConversationStreamEventKind.APPROVAL_REQUIRED
        ]
        assert not [
            event
            for event in streamed
            if event.event is ConversationStreamEventKind.ERROR
        ]
        assert messages[-1].content == "Created cube.py and verified it with pytest."
        changed_files = messages[-1].metadata.get("changed_files")
        assert changed_files == [
            {"path": "cube.py", "status": "created"},
            {"path": "test_cube.py", "status": "created"},
        ]
        assert streamed[-1].event is ConversationStreamEventKind.TURN_COMPLETED
    finally:
        container.close()


@pytest.mark.asyncio
async def test_sensitive_write_reuses_same_run_approval_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = SensitiveWriteRetryProvider()
    sandbox = RecordingLocalSandbox()
    monkeypatch.setattr(
        assembly_module,
        "create_sandbox",
        lambda **_: sandbox,
    )
    container = LocalRuntimeContainer(
        settings=_settings(tmp_path),
        provider_factory=lambda _model: provider,
        default_model="fake-model",
        project_root=tmp_path,
    )
    try:
        thread = await container.conversations.create_thread(
            title="Sensitive write",
            context_path=str(tmp_path),
        )
        stream = container.conversation_service.start_turn(
            thread_id=thread.id,
            content="write a token file and correct it",
        )
        first = await anext(stream)
        run_id = UUID(str(first.payload["run_id"]))
        await container.worker_pump.drain_until_run_terminal_or_waiting(str(run_id))
        initial_events = [first]
        while True:
            event = await anext(stream)
            initial_events.append(event)
            if event.event is ConversationStreamEventKind.APPROVAL_REQUIRED:
                break
        await stream.aclose()

        waiting = await container.runtime.get_run(run_id)
        approvals = await container.approvals.list_for_run(run_id)
        assert waiting.status is RunStatus.PAUSED
        assert waiting.dispatch_status is DispatchStatus.WAITING
        assert len(approvals) == 1
        assert approvals[0].status is ApprovalStatus.PENDING
        assert any(
            event.event is ConversationStreamEventKind.APPROVAL_REQUIRED
            for event in initial_events
        )
        last_runtime_sequence = max(
            event.runtime_sequence or 0 for event in initial_events
        )

        await container.approvals.decide(
            approvals[0].id,
            approved=True,
            decided_by="test",
            reason="approved",
            now=datetime.now(UTC),
        )
        await container.dispatcher.requeue_after_approval(
            run_id=run_id,
            approval_id=approvals[0].id,
            reason="approval_decided",
        )
        continued = container.conversation_service.continue_turn(
            thread_id=thread.id,
            expected_run_id=run_id,
            after_sequence=last_runtime_sequence,
        )
        continued_first = await anext(continued)
        assert continued_first.event is ConversationStreamEventKind.TURN_CONTINUED

        await container.worker_pump.drain_until_run_terminal_or_waiting(str(run_id))
        continued_events = [continued_first, *[event async for event in continued]]

        completed = await container.runtime.get_run(run_id)
        all_approvals = await container.approvals.list_for_run(run_id)
        runtime_events = await container.runtime.list_events(run_id)
        approval_requested = [
            event
            for event in runtime_events
            if event.event_type is EventType.APPROVAL_REQUESTED
        ]
        approval_reused = [
            event
            for event in runtime_events
            if event.event_type is EventType.APPROVAL_REUSED
        ]
        tool_events = [
            event
            for event in runtime_events
            if event.event_type is EventType.TOOL_CALL_CREATED
        ]
        messages = await container.conversations.list_messages(thread.id)

        assert completed.status is RunStatus.COMPLETED
        assert completed.dispatch_status is DispatchStatus.TERMINAL
        assert len(all_approvals) == 1
        assert len(approval_requested) == 1
        assert len(approval_reused) == 1
        assert approval_reused[0].payload["approval_id"] == str(approvals[0].id)
        assert approval_reused[0].payload["status"] == "approved"
        assert [event.payload["tool"] for event in tool_events] == [
            "WriteFile",
            "WriteFile",
        ]
        assert (tmp_path / ".env").read_text(encoding="utf-8") == "TOKEN=two\n"
        assert len(provider.requests) == 3
        assert messages[-1].content == "Updated .env after approval reuse."
        assert not [
            event
            for event in continued_events
            if event.event is ConversationStreamEventKind.APPROVAL_REQUIRED
        ]
        assert continued_events[-1].event is ConversationStreamEventKind.TURN_COMPLETED
    finally:
        container.close()
