from __future__ import annotations

from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

from awesome_agent.conversation.events import (
    ConversationStreamEvent,
    ConversationStreamEventKind,
)
from awesome_agent.tui.app import AwesomeAgentTui
from awesome_agent.tui.chat_state import ChatEventKind
from awesome_agent.tui.events import ApprovalPromptState


class FakeSurfaceClient:
    def __init__(self) -> None:
        self.continued: list[tuple[str, str | None, int]] = []
        self.cancelled: list[tuple[str, str | None]] = []
        self.approvals: list[tuple[str, str, bool, str | None]] = []

    def close(self) -> None:
        pass

    def stream_turn(self, *_args: object, **_kwargs: object) -> Iterable[object]:
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

    def cancel(
        self,
        run_id: str,
        *,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        self.cancelled.append((run_id, thread_id))
        return {"id": run_id, "status": "cancel_requested"}

    def decide_approval(
        self,
        run_id: str,
        approval_id: str,
        *,
        approved: bool,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        self.approvals.append((run_id, approval_id, approved, thread_id))
        return {"event_type": "approval.decided"}


def test_tui_continue_input_uses_continuation_without_user_message() -> None:
    client = FakeSurfaceClient()
    app = _app(client)
    started: list[str | None] = []
    app.state = app.state.with_backend_thread("thread-1").mark_operation_paused("run-1")
    app._start_continue_turn = lambda *, expected_run_id=None: started.append(  # type: ignore[method-assign]
        expected_run_id
    )

    app.on_input_submitted(cast(Any, _submitted("continue")))

    assert started == ["run-1"]
    assert [message.content for message in app.state.messages] == []


def test_tui_approval_approve_once_decides_and_continues() -> None:
    client = FakeSurfaceClient()
    app = _app(client)
    continued: list[str | None] = []
    app.state = app.state.with_backend_thread("thread-1").with_approval_prompt(
        ApprovalPromptState(
            run_id="run-1",
            approval_id="approval-1",
            title="Leader wants to run:",
            subject="git status",
            approval_type="command",
        )
    )
    app._start_continue_turn = lambda *, expected_run_id=None: continued.append(  # type: ignore[method-assign]
        expected_run_id
    )

    app._apply_approval_choice(0)

    assert client.approvals == [("run-1", "approval-1", True, "thread-1")]
    assert continued == ["run-1"]
    assert app.state.pending_approval is None
    assert app.state.messages[-1].content == "Approved once. Continuing response."


def test_tui_approval_deny_decides_and_continues() -> None:
    client = FakeSurfaceClient()
    app = _app(client)
    continued: list[str | None] = []
    app.state = app.state.with_backend_thread("thread-1").with_approval_prompt(
        ApprovalPromptState(
            run_id="run-1",
            approval_id="approval-1",
            title="Leader wants to edit:",
            subject="app.py",
        )
    )
    app._start_continue_turn = lambda *, expected_run_id=None: continued.append(  # type: ignore[method-assign]
        expected_run_id
    )

    app._apply_approval_choice(1)

    assert client.approvals == [("run-1", "approval-1", False, "thread-1")]
    assert continued == ["run-1"]
    assert app.state.messages[-1].content == (
        "Denied. Continuing response with the denied tool result."
    )


def test_tui_approval_cancel_requests_run_cancel() -> None:
    client = FakeSurfaceClient()
    app = _app(client)
    app.state = app.state.with_backend_thread("thread-1").with_approval_prompt(
        ApprovalPromptState(
            run_id="run-1",
            approval_id="approval-1",
            title="Leader wants to run:",
            subject="npm install",
        )
    )

    app._apply_approval_choice(2)

    assert client.cancelled == [("run-1", "thread-1")]
    assert client.approvals == []
    assert app.state.pending_approval is None
    assert app.state.messages[-1].content == "Cancelling Run..."


def test_tui_cancel_active_operation_requests_cancel_without_pausing() -> None:
    client = FakeSurfaceClient()
    app = _app(client)
    app._active_worker = SimpleNamespace(cancel=lambda: None)  # type: ignore[assignment]
    app.state = (
        app.state.with_backend_thread("thread-1")
        .begin_operation("op-1", "streaming")
        .with_run("run-1")
    )

    app.action_cancel()

    assert client.cancelled == [("run-1", "thread-1")]
    assert app.state.status_label == "cancelling"
    assert app.state.last_resumable_run_id is None
    assert app.state.messages[-1].content == (
        "Cancellation requested. Waiting for the runtime to stop safely."
    )


def test_tui_continue_failure_is_not_retryable_as_user_message() -> None:
    client = FakeSurfaceClient()
    app = _app(client)
    app.state = app.state.begin_operation("op-1", "continuing")

    app._finish_stream_worker("", failed=True)

    assert not app.state.last_failed_user_message


def test_tui_renders_explicit_tool_stream_event_without_message_delta() -> None:
    app = _app(FakeSurfaceClient())

    app._apply_stream_event(
        ConversationStreamEvent(
            event=ConversationStreamEventKind.TOOL_STARTED,
            thread_id=UUID("00000000-0000-0000-0000-000000000001"),
            turn_id=UUID("00000000-0000-0000-0000-000000000002"),
            sequence=3,
            trace_id="trace",
            run_id=UUID("00000000-0000-0000-0000-000000000003"),
            runtime_sequence=10,
            payload={"tool": "repo.apply_patch", "status": "started"},
        )
    )

    assert app.state.messages[-1].kind is ChatEventKind.TOOL
    assert "repo.apply_patch" in app.state.messages[-1].content
    assert "started" in app.state.messages[-1].content


def test_tui_ignores_duplicate_runtime_sequence() -> None:
    app = _app(FakeSurfaceClient())
    event = ConversationStreamEvent(
        event=ConversationStreamEventKind.MESSAGE_DELTA,
        thread_id=UUID("00000000-0000-0000-0000-000000000001"),
        turn_id=UUID("00000000-0000-0000-0000-000000000002"),
        sequence=1,
        trace_id="trace",
        run_id=UUID("00000000-0000-0000-0000-000000000003"),
        runtime_sequence=12,
        payload={"text": "hello"},
    )

    app._apply_stream_event(event)
    app._apply_stream_event(event)

    assert app.state.messages[-1].content == "hello"


def test_tui_continue_worker_uses_last_runtime_sequence() -> None:
    client = FakeSurfaceClient()
    app = _app(client)
    app._last_runtime_sequence_by_run["run-1"] = 12
    app.call_from_thread = lambda callback, *args, **kwargs: callback(  # type: ignore[method-assign]
        *args,
        **kwargs,
    )

    app._continue_worker("thread-1", "run-1")

    assert client.continued == [("thread-1", "run-1", 12)]


def _app(client: FakeSurfaceClient) -> AwesomeAgentTui:
    app = AwesomeAgentTui(client=client)  # type: ignore[arg-type]
    app._render = lambda *args, **kwargs: None  # type: ignore[method-assign]
    app._focus_prompt = lambda *args, **kwargs: None  # type: ignore[method-assign]
    return app


def _submitted(value: str) -> SimpleNamespace:
    return SimpleNamespace(value=value, input=SimpleNamespace(value=value))
