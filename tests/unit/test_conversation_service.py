from __future__ import annotations

from pathlib import Path

from tests.conversation_projection_fakes import ProjectedConversationRunIntake

from awesome_agent.conversation.events import ConversationStreamEventKind
from awesome_agent.conversation.models import ThreadMessageRole
from awesome_agent.conversation.service import ConversationService
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
from awesome_agent.persistence.conversations import InMemoryConversationRepository
from awesome_agent.runtime.graphs import CONVERSATION_TURN_ROUTE
from awesome_agent.runtime.repository import InMemoryRuntimeRepository


async def test_conversation_service_projects_and_persists_assistant_message() -> None:
    repository = InMemoryConversationRepository()
    thread = await repository.create_thread(
        title="Greeting",
        context_path="E:/project",
    )
    runtime = InMemoryRuntimeRepository()
    intake = ProjectedConversationRunIntake(
        conversations=repository,
        runtime=runtime,
        usage={"input_tokens": 1, "output_tokens": 2},
    )
    service = ConversationService(
        repository=repository,
        runtime_repository=runtime,
        conversation_run_intake=intake,
        default_model="fake-model",
        event_poll_interval=0,
    )

    events = [
        event
        async for event in service.start_turn(thread_id=thread.id, content="hello?")
    ]
    messages = await repository.list_messages(thread.id)

    assert [event.event for event in events] == [
        ConversationStreamEventKind.TURN_STARTED,
        ConversationStreamEventKind.MESSAGE_CREATED,
        ConversationStreamEventKind.MESSAGE_DELTA,
        ConversationStreamEventKind.MESSAGE_DELTA,
        ConversationStreamEventKind.USAGE_UPDATED,
        ConversationStreamEventKind.MESSAGE_COMPLETED,
        ConversationStreamEventKind.TURN_COMPLETED,
    ]
    assert [message.role for message in messages] == [
        ThreadMessageRole.USER,
        ThreadMessageRole.ASSISTANT,
    ]
    assert messages[0].content == "hello?"
    assert messages[1].content == "hello world"
    assert events[4].payload["output_tokens"] == 2
    assert all(event.trace_id == events[0].trace_id for event in events)


async def test_conversation_service_projects_error_without_assistant_message() -> None:
    repository = InMemoryConversationRepository()
    thread = await repository.create_thread(
        title="Failure",
        context_path="E:/project",
    )
    runtime = InMemoryRuntimeRepository()
    intake = ProjectedConversationRunIntake(
        conversations=repository,
        runtime=runtime,
        assistant_content=None,
        text_deltas=(),
        fail=True,
    )
    service = ConversationService(
        repository=repository,
        runtime_repository=runtime,
        conversation_run_intake=intake,
        default_model="fake-model",
        event_poll_interval=0,
    )

    events = [
        event
        async for event in service.start_turn(thread_id=thread.id, content="hello?")
    ]
    messages = await repository.list_messages(thread.id)

    assert events[-1].event is ConversationStreamEventKind.ERROR
    assert events[-1].payload["message"] == "bad request"
    assert [message.role for message in messages] == [ThreadMessageRole.USER]


async def test_list_thread_runs_projects_from_runtime_created_events() -> None:
    repository = InMemoryConversationRepository()
    thread = await repository.create_thread(title="Chat", context_path=str(Path.cwd()))
    runtime = InMemoryRuntimeRepository()
    service = ConversationService(
        repository=repository,
        runtime_repository=runtime,
        conversation_run_intake=ProjectedConversationRunIntake(
            conversations=repository,
            runtime=runtime,
        ),
        default_model="fake-model",
        event_poll_interval=0,
    )
    run = Run(
        goal="hello",
        intent=RunIntent.CONVERSATION,
        execution_kind=ExecutionKind.CONVERSATION,
        runtime_route=CONVERSATION_TURN_ROUTE,
        status=RunStatus.COMPLETED,
        dispatch_status=DispatchStatus.TERMINAL,
        result_text="done",
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
        payload={"thread_id": str(thread.id), "goal": "hello"},
        agent_id=leader.id,
    )

    projections = await service.list_thread_runs(thread.id)

    assert projections == [
        {
            "run_id": str(run.id),
            "thread_id": str(thread.id),
            "goal": "hello",
            "status": "completed",
            "dispatch_status": "terminal",
            "runtime_route": "conversation-turn",
            "execution_kind": "conversation",
            "result_text": "done",
        }
    ]
