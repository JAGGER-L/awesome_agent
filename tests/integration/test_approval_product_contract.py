from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from tests.type_helpers import test_settings

import awesome_agent.surfaces.local_runtime_container as local_container_module
from awesome_agent.conversation.events import ConversationStreamEventKind
from awesome_agent.conversation.intake import ConversationRunIntakeService
from awesome_agent.conversation.service import ConversationService
from awesome_agent.domain.enums import (
    ApprovalStatus,
    DispatchStatus,
    EventType,
    RunStatus,
)
from awesome_agent.memory.builtin import BuiltinMemoryStore
from awesome_agent.memory.external import NoopMemoryProvider
from awesome_agent.memory.policy import MemoryPolicy
from awesome_agent.memory.service import MemoryService
from awesome_agent.modeling import (
    AssistantMessage,
    InProcessModelExecutionBackend,
    ModelExecutionService,
    ModelRequest,
    ModelStreamEvent,
    ModelTurn,
    StopReason,
    StructuredModelProvider,
    ToolCall,
    TurnCompleted,
)
from awesome_agent.persistence.approvals import PostgresApprovalRepository
from awesome_agent.persistence.conversations import PostgresConversationRepository
from awesome_agent.persistence.database import create_engine, create_session_factory
from awesome_agent.persistence.dispatch import PostgresRunDispatcher
from awesome_agent.persistence.models import Base
from awesome_agent.persistence.runtime_repository import PostgresRuntimeRepository
from awesome_agent.runtime.conversation_graph import ConversationGraph
from awesome_agent.runtime.events import EventStream
from awesome_agent.runtime.probe_graph import RuntimeProbeState
from awesome_agent.runtime.worker import DurableWorker, WorkerConfig
from awesome_agent.sandbox.base import CommandRequest, CommandResult, SandboxBackend
from awesome_agent.settings import Settings
from awesome_agent.surfaces.local_runtime_container import LocalRuntimeContainer
from awesome_agent.tools.repository import (
    build_modifying_executor,
    build_modifying_registry,
)

pytestmark = pytest.mark.integration


class ApprovalContractProvider(StructuredModelProvider):
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
                                    call_id="call-shell",
                                    name="shell.execute",
                                    arguments_json='{"argv":["python","task.py"]}',
                                )
                            ],
                        ),
                        stop_reason=StopReason.TOOL_CALLS,
                        model="fake-model",
                        provider="fake",
                    )
                )
                return
            raise AssertionError("approval resume must not call the model again")

        return events()


class RecordingSandbox(SandboxBackend):
    name = "recording"

    def __init__(self) -> None:
        self.requests: list[CommandRequest] = []

    async def execute(self, request: CommandRequest) -> CommandResult:
        self.requests.append(request)
        return CommandResult(
            command=request.command_label,
            exit_code=0,
            stdout="tool-ok",
            stderr="",
            sandbox=self.name,
        )


class UnsupportedProbeGraph:
    async def execute(self, run: object) -> tuple[RuntimeProbeState, bool]:
        raise AssertionError("approval contract should execute conversation route")


def _settings(tmp_path: Path) -> Settings:
    return test_settings(local_state_dir=tmp_path / "state")


@pytest.mark.asyncio
async def test_local_conversation_approval_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ApprovalContractProvider()
    sandbox = RecordingSandbox()
    monkeypatch.setattr(
        local_container_module,
        "create_sandbox",
        lambda **_: sandbox,
    )
    container = LocalRuntimeContainer(
        settings=_settings(tmp_path),
        provider_factory=lambda _model: provider,
        default_model="fake-model",
        project_root=tmp_path,
    )
    try:
        thread = await container.conversations.create_thread(
            title="Approval contract",
            context_path=str(tmp_path),
        )
        stream = container.conversation_service.start_turn(
            thread_id=thread.id,
            content="run approved command",
        )
        first = await anext(stream)
        run_id = UUID(str(first.payload["run_id"]))
        await container.worker_pump.drain_until_run_terminal_or_waiting(str(run_id))

        waiting = await container.runtime.get_run(run_id)
        approvals = await container.approvals.list_for_run(run_id)
        assert waiting.status is RunStatus.PAUSED
        assert waiting.dispatch_status is DispatchStatus.WAITING
        assert len(approvals) == 1
        assert approvals[0].status is ApprovalStatus.PENDING

        await container.approvals.decide(
            approvals[0].id,
            approved=True,
            decided_by="test",
            reason="approved",
            now=datetime.now(UTC),
        )
        await container.dispatcher.requeue_after_approval(
            run_id=run_id,
            approval_id=approvals[0].id,
            reason="approval_decided",
        )
        continued = container.conversation_service.continue_turn(
            thread_id=thread.id,
            expected_run_id=run_id,
        )
        continued_first = await anext(continued)
        assert continued_first.event is ConversationStreamEventKind.TURN_CONTINUED

        await container.worker_pump.drain_until_run_terminal_or_waiting(str(run_id))
        remaining = [event async for event in continued]

        restored = await container.runtime.get_run(run_id)
        events = await container.runtime.list_events(run_id)
        approval_events = [
            event
            for event in events
            if event.event_type is EventType.APPROVAL_REQUESTED
        ]
        tool_events = [
            event for event in events if event.event_type is EventType.TOOL_CALL_CREATED
        ]

        assert restored.status is RunStatus.COMPLETED
        assert restored.dispatch_status is DispatchStatus.TERMINAL
        assert len(approval_events) == 1
        assert len(tool_events) == 1
        assert len(provider.requests) == 1
        assert len(sandbox.requests) == 1
        assert remaining[-1].event is ConversationStreamEventKind.TURN_COMPLETED
    finally:
        container.close()


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ,
    reason="Runtime database is not configured.",
)
@pytest.mark.asyncio
async def test_postgres_conversation_approval_contract(tmp_path: Path) -> None:
    provider = ApprovalContractProvider()
    sandbox = RecordingSandbox()
    engine = create_engine(os.environ["AWESOME_AGENT_TEST_DATABASE_URL"])
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = create_session_factory(engine)
    conversations = PostgresConversationRepository(sessions)
    runtime = PostgresRuntimeRepository(sessions)
    approvals = PostgresApprovalRepository(sessions)
    dispatcher = PostgresRunDispatcher(sessions)
    memory = MemoryService(
        builtin=BuiltinMemoryStore(root=tmp_path / "memory", policy=MemoryPolicy()),
        provider=NoopMemoryProvider(),
        builtin_enabled=False,
        provider_enabled=False,
    )
    registry = build_modifying_registry(sandbox=sandbox)
    executor = build_modifying_executor(registry)
    graph = ConversationGraph(
        conversations=conversations,
        runtime=runtime,
        provider_factory=lambda _model: provider,
        default_model="fake-model",
        tool_registry=registry,
        tool_executor=executor,
        memory_service=memory,
        model_execution_service=ModelExecutionService(
            InProcessModelExecutionBackend(lambda _model: provider)
        ),
        approval_repository=approvals,
    )
    service = ConversationService(
        repository=conversations,
        runtime_repository=runtime,
        conversation_run_intake=ConversationRunIntakeService(
            conversations=conversations,
            runtime=runtime,
            events=EventStream(),
            default_model="fake-model",
        ),
        default_model="fake-model",
        event_poll_interval=0,
    )
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=runtime,
        probe_graph=UnsupportedProbeGraph(),  # type: ignore[arg-type]
        conversation_graph=graph,
        config=WorkerConfig(
            lease_duration=timedelta(seconds=60),
            heartbeat_interval=timedelta(seconds=15),
            poll_interval=0.01,
            recovery_interval=15,
            shutdown_grace=0.01,
            retry_delay=timedelta(seconds=0),
            max_attempts=3,
        ),
    )

    try:
        thread = await conversations.create_thread(
            title="Approval contract",
            context_path=str(tmp_path),
        )
        stream = service.start_turn(thread_id=thread.id, content="run approved command")
        first = await anext(stream)
        run_id = UUID(str(first.payload["run_id"]))

        assert await worker.run_once()
        waiting = await runtime.get_run(run_id)
        pending = await approvals.list_for_run(run_id)
        assert waiting.status is RunStatus.PAUSED
        assert waiting.dispatch_status is DispatchStatus.WAITING
        assert len(pending) == 1
        assert pending[0].status is ApprovalStatus.PENDING

        await approvals.decide(
            pending[0].id,
            approved=True,
            decided_by="test",
            reason="approved",
            now=datetime.now(UTC),
        )
        await dispatcher.requeue_after_approval(
            run_id=run_id,
            approval_id=pending[0].id,
            reason="approval_decided",
        )
        continued = service.continue_turn(thread_id=thread.id, expected_run_id=run_id)
        assert (
            await anext(continued)
        ).event is ConversationStreamEventKind.TURN_CONTINUED

        assert await worker.run_once()
        remaining = [event async for event in continued]
        restored = await runtime.get_run(run_id)
        events = await runtime.list_events(run_id)

        assert restored.status is RunStatus.COMPLETED
        assert restored.dispatch_status is DispatchStatus.TERMINAL
        assert (
            len(
                [
                    event
                    for event in events
                    if event.event_type is EventType.APPROVAL_REQUESTED
                ]
            )
            == 1
        )
        assert (
            len(
                [
                    event
                    for event in events
                    if event.event_type is EventType.TOOL_CALL_CREATED
                ]
            )
            == 1
        )
        assert len(provider.requests) == 1
        assert len(sandbox.requests) == 1
        assert remaining[-1].event is ConversationStreamEventKind.TURN_COMPLETED
    finally:
        await engine.dispose()
