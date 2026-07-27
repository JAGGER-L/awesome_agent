from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from awesome_agent.application.bootstrap import BootstrapRejection
from awesome_agent.application.command_results import (
    CommandOutcome,
    NoticeCommandPayload,
    result,
)
from awesome_agent.application.commands import CommandIntent
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
    SkillInstallRequest,
    SkillInstallResult,
    SkillListResult,
    SkillPackageSummary,
    SkillRemoveRequest,
    SkillRemoveResult,
    ThreadListQuery,
    ThreadListResult,
    ThreadReadQuery,
    ThreadReadResult,
    ThreadSearchQuery,
    WorkspacePresentation,
)
from awesome_agent.application.middleware import ApplicationOperation
from awesome_agent.config import CredentialSource, SecretStatus
from awesome_agent.core.events import EventEnvelope, EventType, WarningPayload
from awesome_agent.modeling import MODEL_CATALOG
from awesome_agent.protocol import stdio
from awesome_agent.protocol.jsonrpc import JsonRpcDispatcher
from awesome_agent.protocol.stdio import (
    JsonLineWriter,
    ProtocolEventSink,
    serve_stdio,
)
from awesome_agent.version import PRODUCT_VERSION

_INITIALIZATION_IN_PROGRESS = BootstrapRejection(
    message="Server initialization is in progress",
    diagnostic_code="initialization_in_progress",
)
_SERVER_NOT_INITIALIZED = BootstrapRejection(
    message="Server not initialized",
    diagnostic_code="server_not_initialized",
)
_SERVER_NOT_READY = BootstrapRejection(
    message="Server not ready",
    diagnostic_code="server_not_ready",
)
_SKILL_MANAGEMENT_UNAVAILABLE = BootstrapRejection(
    message="Skill package management is only available before initialization",
    diagnostic_code="skill_management_requires_uninitialized",
)


class BootstrapAdmissionStub:
    """Scripted Application-owned admission authority for protocol tests."""

    def __init__(self) -> None:
        self.operations: list[ApplicationOperation | None] = []
        self._default: BootstrapRejection | None = _SERVER_NOT_INITIALIZED
        self._overrides: dict[
            ApplicationOperation | None,
            BootstrapRejection | None,
        ] = {}
        self.uninitialized()

    def rejection(
        self,
        operation: ApplicationOperation | None,
    ) -> BootstrapRejection | None:
        self.operations.append(operation)
        return self._overrides.get(operation, self._default)

    def uninitialized(self) -> None:
        self._configure(
            _SERVER_NOT_INITIALIZED,
            initialize=None,
            respond=_SERVER_NOT_INITIALIZED,
            skills=None,
        )

    def initializing(self) -> None:
        self._configure(
            _SERVER_NOT_READY,
            initialize=_INITIALIZATION_IN_PROGRESS,
            respond=_SERVER_NOT_READY,
            skills=_SKILL_MANAGEMENT_UNAVAILABLE,
        )

    def interaction_required(self) -> None:
        self._configure(
            _SERVER_NOT_READY,
            initialize=None,
            respond=None,
            skills=_SKILL_MANAGEMENT_UNAVAILABLE,
        )

    def ready(self) -> None:
        self._configure(
            None,
            initialize=None,
            respond=None,
            skills=_SKILL_MANAGEMENT_UNAVAILABLE,
        )

    def _configure(
        self,
        default: BootstrapRejection | None,
        *,
        initialize: BootstrapRejection | None,
        respond: BootstrapRejection | None,
        skills: BootstrapRejection | None,
    ) -> None:
        self._default = default
        self._overrides = {
            ApplicationOperation.INITIALIZE: initialize,
            ApplicationOperation.RESPOND_INTERACTION: respond,
            ApplicationOperation.SKILL_LIST: skills,
            ApplicationOperation.SKILL_INSTALL: skills,
            ApplicationOperation.SKILL_REMOVE: skills,
            ApplicationOperation.CANCEL_OPERATION: None,
            ApplicationOperation.SHUTDOWN: None,
        }


class Chunks:
    def __init__(self, *chunks: bytes) -> None:
        self.chunks = list(chunks)

    async def read(self, maximum: int) -> bytes:
        del maximum
        return self.chunks.pop(0) if self.chunks else b""


class SlowThenControlRequests:
    def __init__(self, slow_started: asyncio.Event) -> None:
        self._slow_started = slow_started
        self._step = 0

    async def read(self, maximum: int) -> bytes:
        del maximum
        self._step += 1
        if self._step == 1:
            return _initialize_request(0) + _request(
                1,
                "command.execute",
                {"name": "status", "arguments": []},
            )
        if self._step == 2:
            await self._slow_started.wait()
            return _request(
                2,
                "operation.cancel",
                {"operation_id": "operation_1"},
            ) + _request(3, "shutdown", {})
        return b""


class RequestThenOpenInput:
    def __init__(self, request: bytes) -> None:
        self._request = request
        self._delivered = False
        self.blocked = asyncio.Event()

    async def read(self, maximum: int) -> bytes:
        del maximum
        if not self._delivered:
            self._delivered = True
            return self._request
        self.blocked.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class Output:
    def __init__(self) -> None:
        self.frames: list[bytes] = []

    async def write(self, data: bytes) -> None:
        self.frames.append(data)


class InvalidRequestOutput(Output):
    def __init__(self) -> None:
        super().__init__()
        self.invalid_request_seen = asyncio.Event()

    async def write(self, data: bytes) -> None:
        await super().write(data)
        frame = json.loads(data)
        if frame.get("id") is None and frame.get("error", {}).get("code") == -32600:
            self.invalid_request_seen.set()


class FailingOutput:
    def __init__(self, failure_gate: asyncio.Event) -> None:
        self._failure_gate = failure_gate

    async def write(self, data: bytes) -> None:
        del data
        await self._failure_gate.wait()
        raise RuntimeError("protocol writer failed")


class BlockingBinaryOutput:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def write(self, data: bytes) -> int:
        self.entered.set()
        self.release.wait(timeout=1)
        return len(data)

    def flush(self) -> None:
        return None


class Facade:
    def __init__(self, event_sink: ProtocolEventSink | None = None) -> None:
        self.event_sink = event_sink
        self.shutdown_calls = 0
        self.skill_calls: list[tuple[str, object]] = []
        self.bootstrap = BootstrapAdmissionStub()

    def bootstrap_rejection(
        self,
        operation: ApplicationOperation | None,
    ) -> BootstrapRejection | None:
        return self.bootstrap.rejection(operation)

    async def initialize(self) -> ApplicationResult[InitializeResult]:
        self.bootstrap.ready()
        return ApplicationResult.success(
            InitializeResult(
                product_version=PRODUCT_VERSION,
                protocol_version=4,
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
                model_catalog=MODEL_CATALOG,
                configuration_valid=True,
                secret_status=SecretStatus(),
            )
        )

    async def list_skills(self) -> ApplicationResult[SkillListResult]:
        self.skill_calls.append(("list", None))
        return ApplicationResult.success(
            SkillListResult(
                skills=(SkillPackageSummary(name="review", description="Review code"),)
            )
        )

    async def install_skill(
        self,
        request: SkillInstallRequest,
    ) -> ApplicationResult[SkillInstallResult]:
        self.skill_calls.append(("install", request))
        return ApplicationResult.success(
            SkillInstallResult(
                name="review",
                status="replaced" if request.replace else "installed",
            )
        )

    async def remove_skill(
        self,
        request: SkillRemoveRequest,
    ) -> ApplicationResult[SkillRemoveResult]:
        self.skill_calls.append(("remove", request))
        return ApplicationResult.success(
            SkillRemoveResult(name=request.name, status="removed")
        )

    async def list_threads(
        self, query: ThreadListQuery
    ) -> ApplicationResult[ThreadListResult]:
        del query
        return ApplicationResult.success(ThreadListResult())

    async def search_threads(
        self, query: ThreadSearchQuery
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
    ) -> ApplicationResult[CommandOutcome]:
        del intent
        return ApplicationResult.success(
            result(NoticeCommandPayload(message="Command completed."))
        )

    async def set_provider_credential(
        self, request: ProviderCredentialSetRequest
    ) -> ApplicationResult[ProviderCredentialSetResult]:
        return ApplicationResult.success(
            ProviderCredentialSetResult(
                provider=request.provider,
                status=ProviderCredentialSetStatus.CONFIGURED,
                source=CredentialSource.AWESOME,
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


class SlowCommandFacade(Facade):
    def __init__(self) -> None:
        super().__init__()
        self.slow_started = asyncio.Event()
        self.release_slow = asyncio.Event()
        self.slow_completed = asyncio.Event()
        self.slow_cancelled = asyncio.Event()
        self.cancel_seen = asyncio.Event()
        self.shutdown_seen = asyncio.Event()

    async def execute_command(
        self, intent: CommandIntent
    ) -> ApplicationResult[CommandOutcome]:
        self.slow_started.set()
        try:
            await self.release_slow.wait()
            return await super().execute_command(intent)
        except asyncio.CancelledError:
            self.slow_cancelled.set()
            raise
        finally:
            self.slow_completed.set()

    async def cancel_operation(
        self, operation_id: str
    ) -> ApplicationResult[CancelResult]:
        self.cancel_seen.set()
        return await super().cancel_operation(operation_id)

    async def shutdown(self) -> ApplicationResult[ShutdownResult]:
        self.shutdown_seen.set()
        self.release_slow.set()
        return await super().shutdown()


class BlockingControlFacade(Facade):
    def __init__(self, blocked_method: str) -> None:
        super().__init__()
        self._blocked_method = blocked_method
        self.blocked_started = asyncio.Event()
        self.blocked_cancelled = asyncio.Event()
        self.release_blocked = asyncio.Event()
        self.cancel_seen = asyncio.Event()

    async def initialize(self) -> ApplicationResult[InitializeResult]:
        if self._blocked_method == "initialize":
            self.bootstrap.initializing()
            await self._block()
        if self._blocked_method == "interaction.respond":
            self.bootstrap.interaction_required()
            return ApplicationResult.success(
                InitializeResult(
                    product_version=PRODUCT_VERSION,
                    protocol_version=4,
                    status=InitializeStatus.TRUST_REQUIRED,
                    session_id="session_1",
                    interaction_id="interaction_1",
                    workspace=WorkspacePresentation(display_path="C:\\workspace"),
                )
            )
        return await super().initialize()

    async def respond_interaction(
        self, interaction_id: str, decision: str
    ) -> ApplicationResult[InteractionResult]:
        if self._blocked_method == "interaction.respond":
            await self._block()
        return await super().respond_interaction(interaction_id, decision)

    async def cancel_operation(
        self, operation_id: str
    ) -> ApplicationResult[CancelResult]:
        self.cancel_seen.set()
        return await super().cancel_operation(operation_id)

    async def _block(self) -> None:
        self.blocked_started.set()
        try:
            await self.release_blocked.wait()
        except asyncio.CancelledError:
            self.blocked_cancelled.set()
            raise


class FailFirstShutdownFacade(Facade):
    async def shutdown(self) -> ApplicationResult[ShutdownResult]:
        self.shutdown_calls += 1
        if self.shutdown_calls == 1:
            raise RuntimeError("shutdown failed before cleanup")
        return ApplicationResult.success(ShutdownResult())


class HandshakeTrackingFacade(Facade):
    def __init__(
        self,
        *,
        initialize_status: InitializeStatus = InitializeStatus.READY,
        block_initialize: bool = False,
    ) -> None:
        super().__init__()
        self.initialize_status = initialize_status
        self.block_initialize = block_initialize
        self.initialize_calls = 0
        self.initialize_started = asyncio.Event()
        self.release_initialize = asyncio.Event()
        self.business_calls: list[str] = []

    async def initialize(self) -> ApplicationResult[InitializeResult]:
        self.initialize_calls += 1
        self.initialize_started.set()
        self.bootstrap.initializing()
        if self.block_initialize:
            await self.release_initialize.wait()
        if self.initialize_status is InitializeStatus.READY:
            self.bootstrap.ready()
        else:
            self.bootstrap.interaction_required()
        return ApplicationResult.success(
            InitializeResult(
                product_version=PRODUCT_VERSION,
                protocol_version=4,
                status=self.initialize_status,
                session_id="session_1",
                interaction_id=(
                    "interaction_1"
                    if self.initialize_status is not InitializeStatus.READY
                    else None
                ),
                workspace=WorkspacePresentation(display_path="C:\\workspace"),
            )
        )

    async def respond_interaction(
        self,
        interaction_id: str,
        decision: str,
    ) -> ApplicationResult[InteractionResult]:
        response = await super().respond_interaction(interaction_id, decision)
        if (
            self.initialize_status is InitializeStatus.TRUST_REQUIRED
            and decision == "trust"
            and response.ok
            and response.value is not None
            and response.value.accepted
        ):
            self.bootstrap.ready()
        return response

    async def get_state(self) -> ApplicationResult[ApplicationState]:
        self.business_calls.append("application.getState")
        return await super().get_state()

    async def submit_turn(
        self, thread_id: str, content: str, client_message_id: str
    ) -> ApplicationResult[OperationAccepted]:
        self.business_calls.append("turn.submit")
        return await super().submit_turn(thread_id, content, client_message_id)

    async def execute_command(
        self, intent: CommandIntent
    ) -> ApplicationResult[CommandOutcome]:
        self.business_calls.append("command.execute")
        return await super().execute_command(intent)

    async def set_provider_credential(
        self, request: ProviderCredentialSetRequest
    ) -> ApplicationResult[ProviderCredentialSetResult]:
        self.business_calls.append("provider.credential.set")
        return await super().set_provider_credential(request)


class StateResetThenReadyFacade(HandshakeTrackingFacade):
    def __init__(self) -> None:
        super().__init__(initialize_status=InitializeStatus.STATE_RESET_REQUIRED)

    async def initialize(self) -> ApplicationResult[InitializeResult]:
        self.initialize_status = (
            InitializeStatus.STATE_RESET_REQUIRED
            if self.initialize_calls == 0
            else InitializeStatus.READY
        )
        return await super().initialize()


class ReadyPayloadWithoutAdmissionFacade(Facade):
    async def initialize(self) -> ApplicationResult[InitializeResult]:
        result = await super().initialize()
        self.bootstrap.uninitialized()
        return result


class InitializeThenPipeline:
    def __init__(
        self,
        initialize_started: asyncio.Event,
        initialize: bytes,
        pipeline: bytes,
    ) -> None:
        self._initialize_started = initialize_started
        self._initialize = initialize
        self._pipeline = pipeline
        self._step = 0

    async def read(self, maximum: int) -> bytes:
        del maximum
        self._step += 1
        if self._step == 1:
            return self._initialize
        if self._step == 2:
            await self._initialize_started.wait()
            return self._pipeline
        return b""


class InvalidShutdownDuringCommand:
    def __init__(
        self,
        slow_started: asyncio.Event,
        invalid_identifier: object,
    ) -> None:
        self._slow_started = slow_started
        self._invalid_identifier = invalid_identifier
        self._step = 0
        self.continue_to_shutdown = asyncio.Event()

    async def read(self, maximum: int) -> bytes:
        del maximum
        self._step += 1
        if self._step == 1:
            return _request(
                1,
                "initialize",
                {
                    "protocol_version": 4,
                    "client_name": "awesome",
                    "client_version": PRODUCT_VERSION,
                },
            ) + _request(2, "command.execute", {"name": "status", "arguments": []})
        if self._step == 2:
            await self._slow_started.wait()
            return _request_with_id(self._invalid_identifier, "shutdown", {})
        if self._step == 3:
            await self.continue_to_shutdown.wait()
            return _request(4, "shutdown", {})
        return b""


def _request(identifier: int, method: str, params: dict[str, object]) -> bytes:
    return (
        json.dumps(
            {"jsonrpc": "2.0", "id": identifier, "method": method, "params": params}
        ).encode()
        + b"\n"
    )


def _request_with_id(
    identifier: object,
    method: str,
    params: dict[str, object],
) -> bytes:
    return (
        json.dumps(
            {"jsonrpc": "2.0", "id": identifier, "method": method, "params": params}
        ).encode()
        + b"\n"
    )


def _initialize_request(identifier: int) -> bytes:
    return _request(
        identifier,
        "initialize",
        {
            "protocol_version": 4,
            "client_name": "awesome",
            "client_version": PRODUCT_VERSION,
        },
    )


@pytest.mark.asyncio
async def test_fragmented_ndjson_malformed_duplicate_and_shutdown() -> None:
    first = _request(
        1,
        "initialize",
        {
            "protocol_version": 4,
            "client_name": "awesome",
            "client_version": PRODUCT_VERSION,
        },
    )
    duplicate = _request(1, "application.getState", {})
    non_json_number = (
        b'{"jsonrpc":"2.0","id":9,"method":"application.getState",'
        b'"params":{"value":NaN}}\n'
    )
    shutdown = _request(2, "shutdown", {})
    reader = Chunks(
        first[:7],
        first[7:] + b"not json\n" + non_json_number + duplicate + shutdown,
    )
    output = Output()
    facade = Facade()

    await serve_stdio(facade, reader=reader, writer=JsonLineWriter(output))

    frames = [json.loads(frame) for frame in output.frames]
    assert [frame.get("id") for frame in frames] == [1, None, None, 1, 2]
    assert frames[1]["error"]["code"] == -32700
    assert frames[2]["error"]["code"] == -32700
    assert frames[3]["error"]["code"] == -32600
    assert frames[4]["result"] == {"ok": True, "value": {"stopped": True}}
    assert facade.shutdown_calls == 1
    assert facade.bootstrap.operations == [
        ApplicationOperation.INITIALIZE,
        ApplicationOperation.SHUTDOWN,
    ]
    assert all(frame.endswith(b"\n") for frame in output.frames)


@pytest.mark.asyncio
async def test_thread_search_routes_through_initialized_stdio_host() -> None:
    output = Output()
    facade = Facade()
    requests = (
        _initialize_request(0)
        + _request(1, "thread.search", {"query": "provider retry", "limit": 50})
        + _request(2, "shutdown", {})
    )

    await serve_stdio(
        facade,
        reader=Chunks(requests),
        writer=JsonLineWriter(output),
    )

    frames = [json.loads(frame) for frame in output.frames]
    assert frames[1] == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "ok": True,
            "value": {"threads": [], "has_more": False},
        },
    }
    assert facade.bootstrap.operations == [
        ApplicationOperation.INITIALIZE,
        ApplicationOperation.SEARCH_THREADS,
        ApplicationOperation.SHUTDOWN,
    ]


@pytest.mark.asyncio
async def test_deeply_nested_json_is_rejected_without_stopping_host() -> None:
    nested = b"[" * 100_000 + b"0" + b"]" * 100_000 + b"\n"
    output = Output()
    facade = Facade()

    await serve_stdio(
        facade,
        reader=Chunks(nested, _request(2, "shutdown", {})),
        writer=JsonLineWriter(output),
    )

    frames = [json.loads(frame) for frame in output.frames]
    assert frames[0]["error"] == {
        "code": -32700,
        "message": "Parse error",
    }
    assert frames[1]["id"] == 2
    assert frames[1]["result"] == {"ok": True, "value": {"stopped": True}}
    assert facade.shutdown_calls == 1


@pytest.mark.asyncio
async def test_event_and_response_share_one_serialized_protocol_writer() -> None:
    request = _initialize_request(0) + _request(
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
    assert frames[0]["id"] == 0
    assert frames[1]["method"] == "event"
    assert frames[1]["params"]["event_id"] == "event_1"
    assert frames[2]["id"] == 1
    assert facade.shutdown_calls == 1


@pytest.mark.asyncio
async def test_oversized_response_is_replaced_before_reaching_transport() -> None:
    class OversizedDispatcher(JsonRpcDispatcher):
        def __init__(self) -> None:
            pass

        async def dispatch(self, value: object) -> dict[str, Any] | None:
            del value
            return {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "ok": True,
                    "value": {"content": "x" * stdio.MAX_JSON_LINE_BYTES},
                },
            }

    request = {"jsonrpc": "2.0", "id": 1, "method": "command.execute"}
    request_ids = stdio._RequestIdTracker()
    assert request_ids.accept(request) is None
    output = Output()

    response = await stdio._dispatch_request(
        OversizedDispatcher(),
        JsonLineWriter(output),
        request_ids,
        request,
        1,
    )

    assert response is not None
    assert response["result"] == {
        "ok": False,
        "error": {
            "code": "result_too_large",
            "message": "The result exceeds the protocol frame limit.",
            "retryable": False,
            "data": {"maximum_bytes": stdio.MAX_JSON_LINE_BYTES},
        },
    }
    assert len(output.frames) == 1
    assert len(output.frames[0].removesuffix(b"\n")) <= stdio.MAX_JSON_LINE_BYTES
    assert request_ids.accept(request) == 1


@pytest.mark.asyncio
async def test_nonfinite_response_is_replaced_with_typed_internal_error() -> None:
    class NonfiniteDispatcher(JsonRpcDispatcher):
        def __init__(self) -> None:
            pass

        async def dispatch(self, value: object) -> dict[str, Any] | None:
            del value
            return {
                "jsonrpc": "2.0",
                "id": "finite_response",
                "result": {"ok": True, "value": {"usage": float("inf")}},
            }

    request = {
        "jsonrpc": "2.0",
        "id": "finite_response",
        "method": "application.getState",
    }
    request_ids = stdio._RequestIdTracker()
    assert request_ids.accept(request) is None
    output = Output()

    response = await stdio._dispatch_request(
        NonfiniteDispatcher(),
        JsonLineWriter(output),
        request_ids,
        request,
        "finite_response",
    )

    assert response is not None
    assert response["result"] == {
        "ok": False,
        "error": {
            "code": "internal_error",
            "message": "The result could not be represented by the protocol.",
            "retryable": False,
            "data": {},
        },
    }
    assert len(output.frames) == 1
    assert b"Infinity" not in output.frames[0]
    assert request_ids.accept(request) == "finite_response"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe_number",
    [
        stdio.MAX_JSON_LINE_BYTES * 0 + 9_007_199_254_740_992,
        float(9_007_199_254_740_992),
    ],
)
async def test_unsafe_integer_response_is_replaced_with_typed_internal_error(
    unsafe_number: int | float,
) -> None:
    class UnsafeIntegerDispatcher(JsonRpcDispatcher):
        def __init__(self) -> None:
            pass

        async def dispatch(self, value: object) -> dict[str, Any] | None:
            del value
            return {
                "jsonrpc": "2.0",
                "id": "unsafe_integer",
                "result": {"ok": True, "value": {"usage": unsafe_number}},
            }

    request = {
        "jsonrpc": "2.0",
        "id": "unsafe_integer",
        "method": "application.getState",
    }
    request_ids = stdio._RequestIdTracker()
    assert request_ids.accept(request) is None
    output = Output()

    response = await stdio._dispatch_request(
        UnsafeIntegerDispatcher(),
        JsonLineWriter(output),
        request_ids,
        request,
        "unsafe_integer",
    )

    assert response is not None
    assert response["result"] == {
        "ok": False,
        "error": {
            "code": "internal_error",
            "message": "The result could not be represented by the protocol.",
            "retryable": False,
            "data": {},
        },
    }
    assert len(output.frames) == 1
    assert request_ids.accept(request) == "unsafe_integer"


@pytest.mark.asyncio
async def test_protocol_writer_recursively_rejects_non_interoperable_json() -> None:
    deep: object = 0
    for _ in range(70):
        deep = [deep]
    cases = (
        {"value": 9_007_199_254_740_992},
        {"value": float(9_007_199_254_740_992)},
        cast(dict[str, object], {1: "non-string key"}),
        {"value": "\ud800"},
        {"value": deep},
        {"value": ("tuple",)},
    )

    for value in cases:
        output = Output()
        with pytest.raises(ValueError, match="Protocol frame"):
            await JsonLineWriter(output).send(value)
        assert output.frames == []


@pytest.mark.asyncio
async def test_protocol_writer_serializes_one_validated_plain_snapshot() -> None:
    class MutatingFrame(dict[str, object]):
        calls = 0

        def items(self) -> Any:
            self.calls += 1

            def first_traversal() -> Any:
                yield "value", 1
                self["value"] = 9_007_199_254_740_992

            return first_traversal()

    value = MutatingFrame(value=1)
    output = Output()

    await JsonLineWriter(output).send(value)

    assert value.calls == 1
    assert value["value"] == 9_007_199_254_740_992
    assert json.loads(output.frames[0]) == {"value": 1}


@pytest.mark.asyncio
async def test_protocol_writer_accepts_safe_integer_and_finite_float_boundaries() -> (
    None
):
    output = Output()

    await JsonLineWriter(output).send(
        {
            "minimum": -9_007_199_254_740_991,
            "maximum": 9_007_199_254_740_991,
            "integral_float": 1.0,
            "fractional_float": 0.5,
        }
    )

    assert json.loads(output.frames[0]) == {
        "minimum": -9_007_199_254_740_991,
        "maximum": 9_007_199_254_740_991,
        "integral_float": 1.0,
        "fractional_float": 0.5,
    }


@pytest.mark.asyncio
async def test_protocol_writer_enforces_utf8_content_byte_limit_before_output() -> None:
    output = Output()
    writer = JsonLineWriter(output)
    overhead = len(b'{"value":""}')
    exact = "x" * (stdio.MAX_JSON_LINE_BYTES - overhead)

    await writer.send({"value": exact})

    assert len(output.frames[0].removesuffix(b"\n")) == stdio.MAX_JSON_LINE_BYTES
    output.frames.clear()
    multibyte = "\U0001f600" * ((stdio.MAX_JSON_LINE_BYTES - overhead) // 4 + 1)

    with pytest.raises(ValueError, match="Protocol frame exceeds"):
        await writer.send({"value": multibyte})

    assert output.frames == []


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
        _initialize_request(0)
        + _request(1, "shutdown", {"force": True})
        + _request(2, "application.getState", {})
        + _request(3, "shutdown", {})
    )

    await serve_stdio(facade, reader=reader, writer=JsonLineWriter(output))

    frames = [json.loads(frame) for frame in output.frames]
    assert [frame["id"] for frame in frames] == [0, 1, 2, 3]
    assert frames[1]["error"]["code"] == -32602
    assert frames[2]["result"]["ok"] is True
    assert frames[3]["result"]["value"] == {"stopped": True}
    assert facade.shutdown_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "params"),
    [
        (
            "turn.submit",
            {
                "thread_id": "thread_1",
                "content": "must not start",
                "client_message_id": "client_pre_initialize",
            },
        ),
        ("command.execute", {"name": "status", "arguments": []}),
        (
            "provider.credential.set",
            {
                "provider": "deepseek",
                "action": "add",
                "api_key": "must-not-reach-facade",
            },
        ),
    ],
)
async def test_business_request_before_initialize_is_explicitly_rejected(
    method: str,
    params: dict[str, object],
) -> None:
    facade = HandshakeTrackingFacade()
    output = Output()

    await serve_stdio(
        facade,
        reader=Chunks(_request(1, method, params) + _request(2, "shutdown", {})),
        writer=JsonLineWriter(output),
    )

    frames = {frame["id"]: frame for frame in map(json.loads, output.frames)}
    assert frames[1]["error"] == {
        "code": -32002,
        "message": "Server not initialized",
        "data": {"diagnostic_code": "server_not_initialized"},
    }
    assert facade.business_calls == []
    assert facade.initialize_calls == 0
    assert facade.bootstrap.operations == [
        ApplicationOperation(method),
        ApplicationOperation.SHUTDOWN,
    ]


@pytest.mark.asyncio
async def test_skill_management_methods_are_available_only_before_initialize() -> None:
    facade = Facade()
    output = Output()
    private_source = "C:\\private\\review.zip"

    await serve_stdio(
        facade,
        reader=Chunks(
            _request(1, "skill.list", {})
            + _request(
                2,
                "skill.install",
                {"source_path": private_source, "replace": True},
            )
            + _request(3, "skill.remove", {"name": "review"})
            + _request(4, "shutdown", {})
        ),
        writer=JsonLineWriter(output),
    )

    frames = {frame["id"]: frame for frame in map(json.loads, output.frames)}
    assert frames[1]["result"]["value"] == {
        "skills": [{"name": "review", "description": "Review code"}]
    }
    assert frames[2]["result"]["value"] == {
        "name": "review",
        "status": "replaced",
    }
    assert frames[3]["result"]["value"] == {
        "name": "review",
        "status": "removed",
    }
    encoded = b"".join(output.frames).decode("utf-8")
    assert private_source not in encoded
    assert "restart_required" not in encoded
    assert {call[0] for call in facade.skill_calls} == {"list", "install", "remove"}

    initialized_facade = Facade()
    initialized_output = Output()
    await serve_stdio(
        initialized_facade,
        reader=Chunks(
            _initialize_request(10)
            + _request(11, "skill.list", {})
            + _request(12, "shutdown", {})
        ),
        writer=JsonLineWriter(initialized_output),
    )
    initialized_frames = {
        frame["id"]: frame for frame in map(json.loads, initialized_output.frames)
    }
    assert initialized_frames[11]["error"] == {
        "code": -32002,
        "message": "Skill package management is only available before initialization",
        "data": {"diagnostic_code": "skill_management_requires_uninitialized"},
    }
    assert initialized_facade.skill_calls == []


@pytest.mark.asyncio
async def test_parsed_unknown_method_uses_application_bootstrap_admission() -> None:
    facade = Facade()
    output = Output()

    await serve_stdio(
        facade,
        reader=Chunks(_request(1, "unknown", {}) + _request(2, "shutdown", {})),
        writer=JsonLineWriter(output),
    )

    frames = {frame["id"]: frame for frame in map(json.loads, output.frames)}
    assert frames[1]["error"] == {
        "code": -32002,
        "message": "Server not initialized",
        "data": {"diagnostic_code": "server_not_initialized"},
    }
    assert facade.bootstrap.operations == [None, ApplicationOperation.SHUTDOWN]


@pytest.mark.asyncio
async def test_malformed_initialize_does_not_advance_application_bootstrap() -> None:
    facade = Facade()
    output = Output()
    requests = (
        _request(
            1,
            "initialize",
            {
                "protocol_version": 4,
                "client_name": "awesome",
            },
        )
        + _request(2, "application.getState", {})
        + _initialize_request(3)
        + _request(4, "application.getState", {})
        + _request(5, "shutdown", {})
    )

    await serve_stdio(
        facade,
        reader=Chunks(requests),
        writer=JsonLineWriter(output),
    )

    frames = {frame["id"]: frame for frame in map(json.loads, output.frames)}
    assert frames[1]["error"]["code"] == -32602
    assert frames[2]["error"] == {
        "code": -32002,
        "message": "Server not initialized",
        "data": {"diagnostic_code": "server_not_initialized"},
    }
    assert frames[3]["result"]["value"]["status"] == "ready"
    assert frames[4]["result"]["ok"] is True
    assert facade.bootstrap.operations == [
        ApplicationOperation.INITIALIZE,
        ApplicationOperation.GET_STATE,
        ApplicationOperation.INITIALIZE,
        ApplicationOperation.GET_STATE,
        ApplicationOperation.SHUTDOWN,
    ]


@pytest.mark.asyncio
async def test_initialize_response_payload_cannot_override_application_admission() -> (
    None
):
    facade = ReadyPayloadWithoutAdmissionFacade()
    output = Output()

    await serve_stdio(
        facade,
        reader=Chunks(
            _initialize_request(1)
            + _request(2, "application.getState", {})
            + _request(3, "shutdown", {})
        ),
        writer=JsonLineWriter(output),
    )

    frames = {frame["id"]: frame for frame in map(json.loads, output.frames)}
    assert frames[1]["result"]["value"]["status"] == "ready"
    assert frames[2]["error"] == {
        "code": -32002,
        "message": "Server not initialized",
        "data": {"diagnostic_code": "server_not_initialized"},
    }


@pytest.mark.asyncio
async def test_initialize_notification_advances_application_owned_admission() -> None:
    facade = Facade()
    output = Output()
    initialize_notification = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {
                    "protocol_version": 4,
                    "client_name": "awesome",
                    "client_version": PRODUCT_VERSION,
                },
            }
        ).encode()
        + b"\n"
    )

    await serve_stdio(
        facade,
        reader=Chunks(
            initialize_notification
            + _request(1, "application.getState", {})
            + _request(2, "shutdown", {})
        ),
        writer=JsonLineWriter(output),
    )

    frames = {frame["id"]: frame for frame in map(json.loads, output.frames)}
    assert set(frames) == {1, 2}
    assert frames[1]["result"]["ok"] is True
    assert facade.bootstrap.operations == [
        ApplicationOperation.INITIALIZE,
        ApplicationOperation.GET_STATE,
        ApplicationOperation.SHUTDOWN,
    ]


@pytest.mark.asyncio
async def test_incompatible_initialize_does_not_open_business_request_gate() -> None:
    facade = HandshakeTrackingFacade()
    output = Output()
    pipeline = (
        _request(
            1,
            "initialize",
            {
                "protocol_version": 3,
                "client_name": "awesome",
                "client_version": PRODUCT_VERSION,
            },
        )
        + _request(
            2,
            "turn.submit",
            {
                "thread_id": "thread_1",
                "content": "must not start",
                "client_message_id": "client_incompatible",
            },
        )
        + _request(3, "command.execute", {"name": "status", "arguments": []})
        + _request(
            4,
            "provider.credential.set",
            {
                "provider": "deepseek",
                "action": "delete",
            },
        )
        + _request(5, "shutdown", {})
    )

    await serve_stdio(
        facade,
        reader=Chunks(pipeline),
        writer=JsonLineWriter(output),
    )

    frames = {frame["id"]: frame for frame in map(json.loads, output.frames)}
    assert frames[1]["result"]["error"]["code"] == "protocol_version_incompatible"
    assert all(
        frames[identifier]["error"]["code"] == -32002 for identifier in (2, 3, 4)
    )
    assert facade.initialize_calls == 0
    assert facade.business_calls == []


@pytest.mark.asyncio
async def test_blocked_initialize_rejects_pipeline_and_duplicate_initialize() -> None:
    facade = HandshakeTrackingFacade(block_initialize=True)
    output = Output()
    initialize = _request(
        1,
        "initialize",
        {
            "protocol_version": 4,
            "client_name": "awesome",
            "client_version": PRODUCT_VERSION,
        },
    )
    pipeline = (
        _request(
            2,
            "turn.submit",
            {
                "thread_id": "thread_1",
                "content": "must not start",
                "client_message_id": "client_blocked_initialize",
            },
        )
        + _request(3, "command.execute", {"name": "status", "arguments": []})
        + _request(
            4,
            "provider.credential.set",
            {"provider": "kimi", "action": "delete"},
        )
        + _request(
            5,
            "initialize",
            {
                "protocol_version": 4,
                "client_name": "awesome",
                "client_version": PRODUCT_VERSION,
            },
        )
        + _request(6, "shutdown", {})
    )

    try:
        await serve_stdio(
            facade,
            reader=InitializeThenPipeline(
                facade.initialize_started,
                initialize,
                pipeline,
            ),
            writer=JsonLineWriter(output),
        )
    finally:
        facade.release_initialize.set()

    frames = {frame["id"]: frame for frame in map(json.loads, output.frames)}
    assert all(
        frames[identifier]["error"]["code"] == -32002 for identifier in (2, 3, 4)
    )
    assert frames[5]["error"] == {
        "code": -32002,
        "message": "Server initialization is in progress",
        "data": {"diagnostic_code": "initialization_in_progress"},
    }
    assert facade.initialize_calls == 1
    assert facade.business_calls == []


@pytest.mark.asyncio
async def test_trust_bootstrap_only_opens_business_gate_after_trust_resolution() -> (
    None
):
    facade = HandshakeTrackingFacade(initialize_status=InitializeStatus.TRUST_REQUIRED)
    output = Output()
    requests = (
        _request(
            1,
            "initialize",
            {
                "protocol_version": 4,
                "client_name": "awesome",
                "client_version": PRODUCT_VERSION,
            },
        )
        + _request(2, "application.getState", {})
        + _request(
            3,
            "interaction.respond",
            {"interaction_id": "interaction_1", "decision": "trust"},
        )
        + _request(4, "application.getState", {})
        + _request(5, "shutdown", {})
    )

    await serve_stdio(
        facade,
        reader=Chunks(requests),
        writer=JsonLineWriter(output),
    )

    frames = {frame["id"]: frame for frame in map(json.loads, output.frames)}
    assert frames[1]["result"]["value"]["status"] == "trust_required"
    assert frames[2]["error"]["data"]["diagnostic_code"] == "server_not_ready"
    assert frames[3]["result"]["value"]["accepted"] is True
    assert frames[4]["result"]["ok"] is True
    assert facade.business_calls == ["application.getState"]


@pytest.mark.asyncio
async def test_state_reset_keeps_gate_closed_until_ready_initialize() -> None:
    facade = StateResetThenReadyFacade()
    output = Output()
    requests = (
        _initialize_request(1)
        + _request(
            2,
            "interaction.respond",
            {"interaction_id": "interaction_1", "decision": "reset_state"},
        )
        + _request(3, "application.getState", {})
        + _initialize_request(4)
        + _request(5, "application.getState", {})
        + _request(6, "shutdown", {})
    )

    await serve_stdio(
        facade,
        reader=Chunks(requests),
        writer=JsonLineWriter(output),
    )

    frames = {frame["id"]: frame for frame in map(json.loads, output.frames)}
    assert frames[1]["result"]["value"]["status"] == "state_reset_required"
    assert frames[2]["result"]["value"]["accepted"] is True
    assert frames[3]["error"]["data"]["diagnostic_code"] == "server_not_ready"
    assert frames[4]["result"]["value"]["status"] == "ready"
    assert frames[5]["result"]["ok"] is True
    assert facade.business_calls == ["application.getState"]


@pytest.mark.asyncio
async def test_initialize_is_repeatable_after_ready() -> None:
    facade = HandshakeTrackingFacade()
    output = Output()
    initialize_params = {
        "protocol_version": 4,
        "client_name": "awesome",
        "client_version": PRODUCT_VERSION,
    }

    await serve_stdio(
        facade,
        reader=Chunks(
            _request(1, "initialize", initialize_params)
            + _request(2, "initialize", initialize_params)
            + _request(3, "application.getState", {})
            + _request(4, "shutdown", {})
        ),
        writer=JsonLineWriter(output),
    )

    frames = {frame["id"]: frame for frame in map(json.loads, output.frames)}
    assert frames[1]["result"]["value"]["status"] == "ready"
    assert frames[2]["result"]["value"]["status"] == "ready"
    assert frames[3]["result"]["ok"] is True
    assert facade.initialize_calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_identifier", [None, True, False, 1.5, [], {}])
async def test_invalid_shutdown_id_does_not_cancel_legal_background_request(
    invalid_identifier: object,
) -> None:
    facade = SlowCommandFacade()
    output = InvalidRequestOutput()
    reader = InvalidShutdownDuringCommand(
        facade.slow_started,
        invalid_identifier,
    )
    serving = asyncio.create_task(
        serve_stdio(facade, reader=reader, writer=JsonLineWriter(output))
    )

    try:
        await asyncio.wait_for(output.invalid_request_seen.wait(), timeout=0.5)
        assert not facade.slow_cancelled.is_set()
        facade.release_slow.set()
        await asyncio.wait_for(facade.slow_completed.wait(), timeout=0.5)
        reader.continue_to_shutdown.set()
        await asyncio.wait_for(serving, timeout=0.5)
    finally:
        facade.release_slow.set()
        reader.continue_to_shutdown.set()
        if not serving.done():
            serving.cancel()
        await asyncio.gather(serving, return_exceptions=True)

    assert not facade.slow_cancelled.is_set()
    assert facade.shutdown_calls == 1


@pytest.mark.asyncio
async def test_control_requests_are_read_while_a_prior_request_is_slow() -> None:
    facade = SlowCommandFacade()
    output = Output()
    serving = asyncio.create_task(
        serve_stdio(
            facade,
            reader=SlowThenControlRequests(facade.slow_started),
            writer=JsonLineWriter(output),
        )
    )

    try:
        await asyncio.wait_for(facade.shutdown_seen.wait(), timeout=0.5)
        await asyncio.wait_for(serving, timeout=0.5)
    finally:
        facade.release_slow.set()
        if not serving.done():
            serving.cancel()
        await asyncio.gather(serving, return_exceptions=True)

    assert facade.cancel_seen.is_set()
    frames = {frame["id"]: frame for frame in map(json.loads, output.frames)}
    assert set(frames) == {0, 2, 3}
    assert frames[2]["result"]["value"]["cancelled"] is True
    assert frames[3]["result"]["value"]["stopped"] is True


@pytest.mark.asyncio
async def test_background_request_failure_stops_while_stdin_remains_open() -> None:
    facade = Facade()
    reader = RequestThenOpenInput(_initialize_request(1))

    with pytest.raises(RuntimeError, match="protocol writer failed"):
        await asyncio.wait_for(
            serve_stdio(
                facade,
                reader=reader,
                writer=JsonLineWriter(FailingOutput(reader.blocked)),
            ),
            timeout=0.5,
        )

    assert reader.blocked.is_set()
    assert facade.shutdown_calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("blocked_method", "blocked_params"),
    [
        (
            "initialize",
            {
                "protocol_version": 4,
                "client_name": "awesome",
                "client_version": PRODUCT_VERSION,
            },
        ),
        (
            "interaction.respond",
            {"interaction_id": "interaction_1", "decision": "allow_once"},
        ),
    ],
)
async def test_urgent_control_bypasses_blocked_control_request(
    blocked_method: str,
    blocked_params: dict[str, object],
) -> None:
    facade = BlockingControlFacade(blocked_method)
    output = Output()
    prefix = b"" if blocked_method == "initialize" else _initialize_request(0)
    reader = Chunks(
        prefix
        + _request(1, blocked_method, blocked_params)
        + _request(2, "operation.cancel", {"operation_id": "operation_1"})
        + _request(3, "shutdown", {})
    )
    serving = asyncio.create_task(
        serve_stdio(facade, reader=reader, writer=JsonLineWriter(output))
    )

    try:
        await asyncio.wait_for(facade.blocked_started.wait(), timeout=0.5)
        await asyncio.wait_for(facade.cancel_seen.wait(), timeout=0.5)
        await asyncio.wait_for(serving, timeout=0.5)
    finally:
        facade.release_blocked.set()
        if not serving.done():
            serving.cancel()
        await asyncio.gather(serving, return_exceptions=True)

    frames = {frame["id"]: frame for frame in map(json.loads, output.frames)}
    expected_ids = {2, 3} if blocked_method == "initialize" else {0, 2, 3}
    assert set(frames) == expected_ids
    assert frames[2]["result"]["value"]["cancelled"] is True
    assert frames[3]["result"]["value"]["stopped"] is True
    assert facade.blocked_cancelled.is_set()
    assert facade.shutdown_calls == 1


@pytest.mark.asyncio
async def test_shutdown_is_not_repeated_when_its_response_write_fails() -> None:
    facade = Facade()
    failure_gate = asyncio.Event()
    failure_gate.set()

    with pytest.raises(RuntimeError, match="protocol writer failed"):
        await serve_stdio(
            facade,
            reader=Chunks(_request(1, "shutdown", {})),
            writer=JsonLineWriter(FailingOutput(failure_gate)),
        )

    assert facade.shutdown_calls == 1


@pytest.mark.asyncio
async def test_failed_shutdown_notification_runs_final_cleanup() -> None:
    facade = FailFirstShutdownFacade()

    await serve_stdio(
        facade,
        reader=Chunks(
            json.dumps({"jsonrpc": "2.0", "method": "shutdown", "params": {}}).encode()
            + b"\n"
        ),
        writer=JsonLineWriter(Output()),
    )

    assert facade.shutdown_calls == 2


@pytest.mark.asyncio
async def test_background_control_lane_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(stdio, "_MAX_IN_FLIGHT_CONTROL_REQUESTS", 0)
    output = Output()

    await serve_stdio(
        Facade(),
        reader=Chunks(
            _request(
                1,
                "initialize",
                {
                    "protocol_version": 4,
                    "client_name": "awesome",
                    "client_version": PRODUCT_VERSION,
                },
            )
            + _request(2, "shutdown", {})
        ),
        writer=JsonLineWriter(output),
    )

    frames = [json.loads(frame) for frame in output.frames]
    assert [frame["id"] for frame in frames] == [1, 2]
    assert frames[0]["error"] == {"code": -32000, "message": "Server busy"}
    assert frames[1]["result"]["value"]["stopped"] is True


@pytest.mark.asyncio
async def test_overloaded_notification_is_dropped_without_a_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(stdio, "_MAX_IN_FLIGHT_REQUESTS", 0)
    notification = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "command.execute",
                "params": {"name": "status", "arguments": []},
            }
        ).encode()
        + b"\n"
    )
    output = Output()

    await serve_stdio(
        Facade(),
        reader=Chunks(notification + _request(1, "shutdown", {})),
        writer=JsonLineWriter(output),
    )

    frames = [json.loads(frame) for frame in output.frames]
    assert [frame["id"] for frame in frames] == [1]


def test_completed_request_id_history_is_bounded() -> None:
    tracker = stdio._RequestIdTracker()
    for identifier in range(stdio._MAX_RECENT_REQUEST_IDS + 10):
        request = {"jsonrpc": "2.0", "id": identifier, "method": "status"}
        assert tracker.accept(request) is None
        tracker.complete(identifier)

    oldest = {"jsonrpc": "2.0", "id": 0, "method": "status"}
    newest_id = stdio._MAX_RECENT_REQUEST_IDS + 9
    newest = {"jsonrpc": "2.0", "id": newest_id, "method": "status"}
    assert tracker.accept(oldest) is None
    assert tracker.accept(newest) == newest_id


@pytest.mark.asyncio
async def test_stdout_backpressure_has_a_bounded_async_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = BlockingBinaryOutput()
    monkeypatch.setattr(stdio, "_OUTPUT_WRITE_TIMEOUT_SECONDS", 0.01)
    writer = stdio._StdoutWriter(output)

    try:
        with pytest.raises(BrokenPipeError, match="not being consumed"):
            await writer.write(b"frame\n")
        assert output.entered.is_set()
    finally:
        output.release.set()


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
