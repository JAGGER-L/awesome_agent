from __future__ import annotations

import json
from collections.abc import Iterator

import httpx

from awesome_agent.conversation.events import (
    ConversationStreamEvent,
    parse_conversation_stream_event,
)


class ConversationClientError(RuntimeError):
    pass


class ConversationHttpError(ConversationClientError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id


class ConversationClient:
    def __init__(
        self,
        api_url: str,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self._client = client or httpx.Client(timeout=30)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def stream_turn(
        self,
        *,
        thread_id: str,
        content: str,
        model: str | None = None,
    ) -> Iterator[ConversationStreamEvent]:
        payload: dict[str, object] = {"content": content}
        if model is not None:
            payload["model"] = model
        with self._client.stream(
            "POST",
            f"{self.api_url}/threads/{thread_id}/turns",
            json=payload,
        ) as response:
            if response.status_code >= 400:
                raise _http_error(response)
            yield from parse_sse_lines(response.iter_lines())


def parse_sse_lines(lines: Iterator[str]) -> Iterator[ConversationStreamEvent]:
    event_lines: list[str] = []
    for line in lines:
        if not line:
            if event_lines:
                yield _parse_sse_block(event_lines)
                event_lines = []
            continue
        event_lines.append(line)
    if event_lines:
        yield _parse_sse_block(event_lines)


def _parse_sse_block(lines: list[str]) -> ConversationStreamEvent:
    data_lines = [
        line.removeprefix("data:").strip() for line in lines if line.startswith("data:")
    ]
    if not data_lines:
        raise ConversationClientError("SSE event did not include data.")
    payload = json.loads("\n".join(data_lines))
    if not isinstance(payload, dict):
        raise ConversationClientError("SSE event data must be an object.")
    return parse_conversation_stream_event(payload)


def _http_error(response: httpx.Response) -> ConversationHttpError:
    request_id = response.headers.get("x-request-id")
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    detail = payload.get("detail") if isinstance(payload, dict) else None
    message = detail if isinstance(detail, str) else response.text
    return ConversationHttpError(
        message or f"HTTP {response.status_code}",
        status_code=response.status_code,
        request_id=request_id,
    )
