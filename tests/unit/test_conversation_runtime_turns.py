from __future__ import annotations

from uuid import uuid4

from awesome_agent.conversation.events import ConversationStreamEventKind
from awesome_agent.conversation.runtime_turns import project_runtime_event
from awesome_agent.domain.enums import EventType
from awesome_agent.domain.models import RuntimeEvent


def test_run_created_projects_turn_started_with_run_id() -> None:
    thread_id = uuid4()
    turn_id = uuid4()
    run_id = uuid4()
    event = RuntimeEvent(
        run_id=run_id,
        sequence=7,
        event_type=EventType.RUN_CREATED,
        payload={"model": "fake-model"},
        trace_id="trace-1",
    )

    [projected] = project_runtime_event(
        thread_id=thread_id,
        turn_id=turn_id,
        event=event,
    )

    assert projected.event is ConversationStreamEventKind.TURN_STARTED
    assert projected.thread_id == thread_id
    assert projected.turn_id == turn_id
    assert projected.sequence == 7
    assert projected.trace_id == "trace-1"
    assert projected.run_id == run_id
    assert projected.runtime_sequence == 7
    assert projected.payload["run_id"] == str(run_id)
    assert projected.payload["model"] == "fake-model"


def test_model_call_text_delta_projects_message_delta() -> None:
    thread_id = uuid4()
    turn_id = uuid4()
    run_id = uuid4()
    event = RuntimeEvent(
        run_id=run_id,
        sequence=2,
        event_type=EventType.MODEL_CALL_CREATED,
        payload={"text_delta": "hello"},
    )

    [projected] = project_runtime_event(
        thread_id=thread_id,
        turn_id=turn_id,
        event=event,
    )

    assert projected.event is ConversationStreamEventKind.MESSAGE_DELTA
    assert projected.run_id == run_id
    assert projected.runtime_sequence == 2
    assert projected.payload == {"text": "hello"}


def test_tool_team_and_validation_events_project_explicit_event_kinds() -> None:
    thread_id = uuid4()
    turn_id = uuid4()
    run_id = uuid4()
    tool_started = RuntimeEvent(
        run_id=run_id,
        sequence=3,
        event_type=EventType.TOOL_CALL_CREATED,
        payload={
            "tool": "shell.execute",
            "status": "started",
            "prompt": "raw prompt",
        },
    )
    tool_progress = RuntimeEvent(
        run_id=run_id,
        sequence=4,
        event_type=EventType.TOOL_PROGRESS,
        payload={
            "tool": "shell.execute",
            "message": "running",
            "secret": "x",
        },
    )
    tool_completed = RuntimeEvent(
        run_id=run_id,
        sequence=5,
        event_type=EventType.TOOL_CALL_CREATED,
        payload={
            "tool": "shell.execute",
            "status": "completed",
        },
    )
    team_event = RuntimeEvent(
        run_id=run_id,
        sequence=6,
        event_type=EventType.TEAM_SUBAGENT_REQUESTED,
        payload={
            "role": "verifier",
            "message": "private assignment",
            "task": "review",
        },
    )
    validation_event = RuntimeEvent(
        run_id=run_id,
        sequence=7,
        event_type=EventType.VERIFICATION_CREATED,
        payload={"status": "passed", "summary": "checks passed"},
    )

    [started] = project_runtime_event(
        thread_id=thread_id,
        turn_id=turn_id,
        event=tool_started,
    )
    [progress] = project_runtime_event(
        thread_id=thread_id,
        turn_id=turn_id,
        event=tool_progress,
    )
    [completed] = project_runtime_event(
        thread_id=thread_id,
        turn_id=turn_id,
        event=tool_completed,
    )
    [projected_team] = project_runtime_event(
        thread_id=thread_id,
        turn_id=turn_id,
        event=team_event,
    )
    [validation] = project_runtime_event(
        thread_id=thread_id,
        turn_id=turn_id,
        event=validation_event,
    )

    assert started.event is ConversationStreamEventKind.TOOL_STARTED
    assert progress.event is ConversationStreamEventKind.TOOL_PROGRESS
    assert completed.event is ConversationStreamEventKind.TOOL_COMPLETED
    assert projected_team.event is ConversationStreamEventKind.TEAM_EVENT
    assert validation.event is ConversationStreamEventKind.VALIDATION_EVENT
    assert "prompt" not in started.payload
    assert "secret" not in progress.payload
    assert projected_team.payload == {"role": "verifier", "task": "review"}


def test_approval_requested_projects_action_required_event() -> None:
    thread_id = uuid4()
    turn_id = uuid4()
    run_id = uuid4()
    event = RuntimeEvent(
        run_id=run_id,
        sequence=8,
        event_type=EventType.APPROVAL_REQUESTED,
        payload={
            "approval_id": "approval-1",
            "tool": "shell.execute",
            "args_summary": "python add.py",
            "risk": "medium",
            "expires_at": "2026-07-05T12:00:00+00:00",
        },
    )

    [projected] = project_runtime_event(
        thread_id=thread_id,
        turn_id=turn_id,
        event=event,
    )

    assert projected.event is ConversationStreamEventKind.APPROVAL_REQUIRED
    assert projected.payload == {
        "code": "approval_required",
        "message": "Approval required for shell.execute.",
        "approval_required": True,
        "run_id": str(run_id),
        "approval_id": "approval-1",
        "approval_type": "command",
        "tool": "shell.execute",
        "command": "python add.py",
        "risk": "medium",
        "expires_at": "2026-07-05T12:00:00+00:00",
    }


def test_usage_and_terminal_status_project_to_conversation_events() -> None:
    thread_id = uuid4()
    turn_id = uuid4()
    run_id = uuid4()
    usage = RuntimeEvent(
        run_id=run_id,
        sequence=8,
        event_type=EventType.MODEL_CALL_CREATED,
        payload={"input_tokens": 3, "output_tokens": 5},
    )
    completed = RuntimeEvent(
        run_id=run_id,
        sequence=9,
        event_type=EventType.RUN_STATUS_CHANGED,
        payload={"status": "completed"},
    )

    [usage_event] = project_runtime_event(
        thread_id=thread_id,
        turn_id=turn_id,
        event=usage,
    )
    [done_event] = project_runtime_event(
        thread_id=thread_id,
        turn_id=turn_id,
        event=completed,
    )

    assert usage_event.event is ConversationStreamEventKind.USAGE_UPDATED
    assert usage_event.payload == {"input_tokens": 3, "output_tokens": 5}
    assert done_event.event is ConversationStreamEventKind.TURN_COMPLETED
    assert done_event.run_id == run_id
    assert done_event.runtime_sequence == 9


def test_unknown_runtime_events_are_ignored() -> None:
    event = RuntimeEvent(
        run_id=uuid4(),
        sequence=1,
        event_type=EventType.AGENT_CREATED,
        payload={},
    )

    assert project_runtime_event(thread_id=uuid4(), turn_id=uuid4(), event=event) == []
