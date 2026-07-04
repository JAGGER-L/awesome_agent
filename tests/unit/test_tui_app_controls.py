from __future__ import annotations

from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any, cast

from awesome_agent.conversation.events import ConversationStreamEvent
from awesome_agent.tui.app import AwesomeAgentTui
from awesome_agent.tui.events import ApprovalPromptState


class FakeSurfaceClient:
    def __init__(self) -> None:
        self.continued: list[tuple[str, str | None]] = []
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
    ) -> Iterable[ConversationStreamEvent]:
        self.continued.append((thread_id, expected_run_id))
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


def _app(client: FakeSurfaceClient) -> AwesomeAgentTui:
    app = AwesomeAgentTui(client=client)  # type: ignore[arg-type]
    app._render = lambda *args, **kwargs: None  # type: ignore[method-assign]
    app._focus_prompt = lambda *args, **kwargs: None  # type: ignore[method-assign]
    return app


def _submitted(value: str) -> SimpleNamespace:
    return SimpleNamespace(value=value, input=SimpleNamespace(value=value))
