from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest

from awesome_agent.conversation.events import ConversationStreamEventKind
from awesome_agent.domain.enums import DispatchStatus, EventType, RunStatus
from awesome_agent.modeling.messages import AssistantMessage
from awesome_agent.modeling.provider import StructuredModelProvider
from awesome_agent.modeling.stream import ModelStreamEvent, TurnCompleted
from awesome_agent.modeling.turns import ModelRequest, ModelTurn, StopReason
from awesome_agent.settings import Settings
from awesome_agent.surfaces.local_runtime_container import LocalRuntimeContainer


def _settings(tmp_path: Path) -> Settings:
    settings_type = cast(Any, Settings)
    return cast(
        Settings,
        settings_type(_env_file=None, local_state_dir=tmp_path / "state"),
    )


class FakeProvider(StructuredModelProvider):
    async def stream(
        self,
        request: ModelRequest,
    ) -> AsyncIterator[ModelStreamEvent]:
        yield TurnCompleted(
            turn=ModelTurn(
                assistant=AssistantMessage(content="worker answer"),
                stop_reason=StopReason.COMPLETED,
                model="fake-model",
                provider="fake",
            )
        )


@pytest.mark.asyncio
async def test_local_runtime_container_turn_runs_through_worker_pump(
    tmp_path: Path,
) -> None:
    container = LocalRuntimeContainer(
        settings=_settings(tmp_path),
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
    )
    try:
        thread = await container.conversations.create_thread(
            title="Chat",
            context_path=str(tmp_path),
        )

        events = []
        async for event in container.conversation_service.start_turn(
            thread_id=thread.id,
            content="hello",
        ):
            events.append(event)
            if event.event is ConversationStreamEventKind.TURN_STARTED:
                run_id = event.payload["run_id"]
                assert isinstance(run_id, str)
                await container.worker_pump.drain_until_run_terminal_or_waiting(
                    run_id,
                )

        messages = await container.conversations.list_messages(thread.id)
        assert [message.content for message in messages] == [
            "hello",
            "worker answer",
        ]
        assert events[-1].event is ConversationStreamEventKind.TURN_COMPLETED

        run_id = events[0].payload["run_id"]
        assert isinstance(run_id, str)
        runtime_events = await container.runtime.list_events(UUID(run_id))
        runtime_event_types = [event.event_type for event in runtime_events]
        assert EventType.DISPATCH_CLAIMED in runtime_event_types
        assert EventType.GRAPH_STARTED in runtime_event_types
        assert EventType.GRAPH_COMPLETED in runtime_event_types
        run = await container.runtime.get_run(UUID(run_id))
        assert run.status is RunStatus.COMPLETED
        assert run.dispatch_status is DispatchStatus.TERMINAL
    finally:
        container.close()


@pytest.mark.asyncio
async def test_local_worker_pump_drain_until_idle_returns_zero_without_runs(
    tmp_path: Path,
) -> None:
    container = LocalRuntimeContainer(
        settings=_settings(tmp_path),
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
    )
    try:
        assert await container.worker_pump.drain_until_idle() == 0
    finally:
        container.close()
