from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from pathlib import Path
from typing import Any, cast

from tests.type_helpers import test_settings

from awesome_agent.conversation.events import ConversationStreamEvent
from awesome_agent.modeling.messages import AssistantMessage
from awesome_agent.modeling.provider import StructuredModelProvider
from awesome_agent.modeling.stream import ModelStreamEvent, TurnCompleted
from awesome_agent.modeling.turns import ModelRequest, ModelTurn, StopReason
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
        resume_run_id: str | None = None,
    ) -> Iterable[ConversationStreamEvent]:
        self.streamed.append((thread_id, content))
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


def test_local_surface_client_cancel_delegates_to_host() -> None:
    host = FakeHost()
    client = LocalSurfaceClient(host=cast(Any, host))

    result = client.cancel("run-1")

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


def test_local_surface_client_approval_delegates_without_fake_unsupported() -> None:
    host = FakeHost()
    client = LocalSurfaceClient(host=cast(Any, host))

    result = client.decide_approval("run-1", "approval-1", approved=False)

    assert host.approvals == [("run-1", "approval-1", False)]
    assert result == {
        "run_id": "run-1",
        "approval_id": "approval-1",
        "approved": False,
        "status": "not_found",
        "reason": "approval_not_found",
    }
