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
from awesome_agent.modeling.errors import ModelErrorCode, ModelErrorInfo
from awesome_agent.modeling.messages import AssistantMessage
from awesome_agent.modeling.stream import (
    ModelStreamEvent,
    TextDelta,
    TurnCompleted,
    TurnFailed,
)
from awesome_agent.modeling.turns import ModelRequest, ModelTurn, StopReason
from awesome_agent.persistence.conversations import InMemoryConversationRepository
from awesome_agent.runtime.conversation_graph import ConversationGraph
from awesome_agent.runtime.graphs import CONVERSATION_TURN_ROUTE
from awesome_agent.runtime.repository import InMemoryRuntimeRepository


class FakeProvider:
    def __init__(self) -> None:
        self.stream_calls = 0

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        async def events() -> AsyncIterator[ModelStreamEvent]:
            self.stream_calls += 1
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


class FailingProvider:
    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        async def events() -> AsyncIterator[ModelStreamEvent]:
            yield TurnFailed(
                error=ModelErrorInfo(
                    code=ModelErrorCode.PROVIDER_PROTOCOL,
                    message="model failed",
                    retryable=False,
                    provider="fake",
                )
            )

        return events()

    async def complete(self, request: ModelRequest) -> ModelTurn:
        raise RuntimeError("model failed")


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


async def _conversation_run(
    runtime: InMemoryRuntimeRepository,
    thread_id: UUID,
    content: str,
) -> tuple[Run, Agent]:
    run = Run(
        goal=content,
        intent=RunIntent.CONVERSATION,
        execution_kind=ExecutionKind.CONVERSATION,
        runtime_route=CONVERSATION_TURN_ROUTE,
        status=RunStatus.CREATED,
        dispatch_status=DispatchStatus.QUEUED,
        working_directory=Path.cwd(),
    )
    leader = Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="fake-model",
        status=AgentStatus.READY,
    )
    await runtime.create_run(run, leader)
    await runtime.append_event(
        run_id=run.id,
        event_type=EventType.RUN_CREATED,
        payload={
            "thread_id": str(thread_id),
            "goal": content,
            "model": "fake-model",
            "thinking": None,
            "memory": {},
            "skill_ids": [],
            "working_directory": str(Path.cwd()),
            "runtime_route": CONVERSATION_TURN_ROUTE,
        },
        agent_id=leader.id,
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
    persisted_run = await runtime.get_run(run.id)
    assert persisted_run.status is RunStatus.CREATED
    assert persisted_run.dispatch_status is DispatchStatus.QUEUED
    runtime_events = await runtime.list_events(run.id)
    assert EventType.RUN_STATUS_CHANGED not in {
        event.event_type for event in runtime_events
    }


@pytest.mark.asyncio
async def test_conversation_graph_does_not_duplicate_user_message_for_same_run() -> (
    None
):
    conversations = InMemoryConversationRepository()
    runtime = InMemoryRuntimeRepository()
    thread = await conversations.create_thread(
        title="Chat", context_path=str(Path.cwd())
    )
    run, leader = await _conversation_run(runtime, thread.id, "hello")
    provider = FakeProvider()
    graph = ConversationGraph(
        conversations=conversations,
        runtime=runtime,
        provider_factory=lambda _model: provider,
        default_model="fake-model",
    )

    await graph.execute(run, leader)
    await graph.execute(run, leader)

    messages = await conversations.list_messages(thread.id)
    assert [message.role for message in messages] == [
        ThreadMessageRole.USER,
        ThreadMessageRole.ASSISTANT,
    ]
    assert messages[0].run_id == run.id
    assert messages[0].content == "hello"
    assert provider.stream_calls == 1


@pytest.mark.asyncio
async def test_conversation_graph_does_not_write_assistant_message_on_failure() -> None:
    conversations = InMemoryConversationRepository()
    runtime = InMemoryRuntimeRepository()
    thread = await conversations.create_thread(
        title="Chat", context_path=str(Path.cwd())
    )
    run, leader = await _conversation_run(runtime, thread.id, "hello")
    graph = ConversationGraph(
        conversations=conversations,
        runtime=runtime,
        provider_factory=lambda _model: FailingProvider(),
        default_model="fake-model",
    )

    with pytest.raises(RuntimeError, match="model failed"):
        await graph.execute(run, leader)

    messages = await conversations.list_messages(thread.id)
    assert [message.role for message in messages] == [ThreadMessageRole.USER]
    assert messages[0].run_id == run.id
    persisted_run = await runtime.get_run(run.id)
    assert persisted_run.status is RunStatus.CREATED
    assert persisted_run.dispatch_status is DispatchStatus.QUEUED
    runtime_events = await runtime.list_events(run.id)
    assert EventType.RUN_STATUS_CHANGED not in {
        event.event_type for event in runtime_events
    }
