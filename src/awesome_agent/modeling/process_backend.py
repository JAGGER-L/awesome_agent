from __future__ import annotations

import asyncio
import json
import os
import subprocess
import threading
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Queue
from typing import Literal, NamedTuple

from awesome_agent.modeling.execution import (
    ModelExecutionContext,
    ModelExecutionProtocolError,
    ModelExecutionTimeout,
)
from awesome_agent.modeling.execution_jsonl import decode_model_stream_event
from awesome_agent.modeling.stream import ModelStreamEvent
from awesome_agent.modeling.turns import ModelRequest

_STDERR_LIMIT = 8192


class _WorkerItem(NamedTuple):
    kind: Literal["line", "eof", "exit_error", "error"]
    value: str | bytes | BaseException | None


@dataclass(frozen=True, slots=True)
class ProcessModelExecutionBackend:
    python_executable: str
    first_event_timeout_seconds: float
    idle_timeout_seconds: float
    total_timeout_seconds: float
    shutdown_grace_seconds: float
    module: str = "awesome_agent.modeling.model_worker"
    extra_env: Mapping[str, str] = field(default_factory=dict)

    async def stream(
        self,
        request: ModelRequest,
        *,
        context: ModelExecutionContext,
    ) -> AsyncIterator[ModelStreamEvent]:
        payload = self._request_payload(request, context)
        process, queue = await asyncio.to_thread(self._start_worker, payload)
        got_event = False
        started = asyncio.get_running_loop().time()
        try:
            while True:
                timeout_phase, timeout_seconds = self._next_timeout(
                    got_event=got_event,
                    started=started,
                )
                item = await asyncio.to_thread(
                    _queue_get,
                    queue,
                    max(0.0, timeout_seconds),
                )
                if item is None:
                    await asyncio.to_thread(
                        _terminate_child,
                        process,
                        self.shutdown_grace_seconds,
                    )
                    raise ModelExecutionTimeout(timeout_phase, timeout_seconds)
                if item.kind == "line":
                    if not isinstance(item.value, (str, bytes)):
                        raise ModelExecutionProtocolError(
                            "Model worker emitted a non-text line."
                        )
                    event = decode_model_stream_event(item.value)
                    got_event = True
                    yield event
                elif item.kind == "eof":
                    return
                elif item.kind == "exit_error":
                    raise ModelExecutionProtocolError(str(item.value))
                elif item.kind == "error":
                    error = item.value
                    if isinstance(error, BaseException):
                        raise ModelExecutionProtocolError(str(error)) from error
                    raise ModelExecutionProtocolError(str(error))
        except asyncio.CancelledError:
            await asyncio.to_thread(
                _terminate_child,
                process,
                self.shutdown_grace_seconds,
            )
            raise
        except Exception:
            await asyncio.to_thread(
                _terminate_child,
                process,
                self.shutdown_grace_seconds,
            )
            raise

    def _request_payload(
        self,
        request: ModelRequest,
        context: ModelExecutionContext,
    ) -> str:
        return json.dumps(
            {
                "provider": context.provider,
                "model": context.model,
                "request": request.model_dump(mode="json"),
            },
            ensure_ascii=False,
        )

    def _start_worker(
        self,
        payload: str,
    ) -> tuple[subprocess.Popen[bytes], Queue[_WorkerItem]]:
        process = subprocess.Popen(
            [self.python_executable, "-m", self.module],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self._child_env(),
        )
        queue: Queue[_WorkerItem] = Queue()
        thread = threading.Thread(
            target=_worker_io_loop,
            args=(process, payload, queue),
            daemon=True,
        )
        thread.start()
        return process, queue

    def _next_timeout(
        self,
        *,
        got_event: bool,
        started: float,
    ) -> tuple[str, float]:
        elapsed = asyncio.get_running_loop().time() - started
        remaining_total = self.total_timeout_seconds - elapsed
        if remaining_total <= 0:
            return "total", 0.0
        phase = "idle" if got_event else "first_event"
        phase_timeout = (
            self.idle_timeout_seconds if got_event else self.first_event_timeout_seconds
        )
        if remaining_total < phase_timeout:
            return "total", remaining_total
        return phase, phase_timeout

    def _child_env(self) -> dict[str, str]:
        env = dict(os.environ)
        src_root = str(Path(__file__).resolve().parents[2])
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            src_root if not existing else f"{src_root}{os.pathsep}{existing}"
        )
        env.update(self.extra_env)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        return env


def _worker_io_loop(
    process: subprocess.Popen[bytes],
    payload: str,
    queue: Queue[_WorkerItem],
) -> None:
    stderr_buffer: list[bytes] = []
    stderr_thread = threading.Thread(
        target=_drain_stderr,
        args=(process, stderr_buffer),
        daemon=True,
    )
    stderr_thread.start()
    try:
        if process.stdin is None or process.stdout is None:
            queue.put(_WorkerItem("error", RuntimeError("worker pipes unavailable")))
            return
        process.stdin.write(payload.encode("utf-8"))
        process.stdin.write(b"\n")
        process.stdin.flush()
        process.stdin.close()
        for line in process.stdout:
            queue.put(_WorkerItem("line", line))
        return_code = process.wait()
        if return_code == 0:
            queue.put(_WorkerItem("eof", None))
        else:
            queue.put(
                _WorkerItem(
                    "exit_error",
                    _child_exit_message(return_code, stderr_buffer),
                )
            )
    except BaseException as error:
        queue.put(_WorkerItem("error", error))


def _drain_stderr(
    process: subprocess.Popen[bytes],
    stderr_buffer: list[bytes],
) -> None:
    stderr = process.stderr
    if stderr is None:
        return
    for chunk in iter(lambda: stderr.read(1024), b""):
        stderr_buffer.append(chunk)
        joined = b"".join(stderr_buffer)
        if len(joined) > _STDERR_LIMIT:
            stderr_buffer[:] = [joined[-_STDERR_LIMIT:]]


def _queue_get(
    queue: Queue[_WorkerItem],
    timeout_seconds: float,
) -> _WorkerItem | None:
    try:
        return queue.get(timeout=timeout_seconds)
    except Empty:
        return None


def _terminate_child(
    process: subprocess.Popen[bytes],
    shutdown_grace_seconds: float,
) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=shutdown_grace_seconds)
    except subprocess.TimeoutExpired:
        if process.poll() is None:
            process.kill()
        process.wait()


def _child_exit_message(return_code: int, stderr_buffer: list[bytes]) -> str:
    stderr = b"".join(stderr_buffer).decode("utf-8", errors="replace").strip()
    if stderr:
        return f"Model worker exited with code {return_code}: {stderr[-1000:]}"
    return f"Model worker exited with code {return_code}."
