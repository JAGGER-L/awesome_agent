from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import pytest

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
from awesome_agent.modeling.messages import AssistantMessage
from awesome_agent.modeling.stream import ModelStreamEvent, TextDelta, TurnCompleted
from awesome_agent.modeling.turns import ModelRequest, ModelTurn, StopReason
from awesome_agent.persistence.conversations import InMemoryConversationRepository
from awesome_agent.runtime.conversation_graph import ConversationGraph
from awesome_agent.runtime.repository import InMemoryRuntimeRepository


class FakeProvider:
    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        async def events() -> AsyncIterator[ModelStreamEvent]:
            yield TextDelta(text="hello")
            yield TurnCompleted(
                turn=ModelTurn(
                    assistant=AssistantMessage(content="hello"),
                    stop_reason=StopReason.COMPLETED,
                    model="fake-model",
                    provider="fake",
                )
            )

        return events()

    async def complete(self, request: ModelRequest) -> ModelTurn:
        return ModelTurn(
            assistant=AssistantMessage(content="hello"),
            stop_reason=StopReason.COMPLETED,
            model="fake-model",
            provider="fake",
        )


def make_conversation_run(
    *,
    thread_id: UUID,
    content: str,
    working_directory: Path,
) -> tuple[Run, Agent]:
    run = Run(
        goal=content,
        intent=RunIntent.CONVERSATION,
        execution_kind=ExecutionKind.CONVERSATION,
        runtime_route="conversation-turn",
        status=RunStatus.CREATED,
        dispatch_status=DispatchStatus.QUEUED,
        working_directory=working_directory,
        graph_thread_id=f"conversation:{thread_id}",
    )
    leader = Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="fake-model",
        status=AgentStatus.READY,
    )
    return run, leader


@pytest.mark.asyncio
async def test_conversation_graph_writes_user_then_assistant_messages() -> None:
    conversations = InMemoryConversationRepository()
    thread = await conversations.create_thread(
        title="Chat",
        context_path="E:/project",
    )
    runtime = InMemoryRuntimeRepository()
    run, leader = make_conversation_run(
        thread_id=thread.id,
        content="Say hi",
        working_directory=Path("E:/project"),
    )
    await runtime.create_run(run, leader)
    await runtime.append_event(
        run_id=run.id,
        event_type=EventType.RUN_CREATED,
        payload={"thread_id": str(thread.id), "goal": "Say hi", "model": "fake-model"},
        agent_id=leader.id,
    )
    graph = ConversationGraph(
        conversations=conversations,
        runtime=runtime,
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
    )

    state = await graph.execute(run, leader)

    messages = await conversations.list_messages(thread.id)
    assert [message.role for message in messages] == [
        ThreadMessageRole.USER,
        ThreadMessageRole.ASSISTANT,
    ]
    assert messages[0].run_id == run.id
    assert messages[1].run_id == run.id
    assert messages[1].content == "hello"
    assert state["final_answer"] == "hello"
