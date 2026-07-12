from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from awesome_agent.application.commands import (
    CommandIntent,
    CommandResult,
    CommandStatus,
)
from awesome_agent.application.contracts import (
    ApplicationResult,
    ApplicationState,
    CancelResult,
    InitializeResult,
    InitializeStatus,
    InteractionResult,
    OperationAccepted,
    ProviderCredentialSetRequest,
    ProviderCredentialSetResult,
    ProviderCredentialSetStatus,
    ShutdownResult,
    ThreadListQuery,
    ThreadListResult,
    ThreadReadQuery,
    ThreadReadResult,
    WorkspacePresentation,
)
from awesome_agent.config import CredentialSource, SecretStatus
from awesome_agent.core.events import EventEnvelope, EventType, WarningPayload
from awesome_agent.protocol import stdio
from awesome_agent.protocol.stdio import (
    JsonLineWriter,
    ProtocolEventSink,
    serve_stdio,
)
from awesome_agent.version import PRODUCT_VERSION


class Chunks:
    def __init__(self, *chunks: bytes) -> None:
        self.chunks = list(chunks)

    async def read(self, maximum: int) -> bytes:
        del maximum
        return self.chunks.pop(0) if self.chunks else b""


class Output:
    def __init__(self) -> None:
        self.frames: list[bytes] = []

    async def write(self, data: bytes) -> None:
        self.frames.append(data)


class Facade:
    def __init__(self, event_sink: ProtocolEventSink | None = None) -> None:
        self.event_sink = event_sink
        self.shutdown_calls = 0

    async def initialize(self) -> ApplicationResult[InitializeResult]:
        return ApplicationResult.success(
            InitializeResult(
                product_version=PRODUCT_VERSION,
                protocol_version=1,
                status=InitializeStatus.READY,
                session_id="session_1",
                workspace=WorkspacePresentation(display_path="C:\\workspace"),
            )
        )

    async def get_state(self) -> ApplicationResult[ApplicationState]:
        return ApplicationResult.success(
            ApplicationState(
                initialized=True,
                session_id="session_1",
                workspace_key="ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                workspace=WorkspacePresentation(display_path="C:\\workspace"),
                workspace_trusted=True,
                configuration_valid=True,
                secret_status=SecretStatus(),
            )
        )

    async def list_threads(
        self, query: ThreadListQuery
    ) -> ApplicationResult[ThreadListResult]:
        del query
        return ApplicationResult.success(ThreadListResult())

    async def read_thread(
        self, query: ThreadReadQuery
    ) -> ApplicationResult[ThreadReadResult]:
        raise LookupError(query.thread_id)

    async def submit_turn(
        self, thread_id: str, content: str, client_message_id: str
    ) -> ApplicationResult[OperationAccepted]:
        del content
        if self.event_sink is not None:
            await self.event_sink.emit(
                EventEnvelope(
                    event_id="event_1",
                    sequence=1,
                    session_id="session_1",
                    workspace_key="ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    thread_id=thread_id,
                    event_type=EventType.WARNING,
                    timestamp=datetime.now(UTC),
                    payload=WarningPayload(code="safe", message="Safe warning."),
                )
            )
        return ApplicationResult.success(
            OperationAccepted(
                operation_id="operation_1",
                thread_id=thread_id,
                turn_id="turn_1",
                client_message_id=client_message_id,
            )
        )

    async def execute_direct(
        self, thread_id: str, command: str
    ) -> ApplicationResult[OperationAccepted]:
        del command
        return ApplicationResult.success(
            OperationAccepted(operation_id="operation_2", thread_id=thread_id)
        )

    async def execute_command(
        self, intent: CommandIntent
    ) -> ApplicationResult[CommandResult]:
        del intent
        return ApplicationResult.success(CommandResult(status=CommandStatus.SUCCESS))

    async def set_provider_credential(
        self, request: ProviderCredentialSetRequest
    ) -> ApplicationResult[ProviderCredentialSetResult]:
        return ApplicationResult.success(
            ProviderCredentialSetResult(
                provider=request.provider,
                status=ProviderCredentialSetStatus.CONFIGURED,
                source=CredentialSource.USER_ENV_FILE,
                code="credential_saved",
            )
        )

    async def respond_interaction(
        self, interaction_id: str, decision: str
    ) -> ApplicationResult[InteractionResult]:
        del interaction_id, decision
        return ApplicationResult.success(
            InteractionResult(accepted=True, status="resolved")
        )

    async def cancel_operation(
        self, operation_id: str
    ) -> ApplicationResult[CancelResult]:
        return ApplicationResult.success(
            CancelResult(operation_id=operation_id, cancelled=True)
        )

    async def shutdown(self) -> ApplicationResult[ShutdownResult]:
        self.shutdown_calls += 1
        return ApplicationResult.success(ShutdownResult())


def _request(identifier: int, method: str, params: dict[str, object]) -> bytes:
    return (
        json.dumps(
            {"jsonrpc": "2.0", "id": identifier, "method": method, "params": params}
        ).encode()
        + b"\n"
    )


@pytest.mark.asyncio
async def test_fragmented_ndjson_malformed_duplicate_and_shutdown() -> None:
    first = _request(
        1,
        "initialize",
        {
            "protocol_version": 1,
            "client_name": "awesome",
            "client_version": PRODUCT_VERSION,
        },
    )
    duplicate = _request(1, "application.getState", {})
    shutdown = _request(2, "shutdown", {})
    reader = Chunks(first[:7], first[7:] + b"not json\n" + duplicate + shutdown)
    output = Output()
    facade = Facade()

    await serve_stdio(facade, reader=reader, writer=JsonLineWriter(output))

    frames = [json.loads(frame) for frame in output.frames]
    assert [frame.get("id") for frame in frames] == [1, None, 1, 2]
    assert frames[1]["error"]["code"] == -32700
    assert frames[2]["error"]["code"] == -32600
    assert frames[3]["result"] == {"ok": True, "value": {"stopped": True}}
    assert facade.shutdown_calls == 1
    assert all(frame.endswith(b"\n") for frame in output.frames)


@pytest.mark.asyncio
async def test_event_and_response_share_one_serialized_protocol_writer() -> None:
    request = _request(
        1,
        "turn.submit",
        {
            "thread_id": "thread_1",
            "content": "inspect",
            "client_message_id": "client_1",
        },
    )
    output = Output()
    writer = JsonLineWriter(output)
    facade = Facade(ProtocolEventSink(writer))

    await serve_stdio(facade, reader=Chunks(request), writer=writer)

    frames = [json.loads(frame) for frame in output.frames]
    assert frames[0]["method"] == "event"
    assert frames[0]["params"]["event_id"] == "event_1"
    assert frames[1]["id"] == 1
    assert facade.shutdown_calls == 1


@pytest.mark.asyncio
async def test_oversized_line_is_rejected_and_logging_never_reaches_protocol() -> None:
    output = Output()
    logger = logging.getLogger("awesome_agent.protocol.test")
    logger.warning("stderr-only diagnostic")
    reader = Chunks(b"{" + b"x" * 1_100_000 + b"}\n", _request(2, "shutdown", {}))
    facade = Facade()

    await serve_stdio(facade, reader=reader, writer=JsonLineWriter(output))

    frames = [json.loads(frame) for frame in output.frames]
    assert frames[0]["error"]["code"] == -32700
    assert frames[-1]["id"] == 2
    assert all(b"stderr-only diagnostic" not in frame for frame in output.frames)


@pytest.mark.asyncio
async def test_invalid_shutdown_does_not_terminate_before_valid_shutdown() -> None:
    output = Output()
    facade = Facade()
    reader = Chunks(
        _request(1, "shutdown", {"force": True})
        + _request(2, "application.getState", {})
        + _request(3, "shutdown", {})
    )

    await serve_stdio(facade, reader=reader, writer=JsonLineWriter(output))

    frames = [json.loads(frame) for frame in output.frames]
    assert [frame["id"] for frame in frames] == [1, 2, 3]
    assert frames[0]["error"]["code"] == -32602
    assert frames[1]["result"]["ok"] is True
    assert frames[2]["result"]["value"] == {"stopped": True}
    assert facade.shutdown_calls == 1


@pytest.mark.asyncio
async def test_production_main_forwards_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def compose(
        *,
        home: Path,
        workspace: Path,
        event_sink: object,
        environ: Mapping[str, str],
    ) -> Facade:
        del home, workspace, event_sink
        captured.update(environ)
        return Facade()

    async def serve(facade: object, *, writer: object) -> None:
        del facade, writer

    monkeypatch.setenv("DEEPSEEK_API_KEY", "release-smoke-key")
    monkeypatch.setattr(stdio, "compose_local_application", compose)
    monkeypatch.setattr(stdio, "serve_stdio", serve)

    await stdio._run_main()

    assert captured["DEEPSEEK_API_KEY"] == "release-smoke-key"
