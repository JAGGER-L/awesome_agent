from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from pydantic import ValidationError

from awesome_agent.modeling.errors import (
    ModelErrorCode,
    ModelErrorInfo,
)
from awesome_agent.modeling.execution_jsonl import encode_model_stream_event
from awesome_agent.modeling.messages import AssistantMessage
from awesome_agent.modeling.stream import (
    ModelStreamEvent,
    TextDelta,
    TurnCompleted,
    TurnFailed,
)
from awesome_agent.modeling.turns import ModelRequest, ModelTurn, StopReason
from awesome_agent.providers.factory import ModelProviderFactory
from awesome_agent.settings import Settings


async def main() -> int:
    try:
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line:
            print("missing request", file=sys.stderr)
            return 2
        payload = json.loads(line)
        provider_id, model, request = _decode_request(payload)
        if _test_fake_enabled():
            await _run_fake_worker(provider_id=provider_id, model=model)
            return 0
        provider = ModelProviderFactory(Settings()).create(model)
        async for event in provider.stream(request):
            _emit(event)
        return 0
    except (json.JSONDecodeError, ValidationError, ValueError) as error:
        print(f"invalid request: {error}", file=sys.stderr)
        return 2
    except Exception as error:
        _emit(
            TurnFailed(
                error=ModelErrorInfo(
                    code=ModelErrorCode.TRANSIENT,
                    message=_safe_error(error),
                    retryable=True,
                    provider="deepseek",
                )
            )
        )
        return 0


def _decode_request(payload: Any) -> tuple[str, str, ModelRequest]:
    if not isinstance(payload, dict):
        raise ValueError("request payload must be an object")
    provider = payload.get("provider")
    model = payload.get("model")
    request_payload = payload.get("request")
    if not isinstance(provider, str) or not provider:
        raise ValueError("provider is required")
    if provider != "deepseek":
        raise ValueError(f"unsupported provider: {provider}")
    if not isinstance(model, str) or not model:
        raise ValueError("model is required")
    if not isinstance(request_payload, dict):
        raise ValueError("request is required")
    return provider, model, ModelRequest.model_validate(request_payload)


def _test_fake_enabled() -> bool:
    return (
        "PYTEST_CURRENT_TEST" in os.environ
        and os.environ.get("AWESOME_AGENT_MODEL_WORKER_FAKE") == "echo"
    )


async def _run_fake_worker(*, provider_id: str, model: str) -> None:
    _emit(TextDelta(text="hello"))
    _emit(
        TurnCompleted(
            turn=ModelTurn(
                assistant=AssistantMessage(content="hello"),
                stop_reason=StopReason.COMPLETED,
                model=model,
                provider=provider_id,
            )
        )
    )


def _emit(event: ModelStreamEvent) -> None:
    print(encode_model_stream_event(event), flush=True)


def _safe_error(error: Exception) -> str:
    text = str(error) or type(error).__name__
    return text[:500]


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
