from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Mapping
from typing import Protocol, cast

from awesome_agent.memory.identity import Mem0Identity
from awesome_agent.memory.models import (
    CloudDeleteOutcome,
    CloudDeleteStatus,
    CloudMemory,
    CloudWriteOutcome,
    Mem0Diagnostic,
    MemoryCandidate,
    MemoryScope,
)

MEM0_TIMEOUT_SECONDS = 3.0
MEM0_MAX_RESULTS = 8


class Mem0Client(Protocol):
    async def search(self, query: str, **kwargs: object) -> object: ...

    async def add(self, messages: object, **kwargs: object) -> object: ...

    async def get(self, memory_id: str) -> object: ...

    async def delete(self, memory_id: str) -> object: ...


class Mem0CloudError(RuntimeError):
    def __init__(self, diagnostic: Mem0Diagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(diagnostic.code)


def create_mem0_client(api_key: str | None) -> Mem0Client:
    if api_key is None or not api_key.strip():
        raise _error("mem0_credential_missing", "initialize")
    try:
        from mem0 import AsyncMemoryClient
    except ImportError as error:
        raise _error("mem0_dependency_missing", "initialize") from error
    return cast(Mem0Client, AsyncMemoryClient(api_key=api_key))


class Mem0CloudAdapter:
    def __init__(
        self,
        client: Mem0Client,
        *,
        timeout_seconds: float = MEM0_TIMEOUT_SECONDS,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._client = client
        self._timeout_seconds = timeout_seconds

    async def search(
        self,
        query: str,
        *,
        user_id: str,
        workspace_key: str,
        limit: int = MEM0_MAX_RESULTS,
    ) -> tuple[CloudMemory, ...]:
        bounded_limit = max(1, min(limit, MEM0_MAX_RESULTS))
        filters = _recall_filters(workspace_key)
        payload = await self._call(
            "search",
            self._client.search(
                query,
                user_id=user_id,
                filters=filters,
                limit=bounded_limit,
            ),
        )
        return _normalize_search(
            payload,
            user_id=user_id,
            workspace_key=workspace_key,
            limit=bounded_limit,
        )

    async def has_fact_hash(
        self,
        fact_hash: str,
        *,
        user_id: str,
        scope: MemoryScope,
        workspace_key: str | None,
    ) -> bool:
        filters: list[dict[str, str]] = [
            {"app_id": "awesome-agent"},
            {"scope": scope.value},
            {"fact_hash": fact_hash},
        ]
        if scope is MemoryScope.WORKSPACE:
            if workspace_key is None:
                raise ValueError("workspace scope requires workspace_key")
            filters.append({"workspace_key": workspace_key})
        payload = await self._call(
            "dedupe",
            self._client.search(
                fact_hash,
                user_id=user_id,
                filters={"AND": filters},
                limit=1,
            ),
        )
        results = _normalize_search(
            payload,
            user_id=user_id,
            workspace_key=workspace_key,
            limit=1,
            expected_scope=scope,
        )
        return any(item.fact_hash == fact_hash for item in results)

    async def add(
        self,
        candidate: MemoryCandidate,
        identity: Mem0Identity,
    ) -> CloudWriteOutcome:
        metadata = {
            "app_id": identity.app_id,
            "scope": candidate.scope.value,
            "fact_hash": candidate.fact_hash,
        }
        if candidate.scope is MemoryScope.WORKSPACE:
            metadata["workspace_key"] = identity.workspace_key
        try:
            payload = await self._call(
                "add",
                self._client.add(
                    [{"role": "user", "content": candidate.content}],
                    user_id=identity.user_id,
                    metadata=metadata,
                    infer=False,
                ),
            )
        except Mem0CloudError as error:
            return CloudWriteOutcome(accepted=False, diagnostic=error.diagnostic)
        if isinstance(payload, Mapping) and isinstance(payload.get("event_id"), str):
            return CloudWriteOutcome(accepted=True, queued=True)
        memory_id = _added_memory_id(payload)
        if memory_id is not None:
            return CloudWriteOutcome(accepted=True, memory_id=memory_id)
        return CloudWriteOutcome(
            accepted=False,
            diagnostic=_diagnostic("mem0_malformed_response", "add"),
        )

    async def get_scoped(
        self,
        memory_id: str,
        identity: Mem0Identity,
    ) -> CloudMemory | None:
        payload = await self._call("get", self._client.get(memory_id))
        if payload is None:
            return None
        try:
            return _normalize_record(
                payload,
                user_id=identity.user_id,
                workspace_key=identity.workspace_key,
                require_user_id=True,
            )
        except (TypeError, ValueError):
            return None

    async def remove_scoped(
        self,
        memory_id: str,
        identity: Mem0Identity,
    ) -> CloudDeleteOutcome:
        try:
            memory = await self.get_scoped(memory_id, identity)
        except Mem0CloudError as error:
            return CloudDeleteOutcome(
                status=CloudDeleteStatus.FAILED,
                memory_id=memory_id,
                diagnostic=error.diagnostic,
            )
        if memory is None:
            return CloudDeleteOutcome(
                status=CloudDeleteStatus.NOT_FOUND,
                memory_id=memory_id,
            )
        try:
            payload = await self._call("delete", self._client.delete(memory_id))
        except Mem0CloudError as error:
            return CloudDeleteOutcome(
                status=CloudDeleteStatus.FAILED,
                memory_id=memory_id,
                diagnostic=error.diagnostic,
            )
        if isinstance(payload, Mapping) and (
            "error" in payload or payload.get("status") in {"failed", "error"}
        ):
            return CloudDeleteOutcome(
                status=CloudDeleteStatus.FAILED,
                memory_id=memory_id,
                diagnostic=_diagnostic("mem0_delete_failed", "delete"),
            )
        return CloudDeleteOutcome(
            status=CloudDeleteStatus.REMOVED,
            memory_id=memory_id,
        )

    async def _call(self, operation: str, awaitable: Awaitable[object]) -> object:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                return await awaitable
        except TimeoutError as error:
            raise _error("mem0_timeout", operation) from error
        except Exception as error:
            raise _error(_exception_code(error), operation) from error


def _recall_filters(workspace_key: str) -> dict[str, object]:
    return {
        "AND": [
            {"app_id": "awesome-agent"},
            {
                "OR": [
                    {"scope": "user"},
                    {
                        "AND": [
                            {"scope": "workspace"},
                            {"workspace_key": workspace_key},
                        ]
                    },
                ]
            },
        ]
    }


def _normalize_search(
    payload: object,
    *,
    user_id: str,
    workspace_key: str | None,
    limit: int,
    expected_scope: MemoryScope | None = None,
) -> tuple[CloudMemory, ...]:
    raw_results: object
    if isinstance(payload, list):
        raw_results = payload
    elif isinstance(payload, Mapping) and "results" in payload:
        raw_results = payload["results"]
    else:
        raise _error("mem0_malformed_response", "search")
    if not isinstance(raw_results, list):
        raise _error("mem0_malformed_response", "search")
    results: list[CloudMemory] = []
    identifiers: set[str] = set()
    try:
        for raw in raw_results[:limit]:
            memory = _normalize_record(
                raw,
                user_id=user_id,
                workspace_key=workspace_key,
                require_user_id=False,
            )
            if expected_scope is not None and memory.scope is not expected_scope:
                raise ValueError("unexpected scope")
            if memory.id in identifiers:
                raise ValueError("duplicate id")
            identifiers.add(memory.id)
            results.append(memory)
    except (TypeError, ValueError) as error:
        raise _error("mem0_malformed_response", "search") from error
    return tuple(results)


def _normalize_record(
    payload: object,
    *,
    user_id: str,
    workspace_key: str | None,
    require_user_id: bool,
) -> CloudMemory:
    if not isinstance(payload, Mapping):
        raise TypeError("record")
    identifier = payload.get("id")
    content = payload.get("memory", payload.get("content"))
    metadata = payload.get("metadata")
    record_user_id = payload.get("user_id")
    if not isinstance(identifier, str) or not identifier:
        raise ValueError("id")
    if not isinstance(content, str) or not content:
        raise ValueError("content")
    if not isinstance(metadata, Mapping):
        raise ValueError("metadata")
    if require_user_id and record_user_id != user_id:
        raise ValueError("user")
    if isinstance(record_user_id, str) and record_user_id != user_id:
        raise ValueError("user")
    if metadata.get("app_id") != "awesome-agent":
        raise ValueError("app")
    scope = MemoryScope(str(metadata.get("scope")))
    record_workspace = metadata.get("workspace_key")
    if scope is MemoryScope.USER:
        normalized_workspace = None
    elif isinstance(record_workspace, str) and record_workspace == workspace_key:
        normalized_workspace = record_workspace
    else:
        raise ValueError("workspace")
    return CloudMemory(
        id=identifier,
        content=content,
        scope=scope,
        fact_hash=str(metadata.get("fact_hash") or ""),
        workspace_key=normalized_workspace,
    )


def _added_memory_id(payload: object) -> str | None:
    if isinstance(payload, Mapping):
        direct = payload.get("id")
        if isinstance(direct, str) and direct:
            return direct
        results = payload.get("results")
        if isinstance(results, list) and results and isinstance(results[0], Mapping):
            identifier = results[0].get("id")
            return identifier if isinstance(identifier, str) and identifier else None
    return None


def _exception_code(error: Exception) -> str:
    status = getattr(error, "status_code", None)
    if status in {401, 403}:
        return "mem0_auth_failed"
    if status == 429:
        return "mem0_rate_limited"
    return "mem0_unavailable"


def _diagnostic(code: str, operation: str) -> Mem0Diagnostic:
    return Mem0Diagnostic(code=code, operation=operation)


def _error(code: str, operation: str) -> Mem0CloudError:
    return Mem0CloudError(_diagnostic(code, operation))
