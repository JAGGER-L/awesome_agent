from __future__ import annotations

import logging
import traceback
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    model_validator,
)

from awesome_agent.application.commands import CommandIntent
from awesome_agent.application.contracts import (
    ApplicationResult,
    InitializeParams,
    InitializeResult,
    ProductError,
    ProductErrorCode,
    ProviderCredentialSetRequest,
    ThreadListQuery,
    ThreadReadQuery,
)
from awesome_agent.application.facade import ApplicationFacade
from awesome_agent.application.interactions import InteractionDecision
from awesome_agent.core.contracts import (
    JsonSafeInteger,
    normalize_json_safe_integer,
)
from awesome_agent.core.events import EventEnvelope
from awesome_agent.version import PRODUCT_VERSION

JSONRPC_VERSION = "2.0"
PROTOCOL_VERSION = 3

logger = logging.getLogger(__name__)

type JsonObject = dict[str, Any]
type MethodHandler = Callable[[Mapping[str, object]], Awaitable[object]]


def _reject_explicit_nulls(
    value: object,
    fields: frozenset[str],
) -> object:
    if isinstance(value, Mapping) and any(
        field in value and value[field] is None for field in fields
    ):
        raise ValueError("Optional wire fields cannot be null.")
    return value


class _EmptyParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _InitializeWireParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: JsonSafeInteger
    client_name: str = Field(min_length=1, max_length=128, strict=True)
    client_version: str = Field(min_length=1, max_length=64, strict=True)


class _ThreadParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    thread_id: str = Field(min_length=1, max_length=128)


class _ThreadListParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cursor: str | None = Field(default=None, min_length=1, max_length=1_024)
    limit: JsonSafeInteger = Field(default=50, ge=1, le=200)

    @model_validator(mode="before")
    @classmethod
    def reject_null_fields(cls, value: object) -> object:
        return _reject_explicit_nulls(value, frozenset({"cursor", "limit"}))


class _ThreadReadParams(_ThreadParams):
    before_sequence: JsonSafeInteger | None = Field(default=None, ge=1)
    limit: JsonSafeInteger = Field(default=100, ge=1, le=500)

    @model_validator(mode="before")
    @classmethod
    def reject_null_fields(cls, value: object) -> object:
        return _reject_explicit_nulls(
            value,
            frozenset({"before_sequence", "limit"}),
        )


class _TurnParams(_ThreadParams):
    content: str = Field(min_length=1, max_length=200_000)
    client_message_id: str = Field(
        pattern=r"^client_[A-Za-z0-9_-]+$",
        max_length=128,
    )


class _DirectParams(_ThreadParams):
    command: str = Field(min_length=1, max_length=30_000)


class _InteractionParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    interaction_id: str = Field(min_length=1, max_length=128)
    decision: InteractionDecision


class _OperationParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str = Field(min_length=1, max_length=128)


class _ProviderCredentialParams(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["deepseek", "kimi", "mem0"]
    action: Literal["add", "replace", "delete"]
    api_key: str | None = Field(default=None, min_length=1, max_length=20_000)
    allow_unverified: bool = Field(default=False, strict=True)

    @model_validator(mode="before")
    @classmethod
    def reject_null_fields(cls, value: object) -> object:
        return _reject_explicit_nulls(value, frozenset({"api_key"}))


class JsonRpcDispatcher:
    def __init__(
        self,
        facade: ApplicationFacade,
        *,
        method_completed: Callable[[str], None] | None = None,
    ) -> None:
        self._facade = facade
        self._method_completed = method_completed
        self._methods: dict[str, MethodHandler] = {
            "initialize": self._initialize,
            "application.getState": self._get_state,
            "thread.list": self._list_threads,
            "thread.read": self._read_thread,
            "turn.submit": self._submit_turn,
            "direct.execute": self._execute_direct,
            "command.execute": self._execute_command,
            "provider.credential.set": self._set_provider_credential,
            "interaction.respond": self._respond_interaction,
            "operation.cancel": self._cancel_operation,
            "shutdown": self._shutdown,
        }

    @property
    def methods(self) -> tuple[str, ...]:
        return tuple(self._methods)

    async def dispatch(self, value: object) -> JsonObject | None:
        request = parse_jsonrpc_request(value)
        if request is None:
            return jsonrpc_error(-32600, "Invalid Request")
        request_id, has_id, method, params = request
        handler = self._methods.get(method)
        if handler is None:
            return (
                jsonrpc_error(-32601, "Method not found", request_id=request_id)
                if has_id
                else None
            )
        if not isinstance(params, Mapping):
            return (
                jsonrpc_error(-32602, "Invalid params", request_id=request_id)
                if has_id
                else None
            )
        try:
            result = await handler(params)
        except ValidationError:
            return (
                jsonrpc_error(-32602, "Invalid params", request_id=request_id)
                if has_id
                else None
            )
        except Exception as error:
            _log_unexpected_request_failure(
                error,
                method=method,
                request_id=request_id,
            )
            return (
                jsonrpc_error(
                    -32603,
                    "Internal error",
                    request_id=request_id,
                    data={"diagnostic_code": "core_request_failed"},
                )
                if has_id
                else None
            )
        if self._method_completed is not None:
            self._method_completed(method)
        if not has_id:
            return None
        return {
            "jsonrpc": JSONRPC_VERSION,
            "id": request_id,
            "result": _serialize(result),
        }

    async def _initialize(self, params: Mapping[str, object]) -> object:
        wire = _InitializeWireParams.model_validate(params)
        if wire.protocol_version != PROTOCOL_VERSION:
            return ApplicationResult[InitializeResult].failure(
                ProductError(
                    code=ProductErrorCode.PROTOCOL_VERSION_INCOMPATIBLE,
                    message="Protocol version is incompatible.",
                )
            )
        if wire.client_name != "awesome" or wire.client_version != PRODUCT_VERSION:
            return ApplicationResult[InitializeResult].failure(
                ProductError(
                    code=ProductErrorCode.CLIENT_VERSION_INCOMPATIBLE,
                    message="Client identity is incompatible.",
                )
            )
        InitializeParams.model_validate(wire.model_dump())
        return await self._facade.initialize()

    async def _get_state(self, params: Mapping[str, object]) -> object:
        _EmptyParams.model_validate(params)
        return await self._facade.get_state()

    async def _list_threads(self, params: Mapping[str, object]) -> object:
        wire = _ThreadListParams.model_validate(params)
        query = ThreadListQuery(cursor=wire.cursor, limit=wire.limit)
        return await self._facade.list_threads(query)

    async def _read_thread(self, params: Mapping[str, object]) -> object:
        wire = _ThreadReadParams.model_validate(params)
        query = ThreadReadQuery(
            thread_id=wire.thread_id,
            before_sequence=wire.before_sequence,
            limit=wire.limit,
        )
        return await self._facade.read_thread(query)

    async def _submit_turn(self, params: Mapping[str, object]) -> object:
        parsed = _TurnParams.model_validate(params)
        return await self._facade.submit_turn(
            parsed.thread_id,
            parsed.content,
            parsed.client_message_id,
        )

    async def _execute_direct(self, params: Mapping[str, object]) -> object:
        parsed = _DirectParams.model_validate(params)
        return await self._facade.execute_direct(parsed.thread_id, parsed.command)

    async def _execute_command(self, params: Mapping[str, object]) -> object:
        intent = CommandIntent.model_validate(params)
        return await self._facade.execute_command(intent)

    async def _set_provider_credential(self, params: Mapping[str, object]) -> object:
        parsed = _ProviderCredentialParams.model_validate(params)
        request = ProviderCredentialSetRequest(
            provider=parsed.provider,
            action=parsed.action,
            api_key=SecretStr(parsed.api_key) if parsed.api_key is not None else None,
            allow_unverified=parsed.allow_unverified,
        )
        return await self._facade.set_provider_credential(request)

    async def _respond_interaction(self, params: Mapping[str, object]) -> object:
        parsed = _InteractionParams.model_validate(params)
        return await self._facade.respond_interaction(
            parsed.interaction_id,
            parsed.decision.value,
        )

    async def _cancel_operation(self, params: Mapping[str, object]) -> object:
        parsed = _OperationParams.model_validate(params)
        return await self._facade.cancel_operation(parsed.operation_id)

    async def _shutdown(self, params: Mapping[str, object]) -> object:
        _EmptyParams.model_validate(params)
        return await self._facade.shutdown()


def event_notification(event: EventEnvelope) -> JsonObject:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "method": "event",
        "params": event.model_dump(mode="json"),
    }


def jsonrpc_error(
    code: int,
    message: str,
    *,
    request_id: str | int | None = None,
    data: JsonObject | None = None,
) -> JsonObject:
    error: JsonObject = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "error": error,
    }


def _log_unexpected_request_failure(
    error: Exception,
    *,
    method: str,
    request_id: str | int | None,
) -> None:
    frames = traceback.extract_tb(error.__traceback__)
    stack = " > ".join(
        f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}" for frame in frames
    )
    logger.error(
        "Unhandled JSON-RPC request method=%s request_id=%r exception_type=%s stack=%s",
        method,
        request_id,
        type(error).__name__,
        stack or "unavailable",
    )


def parse_jsonrpc_request(
    value: object,
) -> tuple[str | int | None, bool, str, object] | None:
    if not isinstance(value, Mapping):
        return None
    if set(value) - {"jsonrpc", "id", "method", "params"}:
        return None
    if value.get("jsonrpc") != JSONRPC_VERSION:
        return None
    method = value.get("method")
    if not isinstance(method, str) or not method:
        return None
    has_id = "id" in value
    request_id = normalize_jsonrpc_request_id(value.get("id"))
    if has_id and request_id is None:
        return None
    params = value.get("params", {})
    return request_id, has_id, method, params


def normalize_jsonrpc_request_id(value: object) -> str | int | None:
    if isinstance(value, str):
        if not 1 <= len(value) <= 128:
            return None
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return None
        return value
    if type(value) is int:
        try:
            return normalize_json_safe_integer(value)
        except ValueError:
            return None
    if type(value) is float:
        try:
            return normalize_json_safe_integer(value)
        except ValueError:
            return None
    return None


def _serialize(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=True)
    return value
