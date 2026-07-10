from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest

from awesome_agent.application.commands import CommandIntent, CommandName, CommandStatus
from awesome_agent.application.composition import compose_local_application
from awesome_agent.application.contracts import InitializeStatus
from awesome_agent.core.events import CollectingEventSink, EventType
from awesome_agent.modeling import (
    AssistantMessage,
    GatewayEvent,
    ModelRequest,
    ModelTurn,
    SelectedModel,
    StopReason,
    TextDelta,
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


async def _wait_for_thread(
    application: object,
    thread_id: str,
    *,
    entries: int,
) -> object:
    for _ in range(200):
        view = (await application.read_thread(thread_id)).view
        state = await application.get_state()
        if len(view.entries) >= entries and state.active_operation_id is None:
            return view
        await asyncio.sleep(0.01)
    raise AssertionError("foreground operation did not complete")


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

    pending = await application.initialize()
    assert pending.status is InitializeStatus.TRUST_REQUIRED
    assert pending.interaction_id is not None
    trusted = await application.respond_interaction(pending.interaction_id, "trust")
    assert trusted.accepted
    assert (await application.initialize()).status is InitializeStatus.READY
    state = await application.get_state()
    assert state.initialized and state.workspace_trusted
    assert state.current_thread_id is not None

    model_result = await application.execute_command(
        CommandIntent(name=CommandName.MODEL, arguments=(model,))
    )
    assert model_result.status is CommandStatus.SUCCESS
    thread_id = state.current_thread_id
    accepted = await application.submit_turn(thread_id, "inspect workspace")
    assert accepted.turn_id is not None
    turn_view = await _wait_for_thread(application, thread_id, entries=2)
    assert turn_view.entries[-1].content == "done"
    assert turn_view.turns[-1].provider == provider
    assert gateways

    direct = await application.execute_direct(thread_id, "echo direct-ok")
    assert direct.turn_id is None
    direct_view = await _wait_for_thread(application, thread_id, entries=3)
    assert direct_view.turns == turn_view.turns
    assert "direct-ok" in direct_view.entries[-1].content
    assert direct_view.tool_activities[-1].turn_id is None

    tools = await application.execute_command(CommandIntent(name=CommandName.TOOLS))
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
    ready = await restarted.initialize()
    assert ready.status is InitializeStatus.READY
    assert [item.id for item in (await restarted.list_threads()).threads] == [thread_id]
    assert (await restarted.read_thread(thread_id)).view.entries[-1].kind.value == (
        "direct_command"
    )
    assert not any(
        event.event_type in {EventType.TURN_STARTED, EventType.TURN_COMPLETED}
        for event in restart_sink.events
    )
    await restarted.shutdown()
