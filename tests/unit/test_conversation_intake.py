from pathlib import Path

import pytest

from awesome_agent.conversation.intake import ConversationRunIntakeService
from awesome_agent.domain.enums import (
    DispatchStatus,
    EventType,
    ExecutionKind,
    RunIntent,
    RunStatus,
)
from awesome_agent.persistence.conversations import InMemoryConversationRepository
from awesome_agent.runtime.events import EventStream
from awesome_agent.runtime.graphs import CONVERSATION_TURN_ROUTE
from awesome_agent.runtime.repository import InMemoryRuntimeRepository


@pytest.mark.asyncio
async def test_conversation_intake_creates_queued_run_from_thread_context() -> None:
    conversations = InMemoryConversationRepository()
    thread = await conversations.create_thread(
        title="Project chat",
        context_kind="workspace",
        context_path="E:/project",
    )
    runtime = InMemoryRuntimeRepository()
    events = EventStream()
    service = ConversationRunIntakeService(
        conversations=conversations,
        runtime=runtime,
        events=events,
        default_model="fake-model",
    )

    run = await service.create_turn_run(
        thread_id=thread.id,
        content="hello",
        model=None,
        thinking="off",
        memory={},
        skill_ids=(),
    )

    assert run.status is RunStatus.CREATED
    assert run.dispatch_status is DispatchStatus.QUEUED
    assert run.intent is RunIntent.CONVERSATION
    assert run.execution_kind is ExecutionKind.CONVERSATION
    assert run.runtime_route == "conversation-turn"
    assert run.working_directory == Path("E:/project")
    assert run.graph_thread_id == f"conversation:{run.id}"


@pytest.mark.asyncio
async def test_conversation_intake_requires_thread_context_path() -> None:
    conversations = InMemoryConversationRepository()
    thread = await conversations.create_thread(title="No cwd")
    service = ConversationRunIntakeService(
        conversations=conversations,
        runtime=InMemoryRuntimeRepository(),
        events=EventStream(),
        default_model="fake-model",
    )

    with pytest.raises(ValueError, match="working directory"):
        await service.create_turn_run(
            thread_id=thread.id,
            content="hello",
            model=None,
            thinking=None,
            memory={},
            skill_ids=(),
        )


@pytest.mark.asyncio
async def test_conversation_intake_records_graph_input_in_run_goal_and_payload() -> (
    None
):
    conversations = InMemoryConversationRepository()
    thread = await conversations.create_thread(
        title="Chat",
        context_path=str(Path.cwd()),
    )
    runtime = InMemoryRuntimeRepository()
    events = EventStream()
    service = ConversationRunIntakeService(
        conversations=conversations,
        runtime=runtime,
        events=events,
        default_model="fake-model",
    )

    run = await service.create_turn_run(
        thread_id=thread.id,
        content="write a file",
        model="alternate-model",
        thinking="off",
        memory={"local_enabled": True},
        skill_ids=("repo",),
    )

    assert run.goal == "write a file"
    [created] = [
        event
        for event in await runtime.list_events(run.id)
        if event.event_type is EventType.RUN_CREATED
    ]
    assert created.payload == {
        "thread_id": str(thread.id),
        "goal": "write a file",
        "model": "alternate-model",
        "thinking": "off",
        "memory": {"local_enabled": True},
        "skill_ids": ["repo"],
        "working_directory": str(Path(thread.context_path or "")),
        "runtime_route": CONVERSATION_TURN_ROUTE,
    }


@pytest.mark.asyncio
async def test_conversation_intake_pins_extension_catalog_version() -> None:
    conversations = InMemoryConversationRepository()
    thread = await conversations.create_thread(
        title="Chat",
        context_path=str(Path.cwd()),
    )
    runtime = InMemoryRuntimeRepository()
    service = ConversationRunIntakeService(
        conversations=conversations,
        runtime=runtime,
        events=EventStream(),
        default_model="fake-model",
        extension_catalog_version="ext_123",
    )

    run = await service.create_turn_run(
        thread_id=thread.id,
        content="hello",
        model=None,
        thinking=None,
        memory={},
        skill_ids=(),
    )

    assert run.extension_catalog_version == "ext_123"
    [created] = [
        event
        for event in await runtime.list_events(run.id)
        if event.event_type is EventType.RUN_CREATED
    ]
    assert created.payload["extension_catalog_version"] == "ext_123"
