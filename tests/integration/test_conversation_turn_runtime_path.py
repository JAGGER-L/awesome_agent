from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import pytest
from tests.type_helpers import test_settings

from awesome_agent.conversation.events import ConversationStreamEventKind
from awesome_agent.conversation.models import ThreadMessageRole
from awesome_agent.domain.enums import DispatchStatus, EventType, RunStatus
from awesome_agent.modeling.messages import (
    AssistantMessage,
    ModelMessage,
    SystemMessage,
)
from awesome_agent.modeling.provider import StructuredModelProvider
from awesome_agent.modeling.stream import ModelStreamEvent, TextDelta, TurnCompleted
from awesome_agent.modeling.turns import ModelRequest, ModelTurn, StopReason
from awesome_agent.settings import Settings
from awesome_agent.surfaces.local_runtime_container import LocalRuntimeContainer


def _settings(tmp_path: Path) -> Settings:
    return test_settings(local_state_dir=tmp_path / "state")


@pytest.mark.asyncio
async def test_conversation_turn_runs_through_intake_graph_and_projection(
    tmp_path: Path,
) -> None:
    container = LocalRuntimeContainer(
        settings=_settings(tmp_path),
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
    )
    try:
        thread = await container.conversations.create_thread(
            title="Runtime path",
            context_path=str(tmp_path),
        )

        stream = container.conversation_service.start_turn(
            thread_id=thread.id,
            content="hello",
        )
        first = await anext(stream)
        run_id = UUID(str(first.payload["run_id"]))
        await container.worker_pump.drain_until_run_terminal_or_waiting(str(run_id))
        remaining = [event async for event in stream]

        assert first.event is ConversationStreamEventKind.TURN_STARTED
        assert remaining[-1].event is ConversationStreamEventKind.TURN_COMPLETED
        messages = await container.conversations.list_messages(thread.id)
        assert [message.role for message in messages] == [
            ThreadMessageRole.USER,
            ThreadMessageRole.ASSISTANT,
        ]
        assert messages[1].content == "hello from graph"
        runtime_events = await container.runtime.list_events(run_id)
        runtime_event_types = [event.event_type for event in runtime_events]
        assert EventType.DISPATCH_CLAIMED in runtime_event_types
        assert EventType.GRAPH_COMPLETED in runtime_event_types
        assert any(
            event.event_type is EventType.MESSAGE_CREATED for event in runtime_events
        )
        run = await container.runtime.get_run(run_id)
        assert run.status is RunStatus.COMPLETED
        assert run.dispatch_status is DispatchStatus.TERMINAL
    finally:
        container.close()


@pytest.mark.asyncio
async def test_conversation_graph_redacts_history_before_model_request(
    tmp_path: Path,
) -> None:
    provider = CapturingProvider()
    container = LocalRuntimeContainer(
        settings=_settings(tmp_path),
        provider_factory=lambda _model: provider,
        default_model="fake-model",
    )
    try:
        thread = await container.conversations.create_thread(
            title="Redacted history",
            context_path=str(tmp_path),
        )
        await container.conversations.append_message(
            thread_id=thread.id,
            role=ThreadMessageRole.USER,
            content="OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz1234567890",
        )

        stream = container.conversation_service.start_turn(
            thread_id=thread.id,
            content="continue",
        )
        first = await anext(stream)
        run_id = str(first.payload["run_id"])
        await container.worker_pump.drain_until_run_terminal_or_waiting(run_id)
        remaining = [event async for event in stream]
        assert remaining[-1].event is ConversationStreamEventKind.TURN_COMPLETED

        serialized = str(
            [message.model_dump(mode="json") for message in provider.last_messages]
        )
        assert "sk-proj-" not in serialized
        assert "[REDACTED:api_key]" in serialized
    finally:
        container.close()


@pytest.mark.asyncio
async def test_embedded_local_turn_injects_cwd_instruction_context(
    tmp_path: Path,
) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "Answer with runtime context.\n",
        encoding="utf-8",
    )
    provider = CapturingProvider()
    container = LocalRuntimeContainer(
        settings=_settings(tmp_path),
        provider_factory=lambda _model: provider,
        default_model="fake-model",
    )
    try:
        thread = await container.conversations.create_thread(
            title="CWD",
            context_path=str(tmp_path),
        )
        stream = container.conversation_service.start_turn(
            thread_id=thread.id,
            content="hello",
        )
        first = await anext(stream)
        run_id = str(first.payload["run_id"])
        await container.worker_pump.drain_until_run_terminal_or_waiting(run_id)
        remaining = [event async for event in stream]

        assert remaining[-1].event is ConversationStreamEventKind.TURN_COMPLETED
        assert any(
            isinstance(message, SystemMessage)
            and "Answer with runtime context." in message.content
            for message in provider.last_messages
        )
        runtime_events = await container.runtime.list_events(UUID(run_id))
        assert any(
            event.event_type is EventType.CWD_CONTEXT_EVALUATED
            for event in runtime_events
        )
    finally:
        container.close()


@pytest.mark.asyncio
async def test_cwd_context_snapshot_metadata_survives_local_restart(
    tmp_path: Path,
) -> None:
    (tmp_path / "AGENTS.md").write_text("Persistent rule.\n", encoding="utf-8")
    state_path = tmp_path / "state" / "awesome-agent.db"

    first = LocalRuntimeContainer(
        settings=_settings(tmp_path),
        provider_factory=lambda _model: CapturingProvider(),
        default_model="fake-model",
        state_path=state_path,
    )
    try:
        thread = await first.conversations.create_thread(
            title="CWD",
            context_path=str(tmp_path),
        )
        stream = first.conversation_service.start_turn(
            thread_id=thread.id,
            content="one",
        )
        first_event = await anext(stream)
        run_id = str(first_event.payload["run_id"])
        await first.worker_pump.drain_until_run_terminal_or_waiting(run_id)
        _ = [event async for event in stream]
    finally:
        first.close()

    second_provider = CapturingProvider()
    second = LocalRuntimeContainer(
        settings=_settings(tmp_path),
        provider_factory=lambda _model: second_provider,
        default_model="fake-model",
        state_path=state_path,
    )
    try:
        stream = second.conversation_service.start_turn(
            thread_id=thread.id,
            content="two",
        )
        first_event = await anext(stream)
        second_run_id = str(first_event.payload["run_id"])
        await second.worker_pump.drain_until_run_terminal_or_waiting(second_run_id)
        _ = [event async for event in stream]
        events = await second.runtime.list_events(UUID(second_run_id))
        cwd_events = [
            event
            for event in events
            if event.event_type is EventType.CWD_CONTEXT_EVALUATED
        ]
        assert cwd_events[0].payload["status"] == "reused"
    finally:
        second.close()


class FakeProvider(StructuredModelProvider):
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        yield TextDelta(text="hello from graph")
        yield TurnCompleted(
            turn=ModelTurn(
                assistant=AssistantMessage(content="hello from graph"),
                stop_reason=StopReason.COMPLETED,
                model="fake-model",
                provider="fake",
            )
        )

    async def complete(self, request: ModelRequest) -> ModelTurn:
        return ModelTurn(
            assistant=AssistantMessage(content="hello from graph"),
            stop_reason=StopReason.COMPLETED,
            model="fake-model",
            provider="fake",
        )


class CapturingProvider(FakeProvider):
    def __init__(self) -> None:
        self.last_messages: list[ModelMessage] = []

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        self.last_messages = list(request.messages)
        return super().stream(request)
