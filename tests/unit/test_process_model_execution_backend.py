from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from awesome_agent.modeling.execution import (
    ModelExecutionContext,
    ModelExecutionProtocolError,
    ModelExecutionTimeout,
)
from awesome_agent.modeling.messages import UserMessage
from awesome_agent.modeling.process_backend import ProcessModelExecutionBackend
from awesome_agent.modeling.stream import ModelStreamEvent, TextDelta, TurnCompleted
from awesome_agent.modeling.turns import ModelRequest


@pytest.mark.asyncio
async def test_process_model_execution_backend_streams_worker_events() -> None:
    backend = ProcessModelExecutionBackend(
        python_executable=sys.executable,
        first_event_timeout_seconds=5,
        idle_timeout_seconds=5,
        total_timeout_seconds=10,
        shutdown_grace_seconds=0.2,
        extra_env={
            "PYTEST_CURRENT_TEST": "test",
            "AWESOME_AGENT_MODEL_WORKER_FAKE": "echo",
        },
    )

    events = await _collect(backend.stream(_request(), context=_context()))

    assert isinstance(events[0], TextDelta)
    assert isinstance(events[-1], TurnCompleted)


@pytest.mark.asyncio
async def test_process_model_execution_backend_preserves_utf8_jsonl_text(
    tmp_path: Path,
) -> None:
    module = _write_helper(
        tmp_path,
        "\n".join(
            [
                "import json, sys",
                "payload = json.loads(sys.stdin.readline())",
                'content = payload["request"]["messages"][0]["content"]',
                'if content != "用户输入中文":',
                "    print(",
                '        "bad request content: " + ascii(content),',
                "        file=sys.stderr,",
                "        flush=True,",
                "    )",
                "    raise SystemExit(3)",
                "print(",
                "    json.dumps(",
                '        {"type": "text.delta", "text": "中文回复"},',
                "        ensure_ascii=False,",
                "    ),",
                "    flush=True,",
                ")",
                "print(json.dumps({",
                '    "type": "turn.completed",',
                '    "turn": {',
                '        "assistant": {',
                '            "role": "assistant",',
                '            "content": "中文回复",',
                '            "tool_calls": [],',
                "        },",
                '        "stop_reason": "completed",',
                '        "model": payload["model"],',
                '        "provider": payload["provider"],',
                "    },",
                "}, ensure_ascii=False), flush=True)",
            ]
        ),
    )
    backend = _backend_for_module(module, tmp_path)

    events = await _collect(
        backend.stream(_request("用户输入中文"), context=_context())
    )

    assert isinstance(events[0], TextDelta)
    assert events[0].text == "中文回复"
    assert isinstance(events[-1], TurnCompleted)
    assert events[-1].turn.assistant.content == "中文回复"


@pytest.mark.asyncio
async def test_process_model_execution_backend_times_out_before_first_event(
    tmp_path: Path,
) -> None:
    module = _write_helper(tmp_path, "import time\ntime.sleep(60)\n")
    backend = _backend_for_module(module, tmp_path, first_event_timeout_seconds=0.05)

    with pytest.raises(ModelExecutionTimeout) as raised:
        await _collect(backend.stream(_request(), context=_context()))

    assert raised.value.phase == "first_event"


@pytest.mark.asyncio
async def test_process_model_execution_backend_times_out_after_idle(
    tmp_path: Path,
) -> None:
    module = _write_helper(
        tmp_path,
        "\n".join(
            [
                "import sys, time",
                'print(\'{"type":"text.delta","text":"hello"}\', flush=True)',
                "time.sleep(60)",
            ]
        ),
    )
    backend = _backend_for_module(module, tmp_path, idle_timeout_seconds=0.05)

    with pytest.raises(ModelExecutionTimeout) as raised:
        await _collect(backend.stream(_request(), context=_context()))

    assert raised.value.phase == "idle"


@pytest.mark.asyncio
async def test_process_model_execution_backend_rejects_invalid_json(
    tmp_path: Path,
) -> None:
    module = _write_helper(tmp_path, "print('not json', flush=True)\n")
    backend = _backend_for_module(module, tmp_path)

    with pytest.raises(ModelExecutionProtocolError):
        await _collect(backend.stream(_request(), context=_context()))


@pytest.mark.asyncio
async def test_process_model_execution_backend_cancellation_terminates_child(
    tmp_path: Path,
) -> None:
    module = _write_helper(tmp_path, "import time\ntime.sleep(60)\n")
    backend = _backend_for_module(module, tmp_path, first_event_timeout_seconds=30)
    stream = backend.stream(_request(), context=_context())

    async def read_first() -> ModelStreamEvent:
        return await anext(stream)

    task: asyncio.Task[ModelStreamEvent] = asyncio.create_task(read_first())

    await asyncio.sleep(0.05)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2)


async def _collect(stream: AsyncIterator[ModelStreamEvent]) -> list[ModelStreamEvent]:
    return [event async for event in stream]


def _request(content: str = "hello") -> ModelRequest:
    return ModelRequest(messages=[UserMessage(content=content)])


def _context() -> ModelExecutionContext:
    return ModelExecutionContext(
        run_id="run-1",
        thread_id="thread-1",
        model="deepseek-v4-pro",
        provider="deepseek",
    )


def _backend_for_module(
    module: str,
    tmp_path: Path,
    *,
    first_event_timeout_seconds: float = 1,
    idle_timeout_seconds: float = 1,
) -> ProcessModelExecutionBackend:
    return ProcessModelExecutionBackend(
        python_executable=sys.executable,
        first_event_timeout_seconds=first_event_timeout_seconds,
        idle_timeout_seconds=idle_timeout_seconds,
        total_timeout_seconds=5,
        shutdown_grace_seconds=0.2,
        module=module,
        extra_env={"PYTHONPATH": str(tmp_path)},
    )


def _write_helper(tmp_path: Path, body: str) -> str:
    module = "model_worker_helper"
    (tmp_path / f"{module}.py").write_text(body, encoding="utf-8")
    return module
