from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

from awesome_agent.conversation.events import ConversationStreamEventKind
from awesome_agent.conversation.intake import ConversationRunIntakeService
from awesome_agent.conversation.models import ThreadMessageRole
from awesome_agent.conversation.service import ConversationService
from awesome_agent.domain.enums import EventType
from awesome_agent.modeling.messages import AssistantMessage
from awesome_agent.modeling.provider import ModelProvider
from awesome_agent.modeling.stream import ModelStreamEvent, TextDelta, TurnCompleted
from awesome_agent.modeling.turns import ModelRequest, ModelTurn, StopReason
from awesome_agent.persistence.conversations import InMemoryConversationRepository
from awesome_agent.runtime.conversation_graph import ConversationGraph
from awesome_agent.runtime.events import EventStream
from awesome_agent.runtime.repository import InMemoryRuntimeRepository


async def test_conversation_turn_runs_through_intake_graph_and_projection() -> None:
    conversations = InMemoryConversationRepository()
    thread = await conversations.create_thread(
        title="Runtime path",
        context_path=str(Path.cwd()),
    )
    runtime = InMemoryRuntimeRepository()
    event_stream = EventStream()
    service = ConversationService(
        repository=conversations,
        runtime_repository=runtime,
        conversation_run_intake=ConversationRunIntakeService(
            conversations=conversations,
            runtime=runtime,
            events=event_stream,
            default_model="fake-model",
        ),
        default_model="fake-model",
        event_poll_interval=0,
    )
    graph = ConversationGraph(
        conversations=conversations,
        runtime=runtime,
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
    )

    stream = service.start_turn(thread_id=thread.id, content="hello")
    first = await anext(stream)
    run_id = UUID(str(first.payload["run_id"]))
    run = await runtime.get_run(run_id)
    leader = (await runtime.list_agents(run_id))[0]

    await graph.execute(run, leader)
    remaining = [event async for event in stream]

    assert first.event is ConversationStreamEventKind.TURN_STARTED
    assert remaining[-1].event is ConversationStreamEventKind.TURN_COMPLETED
    messages = await conversations.list_messages(thread.id)
    assert [message.role for message in messages] == [
        ThreadMessageRole.USER,
        ThreadMessageRole.ASSISTANT,
    ]
    assert messages[1].content == "hello from graph"
    runtime_events = await runtime.list_events(run_id)
    assert any(
        event.event_type is EventType.MESSAGE_CREATED for event in runtime_events
    )


class FakeProvider(ModelProvider):
    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        async def events() -> AsyncIterator[ModelStreamEvent]:
            yield TextDelta(text="hello from graph")
            yield TurnCompleted(
                turn=ModelTurn(
                    assistant=AssistantMessage(content="hello from graph"),
                    stop_reason=StopReason.COMPLETED,
                    model="fake-model",
                    provider="fake",
                )
            )

        return events()

    async def complete(self, request: ModelRequest) -> ModelTurn:
        return ModelTurn(
            assistant=AssistantMessage(content="hello from graph"),
            stop_reason=StopReason.COMPLETED,
            model="fake-model",
            provider="fake",
        )
