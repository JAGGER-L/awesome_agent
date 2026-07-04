from __future__ import annotations

from collections.abc import AsyncIterator, Coroutine, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

from tests.type_helpers import test_settings

from awesome_agent.conversation.events import (
    ConversationStreamEvent,
    ConversationStreamEventKind,
)
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
from awesome_agent.modeling.messages import AssistantMessage
from awesome_agent.modeling.provider import StructuredModelProvider
from awesome_agent.modeling.stream import ModelStreamEvent, TurnCompleted
from awesome_agent.modeling.turns import ModelRequest, ModelTurn, StopReason
from awesome_agent.persistence.approval_contracts import DurableApproval
from awesome_agent.surfaces.client import SurfaceThread
from awesome_agent.surfaces.local_client import LocalSurfaceClient
from awesome_agent.surfaces.local_runtime_host import LocalRuntimeHost


class FakeProvider(StructuredModelProvider):
    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        yield TurnCompleted(
            turn=ModelTurn(
                assistant=AssistantMessage(content="done"),
                stop_reason=StopReason.COMPLETED,
                model="fake-model",
                provider="fake",
            )
        )


class FakeHost:
    def __init__(self) -> None:
        self.thread = SurfaceThread(
            id="thread-1",
            title="Test",
            short_id="thread-1",
            context_label="workspace",
        )
        self.streamed: list[tuple[str, str]] = []
        self.continued: list[tuple[str, str | None, int]] = []
        self.cancelled: list[str] = []
        self.approvals: list[tuple[str, str, bool]] = []

    def close(self) -> None:
        pass

    def create_thread(self, title: str, **kwargs: object) -> SurfaceThread:
        return self.thread

    def list_threads(self) -> list[SurfaceThread]:
        return [self.thread]

    def resume_thread(self, query: str) -> SurfaceThread:
        return self.thread

    def list_thread_messages(self, thread_id: str) -> list[dict[str, object]]:
        return []

    def stream_turn(
        self,
        thread_id: str,
        content: str,
        *,
        model: str | None = None,
        thinking: str | None = None,
        memory: dict[str, object] | None = None,
        skill_ids: tuple[str, ...] = (),
        attachment_ids: tuple[str, ...] = (),
    ) -> Iterable[ConversationStreamEvent]:
        self.streamed.append((thread_id, content))
        return []

    def continue_turn(
        self,
        thread_id: str,
        *,
        expected_run_id: str | None = None,
        after_sequence: int = 0,
    ) -> Iterable[ConversationStreamEvent]:
        self.continued.append((thread_id, expected_run_id, after_sequence))
        return []

    def runtime_status(self) -> dict[str, object]:
        return {"runtime": "embedded", "transport": "local"}

    def list_models(self) -> list[dict[str, object]]:
        return [{"name": "fake-model"}]

    def memory_summary(self) -> dict[str, object]:
        return {"enabled": False}

    def config_summary(self) -> dict[str, object]:
        return {"mode": "embedded"}

    def cancel(self, run_id: str) -> dict[str, object]:
        self.cancelled.append(run_id)
        return {
            "run_id": run_id,
            "status": "completed",
            "dispatch_status": "terminal",
            "event_sequence": None,
        }

    def decide_approval(
        self,
        run_id: str,
        approval_id: str,
        *,
        approved: bool,
    ) -> dict[str, object]:
        self.approvals.append((run_id, approval_id, approved))
        return {
            "run_id": run_id,
            "approval_id": approval_id,
            "approved": approved,
            "status": "not_found",
            "reason": "approval_not_found",
        }


def test_local_surface_client_streams_without_http() -> None:
    host = FakeHost()
    client = LocalSurfaceClient(host=cast(Any, host))

    list(client.stream_turn("thread-1", "hi"))

    assert host.streamed == [("thread-1", "hi")]


def test_local_surface_client_continue_delegates_to_host() -> None:
    host = FakeHost()
    client = LocalSurfaceClient(host=cast(Any, host))

    list(client.continue_turn("thread-1", expected_run_id="run-1", after_sequence=8))

    assert host.continued == [("thread-1", "run-1", 8)]


def test_local_surface_client_status_does_not_reference_http_health() -> None:
    client = LocalSurfaceClient(host=cast(Any, FakeHost()))

    assert client.runtime_status() == {"runtime": "embedded", "transport": "local"}


def test_local_surface_client_has_no_explicit_run_creation() -> None:
    client = LocalSurfaceClient(host=cast(Any, FakeHost()))

    assert not hasattr(client, "start_" + "explicit_run")


def test_local_surface_cancel_uses_runtime_state(tmp_path: Path) -> None:
    host = LocalRuntimeHost(
        settings=test_settings(local_state_dir=tmp_path / "state"),
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
    )
    thread = host.create_thread("Chat", context_path=str(tmp_path))
    events = list(host.stream_turn(thread.id, "hello"))
    run_id = str(events[0].payload["run_id"])

    result = host.cancel(run_id)

    assert result["run_id"] == run_id
    assert result["status"] in {
        "cancelled",
        "completed",
        "created",
        "running",
        "recovery_required",
    }
    assert isinstance(result["dispatch_status"], str)
    assert "event_sequence" in result
    host.close()


def test_local_surface_cancel_reports_missing_run_as_not_found(tmp_path: Path) -> None:
    host = LocalRuntimeHost(
        settings=test_settings(local_state_dir=tmp_path / "state"),
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
    )
    run_id = "00000000-0000-0000-0000-000000000000"

    result = host.cancel(run_id)

    assert result == {
        "run_id": run_id,
        "status": "not_found",
        "reason": "run_not_found",
        "dispatch_status": None,
        "event_sequence": None,
    }
    host.close()


def test_local_host_continue_turn_resumes_existing_run_without_new_message(
    tmp_path: Path,
) -> None:
    host = LocalRuntimeHost(
        settings=test_settings(local_state_dir=tmp_path / "state"),
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
    )
    thread = host.create_thread("Chat", context_path=str(tmp_path))
    run = _waiting_conversation_run(tmp_path)
    _run_async(host._container.runtime.create_run(run, _leader(run)))
    _run_async(
        host._container.runtime.append_event(
            run_id=run.id,
            event_type=EventType.RUN_CREATED,
            payload={
                "thread_id": thread.id,
                "goal": "original request",
                "model": "fake-model",
            },
        )
    )
    _run_async(
        host._container.conversations.append_message(
            thread_id=UUID(thread.id),
            role=ThreadMessageRole.USER,
            content="original request",
            run_id=run.id,
            metadata={"run_id": str(run.id)},
        )
    )

    events = list(host.continue_turn(thread.id, expected_run_id=str(run.id)))
    messages = host.list_thread_messages(thread.id)
    stored_run = _run_async(host._container.runtime.get_run(run.id))

    assert events[0].event is ConversationStreamEventKind.TURN_CONTINUED
    assert events[0].payload["run_id"] == str(run.id)
    assert [
        message["content"]
        for message in messages
        if message["role"] == ThreadMessageRole.USER.value
    ] == ["original request"]
    assert stored_run.status is RunStatus.PAUSED
    host.close()


def test_local_host_continue_turn_yields_current_projected_wait_event(
    tmp_path: Path,
) -> None:
    host = LocalRuntimeHost(
        settings=test_settings(local_state_dir=tmp_path / "state"),
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
    )
    thread = host.create_thread("Chat", context_path=str(tmp_path))
    run = _waiting_conversation_run(tmp_path)
    _run_async(host._container.runtime.create_run(run, _leader(run)))
    _run_async(
        host._container.runtime.append_event(
            run_id=run.id,
            event_type=EventType.RUN_CREATED,
            payload={
                "thread_id": thread.id,
                "goal": "original request",
                "model": "fake-model",
            },
        )
    )
    _run_async(
        host._container.runtime.append_event(
            run_id=run.id,
            event_type=EventType.TOOL_CALL_CREATED,
            payload={
                "tool": "shell.execute",
                "status": "approval_pending",
            },
        )
    )

    events = list(host.continue_turn(thread.id, expected_run_id=str(run.id)))

    assert [event.event for event in events] == [
        ConversationStreamEventKind.TURN_CONTINUED,
        ConversationStreamEventKind.TOOL_PROGRESS,
    ]
    assert events[1].payload["tool"] == "shell.execute"
    assert events[1].payload["status"] == "approval_pending"
    host.close()


def test_local_surface_client_cancel_delegates_to_host() -> None:
    host = FakeHost()
    client = LocalSurfaceClient(host=cast(Any, host))

    result = client.cancel("run-1", thread_id="thread-1")

    assert host.cancelled == ["run-1"]
    assert result == {
        "run_id": "run-1",
        "status": "completed",
        "dispatch_status": "terminal",
        "event_sequence": None,
    }


def test_local_surface_approval_reports_no_pending_invocation_without_fake_unsupported(
    tmp_path: Path,
) -> None:
    host = LocalRuntimeHost(
        settings=test_settings(local_state_dir=tmp_path / "state"),
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
    )

    result = host.decide_approval(
        "00000000-0000-0000-0000-000000000000",
        "approval-1",
        approved=True,
    )

    assert result["status"] == "not_found"
    assert result["reason"] == "approval_not_found"
    host.close()


def test_local_surface_approval_decision_requeues_waiting_run(tmp_path: Path) -> None:
    host = LocalRuntimeHost(
        settings=test_settings(local_state_dir=tmp_path / "state"),
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
    )
    run = _waiting_conversation_run(tmp_path)
    approval = _approval(run.id, tmp_path)
    pump = _RecordingPump()
    host._container.worker_pump = cast(Any, pump)
    _run_async(host._container.runtime.create_run(run, _leader(run)))
    _run_async(host._container.approvals.upsert(approval))
    _run_async(_append_approval_wait(host, run.id, approval.id))

    result = host.decide_approval(str(run.id), str(approval.id), approved=True)
    stored_approval = _run_async(host._container.approvals.get(approval.id))
    stored_run = _run_async(host._container.runtime.get_run(run.id))
    events = _run_async(host._container.runtime.list_events(run.id))

    assert result == {
        "run_id": str(run.id),
        "approval_id": str(approval.id),
        "approved": True,
        "status": "approved",
        "reason": "approval_decided",
    }
    assert stored_approval.status is ApprovalStatus.APPROVED
    assert stored_run.status is RunStatus.RUNNING
    assert stored_run.dispatch_status is DispatchStatus.QUEUED
    assert pump.drained == [str(run.id)]
    assert EventType.APPROVAL_DECIDED in [event.event_type for event in events]
    host.close()


def test_local_surface_repeated_approval_decision_does_not_requeue(
    tmp_path: Path,
) -> None:
    host = LocalRuntimeHost(
        settings=test_settings(local_state_dir=tmp_path / "state"),
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
    )
    run = _waiting_conversation_run(tmp_path)
    approval = _approval(run.id, tmp_path)
    pump = _RecordingPump()
    host._container.worker_pump = cast(Any, pump)
    _run_async(host._container.runtime.create_run(run, _leader(run)))
    _run_async(host._container.approvals.upsert(approval))
    _run_async(_append_approval_wait(host, run.id, approval.id))
    first = host.decide_approval(str(run.id), str(approval.id), approved=True)
    rewaiting = _run_async(host._container.runtime.get_run(run.id)).model_copy(
        update={
            "status": RunStatus.PAUSED,
            "dispatch_status": DispatchStatus.WAITING,
            "last_release_reason": "waiting_new_approval",
        }
    )
    _run_async(host._container.runtime.update_run(rewaiting))

    replay = host.decide_approval(str(run.id), str(approval.id), approved=False)
    stored_run = _run_async(host._container.runtime.get_run(run.id))

    assert first["status"] == "approved"
    assert replay == {
        "run_id": str(run.id),
        "approval_id": str(approval.id),
        "approved": False,
        "status": "approved",
        "reason": "approval_not_pending",
    }
    assert stored_run.status is RunStatus.PAUSED
    assert stored_run.dispatch_status is DispatchStatus.WAITING
    assert stored_run.last_release_reason == "waiting_new_approval"
    assert pump.drained == [str(run.id)]
    host.close()


def test_local_surface_stale_pending_approval_decision_does_not_requeue(
    tmp_path: Path,
) -> None:
    host = LocalRuntimeHost(
        settings=test_settings(local_state_dir=tmp_path / "state"),
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
    )
    run = _waiting_conversation_run(tmp_path)
    stale_approval = _approval(run.id, tmp_path)
    current_approval = _approval(run.id, tmp_path)
    pump = _RecordingPump()
    host._container.worker_pump = cast(Any, pump)
    _run_async(host._container.runtime.create_run(run, _leader(run)))
    _run_async(host._container.approvals.upsert(stale_approval))
    _run_async(host._container.approvals.upsert(current_approval))
    _run_async(_append_approval_wait(host, run.id, stale_approval.id))
    _run_async(_append_approval_wait(host, run.id, current_approval.id))

    result = host.decide_approval(str(run.id), str(stale_approval.id), approved=True)
    stored_run = _run_async(host._container.runtime.get_run(run.id))
    stored_stale = _run_async(host._container.approvals.get(stale_approval.id))

    assert result == {
        "run_id": str(run.id),
        "approval_id": str(stale_approval.id),
        "approved": True,
        "status": "pending",
        "reason": "approval_not_current",
    }
    assert stored_stale.status is ApprovalStatus.PENDING
    assert stored_run.status is RunStatus.PAUSED
    assert stored_run.dispatch_status is DispatchStatus.WAITING
    assert pump.drained == []
    host.close()


def test_local_surface_cancelled_approval_decision_does_not_requeue(
    tmp_path: Path,
) -> None:
    host = LocalRuntimeHost(
        settings=test_settings(local_state_dir=tmp_path / "state"),
        provider_factory=lambda _model: FakeProvider(),
        default_model="fake-model",
    )
    run = _waiting_conversation_run(tmp_path)
    approval = _approval(run.id, tmp_path)
    pump = _RecordingPump()
    host._container.worker_pump = cast(Any, pump)
    _run_async(host._container.runtime.create_run(run, _leader(run)))
    _run_async(host._container.approvals.upsert(approval))
    _run_async(_append_approval_wait(host, run.id, approval.id))
    host.cancel(str(run.id))
    event_count = len(
        [
            event
            for event in _run_async(host._container.runtime.list_events(run.id))
            if event.event_type is EventType.APPROVAL_DECIDED
        ]
    )

    result = host.decide_approval(str(run.id), str(approval.id), approved=True)
    stored_run = _run_async(host._container.runtime.get_run(run.id))
    final_event_count = len(
        [
            event
            for event in _run_async(host._container.runtime.list_events(run.id))
            if event.event_type is EventType.APPROVAL_DECIDED
        ]
    )

    assert result == {
        "run_id": str(run.id),
        "approval_id": str(approval.id),
        "approved": True,
        "status": "denied",
        "reason": "approval_not_pending",
    }
    assert stored_run.status is RunStatus.CANCELLED
    assert stored_run.dispatch_status is DispatchStatus.TERMINAL
    assert final_event_count == event_count
    assert pump.drained == []
    host.close()


def test_local_surface_client_approval_delegates_without_fake_unsupported() -> None:
    host = FakeHost()
    client = LocalSurfaceClient(host=cast(Any, host))

    result = client.decide_approval(
        "run-1",
        "approval-1",
        approved=False,
        thread_id="thread-1",
    )

    assert host.approvals == [("run-1", "approval-1", False)]
    assert result == {
        "run_id": "run-1",
        "approval_id": "approval-1",
        "approved": False,
        "status": "not_found",
        "reason": "approval_not_found",
    }


def _run_async[T](awaitable: Coroutine[Any, Any, T]) -> T:
    import asyncio

    return asyncio.run(awaitable)


def _waiting_conversation_run(tmp_path: Path) -> Run:
    return Run(
        goal="original request",
        intent=RunIntent.CONVERSATION,
        execution_kind=ExecutionKind.CONVERSATION,
        runtime_route="conversation-turn",
        status=RunStatus.PAUSED,
        dispatch_status=DispatchStatus.WAITING,
        working_directory=tmp_path,
    )


def _leader(run: Run) -> Agent:
    return Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="fake-model",
        status=AgentStatus.READY,
    )


async def _append_approval_wait(
    host: LocalRuntimeHost,
    run_id: UUID,
    approval_id: UUID,
) -> None:
    await host._container.runtime.append_event(
        run_id=run_id,
        event_type=EventType.DISPATCH_RELEASED,
        payload={
            "dispatch_status": DispatchStatus.WAITING.value,
            "approval_id": str(approval_id),
            "reason": "approval_wait",
        },
    )
    await host._container.runtime.append_event(
        run_id=run_id,
        event_type=EventType.RUN_STATUS_CHANGED,
        payload={
            "status": RunStatus.PAUSED.value,
            "dispatch_status": DispatchStatus.WAITING.value,
            "approval_id": str(approval_id),
            "reason": "approval_wait",
        },
    )


def _approval(run_id: UUID, tmp_path: Path) -> DurableApproval:
    approval_id = uuid4()
    return DurableApproval(
        id=approval_id,
        run_id=run_id,
        tool_invocation_id=approval_id,
        tool_call_id="call_shell",
        tool_name="shell.execute",
        tool_version="1",
        canonical_arguments={"argv": ["git", "status"]},
        arguments_hash="hash",
        workspace_path=str(tmp_path),
        workspace_fingerprint="fingerprint",
        capabilities=["shell:execute"],
        risk_level="medium",
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )


class _RecordingPump:
    def __init__(self) -> None:
        self.drained: list[str] = []

    async def drain_until_run_terminal_or_waiting(self, run_id: str) -> int:
        self.drained.append(run_id)
        return 0
