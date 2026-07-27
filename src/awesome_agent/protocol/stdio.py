from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import queue
import sys
import threading
from collections import OrderedDict
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol, cast

from awesome_agent.application.composition import compose_local_application
from awesome_agent.application.contracts import (
    ApplicationResult,
    ProductError,
    ProductErrorCode,
)
from awesome_agent.application.facade import ApplicationFacade
from awesome_agent.application.middleware import ApplicationOperation
from awesome_agent.core.contracts import MAX_JSON_SAFE_INTEGER
from awesome_agent.core.events import EventEnvelope, EventSink
from awesome_agent.core.process_lifetime import (
    ProcessTreeGuardError,
    install_process_tree_guard,
)
from awesome_agent.paths import AwesomePaths
from awesome_agent.protocol.jsonrpc import (
    JsonRpcDispatcher,
    event_notification,
    jsonrpc_error,
    normalize_jsonrpc_request_id,
    parse_jsonrpc_request,
)

MAX_JSON_LINE_BYTES = 1_048_576
_READ_CHUNK_BYTES = 65_536
_MAX_IN_FLIGHT_REQUESTS = 128
_MAX_IN_FLIGHT_CONTROL_REQUESTS = 16
_MAX_RECENT_REQUEST_IDS = 4_096
_OUTPUT_QUEUE_SIZE = 64
_OUTPUT_WRITE_TIMEOUT_SECONDS = 5.0
_MAX_PROTOCOL_JSON_DEPTH = 64
_BACKGROUND_CONTROL_METHODS = frozenset({"initialize", "interaction.respond"})
_URGENT_CONTROL_METHODS = frozenset({"operation.cancel", "shutdown"})


class AsyncByteReader(Protocol):
    async def read(self, maximum: int) -> bytes: ...


class AsyncByteWriter(Protocol):
    async def write(self, data: bytes) -> None: ...


class SyncByteWriter(Protocol):
    def write(self, data: bytes, /) -> object: ...

    def flush(self) -> object: ...


class JsonLineWriter:
    def __init__(self, output: AsyncByteWriter) -> None:
        self._output = output
        self._lock = asyncio.Lock()

    async def send(self, value: Mapping[str, object]) -> None:
        try:
            snapshot = _snapshot_json_frame(value)
            content = json.dumps(
                snapshot,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except (TypeError, ValueError, RecursionError) as error:
            raise _FrameInvalid("Protocol frame is not strict UTF-8 JSON.") from error
        if len(content) > MAX_JSON_LINE_BYTES:
            raise _FrameTooLarge(f"Protocol frame exceeds {MAX_JSON_LINE_BYTES} bytes.")
        payload = content + b"\n"
        async with self._lock:
            await self._output.write(payload)


class _FrameTooLarge(ValueError):
    pass


class _FrameInvalid(ValueError):
    pass


def _snapshot_json_frame(value: object, *, depth: int = 1) -> object:
    """Validate and copy one immutable-by-ownership protocol JSON snapshot."""

    if depth > _MAX_PROTOCOL_JSON_DEPTH:
        raise _FrameInvalid("Protocol frame exceeds the JSON depth limit.")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        _validate_unicode(value)
        return str(value)
    if type(value) is int:
        if abs(value) > MAX_JSON_SAFE_INTEGER:
            raise _FrameInvalid("Protocol frame contains an unsafe JSON integer.")
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise _FrameInvalid("Protocol frame contains a non-finite number.")
        if value.is_integer() and abs(value) > MAX_JSON_SAFE_INTEGER:
            raise _FrameInvalid("Protocol frame contains an unsafe JSON integer.")
        return value
    if isinstance(value, dict):
        snapshot: dict[str, object] = {}
        try:
            for key, child in value.items():
                if not isinstance(key, str):
                    raise _FrameInvalid(
                        "Protocol frame contains a non-string object key."
                    )
                _validate_unicode(key)
                snapshot[str(key)] = _snapshot_json_frame(child, depth=depth + 1)
        except RuntimeError as error:
            raise _FrameInvalid("Protocol frame changed during validation.") from error
        return snapshot
    if isinstance(value, list):
        try:
            return [_snapshot_json_frame(child, depth=depth + 1) for child in value]
        except RuntimeError as error:
            raise _FrameInvalid("Protocol frame changed during validation.") from error
    raise _FrameInvalid("Protocol frame contains a non-JSON value.")


def _validate_unicode(value: str) -> None:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise _FrameInvalid("Protocol frame contains invalid Unicode.") from error


def _reject_non_json_constant(value: str) -> None:
    del value
    raise ValueError("Non-finite numbers are not valid JSON.")


def _result_too_large_response(
    request_id: str | int | None,
) -> dict[str, Any]:
    result = ApplicationResult[object].failure(
        ProductError(
            code=ProductErrorCode.RESULT_TOO_LARGE,
            message="The result exceeds the protocol frame limit.",
            data={"maximum_bytes": MAX_JSON_LINE_BYTES},
        )
    )
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result.model_dump(mode="json", exclude_none=True),
    }


def _invalid_result_response(request_id: str | int | None) -> dict[str, Any]:
    result = ApplicationResult[object].failure(
        ProductError(
            code=ProductErrorCode.INTERNAL_ERROR,
            message="The result could not be represented by the protocol.",
        )
    )
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result.model_dump(mode="json", exclude_none=True),
    }


class ProtocolEventSink(EventSink):
    def __init__(self, writer: JsonLineWriter) -> None:
        self._writer = writer

    async def emit(self, event: EventEnvelope) -> None:
        await self._writer.send(event_notification(event))


class _LineTooLarge(ValueError):
    pass


class _NdjsonReader:
    def __init__(self, source: AsyncByteReader) -> None:
        self._source = source
        self._buffer = bytearray()
        self._eof = False

    async def read_line(self) -> bytes | None:
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                line = bytes(self._buffer[:newline])
                del self._buffer[: newline + 1]
                if len(line) > MAX_JSON_LINE_BYTES:
                    raise _LineTooLarge
                return line
            if len(self._buffer) > MAX_JSON_LINE_BYTES:
                await self._discard_oversized_line()
                raise _LineTooLarge
            if self._eof:
                if not self._buffer:
                    return None
                line = bytes(self._buffer)
                self._buffer.clear()
                return line
            chunk = await self._source.read(_READ_CHUNK_BYTES)
            if chunk:
                self._buffer.extend(chunk)
            else:
                self._eof = True

    async def _discard_oversized_line(self) -> None:
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                del self._buffer[: newline + 1]
                return
            self._buffer.clear()
            chunk = await self._source.read(_READ_CHUNK_BYTES)
            if not chunk:
                self._eof = True
                return
            self._buffer.extend(chunk)


async def serve_stdio(
    facade: ApplicationFacade,
    *,
    reader: AsyncByteReader | None = None,
    writer: JsonLineWriter | None = None,
) -> None:
    source = reader or _StdinReader()
    protocol_writer = writer or JsonLineWriter(_StdoutWriter())
    lines = _NdjsonReader(source)
    shutdown_invoked = False

    def method_completed(method: str) -> None:
        nonlocal shutdown_invoked
        if method == "shutdown":
            shutdown_invoked = True

    dispatcher = JsonRpcDispatcher(facade, method_completed=method_completed)
    request_ids = _RequestIdTracker()
    pending_requests: set[asyncio.Task[Mapping[str, object] | None]] = set()
    pending_controls: set[asyncio.Task[Mapping[str, object] | None]] = set()
    request_failures: list[BaseException] = []
    request_failed = asyncio.Event()

    def request_completed(
        task: asyncio.Task[Mapping[str, object] | None],
    ) -> None:
        pending_requests.discard(task)
        pending_controls.discard(task)
        if task.cancelled():
            return
        failure = task.exception()
        if failure is not None and not request_failures:
            request_failures.append(failure)
            request_failed.set()

    def start_background_request(
        value: object,
        request_id: str | int | None,
        *,
        control: bool,
    ) -> None:
        async def dispatch() -> Mapping[str, object] | None:
            return await _dispatch_request(
                dispatcher,
                protocol_writer,
                request_ids,
                value,
                request_id,
            )

        task = asyncio.create_task(
            dispatch(),
            name=f"protocol-request-{request_id}",
        )
        target = pending_controls if control else pending_requests
        target.add(task)
        task.add_done_callback(request_completed)

    async def read_line_or_failure() -> bytes | None:
        read_task = asyncio.create_task(
            lines.read_line(),
            name="protocol-read-line",
        )
        failure_task = asyncio.create_task(
            request_failed.wait(),
            name="protocol-request-failure",
        )
        try:
            completed, _ = await asyncio.wait(
                {read_task, failure_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if failure_task in completed:
                raise request_failures[0]
            return read_task.result()
        finally:
            for task in (read_task, failure_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(read_task, failure_task, return_exceptions=True)

    async def cancel_background_requests() -> None:
        pending = pending_requests | pending_controls
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    try:
        while True:
            if request_failures:
                raise request_failures[0]
            try:
                raw = await read_line_or_failure()
            except _LineTooLarge:
                await protocol_writer.send(jsonrpc_error(-32700, "Parse error"))
                continue
            if raw is None:
                break
            if not raw.strip():
                continue
            try:
                value = json.loads(
                    raw.decode("utf-8"),
                    parse_constant=_reject_non_json_constant,
                )
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                ValueError,
                RecursionError,
            ):
                await protocol_writer.send(jsonrpc_error(-32700, "Parse error"))
                continue
            duplicate = request_ids.accept(value)
            if duplicate is not None:
                await protocol_writer.send(
                    jsonrpc_error(
                        -32600,
                        "Invalid Request",
                        request_id=duplicate,
                    )
                )
                continue
            request_id = _request_id(value)
            method = _method(value)
            parsed_request = parse_jsonrpc_request(value)
            bootstrap_rejection = (
                facade.bootstrap_rejection(_application_operation(parsed_request[2]))
                if parsed_request is not None
                else None
            )
            if bootstrap_rejection is not None:
                if parsed_request is not None and parsed_request[1]:
                    await protocol_writer.send(
                        jsonrpc_error(
                            -32002,
                            bootstrap_rejection.message,
                            request_id=parsed_request[0],
                            data={
                                "diagnostic_code": bootstrap_rejection.diagnostic_code
                            },
                        )
                    )
                request_ids.complete(request_id)
                continue
            if method in _URGENT_CONTROL_METHODS:
                if method == "shutdown" and _valid_shutdown_request(value):
                    await cancel_background_requests()
                response = await _dispatch_request(
                    dispatcher,
                    protocol_writer,
                    request_ids,
                    value,
                    request_id,
                )
                if _shutdown_completed(value, response):
                    break
                continue
            is_control = method in _BACKGROUND_CONTROL_METHODS
            pending = pending_controls if is_control else pending_requests
            maximum = (
                _MAX_IN_FLIGHT_CONTROL_REQUESTS
                if is_control
                else _MAX_IN_FLIGHT_REQUESTS
            )
            if len(pending) >= maximum:
                if request_id is not None:
                    await protocol_writer.send(
                        jsonrpc_error(
                            -32000,
                            "Server busy",
                            request_id=request_id,
                        )
                    )
                request_ids.complete(request_id)
                continue
            start_background_request(
                value,
                request_id,
                control=is_control,
            )
            # Let an accepted request enter the Application boundary before the
            # reader consumes a following control request. The request may stay
            # blocked there, but fast snapshots still preserve arrival order.
            await asyncio.sleep(0)
    finally:
        await cancel_background_requests()
        if not shutdown_invoked:
            await facade.shutdown()
    if request_failures:
        raise request_failures[0]


async def _dispatch_request(
    dispatcher: JsonRpcDispatcher,
    writer: JsonLineWriter,
    request_ids: _RequestIdTracker,
    value: object,
    request_id: str | int | None,
) -> Mapping[str, object] | None:
    try:
        response = await dispatcher.dispatch(value)
        if response is not None:
            try:
                await writer.send(response)
            except _FrameTooLarge:
                response = _result_too_large_response(request_id)
                await writer.send(response)
            except _FrameInvalid:
                response = _invalid_result_response(request_id)
                await writer.send(response)
        return response
    finally:
        request_ids.complete(request_id)


class _RequestIdTracker:
    def __init__(self) -> None:
        self._active: set[str | int] = set()
        self._recent: OrderedDict[str | int, None] = OrderedDict()

    def accept(self, value: object) -> str | int | None:
        identifier = _request_id(value)
        if identifier is None:
            return None
        if identifier in self._active or identifier in self._recent:
            return identifier
        self._active.add(identifier)
        return None

    def complete(self, identifier: str | int | None) -> None:
        if identifier is None or identifier not in self._active:
            return
        self._active.remove(identifier)
        self._recent[identifier] = None
        while len(self._recent) > _MAX_RECENT_REQUEST_IDS:
            self._recent.popitem(last=False)


def _request_id(value: object) -> str | int | None:
    if not isinstance(value, dict) or "id" not in value:
        return None
    return normalize_jsonrpc_request_id(value["id"])


def _method(value: object) -> str | None:
    method = value.get("method") if isinstance(value, dict) else None
    return method if isinstance(method, str) else None


def _application_operation(method: str) -> ApplicationOperation | None:
    try:
        return ApplicationOperation(method)
    except ValueError:
        return None


def _shutdown_completed(
    value: object,
    response: Mapping[str, object] | None,
) -> bool:
    if not _valid_shutdown_request(value) or not isinstance(value, dict):
        return False
    if "id" not in value:
        return True
    if response is None:
        return False
    result = response.get("result")
    return isinstance(result, Mapping) and result.get("ok") is True


def _valid_shutdown_request(value: object) -> bool:
    request = parse_jsonrpc_request(value)
    if request is None:
        return False
    _, _, method, params = request
    if method != "shutdown":
        return False
    return isinstance(params, Mapping) and not params


class _StdinReader:
    async def read(self, maximum: int) -> bytes:
        stream = cast(Any, sys.stdin.buffer)
        return await asyncio.to_thread(stream.read1, maximum)


class _StdoutWriter:
    def __init__(self, output: SyncByteWriter | None = None) -> None:
        self._output = output or sys.stdout.buffer
        self._queue: queue.Queue[
            tuple[bytes, asyncio.AbstractEventLoop, asyncio.Future[None]]
        ] = queue.Queue(maxsize=_OUTPUT_QUEUE_SIZE)
        self._thread: threading.Thread | None = None

    async def write(self, data: bytes) -> None:
        self._ensure_thread()
        loop = asyncio.get_running_loop()
        completed = loop.create_future()
        try:
            self._queue.put_nowait((data, loop, completed))
        except queue.Full as error:
            raise BrokenPipeError("Protocol output queue is full.") from error
        try:
            await asyncio.wait_for(
                completed,
                timeout=_OUTPUT_WRITE_TIMEOUT_SECONDS,
            )
        except TimeoutError as error:
            raise BrokenPipeError("Protocol output is not being consumed.") from error

    def _ensure_thread(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._pump,
            name="awesome-stdout",
            daemon=True,
        )
        self._thread.start()

    def _pump(self) -> None:
        while True:
            data, loop, completed = self._queue.get()
            error: BaseException | None = None
            try:
                self._output.write(data)
                self._output.flush()
            except BaseException as caught:
                error = caught
            try:
                loop.call_soon_threadsafe(_complete_output, completed, error)
            except RuntimeError:
                return


def _complete_output(
    completed: asyncio.Future[None],
    error: BaseException | None,
) -> None:
    if completed.done():
        return
    if error is None:
        completed.set_result(None)
    else:
        completed.set_exception(error)


async def _run_main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(levelname)s %(name)s: %(message)s",
    )
    writer = JsonLineWriter(_StdoutWriter())
    paths = AwesomePaths.resolve()
    facade = await compose_local_application(
        home=paths.home,
        workspace=Path.cwd(),
        event_sink=ProtocolEventSink(writer),
        environ=os.environ,
    )
    await serve_stdio(facade, writer=writer)


def main() -> None:
    try:
        install_process_tree_guard()
    except ProcessTreeGuardError as error:
        print(
            f"awesome-core: fatal process lifetime initialization failure: {error}",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    asyncio.run(_run_main())
