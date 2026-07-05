from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import pytest

from awesome_agent.attachments.models import AttachmentSource
from awesome_agent.attachments.repository import InMemoryAttachmentRepository
from awesome_agent.attachments.service import AttachmentService
from awesome_agent.attachments.store import AttachmentContentStore
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
from awesome_agent.memory.builtin import BuiltinMemoryStore
from awesome_agent.memory.external import NoopMemoryProvider
from awesome_agent.memory.models import MemoryTarget
from awesome_agent.memory.policy import MemoryPolicy
from awesome_agent.memory.service import MemoryService
from awesome_agent.modeling.errors import ModelErrorCode, ModelErrorInfo
from awesome_agent.modeling.messages import AssistantMessage, SystemMessage
from awesome_agent.modeling.stream import (
    ModelStreamEvent,
    TextDelta,
    TurnCompleted,
    TurnFailed,
)
from awesome_agent.modeling.tools import ToolCall
from awesome_agent.modeling.turns import ModelRequest, ModelTurn, StopReason
from awesome_agent.persistence.conversations import InMemoryConversationRepository
from awesome_agent.runtime.conversation_graph import ConversationGraph
from awesome_agent.runtime.cwd_context import (
    CwdContextService,
    InMemoryCwdContextSnapshotRepository,
)
from awesome_agent.runtime.graphs import CONVERSATION_TURN_ROUTE
from awesome_agent.runtime.repository import InMemoryRuntimeRepository
from awesome_agent.tools.approval import ApprovalPolicy
from awesome_agent.tools.executor import ToolExecutor
from awesome_agent.tools.memory import register_memory_tools
from awesome_agent.tools.registry import ToolRegistry


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


class CapturingProvider:
    def __init__(self, content: str = "hello") -> None:
        self.content = content
        self.requests: list[ModelRequest] = []

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        async def events() -> AsyncIterator[ModelStreamEvent]:
            self.requests.append(request)
            yield TurnCompleted(
                turn=ModelTurn(
                    assistant=AssistantMessage(content=self.content),
                    stop_reason=StopReason.COMPLETED,
                    model="fake-model",
                    provider="fake",
                )
            )

        return events()

    async def complete(self, request: ModelRequest) -> ModelTurn:
        self.requests.append(request)
        return ModelTurn(
            assistant=AssistantMessage(content=self.content),
            stop_reason=StopReason.COMPLETED,
            model="fake-model",
            provider="fake",
        )


class PrependingSystemMiddleware:
    async def before_model_call(
        self,
        request: ModelRequest,
        context: object,
    ) -> ModelRequest:
        return request.model_copy(
            update={
                "messages": [
                    SystemMessage(content="Middleware context"),
                    *request.messages,
                ]
            }
        )


class MemoryToolProvider:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        async def events() -> AsyncIterator[ModelStreamEvent]:
            self.requests.append(request)
            if len(self.requests) == 1:
                yield TurnCompleted(
                    turn=ModelTurn(
                        assistant=AssistantMessage(
                            content="",
                            tool_calls=[
                                ToolCall(
                                    call_id="call-memory",
                                    name="memory.manage",
                                    arguments_json=(
                                        '{"action":"add","target":"user",'
                                        '"content":"Prefer concise engineering '
                                        'updates.",'
                                        '"source":"explicit_user_request"}'
                                    ),
                                )
                            ],
                        ),
                        stop_reason=StopReason.TOOL_CALLS,
                        model="fake-model",
                        provider="fake",
                    )
                )
                return
            yield TurnCompleted(
                turn=ModelTurn(
                    assistant=AssistantMessage(content="remembered"),
                    stop_reason=StopReason.COMPLETED,
                    model="fake-model",
                    provider="fake",
                )
            )

        return events()

    async def complete(self, request: ModelRequest) -> ModelTurn:
        self.requests.append(request)
        return ModelTurn(
            assistant=AssistantMessage(content="remembered"),
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


@pytest.mark.asyncio
async def test_graph_injects_enabled_memory_context(tmp_path: Path) -> None:
    conversations = InMemoryConversationRepository()
    runtime = InMemoryRuntimeRepository()
    thread = await conversations.create_thread(title="Chat", context_path=str(tmp_path))
    run, leader = await _conversation_run(runtime, thread.id, "hello")
    await _replace_run_created_memory(
        runtime,
        run.id,
        {"local_enabled": True, "provider": None},
    )
    memory_service = _memory_service(tmp_path, builtin_enabled=True)
    await memory_service.add(
        target=MemoryTarget.USER,
        content="Prefer concise answers.",
        source="explicit_user_request",
        run_id=run.id,
        agent_id=leader.id,
    )
    provider = CapturingProvider("hello")
    graph = ConversationGraph(
        conversations=conversations,
        runtime=runtime,
        provider_factory=lambda _model: provider,
        default_model="fake-model",
        memory_service=memory_service,
    )

    await graph.execute(run, leader)

    assert any(
        isinstance(message, SystemMessage)
        and "Prefer concise answers." in message.content
        for message in provider.requests[0].messages
    )
    events = await runtime.list_events(run.id)
    assert any(
        event.event_type is EventType.MEMORY_OPERATION_CREATED
        and event.payload["operation"] == "context_injected"
        for event in events
    )


@pytest.mark.asyncio
async def test_graph_does_not_expose_memory_tool_when_disabled(tmp_path: Path) -> None:
    conversations = InMemoryConversationRepository()
    runtime = InMemoryRuntimeRepository()
    thread = await conversations.create_thread(title="Chat", context_path=str(tmp_path))
    run, leader = await _conversation_run(runtime, thread.id, "hello")
    await _replace_run_created_memory(
        runtime,
        run.id,
        {"local_enabled": False, "provider": None},
    )
    provider = CapturingProvider("hello")
    graph = ConversationGraph(
        conversations=conversations,
        runtime=runtime,
        provider_factory=lambda _model: provider,
        default_model="fake-model",
        memory_service=_memory_service(tmp_path, builtin_enabled=True),
    )

    await graph.execute(run, leader)

    assert provider.requests[0].tools == []


@pytest.mark.asyncio
async def test_graph_executes_memory_manage_tool(tmp_path: Path) -> None:
    conversations = InMemoryConversationRepository()
    runtime = InMemoryRuntimeRepository()
    thread = await conversations.create_thread(title="Chat", context_path=str(tmp_path))
    run, leader = await _conversation_run(runtime, thread.id, "remember this")
    await _replace_run_created_memory(
        runtime,
        run.id,
        {"local_enabled": True, "provider": None},
    )
    memory_service = _memory_service(tmp_path, builtin_enabled=True)
    registry = ToolRegistry()
    register_memory_tools(registry, memory_service)
    provider = MemoryToolProvider()
    graph = ConversationGraph(
        conversations=conversations,
        runtime=runtime,
        provider_factory=lambda _model: provider,
        default_model="fake-model",
        memory_service=memory_service,
        tool_registry=registry,
        tool_executor=ToolExecutor(registry, ApprovalPolicy()),
    )

    await graph.execute(run, leader)

    entries = memory_service.builtin.list_entries(MemoryTarget.USER)
    assert entries[0].content == "Prefer concise engineering updates."
    events = await runtime.list_events(run.id)
    assert any(
        event.event_type is EventType.MEMORY_OPERATION_CREATED
        and event.payload.get("operation") == "add"
        and event.payload.get("status") == "added"
        and "content" not in event.payload
        for event in events
    )


@pytest.mark.asyncio
async def test_graph_injects_current_run_attachment_context(tmp_path: Path) -> None:
    conversations = InMemoryConversationRepository()
    runtime = InMemoryRuntimeRepository()
    thread = await conversations.create_thread(title="Chat", context_path=str(tmp_path))
    run, leader = await _conversation_run(runtime, thread.id, "use attachment")
    attachment_service = AttachmentService(
        repository=InMemoryAttachmentRepository(),
        store=AttachmentContentStore(tmp_path / "attachments"),
    )
    attachment = await attachment_service.create(
        thread_id=thread.id,
        filename="spec.md",
        content=b"# Spec\nUse this.\n",
        mime_type="text/markdown",
        source=AttachmentSource.API,
    )
    await attachment_service.bind_to_run(
        thread_id=thread.id,
        attachment_ids=[attachment.id],
        run_id=run.id,
        message_id=leader.id,
    )
    provider = CapturingProvider("done")
    graph = ConversationGraph(
        conversations=conversations,
        runtime=runtime,
        provider_factory=lambda _model: provider,
        default_model="fake-model",
        attachment_service=attachment_service,
    )

    await graph.execute(run, leader)

    assert any(
        isinstance(message, SystemMessage)
        and "awesome_agent_attachments" in message.content
        and "# Spec" in message.content
        for message in provider.requests[0].messages
    )
    events = await runtime.list_events(run.id)
    assert any(
        event.event_type is EventType.ATTACHMENT_CONTEXT_INJECTED for event in events
    )


@pytest.mark.asyncio
async def test_graph_injects_cwd_context_for_all_model_calls(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "Always mention constraints.\n",
        encoding="utf-8",
    )
    conversations = InMemoryConversationRepository()
    runtime = InMemoryRuntimeRepository()
    thread = await conversations.create_thread(title="Chat", context_path=str(tmp_path))
    run, leader = await _conversation_run(runtime, thread.id, "hello")
    run = run.model_copy(update={"working_directory": tmp_path})
    await runtime.update_run(run)
    await _replace_run_created_memory(
        runtime,
        run.id,
        {"local_enabled": True, "provider": None},
    )
    memory_service = _memory_service(tmp_path, builtin_enabled=True)
    registry = ToolRegistry()
    register_memory_tools(registry, memory_service)
    provider = MemoryToolProvider()
    graph = ConversationGraph(
        conversations=conversations,
        runtime=runtime,
        provider_factory=lambda _model: provider,
        default_model="fake-model",
        memory_service=memory_service,
        tool_registry=registry,
        tool_executor=ToolExecutor(registry, ApprovalPolicy()),
        cwd_context_service=CwdContextService(
            repository=InMemoryCwdContextSnapshotRepository()
        ),
    )

    await graph.execute(run, leader)

    assert len(provider.requests) == 2
    for request in provider.requests:
        assert any(
            isinstance(message, SystemMessage)
            and "awesome_agent_cwd_context" in message.content
            and "Always mention constraints." in message.content
            for message in request.messages
        )
    events = await runtime.list_events(run.id)
    assert any(
        event.event_type is EventType.CWD_CONTEXT_EVALUATED
        and event.payload["status"] == "created"
        and "Always mention constraints." not in str(event.payload)
        for event in events
    )


@pytest.mark.asyncio
async def test_graph_injects_product_identity_before_cwd_context(
    tmp_path: Path,
) -> None:
    (tmp_path / "CLAUDE.md").write_text(
        "# Claude Repository Instructions\n",
        encoding="utf-8",
    )
    conversations = InMemoryConversationRepository()
    runtime = InMemoryRuntimeRepository()
    thread = await conversations.create_thread(title="Chat", context_path=str(tmp_path))
    run, leader = await _conversation_run(runtime, thread.id, "what model are you")
    run = run.model_copy(update={"working_directory": tmp_path})
    await runtime.update_run(run)
    provider = CapturingProvider("done")
    graph = ConversationGraph(
        conversations=conversations,
        runtime=runtime,
        provider_factory=lambda _model: provider,
        default_model="fake-model",
        skill_context_middleware=PrependingSystemMiddleware(),
        cwd_context_service=CwdContextService(
            repository=InMemoryCwdContextSnapshotRepository()
        ),
    )

    await graph.execute(run, leader)

    system_messages = [
        message
        for message in provider.requests[0].messages
        if isinstance(message, SystemMessage)
    ]
    assert "You are Awesome" in system_messages[0].content
    assert "Provider: deepseek" in system_messages[0].content
    assert "Model: fake-model" in system_messages[0].content
    assert "Do not claim to be Claude" in system_messages[0].content
    assert "local chat-first coding agent product" not in system_messages[0].content
    assert system_messages[1].content == "Middleware context"
    assert "awesome_agent_cwd_context" in system_messages[2].content
    assert "Claude Repository Instructions" in system_messages[2].content


@pytest.mark.asyncio
async def test_graph_soft_fails_invalid_cwd_context_without_blocking_turn(
    tmp_path: Path,
) -> None:
    conversations = InMemoryConversationRepository()
    runtime = InMemoryRuntimeRepository()
    thread = await conversations.create_thread(title="Chat", context_path=str(tmp_path))
    run, leader = await _conversation_run(runtime, thread.id, "hello")
    run = run.model_copy(update={"working_directory": tmp_path / "missing"})
    await runtime.update_run(run)
    provider = CapturingProvider("done")
    graph = ConversationGraph(
        conversations=conversations,
        runtime=runtime,
        provider_factory=lambda _model: provider,
        default_model="fake-model",
        cwd_context_service=CwdContextService(
            repository=InMemoryCwdContextSnapshotRepository()
        ),
    )

    state = await graph.execute(run, leader)

    assert state["final_answer"] == "done"
    assert not any(
        isinstance(message, SystemMessage)
        and "awesome_agent_cwd_context" in message.content
        for message in provider.requests[0].messages
    )
    events = await runtime.list_events(run.id)
    assert any(
        event.event_type is EventType.CWD_CONTEXT_EVALUATED
        and event.payload["status"] == "disabled_invalid_working_directory"
        for event in events
    )


async def _replace_run_created_memory(
    runtime: InMemoryRuntimeRepository,
    run_id: UUID,
    memory: dict[str, object],
) -> None:
    for event in await runtime.list_events(run_id):
        if event.event_type is EventType.RUN_CREATED:
            event.payload["memory"] = memory
            return
    raise AssertionError("missing run.created event")


def _memory_service(tmp_path: Path, *, builtin_enabled: bool) -> MemoryService:
    return MemoryService(
        builtin=BuiltinMemoryStore(root=tmp_path / "memory", policy=MemoryPolicy()),
        provider=NoopMemoryProvider(),
        builtin_enabled=builtin_enabled,
        provider_enabled=False,
    )
