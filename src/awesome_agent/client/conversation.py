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
        code: str | None = None,
        hint: str | None = None,
        recoverable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id
        self.code = code
        self.hint = hint
        self.recoverable = recoverable


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
    message = response.text
    code = None
    hint = None
    recoverable = False
    if isinstance(payload, dict):
        structured_message = payload.get("message")
        detail = payload.get("detail")
        if isinstance(structured_message, str):
            message = structured_message
        elif isinstance(detail, str):
            message = detail
        payload_code = payload.get("code")
        payload_hint = payload.get("hint")
        payload_recoverable = payload.get("recoverable")
        code = payload_code if isinstance(payload_code, str) else None
        hint = payload_hint if isinstance(payload_hint, str) else None
        recoverable = (
            payload_recoverable if isinstance(payload_recoverable, bool) else False
        )
    return ConversationHttpError(
        message or f"HTTP {response.status_code}",
        status_code=response.status_code,
        request_id=request_id,
        code=code,
        hint=hint,
        recoverable=recoverable,
    )
