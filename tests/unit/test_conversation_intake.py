from pathlib import Path

import pytest

from awesome_agent.attachments.models import AttachmentSource, AttachmentStatus
from awesome_agent.attachments.repository import InMemoryAttachmentRepository
from awesome_agent.attachments.service import AttachmentService
from awesome_agent.attachments.store import AttachmentContentStore
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
    assert created.payload["thread_id"] == str(thread.id)
    assert created.payload["goal"] == "write a file"
    assert created.payload["user_message_id"]
    assert created.payload["model"] == "alternate-model"
    assert created.payload["thinking"] == "off"
    assert created.payload["memory"] == {"local_enabled": True}
    assert created.payload["skill_ids"] == ["repo"]
    assert created.payload["attachment_ids"] == []
    assert created.payload["attachments"] == []
    assert created.payload["working_directory"] == str(Path(thread.context_path or ""))
    assert created.payload["runtime_route"] == CONVERSATION_TURN_ROUTE


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


@pytest.mark.asyncio
async def test_conversation_intake_binds_attachments_atomically(tmp_path: Path) -> None:
    attachment_service = AttachmentService(
        repository=InMemoryAttachmentRepository(),
        store=AttachmentContentStore(tmp_path / "attachments"),
    )
    conversations = InMemoryConversationRepository()
    runtime = InMemoryRuntimeRepository()
    events = EventStream()
    thread = await conversations.create_thread(
        title="Attach",
        context_path=str(tmp_path),
    )
    attachment = await attachment_service.create(
        thread_id=thread.id,
        filename="spec.md",
        content=b"# Spec\n",
        mime_type="text/markdown",
        source=AttachmentSource.API,
    )
    intake = ConversationRunIntakeService(
        conversations=conversations,
        runtime=runtime,
        events=events,
        default_model="fake-model",
        attachment_service=attachment_service,
    )

    run = await intake.create_turn_run(
        thread_id=thread.id,
        content="Use the attachment",
        model=None,
        thinking=None,
        memory={},
        skill_ids=(),
        attachment_ids=(attachment.id,),
    )

    runtime_events = await runtime.list_events(run.id)
    created = next(
        event for event in runtime_events if event.event_type is EventType.RUN_CREATED
    )
    assert created.payload["attachment_ids"] == [str(attachment.id)]
    assert created.payload["attachments"][0]["filename"] == "spec.md"
    bound = await attachment_service.get(
        thread_id=thread.id,
        attachment_id=attachment.id,
    )
    assert bound.status is AttachmentStatus.ATTACHED
    assert bound.run_id == run.id
    assert bound.message_id
    assert any(
        event.event_type is EventType.ATTACHMENT_ATTACHED for event in runtime_events
    )
