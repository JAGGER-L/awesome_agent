from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest

from awesome_agent.application.commands import CommandIntent, CommandName, CommandStatus
from awesome_agent.application.composition import compose_local_application
from awesome_agent.application.contracts import (
    ApplicationResult,
    InitializeStatus,
    ThreadListQuery,
    ThreadReadQuery,
)
from awesome_agent.core.events import CollectingEventSink, EventType
from awesome_agent.modeling import (
    AssistantMessage,
    GatewayEvent,
    ModelRequest,
    ModelTurn,
    SelectedModel,
    StopReason,
    TextDelta,
    ToolCall,
    TurnCompleted,
)


class FakeGateway:
    def __init__(self, provider: str, model: str) -> None:
        self.provider = provider
        self.model = model
        self.requests: list[ModelRequest] = []

    async def stream(
        self,
        selected: SelectedModel,
        request: ModelRequest,
    ) -> AsyncIterator[GatewayEvent]:
        self.requests.append(request)
        assert selected.provider == self.provider
        yield TextDelta(text="done")
        yield TurnCompleted(
            turn=ModelTurn(
                provider=selected.provider,
                model=selected.model,
                assistant=AssistantMessage(content="done"),
                stop_reason=StopReason.COMPLETED,
            )
        )

    async def complete(
        self,
        selected: SelectedModel,
        request: ModelRequest,
    ) -> ModelTurn:
        completed = [
            event.turn
            async for event in self.stream(selected, request)
            if isinstance(event, TurnCompleted)
        ]
        return completed[0]


class ExecuteGateway(FakeGateway):
    def __init__(self, provider: str, model: str, command: str) -> None:
        super().__init__(provider, model)
        self.command = command
        self.calls = 0

    async def stream(
        self,
        selected: SelectedModel,
        request: ModelRequest,
    ) -> AsyncIterator[GatewayEvent]:
        self.requests.append(request)
        self.calls += 1
        if self.calls == 1:
            yield TurnCompleted(
                turn=ModelTurn(
                    provider=selected.provider,
                    model=selected.model,
                    assistant=AssistantMessage(
                        tool_calls=(
                            ToolCall(
                                call_id="call_execute",
                                name="execute",
                                arguments_json=json.dumps({"command": self.command}),
                            ),
                        )
                    ),
                    stop_reason=StopReason.TOOL_CALLS,
                )
            )
            return
        yield TextDelta(text="done")
        yield TurnCompleted(
            turn=ModelTurn(
                provider=selected.provider,
                model=selected.model,
                assistant=AssistantMessage(content="done"),
                stop_reason=StopReason.COMPLETED,
            )
        )


def _unwrap[T](result: ApplicationResult[T]) -> T:
    assert result.ok is True
    assert result.value is not None
    return result.value


async def _wait_for_thread(
    application: object,
    thread_id: str,
    *,
    entries: int,
) -> object:
    for _ in range(200):
        view = _unwrap(
            await application.read_thread(ThreadReadQuery(thread_id=thread_id))
        ).view
        state = _unwrap(await application.get_state())
        if len(view.entries) >= entries and state.active_operation_id is None:
            return view
        await asyncio.sleep(0.01)
    raise AssertionError("foreground operation did not complete")


async def _wait_for_interaction(application: object) -> str:
    for _ in range(200):
        state = _unwrap(await application.get_state())
        if state.pending_interaction_id is not None:
            return state.pending_interaction_id
        await asyncio.sleep(0.01)
    raise AssertionError("execute interaction was not requested")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "command_runs", "terminal_event"),
    [
        ("allow_once", True, EventType.TOOL_COMPLETED),
        ("deny", False, EventType.TOOL_FAILED),
    ],
)
async def test_composed_agent_execute_waits_for_application_decision(
    tmp_path: Path,
    decision: str,
    command_runs: bool,
    terminal_event: EventType,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    marker = workspace / "agent-executed.txt"
    command = (
        f'"{sys.executable}" -c "from pathlib import Path; '
        "Path('agent-executed.txt').write_text('ran', encoding='utf-8')\""
    )

    def gateway_factory(provider: str, model: str) -> object:
        return cast(object, ExecuteGateway(provider, model, command))

    sink = CollectingEventSink()
    application = await compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=sink,
        environ={"DEEPSEEK_API_KEY": "fake-key"},
        gateway_factory=cast(object, gateway_factory),
    )
    initialized = _unwrap(await application.initialize())
    assert initialized.interaction_id is not None
    _unwrap(await application.respond_interaction(initialized.interaction_id, "trust"))
    created = _unwrap(
        await application.execute_command(CommandIntent(name=CommandName.NEW))
    )
    thread_id = str(created.data["thread_id"])

    _unwrap(await application.submit_turn(thread_id, "run the command"))
    interaction_id = await _wait_for_interaction(application)
    assert not marker.exists()

    resolved = _unwrap(await application.respond_interaction(interaction_id, decision))
    assert resolved.accepted
    view = await _wait_for_thread(application, thread_id, entries=2)

    assert marker.exists() is command_runs
    assert view.entries[-1].content == "done"
    assert any(
        event.event_type is EventType.INTERACTION_REQUIRED for event in sink.events
    )
    event_types = [event.event_type for event in sink.events]
    assert event_types.count(EventType.TOOL_STARTED) == 1
    assert event_types.count(terminal_event) == 1
    await application.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "model", "secret"),
    [
        ("deepseek", "deepseek/deepseek-v4-flash", "DEEPSEEK_API_KEY"),
        ("kimi", "kimi/kimi-k2.6", "MOONSHOT_API_KEY"),
    ],
)
async def test_fresh_home_trust_turn_direct_and_restart(
    tmp_path: Path,
    provider: str,
    model: str,
    secret: str,
) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    gateways: list[FakeGateway] = []

    def gateway_factory(selected_provider: str, selected_model: str) -> object:
        gateway = FakeGateway(selected_provider, selected_model)
        gateways.append(gateway)
        return cast(object, gateway)

    sink = CollectingEventSink()
    application = await compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=sink,
        environ={secret: "fake-key"},
        gateway_factory=cast(object, gateway_factory),
    )

    pending = _unwrap(await application.initialize())
    assert pending.status is InitializeStatus.TRUST_REQUIRED
    assert pending.interaction_id is not None
    trusted = _unwrap(
        await application.respond_interaction(pending.interaction_id, "trust")
    )
    assert trusted.accepted
    assert _unwrap(await application.initialize()).status is InitializeStatus.READY
    state = _unwrap(await application.get_state())
    assert state.initialized and state.workspace_trusted
    assert state.current_thread_id is None

    created = _unwrap(
        await application.execute_command(CommandIntent(name=CommandName.NEW))
    )
    thread_id = str(created.data["thread_id"])

    model_result = _unwrap(
        await application.execute_command(
            CommandIntent(name=CommandName.MODEL, arguments=(provider, model))
        )
    )
    assert model_result.status is CommandStatus.SUCCESS
    accepted = _unwrap(await application.submit_turn(thread_id, "inspect workspace"))
    assert accepted.turn_id is not None
    turn_view = await _wait_for_thread(application, thread_id, entries=2)
    assert turn_view.entries[-1].content == "done"
    assert turn_view.turns[-1].provider == provider
    assert gateways

    direct = _unwrap(await application.execute_direct(thread_id, "echo direct-ok"))
    assert direct.turn_id is None
    direct_view = await _wait_for_thread(application, thread_id, entries=3)
    assert direct_view.turns == turn_view.turns
    assert "direct-ok" in direct_view.entries[-1].content
    assert direct_view.tool_activities[-1].turn_id is None

    tools = _unwrap(
        await application.execute_command(CommandIntent(name=CommandName.TOOLS))
    )
    names = {item["name"] for item in tools.data["tools"]}
    assert {
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "delete",
        "glob",
        "grep",
        "execute",
    } <= names
    await application.shutdown()

    restart_sink = CollectingEventSink()
    restarted = await compose_local_application(
        home=home,
        workspace=workspace,
        event_sink=restart_sink,
        environ={secret: "fake-key"},
        gateway_factory=cast(object, gateway_factory),
    )
    ready = _unwrap(await restarted.initialize())
    assert ready.status is InitializeStatus.READY
    assert _unwrap(await restarted.get_state()).current_thread_id is None
    assert [
        item.id
        for item in _unwrap(await restarted.list_threads(ThreadListQuery())).threads
    ] == [thread_id]
    _unwrap(
        await restarted.execute_command(
            CommandIntent(name=CommandName.RESUME, arguments=(thread_id,))
        )
    )
    assert (
        _unwrap(await restarted.read_thread(ThreadReadQuery(thread_id=thread_id)))
        .view.entries[-1]
        .kind.value
        == "direct_command"
    )
    assert not any(
        event.event_type in {EventType.TURN_STARTED, EventType.TURN_COMPLETED}
        for event in restart_sink.events
    )
    await restarted.shutdown()
