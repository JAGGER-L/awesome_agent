from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from threading import Event as ThreadEvent
from typing import Any

import pytest
from pydantic import JsonValue

from awesome_agent.application.direct import DirectCommandService
from awesome_agent.application.operations import OperationBusy, OperationController
from awesome_agent.conversation import ConversationService, ThreadEntryKind
from awesome_agent.core.events import CollectingEventSink, EventEmitter, EventType
from awesome_agent.core.tools import (
    ToolActivityDraft,
    ToolExecutionContext,
    ToolExecutionOrigin,
    ToolRequest,
    ToolResult,
    ToolStatus,
)
from awesome_agent.core.workspace import resolve_workspace
from awesome_agent.storage import ApplicationSQLite
from awesome_agent.storage.conversations import SQLiteConversationRepositories

_ACTIVE_DATABASE: ApplicationSQLite | None = None


@pytest.fixture(autouse=True)
async def application_database(tmp_path: Path) -> AsyncIterator[ApplicationSQLite]:
    global _ACTIVE_DATABASE
    assert _ACTIVE_DATABASE is None
    database = ApplicationSQLite(tmp_path / "application.db")
    await database.initialize()
    _ACTIVE_DATABASE = database
    try:
        yield database
    finally:
        _ACTIVE_DATABASE = None
        await database.aclose()


def _database() -> ApplicationSQLite:
    assert _ACTIVE_DATABASE is not None
    return _ACTIVE_DATABASE


def test_direct_service_has_no_agent_model_or_checkpoint_dependency() -> None:
    source = Path("src/awesome_agent/application/direct.py").read_text(encoding="utf-8")

    assert "awesome_agent.agent" not in source
    assert "ModelGateway" not in source
    assert "checkpoint" not in source.casefold()


class Executor:
    def __init__(self, output: str, *, gate: asyncio.Event | None = None) -> None:
        self.output = output
        self.gate = gate
        self.requests: list[ToolRequest] = []

    async def execute(
        self,
        request: ToolRequest,
        *,
        context: ToolExecutionContext,
    ) -> ToolResult:
        self.requests.append(request)
        assert request.tool_name == "execute"
        assert context.origin is ToolExecutionOrigin.DIRECT
        assert context.turn_id is None
        if self.gate is not None:
            await self.gate.wait()
        await context.activity_writer.finalize(
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


class FailingExecutor(Executor):
    async def execute(
        self,
        request: ToolRequest,
        *,
        context: ToolExecutionContext,
    ) -> ToolResult:
        self.requests.append(request)
        raise RuntimeError("executor failed")


async def _service(
    tmp_path: Path,
    executor: Executor,
    *,
    finalize_operation: Callable[[str], Awaitable[None]] | None = None,
    event_sink: CollectingEventSink | None = None,
) -> tuple[DirectCommandService, ConversationService, str]:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = resolve_workspace(workspace_path)
    repositories = SQLiteConversationRepositories(_database())
    conversation = ConversationService(store=repositories)
    thread = await conversation.create_thread(workspace.key)
    emitter = EventEmitter(
        session_id="session_1",
        workspace_key=workspace.key,
        sink=event_sink or CollectingEventSink(),
    )

    async def context_factory(
        thread_id: str,
        operation_id: str,
        request: ToolRequest,
    ) -> ToolExecutionContext:
        assert request.tool_name == "execute"
        return ToolExecutionContext(
            workspace=workspace,
            thread_id=thread_id,
            operation_id=operation_id,
            turn_id=None,
            origin=ToolExecutionOrigin.DIRECT,
            emitter=emitter,
            activity_writer=repositories,
            monotonic=lambda: 1.0,
        )

    return (
        DirectCommandService(
            conversation=conversation,
            executor=executor,
            operations=OperationController(emitter),
            context_factory=context_factory,
            finalize_operation=finalize_operation or _noop_finalize,
        ),
        conversation,
        thread.id,
    )


async def _noop_finalize(operation_id: str) -> None:
    del operation_id


@pytest.mark.asyncio
async def test_direct_command_is_foreground_operation_without_agent_turn(
    tmp_path: Path,
) -> None:
    gate = asyncio.Event()
    executor = Executor("working tree clean", gate=gate)
    service, conversation, thread_id = await _service(tmp_path, executor)

    accepted = await service.start(thread_id, "git status")
    assert accepted.operation_id
    assert accepted.thread_id == thread_id
    assert accepted.turn_id is None
    assert (await conversation.read_thread(thread_id)).turns == ()

    gate.set()
    await service.wait(accepted.operation_id)
    view = await conversation.read_thread(thread_id)

    assert view.turns == ()
    assert len(executor.requests) == 1
    assert len(view.entries) == 1
    assert view.entries[0].kind is ThreadEntryKind.DIRECT_COMMAND
    assert view.entries[0].metadata["operation_id"] == accepted.operation_id
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
    service, conversation, thread_id = await _service(tmp_path, executor)

    accepted = await service.start(thread_id, f"show {private_path} {secret}")
    await service.wait(accepted.operation_id)
    entry = (await conversation.read_thread(thread_id)).entries[0]

    assert len(entry.content) <= 30_000
    assert private_path not in entry.content
    assert "super-secret-value" not in entry.content
    assert "[REDACTED:path]" in entry.content
    assert "[REDACTED:token]" in entry.content
    assert entry.metadata["exit_code"] == 0
    assert entry.metadata["managed_side_effects"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled"])
async def test_terminal_direct_task_is_retained_for_late_wait(
    tmp_path: Path,
    terminal: str,
) -> None:
    gate = asyncio.Event() if terminal == "cancelled" else None
    executor = (
        FailingExecutor("unused")
        if terminal == "failed"
        else Executor("done", gate=gate)
    )
    service, _, thread_id = await _service(tmp_path, executor)

    accepted = await service.start(thread_id, "echo done")
    if terminal == "cancelled":
        assert await service._operations.cancel(accepted.operation_id) is True
    for _ in range(4):
        await asyncio.sleep(0)

    assert accepted.operation_id in service._tasks

    if terminal == "failed":
        with pytest.raises(RuntimeError, match="executor failed"):
            await service.wait(accepted.operation_id)
    elif terminal == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            await service.wait(accepted.operation_id)
    else:
        await service.wait(accepted.operation_id)
    assert service._tasks == {}


@pytest.mark.asyncio
async def test_wait_can_observe_a_direct_task_that_completed_before_lookup(
    tmp_path: Path,
) -> None:
    service, _, thread_id = await _service(tmp_path, Executor("done"))

    accepted = await service.start(thread_id, "echo done")
    for _ in range(4):
        await asyncio.sleep(0)

    await service.wait(accepted.operation_id)

    assert service._tasks == {}


@pytest.mark.asyncio
async def test_unclaimed_direct_task_history_is_bounded(tmp_path: Path) -> None:
    service, _, thread_id = await _service(tmp_path, Executor("done"))

    for index in range(70):
        accepted = await service.start(thread_id, f"echo {index}")
        while not service._tasks[accepted.operation_id].done():
            await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert len(service._tasks) == 64


@pytest.mark.asyncio
async def test_wait_still_propagates_direct_failure_and_releases_task(
    tmp_path: Path,
) -> None:
    service, _, thread_id = await _service(tmp_path, FailingExecutor("unused"))

    accepted = await service.start(thread_id, "false")

    with pytest.raises(RuntimeError, match="executor failed"):
        await service.wait(accepted.operation_id)
    assert service._tasks == {}


@pytest.mark.asyncio
async def test_cancel_preserves_cancellation_when_finalizer_fails(
    tmp_path: Path,
) -> None:
    gate = asyncio.Event()
    finalized: list[str] = []

    async def fail_finalizer(operation_id: str) -> None:
        finalized.append(operation_id)
        raise RuntimeError("finalizer failed")

    service, _, thread_id = await _service(
        tmp_path,
        Executor("unused", gate=gate),
        finalize_operation=fail_finalizer,
    )
    accepted = await service.start(thread_id, "echo cancelled")

    assert await service._operations.cancel(accepted.operation_id) is True
    with pytest.raises(asyncio.CancelledError):
        await service.wait(accepted.operation_id)

    assert finalized == [accepted.operation_id]
    assert service._operations.active_operation_id is None


@pytest.mark.asyncio
async def test_cancel_holds_operation_until_durable_transcript_and_seal_finish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = asyncio.Event()
    finalized: list[str] = []

    async def finalize(operation_id: str) -> None:
        finalized.append(operation_id)

    executor = Executor("unused", gate=gate)
    service, conversation, thread_id = await _service(
        tmp_path,
        executor,
        finalize_operation=finalize,
    )
    accepted = await service.start(thread_id, "echo cancelled")
    while not executor.requests:
        await asyncio.sleep(0)

    worker_entered = ThreadEvent()
    release_worker = ThreadEvent()

    def hold_worker(connection: object) -> None:
        del connection
        worker_entered.set()
        assert release_worker.wait(timeout=2)

    blocker = asyncio.create_task(_database().read(hold_worker))
    while not worker_entered.is_set():
        await asyncio.sleep(0.001)
    monkeypatch.setattr(
        "awesome_agent.application.direct._DIRECT_CANCELLATION_FINALIZATION_SECONDS",
        0.01,
        raising=False,
    )

    started = asyncio.get_running_loop().time()
    assert await service._operations.cancel(accepted.operation_id) is True
    assert asyncio.get_running_loop().time() - started < 0.25
    await asyncio.sleep(0.03)

    assert service._operations.active_operation_id == accepted.operation_id
    with pytest.raises(OperationBusy):
        await service.start(thread_id, "echo second")
    shutdown = asyncio.create_task(service._operations.shutdown())
    await asyncio.sleep(0.03)
    assert not shutdown.done()
    assert not service._tasks[accepted.operation_id].done()
    assert finalized == []

    release_worker.set()
    await blocker
    with pytest.raises(asyncio.CancelledError):
        await service.wait(accepted.operation_id)
    await asyncio.wait_for(shutdown, timeout=1)

    [entry] = (await conversation.read_thread(thread_id)).entries
    assert entry.kind is ThreadEntryKind.DIRECT_COMMAND
    assert entry.metadata["status"] == "cancelled"
    assert finalized == [accepted.operation_id]
    assert service._operations.active_operation_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["cancelled", "failed"])
async def test_direct_primary_failure_survives_transcript_persistence_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    terminal: str,
) -> None:
    gate = asyncio.Event() if terminal == "cancelled" else None
    executor = (
        Executor("unused", gate=gate)
        if terminal == "cancelled"
        else FailingExecutor("unused")
    )
    service, conversation, thread_id = await _service(tmp_path, executor)

    async def fail_persistence(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise OSError("persistence failed")

    monkeypatch.setattr(conversation, "append_direct_command", fail_persistence)
    accepted = await service.start(thread_id, "echo terminal")

    if terminal == "cancelled":
        assert await service._operations.cancel(accepted.operation_id) is True
        with pytest.raises(asyncio.CancelledError):
            await service.wait(accepted.operation_id)
    else:
        with pytest.raises(RuntimeError, match="executor failed"):
            await service.wait(accepted.operation_id)

    assert service._operations.active_operation_id is None


@pytest.mark.asyncio
async def test_direct_write_error_reconciles_one_exact_durable_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sink = CollectingEventSink()
    service, conversation, thread_id = await _service(
        tmp_path,
        Executor("done"),
        event_sink=sink,
    )
    persist = conversation.append_direct_command

    async def commit_then_raise(*args: Any, **kwargs: Any) -> None:
        await persist(*args, **kwargs)
        raise RuntimeError("connection close failed after commit")

    monkeypatch.setattr(conversation, "append_direct_command", commit_then_raise)
    accepted = await service.start(thread_id, "echo done")

    await service.wait(accepted.operation_id)

    entries = (await conversation.read_thread(thread_id)).entries
    assert len(entries) == 1
    assert entries[0].metadata["operation_id"] == accepted.operation_id
    assert [event.event_type for event in sink.events][
        -1
    ] is EventType.OPERATION_COMPLETED


@pytest.mark.asyncio
async def test_direct_write_error_before_commit_fails_without_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sink = CollectingEventSink()
    service, conversation, thread_id = await _service(
        tmp_path,
        Executor("done"),
        event_sink=sink,
    )

    async def fail_before_commit(
        target_thread_id: str,
        content: str,
        metadata: dict[str, JsonValue],
    ) -> None:
        del target_thread_id, content, metadata
        raise RuntimeError("write failed before commit")

    monkeypatch.setattr(conversation, "append_direct_command", fail_before_commit)
    accepted = await service.start(thread_id, "echo done")

    with pytest.raises(RuntimeError, match="write failed before commit"):
        await service.wait(accepted.operation_id)

    assert (await conversation.read_thread(thread_id)).entries == ()
    event_types = [event.event_type for event in sink.events]
    assert event_types.count(EventType.OPERATION_COMPLETED) == 0
    assert event_types.count(EventType.OPERATION_FAILED) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("durable_state", ["conflict", "duplicate"])
async def test_direct_write_error_requires_one_exact_durable_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    durable_state: str,
) -> None:
    sink = CollectingEventSink()
    service, conversation, thread_id = await _service(
        tmp_path,
        Executor("done"),
        event_sink=sink,
    )
    persist = conversation.append_direct_command

    async def persist_invalid_state_then_raise(
        target_thread_id: str,
        content: str,
        metadata: dict[str, JsonValue],
    ) -> None:
        if durable_state == "conflict":
            await persist(target_thread_id, f"{content} conflict", metadata)
        else:
            await persist(target_thread_id, content, metadata)
            await persist(target_thread_id, content, metadata)
        raise RuntimeError("ambiguous write outcome")

    monkeypatch.setattr(
        conversation,
        "append_direct_command",
        persist_invalid_state_then_raise,
    )
    accepted = await service.start(thread_id, "echo done")

    with pytest.raises(RuntimeError, match="ambiguous write outcome"):
        await service.wait(accepted.operation_id)

    entries = (await conversation.read_thread(thread_id)).entries
    assert len(entries) == (1 if durable_state == "conflict" else 2)
    event_types = [event.event_type for event in sink.events]
    assert event_types.count(EventType.OPERATION_COMPLETED) == 0
    assert event_types.count(EventType.OPERATION_FAILED) == 1


@pytest.mark.asyncio
async def test_direct_finalizer_failure_keeps_success_entry_but_fails_operation(
    tmp_path: Path,
) -> None:
    sink = CollectingEventSink()

    async def fail_finalizer(operation_id: str) -> None:
        del operation_id
        raise RuntimeError("journal seal failed")

    service, conversation, thread_id = await _service(
        tmp_path,
        Executor("done"),
        finalize_operation=fail_finalizer,
        event_sink=sink,
    )
    accepted = await service.start(thread_id, "echo done")

    with pytest.raises(RuntimeError, match="journal seal failed"):
        await service.wait(accepted.operation_id)

    entries = (await conversation.read_thread(thread_id)).entries
    assert len(entries) == 1
    assert entries[0].metadata == {
        "operation_id": accepted.operation_id,
        "exit_code": 0,
        "status": "success",
        "truncated": False,
        "managed_side_effects": False,
    }
    assert [event.event_type for event in sink.events][-1] is EventType.OPERATION_FAILED
