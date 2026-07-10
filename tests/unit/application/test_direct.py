from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from awesome_agent.application.direct import DirectCommandService
from awesome_agent.application.operations import OperationController
from awesome_agent.conversation import ConversationService, ThreadEntryKind
from awesome_agent.core.events import CollectingEventSink, EventEmitter
from awesome_agent.core.tools import (
    ToolActivityDraft,
    ToolExecutionContext,
    ToolExecutionOrigin,
    ToolResult,
    ToolStatus,
)
from awesome_agent.core.workspace import resolve_workspace
from awesome_agent.storage.conversations import SQLiteConversationRepositories


def test_direct_service_has_no_agent_model_or_checkpoint_dependency() -> None:
    source = Path("src/awesome_agent/application/direct.py").read_text(encoding="utf-8")

    assert "awesome_agent.agent" not in source
    assert "ModelGateway" not in source
    assert "checkpoint" not in source.casefold()


class Executor:
    def __init__(self, output: str, *, gate: asyncio.Event | None = None) -> None:
        self.output = output
        self.gate = gate
        self.requests: list[object] = []

    async def execute(
        self,
        request: object,
        *,
        context: ToolExecutionContext,
    ) -> ToolResult:
        self.requests.append(request)
        assert request.tool_name == "execute"
        assert context.origin is ToolExecutionOrigin.DIRECT
        assert context.turn_id is None
        if self.gate is not None:
            await self.gate.wait()
        context.activity_writer.finalize(
            ToolActivityDraft(
                thread_id=context.thread_id,
                operation_id=context.operation_id,
                call_id=request.call_id,
                origin=ToolExecutionOrigin.DIRECT,
                tool_name="execute",
                outcome="success",
                result_summary="completed",
                duration_ms=1,
            )
        )
        return ToolResult(
            call_id=request.call_id,
            tool_name="execute",
            status=ToolStatus.SUCCESS,
            content=self.output,
            metadata={"exit_code": 0},
        )


def _service(
    tmp_path: Path,
    executor: Executor,
) -> tuple[DirectCommandService, ConversationService, str]:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = resolve_workspace(workspace_path)
    repositories = SQLiteConversationRepositories(tmp_path / "application.db")
    conversation = ConversationService(store=repositories)
    thread = conversation.create_thread(workspace.key)
    emitter = EventEmitter(
        session_id="session_1",
        workspace_key=workspace.key,
        sink=CollectingEventSink(),
    )

    def context_factory(thread_id: str, operation_id: str) -> ToolExecutionContext:
        return ToolExecutionContext(
            workspace=workspace,
            thread_id=thread_id,
            operation_id=operation_id,
            turn_id=None,
            origin=ToolExecutionOrigin.DIRECT,
            emitter=emitter,
            activity_writer=repositories.tool_activities,
            monotonic=lambda: 1.0,
        )

    return (
        DirectCommandService(
            conversation=conversation,
            executor=executor,
            operations=OperationController(emitter),
            context_factory=context_factory,
        ),
        conversation,
        thread.id,
    )


@pytest.mark.asyncio
async def test_direct_command_is_foreground_operation_without_agent_turn(
    tmp_path: Path,
) -> None:
    gate = asyncio.Event()
    executor = Executor("working tree clean", gate=gate)
    service, conversation, thread_id = _service(tmp_path, executor)

    accepted = await service.start(thread_id, "git status")
    assert accepted.operation_id
    assert accepted.thread_id == thread_id
    assert accepted.turn_id is None
    assert conversation.read_thread(thread_id).turns == ()

    gate.set()
    await service.wait(accepted.operation_id)
    view = conversation.read_thread(thread_id)

    assert view.turns == ()
    assert len(executor.requests) == 1
    assert len(view.entries) == 1
    assert view.entries[0].kind is ThreadEntryKind.DIRECT_COMMAND
    assert "git status" in view.entries[0].content
    assert "working tree clean" in view.entries[0].content
    assert len(view.tool_activities) == 1
    assert view.tool_activities[0].turn_id is None
    assert view.tool_activities[0].origin.value == "direct"


@pytest.mark.asyncio
async def test_persisted_direct_result_is_bounded_and_redacted(tmp_path: Path) -> None:
    private_path = "C:\\Users\\alice\\private\\secrets.txt"
    secret = "token=super-secret-value"
    executor = Executor(f"{private_path}\n{secret}\n" + "x" * 29_000)
    service, conversation, thread_id = _service(tmp_path, executor)

    accepted = await service.start(thread_id, f"show {private_path} {secret}")
    await service.wait(accepted.operation_id)
    entry = conversation.read_thread(thread_id).entries[0]

    assert len(entry.content) <= 30_000
    assert private_path not in entry.content
    assert "super-secret-value" not in entry.content
    assert "[REDACTED:path]" in entry.content
    assert "[REDACTED:token]" in entry.content
    assert entry.metadata["exit_code"] == 0
    assert entry.metadata["managed_side_effects"] is False
