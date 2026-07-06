import json
import subprocess
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
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
    ApprovalStatus,
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
from awesome_agent.modeling.execution import (
    ModelExecutionContext,
    ModelExecutionService,
    ModelExecutionTimeout,
)
from awesome_agent.modeling.messages import (
    AssistantMessage,
    SystemMessage,
    ToolResultMessage,
)
from awesome_agent.modeling.stream import (
    ModelStreamEvent,
    TextDelta,
    TurnCompleted,
    TurnFailed,
)
from awesome_agent.modeling.tools import ToolCall
from awesome_agent.modeling.turns import ModelRequest, ModelTurn, StopReason
from awesome_agent.persistence.approval_contracts import (
    DurableApproval,
    InMemoryApprovalRepository,
)
from awesome_agent.persistence.conversations import InMemoryConversationRepository
from awesome_agent.runtime.agent_loop.skill_context_middleware import (
    SkillContextMiddleware,
)
from awesome_agent.runtime.conversation_graph import ConversationGraph
from awesome_agent.runtime.cwd_context import (
    CwdContextService,
    InMemoryCwdContextSnapshotRepository,
)
from awesome_agent.runtime.dispatch import (
    ApprovalInterrupt,
    CorruptRuntimeStateError,
    PermanentExecutionError,
)
from awesome_agent.runtime.graphs import CONVERSATION_TURN_ROUTE
from awesome_agent.runtime.repository import InMemoryRuntimeRepository
from awesome_agent.sandbox.base import CommandRequest, CommandResult
from awesome_agent.tools.approval import ApprovalPolicy
from awesome_agent.tools.executor import ToolExecutor
from awesome_agent.tools.memory import register_memory_tools
from awesome_agent.tools.registry import ToolRegistry
from awesome_agent.tools.repository import (
    build_modifying_registry,
    canonical_arguments_hash_from_arguments,
    tool_invocation_uuid,
)


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


class PrependingSystemMiddleware(SkillContextMiddleware):
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


class TimeoutBackend:
    def stream(
        self,
        request: ModelRequest,
        *,
        context: ModelExecutionContext,
    ) -> AsyncIterator[ModelStreamEvent]:
        async def events() -> AsyncIterator[ModelStreamEvent]:
            raise ModelExecutionTimeout("first_event", 0.1)
            yield TextDelta(text="unreachable")

        return events()


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


class ToolCallProvider:
    def __init__(self, call: ToolCall, *, final_after_tool: str = "done") -> None:
        self.call = call
        self.final_after_tool = final_after_tool
        self.requests: list[ModelRequest] = []

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        async def events() -> AsyncIterator[ModelStreamEvent]:
            self.requests.append(request)
            if any(
                isinstance(message, ToolResultMessage) for message in request.messages
            ):
                yield TurnCompleted(
                    turn=ModelTurn(
                        assistant=AssistantMessage(content=self.final_after_tool),
                        stop_reason=StopReason.COMPLETED,
                        model="fake-model",
                        provider="fake",
                    )
                )
                return
            yield TurnCompleted(
                turn=ModelTurn(
                    assistant=AssistantMessage(content="", tool_calls=[self.call]),
                    stop_reason=StopReason.TOOL_CALLS,
                    model="fake-model",
                    provider="fake",
                )
            )

        return events()

    async def complete(self, request: ModelRequest) -> ModelTurn:
        self.requests.append(request)
        if any(isinstance(message, ToolResultMessage) for message in request.messages):
            return ModelTurn(
                assistant=AssistantMessage(content=self.final_after_tool),
                stop_reason=StopReason.COMPLETED,
                model="fake-model",
                provider="fake",
            )
        return ModelTurn(
            assistant=AssistantMessage(content="", tool_calls=[self.call]),
            stop_reason=StopReason.TOOL_CALLS,
            model="fake-model",
            provider="fake",
        )


class SequentialToolProvider:
    def __init__(self, calls: list[ToolCall], *, final_after_tools: str) -> None:
        self.calls = calls
        self.final_after_tools = final_after_tools
        self.requests: list[ModelRequest] = []

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        async def events() -> AsyncIterator[ModelStreamEvent]:
            self.requests.append(request)
            tool_results = [
                message
                for message in request.messages
                if isinstance(message, ToolResultMessage)
            ]
            if len(tool_results) < len(self.calls):
                yield TurnCompleted(
                    turn=ModelTurn(
                        assistant=AssistantMessage(
                            content="",
                            tool_calls=[self.calls[len(tool_results)]],
                        ),
                        stop_reason=StopReason.TOOL_CALLS,
                        model="fake-model",
                        provider="fake",
                    )
                )
                return
            yield TurnCompleted(
                turn=ModelTurn(
                    assistant=AssistantMessage(content=self.final_after_tools),
                    stop_reason=StopReason.COMPLETED,
                    model="fake-model",
                    provider="fake",
                )
            )

        return events()

    async def complete(self, request: ModelRequest) -> ModelTurn:
        self.requests.append(request)
        tool_results = [
            message
            for message in request.messages
            if isinstance(message, ToolResultMessage)
        ]
        if len(tool_results) < len(self.calls):
            return ModelTurn(
                assistant=AssistantMessage(
                    content="",
                    tool_calls=[self.calls[len(tool_results)]],
                ),
                stop_reason=StopReason.TOOL_CALLS,
                model="fake-model",
                provider="fake",
            )
        return ModelTurn(
            assistant=AssistantMessage(content=self.final_after_tools),
            stop_reason=StopReason.COMPLETED,
            model="fake-model",
            provider="fake",
        )


class ApprovalReplayFailingProvider:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        async def events() -> AsyncIterator[ModelStreamEvent]:
            self.requests.append(request)
            raise AssertionError("provider must not be called before approval replay")
            yield  # pragma: no cover

        return events()

    async def complete(self, request: ModelRequest) -> ModelTurn:
        self.requests.append(request)
        raise AssertionError("provider must not be called before approval replay")


class RecordingSandbox:
    name = "recording"

    def __init__(self) -> None:
        self.requests: list[CommandRequest] = []

    async def execute(self, request: CommandRequest) -> CommandResult:
        self.requests.append(request)
        return CommandResult(
            command=request.command_label,
            exit_code=0,
            stdout="ok\n",
            stderr="",
        )


def _assert_reconstructed_tool_context(
    request: ModelRequest,
    *,
    call_id: str,
    is_error: bool | None = None,
) -> None:
    assistant_index = next(
        index
        for index, message in enumerate(request.messages)
        if isinstance(message, AssistantMessage)
        and any(call.call_id == call_id for call in message.tool_calls)
    )
    tool_index = next(
        index
        for index, message in enumerate(request.messages)
        if isinstance(message, ToolResultMessage) and message.call_id == call_id
    )
    assert assistant_index < tool_index
    tool_message = request.messages[tool_index]
    assert isinstance(tool_message, ToolResultMessage)
    if is_error is not None:
        assert tool_message.is_error is is_error


async def _store_bash_approval(
    approvals: InMemoryApprovalRepository,
    *,
    run_id: UUID,
    agent_id: UUID,
    tool_call_id: str,
    command: str,
    workspace: Path,
    status: ApprovalStatus = ApprovalStatus.APPROVED,
) -> DurableApproval:
    arguments = {
        "command": command,
        "timeout_seconds": 60,
        "max_output_chars": 30_000,
    }
    now = datetime.now(UTC)
    approval = DurableApproval(
        id=tool_invocation_uuid(f"{run_id}:{tool_call_id}"),
        run_id=run_id,
        agent_id=agent_id,
        tool_invocation_id=tool_invocation_uuid(f"{run_id}:{tool_call_id}"),
        tool_call_id=tool_call_id,
        tool_name="Bash",
        tool_version="1",
        canonical_arguments=arguments,
        arguments_hash=canonical_arguments_hash_from_arguments(arguments),
        workspace_path=str(workspace.resolve()),
        workspace_fingerprint="fingerprint",
        capabilities=["shell:execute"],
        risk_level="medium",
        expires_at=now + timedelta(minutes=30),
        status=status,
        decided_at=now,
        decided_by="tester",
        decision_reason=status.value,
        created_at=now,
        updated_at=now,
    )
    return await approvals.upsert(approval)


def _write_file_call(
    call_id: str,
    *,
    path: str,
    content: str,
    overwrite: bool = False,
) -> ToolCall:
    return ToolCall(
        call_id=call_id,
        name="WriteFile",
        arguments_json=json.dumps(
            {
                "path": path,
                "content": content,
                "overwrite": overwrite,
            }
        ),
    )


def _git(workspace: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_git_workspace(workspace: Path) -> None:
    _git(workspace, "init")
    _git(workspace, "config", "user.email", "test@example.com")
    _git(workspace, "config", "user.name", "Test User")
    (workspace / "tracked.txt").write_text("initial", encoding="utf-8")
    _git(workspace, "add", "tracked.txt")
    _git(workspace, "commit", "-m", "initial")


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
    model_phase_events = [
        event.payload
        for event in runtime_events
        if event.event_type is EventType.MODEL_CALL_CREATED
        and isinstance(event.payload.get("phase"), str)
    ]
    assert [payload["phase"] for payload in model_phase_events] == [
        "stream_started",
        "stream_completed",
    ]


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
async def test_conversation_graph_bounds_provider_stream_before_first_event() -> None:
    conversations = InMemoryConversationRepository()
    runtime = InMemoryRuntimeRepository()
    thread = await conversations.create_thread(
        title="Chat", context_path=str(Path.cwd())
    )
    run, leader = await _conversation_run(runtime, thread.id, "hello")
    graph = ConversationGraph(
        conversations=conversations,
        runtime=runtime,
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
        model_execution_service=ModelExecutionService(TimeoutBackend()),
    )

    with pytest.raises(PermanentExecutionError, match="first_event_timeout"):
        await graph.execute(run, leader)

    events = await runtime.list_events(run.id)
    model_events = [
        event for event in events if event.event_type is EventType.MODEL_CALL_CREATED
    ]
    assert [event.payload["status"] for event in model_events] == [
        "started",
        "failed",
    ]
    assert model_events[0].payload["phase"] == "stream_started"
    assert model_events[1].payload["phase"] == "first_event_timeout"
    assert "0.1" in str(model_events[1].payload["error"])
    messages = await conversations.list_messages(thread.id)
    assert [message.role for message in messages] == [ThreadMessageRole.USER]


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
async def test_conversation_graph_interrupts_for_bash_approval(
    tmp_path: Path,
) -> None:
    conversations = InMemoryConversationRepository()
    runtime = InMemoryRuntimeRepository()
    approvals = InMemoryApprovalRepository()
    thread = await conversations.create_thread(title="Chat", context_path=str(tmp_path))
    run, leader = await _conversation_run(runtime, thread.id, "run script")
    run = run.model_copy(update={"working_directory": tmp_path})
    await runtime.update_run(run)
    sandbox = RecordingSandbox()
    registry = build_modifying_registry(sandbox=sandbox)
    provider = ToolCallProvider(
        ToolCall(
            call_id="call-bash",
            name="Bash",
            arguments_json='{"command":"python add.py"}',
        )
    )
    graph = ConversationGraph(
        conversations=conversations,
        runtime=runtime,
        provider_factory=lambda _model: provider,
        default_model="fake-model",
        tool_registry=registry,
        tool_executor=ToolExecutor(registry, ApprovalPolicy()),
        approval_repository=approvals,
    )

    with pytest.raises(ApprovalInterrupt) as interrupted:
        await graph.execute(run, leader)

    approval = await approvals.get(interrupted.value.approval_id)
    assert approval.tool_name == "Bash"
    assert approval.tool_call_id == "call-bash"
    assert approval.canonical_arguments["command"] == "python add.py"
    assert sandbox.requests == []
    events = await runtime.list_events(run.id)
    approval_events = [
        event for event in events if event.event_type is EventType.APPROVAL_REQUESTED
    ]
    assert len(approval_events) == 1
    assert approval_events[0].payload["approval_id"] == str(approval.id)
    continuation = approval_events[0].payload["approval_continuation"]
    assert isinstance(continuation, dict)
    assert continuation["approval_id"] == str(approval.id)
    assert continuation["tool_call_id"] == "call-bash"
    assert continuation["tool_name"] == "Bash"
    assert continuation["arguments_json"] == '{"command":"python add.py"}'
    assert continuation["arguments_hash"] == approval.arguments_hash
    assert continuation["workspace_path"] == approval.workspace_path
    assert continuation["workspace_fingerprint"] == approval.workspace_fingerprint
    assert continuation["capabilities"] == approval.capabilities


@pytest.mark.asyncio
async def test_conversation_graph_reuses_approved_bash_call_by_command(
    tmp_path: Path,
) -> None:
    conversations = InMemoryConversationRepository()
    runtime = InMemoryRuntimeRepository()
    approvals = InMemoryApprovalRepository()
    thread = await conversations.create_thread(title="Chat", context_path=str(tmp_path))
    run, leader = await _conversation_run(runtime, thread.id, "run script")
    run = run.model_copy(update={"working_directory": tmp_path})
    await runtime.update_run(run)
    sandbox = RecordingSandbox()
    registry = build_modifying_registry(sandbox=sandbox)
    approval = await _store_bash_approval(
        approvals,
        run_id=run.id,
        agent_id=leader.id,
        tool_call_id="call-bash-1",
        command="python square.py",
        workspace=tmp_path,
    )

    second_provider = ToolCallProvider(
        ToolCall(
            call_id="call-bash-2",
            name="Bash",
            arguments_json='{"command":"python square.py"}',
        )
    )
    second_graph = ConversationGraph(
        conversations=conversations,
        runtime=runtime,
        provider_factory=lambda _model: second_provider,
        default_model="fake-model",
        tool_registry=registry,
        tool_executor=ToolExecutor(registry, ApprovalPolicy()),
        approval_repository=approvals,
    )

    state = await second_graph.execute(run, leader)

    assert state["final_answer"] == "done"
    assert len(sandbox.requests) == 1
    assert len(second_provider.requests) == 2
    _assert_reconstructed_tool_context(
        second_provider.requests[-1],
        call_id="call-bash-2",
        is_error=False,
    )
    events = await runtime.list_events(run.id)
    approval_events = [
        event for event in events if event.event_type is EventType.APPROVAL_REQUESTED
    ]
    assert approval_events == []
    approval_reused_events = [
        event for event in events if event.event_type is EventType.APPROVAL_REUSED
    ]
    assert len(approval_reused_events) == 1
    assert approval_reused_events[0].payload["status"] == "approved"
    assert approval_reused_events[0].payload["approval_id"] == str(approval.id)
    assert approval_reused_events[0].payload["tool_call_id"] == "call-bash-2"


@pytest.mark.asyncio
async def test_conversation_graph_does_not_reuse_bash_grant_for_different_command(
    tmp_path: Path,
) -> None:
    conversations = InMemoryConversationRepository()
    runtime = InMemoryRuntimeRepository()
    approvals = InMemoryApprovalRepository()
    thread = await conversations.create_thread(title="Chat", context_path=str(tmp_path))
    run, leader = await _conversation_run(runtime, thread.id, "run script")
    run = run.model_copy(update={"working_directory": tmp_path})
    await runtime.update_run(run)
    sandbox = RecordingSandbox()
    registry = build_modifying_registry(sandbox=sandbox)
    approval = await _store_bash_approval(
        approvals,
        run_id=run.id,
        agent_id=leader.id,
        tool_call_id="call-bash-1",
        command="python square.py",
        workspace=tmp_path,
    )

    second_graph = ConversationGraph(
        conversations=conversations,
        runtime=runtime,
        provider_factory=lambda _model: ToolCallProvider(
            ToolCall(
                call_id="call-bash-2",
                name="Bash",
                arguments_json='{"command":"python cube.py"}',
            )
        ),
        default_model="fake-model",
        tool_registry=registry,
        tool_executor=ToolExecutor(registry, ApprovalPolicy()),
        approval_repository=approvals,
    )

    with pytest.raises(ApprovalInterrupt) as second_interrupt:
        await second_graph.execute(run, leader)

    assert second_interrupt.value.approval_id != approval.id
    assert sandbox.requests == []
    events = await runtime.list_events(run.id)
    requested = [
        event for event in events if event.event_type is EventType.APPROVAL_REQUESTED
    ]
    assert len(requested) == 1
    assert not any(event.event_type is EventType.APPROVAL_REUSED for event in events)


@pytest.mark.asyncio
async def test_conversation_graph_reuses_approved_write_file_scope_for_same_path(
    tmp_path: Path,
) -> None:
    conversations = InMemoryConversationRepository()
    runtime = InMemoryRuntimeRepository()
    approvals = InMemoryApprovalRepository()
    thread = await conversations.create_thread(title="Chat", context_path=str(tmp_path))
    run, leader = await _conversation_run(runtime, thread.id, "write env")
    run = run.model_copy(update={"working_directory": tmp_path})
    await runtime.update_run(run)
    registry = build_modifying_registry(sandbox=RecordingSandbox())
    first_call = _write_file_call(
        "call-write-1",
        path=".env",
        content="TOKEN=one\n",
    )
    second_call = _write_file_call(
        "call-write-2",
        path=".env",
        content="TOKEN=two\n",
        overwrite=True,
    )
    first_graph = ConversationGraph(
        conversations=conversations,
        runtime=runtime,
        provider_factory=lambda _model: ToolCallProvider(first_call),
        default_model="fake-model",
        tool_registry=registry,
        tool_executor=ToolExecutor(registry, ApprovalPolicy()),
        approval_repository=approvals,
    )

    with pytest.raises(ApprovalInterrupt) as interrupted:
        await first_graph.execute(run, leader)
    await approvals.decide(
        interrupted.value.approval_id,
        approved=True,
        decided_by="tester",
        reason="approved",
        now=datetime.now(UTC),
    )

    resume_provider = SequentialToolProvider(
        [first_call, second_call],
        final_after_tools="all writes done",
    )
    state = await ConversationGraph(
        conversations=conversations,
        runtime=runtime,
        provider_factory=lambda _model: resume_provider,
        default_model="fake-model",
        tool_registry=registry,
        tool_executor=ToolExecutor(registry, ApprovalPolicy()),
        approval_repository=approvals,
    ).execute(run, leader)

    assert state["final_answer"] == "all writes done"
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "TOKEN=two\n"
    events = await runtime.list_events(run.id)
    approval_requested = [
        event for event in events if event.event_type is EventType.APPROVAL_REQUESTED
    ]
    approval_reused = [
        event for event in events if event.event_type is EventType.APPROVAL_REUSED
    ]
    assert len(approval_requested) == 1
    assert len(approval_reused) == 1
    assert approval_reused[0].payload["approval_id"] == str(
        interrupted.value.approval_id
    )
    assert approval_reused[0].payload["status"] == "approved"


@pytest.mark.asyncio
async def test_conversation_graph_does_not_reuse_write_file_grant_for_different_path(
    tmp_path: Path,
) -> None:
    conversations = InMemoryConversationRepository()
    runtime = InMemoryRuntimeRepository()
    approvals = InMemoryApprovalRepository()
    thread = await conversations.create_thread(title="Chat", context_path=str(tmp_path))
    run, leader = await _conversation_run(runtime, thread.id, "write env")
    run = run.model_copy(update={"working_directory": tmp_path})
    await runtime.update_run(run)
    registry = build_modifying_registry(sandbox=RecordingSandbox())
    first_call = _write_file_call(
        "call-write-1",
        path=".env",
        content="TOKEN=one\n",
    )
    second_call = _write_file_call(
        "call-write-2",
        path=".npmrc",
        content="token=two\n",
    )
    first_graph = ConversationGraph(
        conversations=conversations,
        runtime=runtime,
        provider_factory=lambda _model: ToolCallProvider(first_call),
        default_model="fake-model",
        tool_registry=registry,
        tool_executor=ToolExecutor(registry, ApprovalPolicy()),
        approval_repository=approvals,
    )

    with pytest.raises(ApprovalInterrupt) as first:
        await first_graph.execute(run, leader)
    await approvals.decide(
        first.value.approval_id,
        approved=True,
        decided_by="tester",
        reason="approved",
        now=datetime.now(UTC),
    )

    second_graph = ConversationGraph(
        conversations=conversations,
        runtime=runtime,
        provider_factory=lambda _model: SequentialToolProvider(
            [first_call, second_call],
            final_after_tools="unreachable",
        ),
        default_model="fake-model",
        tool_registry=registry,
        tool_executor=ToolExecutor(registry, ApprovalPolicy()),
        approval_repository=approvals,
    )

    with pytest.raises(ApprovalInterrupt) as second:
        await second_graph.execute(run, leader)

    assert second.value.approval_id != first.value.approval_id
    assert not (tmp_path / ".npmrc").exists()
    events = await runtime.list_events(run.id)
    approval_requested = [
        event for event in events if event.event_type is EventType.APPROVAL_REQUESTED
    ]
    assert len(approval_requested) == 2
    assert not any(event.event_type is EventType.APPROVAL_REUSED for event in events)


@pytest.mark.asyncio
async def test_conversation_graph_reuses_denied_write_file_scope_for_same_path(
    tmp_path: Path,
) -> None:
    conversations = InMemoryConversationRepository()
    runtime = InMemoryRuntimeRepository()
    approvals = InMemoryApprovalRepository()
    thread = await conversations.create_thread(title="Chat", context_path=str(tmp_path))
    run, leader = await _conversation_run(runtime, thread.id, "write env")
    run = run.model_copy(update={"working_directory": tmp_path})
    await runtime.update_run(run)
    registry = build_modifying_registry(sandbox=RecordingSandbox())
    first_call = _write_file_call(
        "call-write-1",
        path=".env",
        content="TOKEN=one\n",
    )
    second_call = _write_file_call(
        "call-write-2",
        path=".env",
        content="TOKEN=two\n",
    )
    first_graph = ConversationGraph(
        conversations=conversations,
        runtime=runtime,
        provider_factory=lambda _model: ToolCallProvider(first_call),
        default_model="fake-model",
        tool_registry=registry,
        tool_executor=ToolExecutor(registry, ApprovalPolicy()),
        approval_repository=approvals,
    )

    with pytest.raises(ApprovalInterrupt) as interrupted:
        await first_graph.execute(run, leader)
    await approvals.decide(
        interrupted.value.approval_id,
        approved=False,
        decided_by="tester",
        reason="denied",
        now=datetime.now(UTC),
    )

    resume_provider = SequentialToolProvider(
        [first_call, second_call],
        final_after_tools="denials handled",
    )
    state = await ConversationGraph(
        conversations=conversations,
        runtime=runtime,
        provider_factory=lambda _model: resume_provider,
        default_model="fake-model",
        tool_registry=registry,
        tool_executor=ToolExecutor(registry, ApprovalPolicy()),
        approval_repository=approvals,
    ).execute(run, leader)

    assert state["final_answer"] == "denials handled"
    assert not (tmp_path / ".env").exists()
    events = await runtime.list_events(run.id)
    approval_requested = [
        event for event in events if event.event_type is EventType.APPROVAL_REQUESTED
    ]
    approval_reused = [
        event for event in events if event.event_type is EventType.APPROVAL_REUSED
    ]
    assert len(approval_requested) == 1
    assert len(approval_reused) == 1
    assert approval_reused[0].payload["status"] == "denied"
    tool_messages = [
        message
        for message in await conversations.list_messages(thread.id)
        if message.metadata.get("kind") == "tool_result"
    ]
    assert any("denied" in message.content for message in tool_messages)


@pytest.mark.asyncio
async def test_conversation_graph_replays_approved_bash_and_continues_model_loop(
    tmp_path: Path,
) -> None:
    conversations = InMemoryConversationRepository()
    runtime = InMemoryRuntimeRepository()
    approvals = InMemoryApprovalRepository()
    thread = await conversations.create_thread(title="Chat", context_path=str(tmp_path))
    run, leader = await _conversation_run(runtime, thread.id, "run script")
    run = run.model_copy(update={"working_directory": tmp_path})
    await runtime.update_run(run)
    sandbox = RecordingSandbox()
    registry = build_modifying_registry(sandbox=sandbox)
    first_provider = ToolCallProvider(
        ToolCall(
            call_id="call-bash",
            name="Bash",
            arguments_json='{"command":"python square.py"}',
        )
    )
    first_graph = ConversationGraph(
        conversations=conversations,
        runtime=runtime,
        provider_factory=lambda _model: first_provider,
        default_model="fake-model",
        tool_registry=registry,
        tool_executor=ToolExecutor(registry, ApprovalPolicy()),
        approval_repository=approvals,
    )

    with pytest.raises(ApprovalInterrupt) as interrupted:
        await first_graph.execute(run, leader)
    await approvals.decide(
        interrupted.value.approval_id,
        approved=True,
        decided_by="tester",
        reason="approved",
        now=datetime.now(UTC),
    )

    second_provider = ToolCallProvider(
        ToolCall(
            call_id="call-bash",
            name="Bash",
            arguments_json='{"command":"python square.py"}',
        ),
        final_after_tool="approved tool completed",
    )
    second_graph = ConversationGraph(
        conversations=conversations,
        runtime=runtime,
        provider_factory=lambda _model: second_provider,
        default_model="fake-model",
        tool_registry=registry,
        tool_executor=ToolExecutor(registry, ApprovalPolicy()),
        approval_repository=approvals,
    )

    state = await second_graph.execute(run, leader)

    assert state["final_answer"] == "approved tool completed"
    assert len(sandbox.requests) == 1
    assert len(second_provider.requests) == 1
    _assert_reconstructed_tool_context(
        second_provider.requests[0],
        call_id="call-bash",
        is_error=False,
    )
    messages = await conversations.list_messages(thread.id)
    tool_messages = [
        message for message in messages if message.metadata.get("kind") == "tool_result"
    ]
    assert len(tool_messages) == 1
    assert tool_messages[0].metadata["tool_call_id"] == "call-bash"


@pytest.mark.asyncio
async def test_conversation_graph_replays_denied_approval_as_tool_result(
    tmp_path: Path,
) -> None:
    conversations = InMemoryConversationRepository()
    runtime = InMemoryRuntimeRepository()
    approvals = InMemoryApprovalRepository()
    thread = await conversations.create_thread(title="Chat", context_path=str(tmp_path))
    run, leader = await _conversation_run(runtime, thread.id, "run script")
    run = run.model_copy(update={"working_directory": tmp_path})
    await runtime.update_run(run)
    sandbox = RecordingSandbox()
    registry = build_modifying_registry(sandbox=sandbox)
    first_graph = ConversationGraph(
        conversations=conversations,
        runtime=runtime,
        provider_factory=lambda _model: ToolCallProvider(
            ToolCall(
                call_id="call-bash",
                name="Bash",
                arguments_json='{"command":"python square.py"}',
            )
        ),
        default_model="fake-model",
        tool_registry=registry,
        tool_executor=ToolExecutor(registry, ApprovalPolicy()),
        approval_repository=approvals,
    )

    with pytest.raises(ApprovalInterrupt) as interrupted:
        await first_graph.execute(run, leader)
    await approvals.decide(
        interrupted.value.approval_id,
        approved=False,
        decided_by="tester",
        reason="denied",
        now=datetime.now(UTC),
    )

    second_provider = ToolCallProvider(
        ToolCall(
            call_id="call-bash",
            name="Bash",
            arguments_json='{"command":"python square.py"}',
        ),
        final_after_tool="approval denied handled",
    )

    state = await ConversationGraph(
        conversations=conversations,
        runtime=runtime,
        provider_factory=lambda _model: second_provider,
        default_model="fake-model",
        tool_registry=registry,
        tool_executor=ToolExecutor(registry, ApprovalPolicy()),
        approval_repository=approvals,
    ).execute(run, leader)

    assert state["final_answer"] == "approval denied handled"
    assert sandbox.requests == []
    assert len(second_provider.requests) == 1
    _assert_reconstructed_tool_context(
        second_provider.requests[0],
        call_id="call-bash",
        is_error=True,
    )
    messages = await conversations.list_messages(thread.id)
    tool_messages = [
        message for message in messages if message.metadata.get("kind") == "tool_result"
    ]
    assert tool_messages[-1].metadata["is_error"] is True
    assert "denied" in tool_messages[-1].content


@pytest.mark.asyncio
async def test_conversation_graph_replays_expired_approval_as_tool_result(
    tmp_path: Path,
) -> None:
    conversations = InMemoryConversationRepository()
    runtime = InMemoryRuntimeRepository()
    approvals = InMemoryApprovalRepository()
    thread = await conversations.create_thread(title="Chat", context_path=str(tmp_path))
    run, leader = await _conversation_run(runtime, thread.id, "run script")
    run = run.model_copy(update={"working_directory": tmp_path})
    await runtime.update_run(run)
    sandbox = RecordingSandbox()
    registry = build_modifying_registry(sandbox=sandbox)
    first_graph = ConversationGraph(
        conversations=conversations,
        runtime=runtime,
        provider_factory=lambda _model: ToolCallProvider(
            ToolCall(
                call_id="call-bash",
                name="Bash",
                arguments_json='{"command":"python square.py"}',
            )
        ),
        default_model="fake-model",
        tool_registry=registry,
        tool_executor=ToolExecutor(registry, ApprovalPolicy()),
        approval_repository=approvals,
    )

    with pytest.raises(ApprovalInterrupt):
        await first_graph.execute(run, leader)
    expired = await approvals.expire_expired(datetime.now(UTC) + timedelta(hours=2))
    assert len(expired) == 1

    second_provider = ToolCallProvider(
        ToolCall(
            call_id="call-bash",
            name="Bash",
            arguments_json='{"command":"python square.py"}',
        ),
        final_after_tool="approval expired handled",
    )

    state = await ConversationGraph(
        conversations=conversations,
        runtime=runtime,
        provider_factory=lambda _model: second_provider,
        default_model="fake-model",
        tool_registry=registry,
        tool_executor=ToolExecutor(registry, ApprovalPolicy()),
        approval_repository=approvals,
    ).execute(run, leader)

    assert state["final_answer"] == "approval expired handled"
    assert sandbox.requests == []
    assert len(second_provider.requests) == 1
    _assert_reconstructed_tool_context(
        second_provider.requests[0],
        call_id="call-bash",
        is_error=True,
    )
    messages = await conversations.list_messages(thread.id)
    tool_messages = [
        message for message in messages if message.metadata.get("kind") == "tool_result"
    ]
    assert tool_messages[-1].metadata["is_error"] is True
    assert "expired" in tool_messages[-1].content


@pytest.mark.asyncio
async def test_conversation_graph_rejects_approval_resume_workspace_drift(
    tmp_path: Path,
) -> None:
    _init_git_workspace(tmp_path)
    conversations = InMemoryConversationRepository()
    runtime = InMemoryRuntimeRepository()
    approvals = InMemoryApprovalRepository()
    thread = await conversations.create_thread(title="Chat", context_path=str(tmp_path))
    run, leader = await _conversation_run(runtime, thread.id, "run script")
    run = run.model_copy(update={"working_directory": tmp_path})
    await runtime.update_run(run)
    sandbox = RecordingSandbox()
    registry = build_modifying_registry(sandbox=sandbox)
    graph = ConversationGraph(
        conversations=conversations,
        runtime=runtime,
        provider_factory=lambda _model: ToolCallProvider(
            ToolCall(
                call_id="call-bash",
                name="Bash",
                arguments_json='{"command":"python square.py"}',
            )
        ),
        default_model="fake-model",
        tool_registry=registry,
        tool_executor=ToolExecutor(registry, ApprovalPolicy()),
        approval_repository=approvals,
    )

    with pytest.raises(ApprovalInterrupt) as interrupted:
        await graph.execute(run, leader)
    await approvals.decide(
        interrupted.value.approval_id,
        approved=True,
        decided_by="tester",
        reason="approved",
        now=datetime.now(UTC),
    )
    (tmp_path / "tracked.txt").write_text("changed", encoding="utf-8")

    with pytest.raises(CorruptRuntimeStateError, match="workspace changed"):
        await ConversationGraph(
            conversations=conversations,
            runtime=runtime,
            provider_factory=lambda _model: ApprovalReplayFailingProvider(),
            default_model="fake-model",
            tool_registry=registry,
            tool_executor=ToolExecutor(registry, ApprovalPolicy()),
            approval_repository=approvals,
        ).execute(run, leader)


@pytest.mark.asyncio
async def test_conversation_graph_converts_denied_bash_to_tool_result(
    tmp_path: Path,
) -> None:
    conversations = InMemoryConversationRepository()
    runtime = InMemoryRuntimeRepository()
    thread = await conversations.create_thread(title="Chat", context_path=str(tmp_path))
    run, leader = await _conversation_run(runtime, thread.id, "run script")
    run = run.model_copy(update={"working_directory": tmp_path})
    await runtime.update_run(run)
    sandbox = RecordingSandbox()
    registry = build_modifying_registry(sandbox=sandbox)
    provider = ToolCallProvider(
        ToolCall(
            call_id="call-bash",
            name="Bash",
            arguments_json='{"command":"cmd.exe /c python add.py"}',
        )
    )
    graph = ConversationGraph(
        conversations=conversations,
        runtime=runtime,
        provider_factory=lambda _model: provider,
        default_model="fake-model",
        tool_registry=registry,
        tool_executor=ToolExecutor(registry, ApprovalPolicy()),
    )

    state = await graph.execute(run, leader)

    assert state["final_answer"] == "done"
    assert sandbox.requests == []
    events = await runtime.list_events(run.id)
    tool_events = [
        event.payload
        for event in events
        if event.event_type is EventType.TOOL_CALL_CREATED
    ]
    assert tool_events == [
        {
            "tool": "Bash",
            "status": "failed",
            "changed_files": [],
        }
    ]


@pytest.mark.asyncio
async def test_conversation_graph_exposes_six_public_workspace_tools(
    tmp_path: Path,
) -> None:
    conversations = InMemoryConversationRepository()
    runtime = InMemoryRuntimeRepository()
    thread = await conversations.create_thread(title="Chat", context_path=str(tmp_path))
    run, leader = await _conversation_run(runtime, thread.id, "inspect workspace")
    run = run.model_copy(update={"working_directory": tmp_path})
    await runtime.update_run(run)
    provider = CapturingProvider("done")
    registry = build_modifying_registry()
    graph = ConversationGraph(
        conversations=conversations,
        runtime=runtime,
        provider_factory=lambda _model: provider,
        default_model="fake-model",
        tool_registry=registry,
        tool_executor=ToolExecutor(registry, ApprovalPolicy()),
    )

    await graph.execute(run, leader)

    assert len(provider.requests) == 1
    assert {tool.name for tool in provider.requests[0].tools} == {
        "ReadFile",
        "WriteFile",
        "EditFile",
        "Bash",
        "Glob",
        "Grep",
    }


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
