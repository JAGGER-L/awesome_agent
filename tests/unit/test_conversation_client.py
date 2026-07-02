from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

from awesome_agent.client.conversation import (
    ConversationClient,
    ConversationHttpError,
    parse_sse_lines,
)
from awesome_agent.conversation.events import ConversationStreamEventKind


def test_parse_sse_lines_returns_conversation_events() -> None:
    thread_id = uuid4()
    turn_id = uuid4()
    lines = iter(
        [
            "id: 1",
            "event: message.delta",
            (
                "data: "
                f'{{"event":"message.delta","thread_id":"{thread_id}",'
                f'"turn_id":"{turn_id}","sequence":1,"trace_id":"trace-1",'
                '"payload":{"text":"hello"}}'
            ),
            "",
        ]
    )

    events = list(parse_sse_lines(lines))

    assert len(events) == 1
    assert events[0].event is ConversationStreamEventKind.MESSAGE_DELTA
    assert events[0].payload == {"text": "hello"}


def test_conversation_client_http_error_includes_status_and_request_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={"detail": "Model is not configured."},
            headers={"x-request-id": "request-1"},
        )

    client = ConversationClient(
        "http://testserver",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(ConversationHttpError) as exc_info:
        list(client.stream_turn(thread_id=str(uuid4()), content="hi"))

    assert exc_info.value.status_code == 409
    assert exc_info.value.request_id == "request-1"
    assert "Model is not configured" in str(exc_info.value)
