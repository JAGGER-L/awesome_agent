from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol, cast

from awesome_agent.application.composition import compose_local_application
from awesome_agent.application.facade import ApplicationFacade
from awesome_agent.core.events import EventEnvelope, EventSink
from awesome_agent.paths import AwesomePaths
from awesome_agent.protocol.jsonrpc import (
    JsonRpcDispatcher,
    event_notification,
    jsonrpc_error,
)

MAX_JSON_LINE_BYTES = 1_048_576
_READ_CHUNK_BYTES = 65_536


class AsyncByteReader(Protocol):
    async def read(self, maximum: int) -> bytes: ...


class AsyncByteWriter(Protocol):
    async def write(self, data: bytes) -> None: ...


class JsonLineWriter:
    def __init__(self, output: AsyncByteWriter) -> None:
        self._output = output
        self._lock = asyncio.Lock()

    async def send(self, value: Mapping[str, object]) -> None:
        payload = (
            json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        async with self._lock:
            await self._output.write(payload)


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
    dispatcher = JsonRpcDispatcher(facade)
    seen_ids: set[str | int] = set()
    shutdown_requested = False
    try:
        while True:
            try:
                raw = await lines.read_line()
            except _LineTooLarge:
                await protocol_writer.send(jsonrpc_error(-32700, "Parse error"))
                continue
            if raw is None:
                break
            if not raw.strip():
                continue
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                await protocol_writer.send(jsonrpc_error(-32700, "Parse error"))
                continue
            duplicate = _duplicate_request_id(value, seen_ids)
            if duplicate is not None:
                await protocol_writer.send(
                    jsonrpc_error(
                        -32600,
                        "Invalid Request",
                        request_id=duplicate,
                    )
                )
                continue
            response = await dispatcher.dispatch(value)
            if response is not None:
                await protocol_writer.send(response)
            if isinstance(value, dict) and value.get("method") == "shutdown":
                shutdown_requested = True
                break
    finally:
        if not shutdown_requested:
            await facade.shutdown()


def _duplicate_request_id(
    value: object,
    seen: set[str | int],
) -> str | int | None:
    if not isinstance(value, dict) or "id" not in value:
        return None
    identifier = value["id"]
    if isinstance(identifier, bool) or not isinstance(identifier, (str, int)):
        return None
    if identifier in seen:
        return identifier
    seen.add(identifier)
    return None


class _StdinReader:
    async def read(self, maximum: int) -> bytes:
        stream = cast(Any, sys.stdin.buffer)
        return await asyncio.to_thread(stream.read1, maximum)


class _StdoutWriter:
    async def write(self, data: bytes) -> None:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()


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
    )
    await serve_stdio(facade, writer=writer)


def main() -> None:
    asyncio.run(_run_main())
