from uuid import uuid4

from awesome_agent.api.app import _format_sse
from awesome_agent.domain.enums import EventType
from awesome_agent.domain.models import RuntimeEvent


def test_sse_contains_cursor_type_and_json() -> None:
    event = RuntimeEvent(
        run_id=uuid4(),
        sequence=7,
        event_type=EventType.RUN_CREATED,
        payload={"goal": "test"},
    )

    output = _format_sse(event)

    assert output.startswith("id: 7\nevent: run.created\n")
    assert '"sequence":7' in output
