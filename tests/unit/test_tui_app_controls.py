from __future__ import annotations

from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID

from awesome_agent.cli.slash_commands import SlashCommandKind
from awesome_agent.conversation.events import (
    ConversationStreamEvent,
    ConversationStreamEventKind,
)
from awesome_agent.tui.app import AwesomeAgentTui
from awesome_agent.tui.chat_state import ChatEventKind
from awesome_agent.tui.events import ApprovalPromptState
from awesome_agent.tui.status_panel import StatusPanelTab


class FakeSurfaceClient:
    def __init__(self) -> None:
        self.continued: list[tuple[str, str | None, int]] = []
        self.cancelled: list[tuple[str, str | None]] = []
        self.approvals: list[tuple[str, str, bool, str | None]] = []
        self.started_turns: list[str] = []

    def close(self) -> None:
        pass

    def stream_turn(self, *_args: object, **_kwargs: object) -> Iterable[object]:
        if len(_args) > 1:
            self.started_turns.append(str(_args[1]))
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


def test_tui_user_message_closes_status_panel() -> None:
    app = _app(FakeSurfaceClient())
    started: list[str] = []
    app._start_user_message = lambda content: started.append(content)  # type: ignore[method-assign]
    app.state = app.state.open_status_panel(StatusPanelTab.CONFIG)

    app.on_input_submitted(cast(Any, _submitted("hello")))

    assert started == ["hello"]
    assert app.state.active_status_tab is None


def test_tui_non_status_command_closes_status_panel() -> None:
    app = _app(FakeSurfaceClient())
    started: list[SlashCommandKind] = []
    app._start_command = lambda parsed: started.append(parsed.kind)  # type: ignore[method-assign]
    app.state = app.state.open_status_panel(StatusPanelTab.USAGE)

    app.on_input_submitted(cast(Any, _submitted("/usage")))

    assert started == [SlashCommandKind.USAGE]
    assert app.state.active_status_tab is None


def test_tui_approval_approve_once_decides_and_continues(monkeypatch) -> None:
    client = FakeSurfaceClient()
    app = _app(client)
    continued: list[str | None] = []
    workers: list[object] = []
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
    app.call_from_thread = lambda callback, *args, **kwargs: callback(  # type: ignore[method-assign]
        *args,
        **kwargs,
    )

    def fake_run_worker(fn, *, thread=False, name=None, exclusive=False):
        workers.append(fn)
        return None

    monkeypatch.setattr(app, "run_worker", fake_run_worker)

    app._apply_approval_choice(0)

    assert client.approvals == []
    assert continued == []
    assert app.state.pending_approval is None
    assert app.state.approval_decision_in_flight is True
    assert workers

    cast(Any, workers[0])()

    assert client.approvals == [("run-1", "approval-1", True, "thread-1")]
    assert continued == ["run-1"]
    assert app.state.approval_decision_in_flight is False
    assert app.state.messages[-1].content == "Approved once. Continuing response."


def test_tui_approval_deny_decides_and_continues(monkeypatch) -> None:
    client = FakeSurfaceClient()
    app = _app(client)
    continued: list[str | None] = []
    workers: list[object] = []
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
    app.call_from_thread = lambda callback, *args, **kwargs: callback(  # type: ignore[method-assign]
        *args,
        **kwargs,
    )
    monkeypatch.setattr(
        app,
        "run_worker",
        lambda fn, *, thread=False, name=None, exclusive=False: workers.append(fn),
    )

    app._apply_approval_choice(1)

    assert client.approvals == []
    assert continued == []
    assert workers

    cast(Any, workers[0])()

    assert client.approvals == [("run-1", "approval-1", False, "thread-1")]
    assert continued == ["run-1"]
    assert app.state.messages[-1].content == "Denied. Continuing response."


def test_tui_approval_cancel_requests_run_cancel(monkeypatch) -> None:
    client = FakeSurfaceClient()
    app = _app(client)
    workers: list[object] = []
    app.state = app.state.with_backend_thread("thread-1").with_approval_prompt(
        ApprovalPromptState(
            run_id="run-1",
            approval_id="approval-1",
            title="Leader wants to run:",
            subject="npm install",
        )
    )
    app.call_from_thread = lambda callback, *args, **kwargs: callback(  # type: ignore[method-assign]
        *args,
        **kwargs,
    )
    monkeypatch.setattr(
        app,
        "run_worker",
        lambda fn, *, thread=False, name=None, exclusive=False: workers.append(fn),
    )

    app._apply_approval_choice(2)

    assert client.cancelled == []
    assert app.state.approval_decision_in_flight is True
    assert workers

    cast(Any, workers[0])()

    assert client.cancelled == [("run-1", "thread-1")]
    assert client.approvals == []
    assert app.state.pending_approval is None
    assert app.state.approval_decision_in_flight is False
    assert app.state.messages[-1].content == "Approval cancelled. Cancelling run."


def test_tui_approval_choice_starts_background_decision_worker(monkeypatch) -> None:
    client = FakeSurfaceClient()
    app = _app(client)
    app.state = app.state.with_backend_thread("thread-1").with_approval_prompt(
        ApprovalPromptState(
            run_id="run-1",
            approval_id="approval-1",
            title="Run command",
            subject="python cube.py",
            approval_type="command",
        )
    )
    workers = []

    def fake_run_worker(fn, *, thread=False, name=None, exclusive=False):
        workers.append((fn, thread, name, exclusive))
        return None

    monkeypatch.setattr(app, "run_worker", fake_run_worker)

    app._apply_approval_choice(0)

    assert client.approvals == []
    assert app.state.pending_approval is None
    assert app.state.approval_decision_in_flight is True
    assert workers
    assert workers[0][1] is True


def test_tui_duplicate_approval_choice_is_ignored(monkeypatch) -> None:
    client = FakeSurfaceClient()
    app = _app(client)
    app.state = app.state.with_backend_thread(
        "thread-1"
    ).with_approval_prompt(
        ApprovalPromptState(
            run_id="run-1",
            approval_id="approval-1",
            title="Run command",
            subject="python cube.py",
            approval_type="command",
        )
    ).with_approval_decision_in_flight(run_id="run-1", in_flight=True)
    workers = []
    monkeypatch.setattr(
        app,
        "run_worker",
        lambda fn, *, thread=False, name=None, exclusive=False: workers.append(fn),
    )

    app._apply_approval_choice(0)

    assert client.approvals == []
    assert workers == []


def test_tui_cancel_pending_approval_cancels_prompt_run(monkeypatch) -> None:
    client = FakeSurfaceClient()
    app = _app(client)
    workers: list[object] = []
    app.state = app.state.with_backend_thread("thread-1").with_approval_prompt(
        ApprovalPromptState(
            run_id="run-1",
            approval_id="approval-1",
            title="Run command",
            subject="python cube.py",
            approval_type="command",
        )
    )
    app.call_from_thread = lambda callback, *args, **kwargs: callback(  # type: ignore[method-assign]
        *args,
        **kwargs,
    )
    monkeypatch.setattr(
        app,
        "run_worker",
        lambda fn, *, thread=False, name=None, exclusive=False: workers.append(fn),
    )

    app.action_cancel()
    cast(Any, workers[0])()

    assert client.cancelled == [("run-1", "thread-1")]
    assert app.state.pending_approval is None
    assert all("No active Run" not in message.content for message in app.state.messages)


def test_tui_submit_is_blocked_while_approval_decision_is_in_flight() -> None:
    client = FakeSurfaceClient()
    app = _app(client)
    app.state = app.state.with_approval_decision_in_flight(
        run_id="run-1",
        in_flight=True,
    )

    app.on_input_submitted(cast(Any, _submitted("?")))

    assert client.started_turns == []
    assert (
        "approval decision is still in progress"
        in app.state.messages[-1].content.lower()
    )


def test_tui_cancel_active_run_keeps_stream_worker_for_terminal_event() -> None:
    client = FakeSurfaceClient()
    app = _app(client)
    cancelled = False

    def cancel_worker() -> None:
        nonlocal cancelled
        cancelled = True

    app._active_worker = SimpleNamespace(cancel=cancel_worker)  # type: ignore[assignment]
    app.state = (
        app.state.with_backend_thread("thread-1")
        .begin_operation("op-1", "streaming")
        .with_run("run-1")
    )

    app.action_cancel()

    assert client.cancelled == [("run-1", "thread-1")]
    assert cancelled is False
    assert app.state.status_label == "cancelling"
    assert app.state.last_resumable_run_id is None
    assert app.state.messages[-1].content == (
        "Cancellation requested. Waiting for the runtime to stop safely."
    )


def test_tui_cancel_active_run_is_idempotent_while_cancelling() -> None:
    client = FakeSurfaceClient()
    app = _app(client)
    app.state = (
        app.state.with_backend_thread("thread-1")
        .begin_operation("op-1", "streaming")
        .with_run("run-1")
    )

    app.action_cancel()
    app.action_cancel()

    assert client.cancelled == [("run-1", "thread-1")]
    messages = [
        message.content
        for message in app.state.messages
        if message.kind is ChatEventKind.RUN
    ]
    assert messages == [
        "Cancellation requested. Waiting for the runtime to stop safely."
    ]


def test_tui_continue_failure_is_not_retryable_as_user_message() -> None:
    client = FakeSurfaceClient()
    app = _app(client)
    app.state = app.state.begin_operation("op-1", "continuing")

    app._finish_stream_worker("", failed=True)

    assert not app.state.last_failed_user_message


def test_tui_toggle_tool_groups_action() -> None:
    app = _app(FakeSurfaceClient())

    app.action_toggle_tool_groups()

    assert app.state.tool_groups_expanded is True
    assert app.state.details_enabled is False
    assert app.state.changed_files_expanded is False


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
    assert app.state.messages[-1].content == "called 1, 1 running"
    assert app.state.messages[-1].tool_group is not None
    entry = app.state.messages[-1].tool_group.entries[0]
    assert entry.name == "repo.apply_patch"
    assert entry.summary == "started"
    assert entry.status == "started"


def test_tui_tool_timeline_keeps_failure_details_without_details_mode() -> None:
    app = _app(FakeSurfaceClient())

    app._apply_stream_event(
        ConversationStreamEvent(
            event=ConversationStreamEventKind.TOOL_COMPLETED,
            thread_id=UUID("00000000-0000-0000-0000-000000000001"),
            turn_id=UUID("00000000-0000-0000-0000-000000000002"),
            sequence=3,
            trace_id="trace-success",
            run_id=UUID("00000000-0000-0000-0000-000000000003"),
            runtime_sequence=10,
            payload={
                "tool": "write_file",
                "summary": "created square.py",
                "path": "square.py",
            },
        )
    )
    app._apply_stream_event(
        ConversationStreamEvent(
            event=ConversationStreamEventKind.TOOL_COMPLETED,
            thread_id=UUID("00000000-0000-0000-0000-000000000001"),
            turn_id=UUID("00000000-0000-0000-0000-000000000002"),
            sequence=4,
            trace_id="trace-failed",
            run_id=UUID("00000000-0000-0000-0000-000000000003"),
            runtime_sequence=11,
            payload={
                "tool": "shell.execute",
                "summary": "failed",
                "error": "approval denied",
            },
        )
    )

    assert app.state.messages[-1].tool_group is not None
    success, failure = app.state.messages[-1].tool_group.entries
    assert success.details == {}
    assert failure.details == {"error": "approval denied"}


def test_tui_tool_stream_event_stores_optional_metadata() -> None:
    app = _app(FakeSurfaceClient())

    app._apply_stream_event(
        ConversationStreamEvent(
            event=ConversationStreamEventKind.TOOL_COMPLETED,
            thread_id=UUID("00000000-0000-0000-0000-000000000001"),
            turn_id=UUID("00000000-0000-0000-0000-000000000002"),
            sequence=3,
            trace_id="trace",
            run_id=UUID("00000000-0000-0000-0000-000000000003"),
            runtime_sequence=10,
            payload={
                "tool": "WriteFile",
                "status": "completed",
                "invocation_id": "invocation-1",
                "call_id": "call-1",
                "started_at": "2026-07-06T10:00:00+00:00",
                "completed_at": "2026-07-06T10:00:01.250000+00:00",
                "duration_ms": 1250,
                "change_stats": {
                    "files": 1,
                    "additions": 6,
                    "deletions": 0,
                    "items": [
                        {
                            "path": "cube.py",
                            "status": "created",
                            "additions": 6,
                            "deletions": 0,
                        }
                    ],
                },
            },
        )
    )

    assert app.state.messages[-1].tool_group is not None
    entry = app.state.messages[-1].tool_group.entries[0]
    assert entry.name == "WriteFile"
    assert entry.status == "completed"
    assert entry.invocation_id == "invocation-1"
    assert entry.call_id == "call-1"
    assert entry.started_at == "2026-07-06T10:00:00+00:00"
    assert entry.completed_at == "2026-07-06T10:00:01.250000+00:00"
    assert entry.duration_ms == 1250
    assert entry.change_stats is not None
    assert entry.change_stats["additions"] == 6


def test_tui_renders_approval_required_without_error_message() -> None:
    app = _app(FakeSurfaceClient())

    app._apply_stream_event(
        ConversationStreamEvent(
            event=ConversationStreamEventKind.APPROVAL_REQUIRED,
            thread_id=UUID("00000000-0000-0000-0000-000000000001"),
            turn_id=UUID("00000000-0000-0000-0000-000000000002"),
            sequence=3,
            trace_id="trace",
            run_id=UUID("00000000-0000-0000-0000-000000000003"),
            runtime_sequence=10,
            payload={
                "code": "approval_required",
                "message": "Approval required for shell.execute.",
                "approval_required": True,
                "run_id": "run-1",
                "approval_id": "approval-1",
                "approval_type": "command",
                "tool": "shell.execute",
                "command": "python square.py",
            },
        )
    )

    assert app.state.pending_approval == ApprovalPromptState(
        run_id="run-1",
        approval_id="approval-1",
        title="Leader wants to run:",
        subject="python square.py",
        approval_type="command",
    )
    assert not [
        message for message in app.state.messages if message.kind is ChatEventKind.ERROR
    ]


def test_tui_approval_required_error_sets_prompt_without_error_message() -> None:
    app = _app(FakeSurfaceClient())

    app._apply_stream_event(
        ConversationStreamEvent(
            event=ConversationStreamEventKind.ERROR,
            thread_id=UUID("00000000-0000-0000-0000-000000000001"),
            turn_id=UUID("00000000-0000-0000-0000-000000000002"),
            sequence=3,
            trace_id="trace",
            run_id=UUID("00000000-0000-0000-0000-000000000003"),
            runtime_sequence=10,
            payload={
                "run_id": "run-1",
                "approval_id": "approval-1",
                "approval_required": True,
                "approval_type": "command",
                "message": "Approval required for shell.execute.",
            },
        )
    )

    assert app.state.pending_approval is not None
    assert app.state.pending_approval.approval_id == "approval-1"
    assert all(
        message.kind is not ChatEventKind.ERROR for message in app.state.messages
    )


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


def test_tui_cancelled_turn_completed_is_not_error() -> None:
    app = _app(FakeSurfaceClient())
    app.state = app.state.begin_operation("op-1", "streaming")

    app._apply_stream_event(
        ConversationStreamEvent(
            event=ConversationStreamEventKind.TURN_COMPLETED,
            thread_id=UUID("00000000-0000-0000-0000-000000000001"),
            turn_id=UUID("00000000-0000-0000-0000-000000000002"),
            sequence=3,
            trace_id="trace",
            run_id=UUID("00000000-0000-0000-0000-000000000003"),
            runtime_sequence=10,
            payload={"status": "cancelled"},
        )
    )
    app._finish_stream_worker("", failed=False)

    assert app.state.active_operation_id is None
    assert app.state.status_label == "ready"
    assert app.state.messages[-1].kind is ChatEventKind.RUN
    assert app.state.messages[-1].content == "Response cancelled."


def test_tui_terminal_turn_clears_current_run_before_next_cancel() -> None:
    client = FakeSurfaceClient()
    app = _app(client)
    run_id = "00000000-0000-0000-0000-000000000003"
    app.state = (
        app.state.with_backend_thread("thread-1")
        .begin_operation("op-1", "streaming")
        .with_run(run_id)
    )

    app._apply_stream_event(
        ConversationStreamEvent(
            event=ConversationStreamEventKind.TURN_COMPLETED,
            thread_id=UUID("00000000-0000-0000-0000-000000000001"),
            turn_id=UUID("00000000-0000-0000-0000-000000000002"),
            sequence=3,
            trace_id="trace",
            run_id=UUID(run_id),
            runtime_sequence=10,
            payload={"status": "cancelled"},
        )
    )
    app._finish_stream_worker("", failed=False)
    app.action_cancel()

    assert app.state.current_run_id is None
    assert client.cancelled == []
    assert app.state.messages[-1].content == "No active Run."


def _app(client: FakeSurfaceClient) -> AwesomeAgentTui:
    app = AwesomeAgentTui(client=client)  # type: ignore[arg-type]
    app._render = lambda *args, **kwargs: None  # type: ignore[method-assign]
    app._focus_prompt = lambda *args, **kwargs: None  # type: ignore[method-assign]
    return app


def _submitted(value: str) -> SimpleNamespace:
    return SimpleNamespace(value=value, input=SimpleNamespace(value=value))
