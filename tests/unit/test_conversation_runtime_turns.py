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
    assert projected.payload == {"text": "hello", "run_id": str(run_id)}


def test_tool_and_team_events_project_structured_payload_without_prompts() -> None:
    thread_id = uuid4()
    turn_id = uuid4()
    run_id = uuid4()
    tool_event = RuntimeEvent(
        run_id=run_id,
        sequence=3,
        event_type=EventType.TOOL_CALL_CREATED,
        payload={
            "name": "shell",
            "status": "denied",
            "prompt": "raw prompt",
            "api_key": "secret",
        },
    )
    team_event = RuntimeEvent(
        run_id=run_id,
        sequence=4,
        event_type=EventType.TEAM_SUBAGENT_REQUESTED,
        payload={
            "role": "verifier",
            "message": "private assignment",
            "task": "review",
        },
    )

    [projected_tool] = project_runtime_event(
        thread_id=thread_id,
        turn_id=turn_id,
        event=tool_event,
    )
    [projected_team] = project_runtime_event(
        thread_id=thread_id,
        turn_id=turn_id,
        event=team_event,
    )

    assert projected_tool.payload["tool_event"] == {
        "name": "shell",
        "status": "denied",
    }
    assert projected_team.payload["team_event"] == {
        "role": "verifier",
        "task": "review",
    }


def test_unknown_runtime_events_are_ignored() -> None:
    event = RuntimeEvent(
        run_id=uuid4(),
        sequence=1,
        event_type=EventType.AGENT_CREATED,
        payload={},
    )

    assert project_runtime_event(thread_id=uuid4(), turn_id=uuid4(), event=event) == []
