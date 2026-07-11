from __future__ import annotations

import tomllib
from datetime import UTC, datetime
from importlib.metadata import version as installed_version
from pathlib import Path

import pytest

from awesome_agent.application.commands import CommandResult
from awesome_agent.application.contracts import (
    ApplicationResult,
    ApplicationState,
    CancelResult,
    InitializeResult,
    InitializeStatus,
    InteractionResult,
    OperationAccepted,
    ProductError,
    ProductErrorCode,
    ShutdownResult,
    ThreadListResult,
    WorkspacePresentation,
)
from awesome_agent.config import SecretStatus
from awesome_agent.core.events import EventEnvelope, EventType, WarningPayload
from awesome_agent.protocol.jsonrpc import (
    PROTOCOL_VERSION,
    JsonRpcDispatcher,
    event_notification,
    jsonrpc_error,
)
from awesome_agent.version import PRODUCT_VERSION

INITIALIZE_PARAMS = {
    "protocol_version": 1,
    "client_name": "awesome-tui",
    "client_version": PRODUCT_VERSION,
}


class Facade:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def initialize(self) -> ApplicationResult[InitializeResult]:
        self.calls.append(("initialize", None))
        return ApplicationResult.success(
            InitializeResult(
                product_version=PRODUCT_VERSION,
                protocol_version=PROTOCOL_VERSION,
                status=InitializeStatus.READY,
                session_id="session_1",
                workspace=WorkspacePresentation(display_path="C:\\workspace"),
            )
        )

    async def get_state(self) -> ApplicationResult[ApplicationState]:
        self.calls.append(("state", None))
        return ApplicationResult.success(
            ApplicationState(
                initialized=True,
                session_id="session_1",
                workspace_key="ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                workspace=WorkspacePresentation(
                    display_path="C:\\workspace", branch="feature/auth"
                ),
                workspace_trusted=True,
                configuration_valid=True,
                secret_status=SecretStatus(),
            )
        )

    async def list_threads(self) -> ApplicationResult[ThreadListResult]:
        self.calls.append(("list", None))
        return ApplicationResult.success(ThreadListResult())

    async def read_thread(self, thread_id: str) -> object:
        self.calls.append(("read", thread_id))
        raise RuntimeError("private database traceback")

    async def submit_turn(
        self, thread_id: str, content: str
    ) -> ApplicationResult[OperationAccepted]:
        self.calls.append(("turn", (thread_id, content)))
        return ApplicationResult.success(
            OperationAccepted(operation_id="operation_1", thread_id=thread_id)
        )

    async def execute_direct(
        self,
        thread_id: str,
        command: str,
    ) -> ApplicationResult[OperationAccepted]:
        self.calls.append(("direct", (thread_id, command)))
        return ApplicationResult.success(
            OperationAccepted(operation_id="operation_2", thread_id=thread_id)
        )

    async def execute_command(self, intent: object) -> ApplicationResult[CommandResult]:
        self.calls.append(("command", intent))
        return ApplicationResult.failure(
            ProductError(
                code=ProductErrorCode.INVALID_ARGUMENTS,
                message="Expected product failure.",
            )
        )

    async def respond_interaction(
        self,
        interaction_id: str,
        decision: str,
    ) -> ApplicationResult[InteractionResult]:
        self.calls.append(("interaction", (interaction_id, decision)))
        return ApplicationResult.success(
            InteractionResult(accepted=True, status="resolved")
        )

    async def cancel_operation(
        self, operation_id: str
    ) -> ApplicationResult[CancelResult]:
        self.calls.append(("cancel", operation_id))
        return ApplicationResult.success(
            CancelResult(operation_id=operation_id, cancelled=True)
        )

    async def shutdown(self) -> ApplicationResult[ShutdownResult]:
        self.calls.append(("shutdown", None))
        return ApplicationResult.success(ShutdownResult())


def test_dispatcher_exposes_exact_protocol_v1_method_table() -> None:
    assert set(JsonRpcDispatcher(Facade()).methods) == {
        "initialize",
        "application.getState",
        "thread.list",
        "thread.read",
        "turn.submit",
        "direct.execute",
        "command.execute",
        "interaction.respond",
        "operation.cancel",
        "shutdown",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "params", "call"),
    [
        ("initialize", INITIALIZE_PARAMS, "initialize"),
        ("application.getState", {}, "state"),
        ("thread.list", {}, "list"),
        (
            "turn.submit",
            {"thread_id": "thread_1", "content": "inspect"},
            "turn",
        ),
        (
            "direct.execute",
            {"thread_id": "thread_1", "command": "git status"},
            "direct",
        ),
        (
            "command.execute",
            {"name": "status", "arguments": []},
            "command",
        ),
        (
            "interaction.respond",
            {"interaction_id": "interaction_1", "decision": "trust"},
            "interaction",
        ),
        (
            "operation.cancel",
            {"operation_id": "operation_1"},
            "cancel",
        ),
        ("shutdown", {}, "shutdown"),
    ],
)
async def test_closed_method_table_dispatches_typed_params(
    method: str,
    params: dict[str, object],
    call: str,
) -> None:
    facade = Facade()
    dispatcher = JsonRpcDispatcher(facade)

    response = await dispatcher.dispatch(
        {"jsonrpc": "2.0", "id": "request_1", "method": method, "params": params}
    )

    assert response is not None
    assert response["jsonrpc"] == "2.0"
    assert response["id"] == "request_1"
    assert "result" in response
    assert response["result"]["ok"] is (method != "command.execute")
    assert facade.calls[0][0] == call


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("params", "error_code"),
    [
        (
            {**INITIALIZE_PARAMS, "protocol_version": 2},
            "protocol_version_incompatible",
        ),
        (
            {**INITIALIZE_PARAMS, "client_name": "other-client"},
            "client_version_incompatible",
        ),
        (
            {**INITIALIZE_PARAMS, "client_version": "999.0.0"},
            "client_version_incompatible",
        ),
    ],
)
async def test_initialize_rejects_incompatible_identity_before_facade_work(
    params: dict[str, object],
    error_code: str,
) -> None:
    facade = Facade()

    response = await JsonRpcDispatcher(facade).dispatch(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": params,
        }
    )

    assert response is not None
    assert response["result"]["ok"] is False
    assert response["result"]["error"]["code"] == error_code
    assert facade.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {},
        {"protocol_version": 1, "client_name": "awesome-tui"},
        {**INITIALIZE_PARAMS, "extra": True},
        {**INITIALIZE_PARAMS, "protocol_version": "1"},
    ],
)
async def test_initialize_rejects_malformed_identity_as_invalid_params(
    params: dict[str, object],
) -> None:
    facade = Facade()

    response = await JsonRpcDispatcher(facade).dispatch(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": params,
        }
    )

    assert response is not None
    assert response["error"]["code"] == -32602
    assert facade.calls == []


def test_product_version_matches_distribution_and_repository_metadata() -> None:
    root = Path(__file__).parents[3]
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

    assert installed_version("awesome-agent") == PRODUCT_VERSION
    assert metadata["project"]["version"] == PRODUCT_VERSION


@pytest.mark.asyncio
async def test_product_error_is_result_and_internal_error_is_redacted() -> None:
    dispatcher = JsonRpcDispatcher(Facade())

    product = await dispatcher.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "command.execute",
            "params": {"name": "status"},
        }
    )
    internal = await dispatcher.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "thread.read",
            "params": {"thread_id": "thread_1"},
        }
    )

    assert product["result"]["ok"] is False
    assert product["result"]["error"]["code"] == "invalid_arguments"
    assert "error" not in product
    assert internal["error"]["code"] == -32603
    assert "private" not in str(internal)
    assert "traceback" not in str(internal).casefold()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (
            {
                "jsonrpc": "1.0",
                "id": 1,
                "method": "initialize",
                "params": INITIALIZE_PARAMS,
            },
            -32600,
        ),
        (
            {
                "jsonrpc": "2.0",
                "id": True,
                "method": "initialize",
                "params": INITIALIZE_PARAMS,
            },
            -32600,
        ),
        ({"jsonrpc": "2.0", "id": 1, "method": "unknown"}, -32601),
        (
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "turn.submit",
                "params": {"thread_id": "thread_1"},
            },
            -32602,
        ),
        (
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": [],
            },
            -32602,
        ),
    ],
)
async def test_standard_request_errors(payload: object, code: int) -> None:
    response = await JsonRpcDispatcher(Facade()).dispatch(payload)

    assert response is not None
    assert response["error"]["code"] == code


@pytest.mark.asyncio
async def test_notification_executes_without_response() -> None:
    facade = Facade()

    response = await JsonRpcDispatcher(facade).dispatch(
        {"jsonrpc": "2.0", "method": "application.getState", "params": {}}
    )

    assert response is None
    assert facade.calls == [("state", None)]


def test_event_notification_contains_stable_envelope() -> None:
    event = EventEnvelope(
        event_id="event_1",
        sequence=1,
        session_id="session_1",
        workspace_key="ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        event_type=EventType.WARNING,
        timestamp=datetime.now(UTC),
        payload=WarningPayload(code="safe", message="Safe warning."),
    )

    notification = event_notification(event)

    assert notification["jsonrpc"] == "2.0"
    assert notification["method"] == "event"
    assert notification["params"]["version"] == 1
    assert notification["params"]["event_id"] == "event_1"


def test_parse_error_helper_uses_null_id() -> None:
    assert jsonrpc_error(-32700, "Parse error")["id"] is None
