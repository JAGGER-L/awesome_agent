from __future__ import annotations

import json

from pydantic import TypeAdapter, ValidationError

from awesome_agent.modeling.execution import ModelExecutionProtocolError
from awesome_agent.modeling.stream import ModelStreamEvent

_MODEL_STREAM_EVENT_ADAPTER: TypeAdapter[ModelStreamEvent] = TypeAdapter(
    ModelStreamEvent
)


def encode_model_stream_event(event: ModelStreamEvent) -> str:
    return json.dumps(event.model_dump(mode="json"), ensure_ascii=False)


def decode_model_stream_event(line: str | bytes) -> ModelStreamEvent:
    try:
        if isinstance(line, bytes):
            line = line.decode("utf-8")
        payload = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ModelExecutionProtocolError("Invalid model event JSON.") from error
    try:
        return _MODEL_STREAM_EVENT_ADAPTER.validate_python(payload)
    except ValidationError as error:
        raise ModelExecutionProtocolError("Invalid model event payload.") from error
