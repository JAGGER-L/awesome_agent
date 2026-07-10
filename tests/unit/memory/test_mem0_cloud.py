import asyncio
import hashlib
from collections.abc import Callable

import pytest

from awesome_agent.memory.identity import Mem0Identity
from awesome_agent.memory.mem0_cloud import (
    Mem0CloudAdapter,
    Mem0CloudError,
    create_mem0_client,
)
from awesome_agent.memory.models import (
    CloudDeleteStatus,
    MemoryCandidate,
    MemoryScope,
)

IDENTITY = Mem0Identity(
    user_id="user_11111111111111111111111111111111",
    workspace_key="ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
)


def _record(
    identifier: str,
    content: str,
    *,
    scope: str = "user",
    workspace_key: str | None = None,
    user_id: str = IDENTITY.user_id,
    fact_hash: str | None = None,
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "app_id": "awesome-agent",
        "scope": scope,
        "fact_hash": fact_hash or hashlib.sha256(identifier.encode()).hexdigest(),
    }
    if workspace_key is not None:
        metadata["workspace_key"] = workspace_key
    return {
        "id": identifier,
        "memory": content,
        "user_id": user_id,
        "metadata": metadata,
        "raw_private_field": "discard",
    }


class FakeClient:
    def __init__(self) -> None:
        self.search_result: object = {"results": []}
        self.add_result: object = {"event_id": "queued"}
        self.get_result: object = None
        self.delete_result: object = {"status": "deleted"}
        self.search_error: Exception | None = None
        self.delay = 0.0
        self.search_calls: list[dict[str, object]] = []
        self.add_calls: list[dict[str, object]] = []
        self.get_calls: list[str] = []
        self.delete_calls: list[str] = []

    async def search(self, query: str, **kwargs: object) -> object:
        self.search_calls.append({"query": query, **kwargs})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.search_error is not None:
            raise self.search_error
        return self.search_result

    async def add(self, messages: object, **kwargs: object) -> object:
        self.add_calls.append({"messages": messages, **kwargs})
        return self.add_result

    async def get(self, memory_id: str) -> object:
        self.get_calls.append(memory_id)
        return self.get_result

    async def delete(self, memory_id: str) -> object:
        self.delete_calls.append(memory_id)
        return self.delete_result


@pytest.mark.asyncio
async def test_search_normalizes_and_enforces_remote_scope_and_limit() -> None:
    client = FakeClient()
    client.search_result = {
        "results": [
            _record("memory_user", "User preference"),
            _record(
                "memory_workspace",
                "Workspace fact",
                scope="workspace",
                workspace_key=IDENTITY.workspace_key,
            ),
        ]
    }
    adapter = Mem0CloudAdapter(client)

    results = await adapter.search(
        "current task",
        user_id=IDENTITY.user_id,
        workspace_key=IDENTITY.workspace_key,
        limit=99,
    )

    assert [item.id for item in results] == ["memory_user", "memory_workspace"]
    assert results[0].model_dump() == {
        "id": "memory_user",
        "content": "User preference",
        "scope": MemoryScope.USER,
        "fact_hash": hashlib.sha256(b"memory_user").hexdigest(),
        "workspace_key": None,
    }
    [call] = client.search_calls
    assert call["user_id"] == IDENTITY.user_id
    assert call["limit"] == 8
    assert call["filters"] == {
        "AND": [
            {"app_id": "awesome-agent"},
            {
                "OR": [
                    {"scope": "user"},
                    {
                        "AND": [
                            {"scope": "workspace"},
                            {"workspace_key": IDENTITY.workspace_key},
                        ]
                    },
                ]
            },
        ]
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_factory", "code"),
    [
        (lambda: _StatusError(401), "mem0_auth_failed"),
        (lambda: _StatusError(429), "mem0_rate_limited"),
        (lambda: RuntimeError("request body secret"), "mem0_unavailable"),
    ],
)
async def test_search_errors_are_typed_and_safe(
    error_factory: Callable[[], Exception],
    code: str,
) -> None:
    client = FakeClient()
    client.search_error = error_factory()

    with pytest.raises(Mem0CloudError) as raised:
        await Mem0CloudAdapter(client).search(
            "secret query",
            user_id=IDENTITY.user_id,
            workspace_key=IDENTITY.workspace_key,
        )

    assert raised.value.diagnostic.code == code
    assert "secret" not in raised.value.diagnostic.model_dump_json()


@pytest.mark.asyncio
async def test_timeout_and_cancellation_are_distinct() -> None:
    client = FakeClient()
    client.delay = 1.0
    adapter = Mem0CloudAdapter(client, timeout_seconds=0.01)
    with pytest.raises(Mem0CloudError) as timed_out:
        await adapter.search(
            "query",
            user_id=IDENTITY.user_id,
            workspace_key=IDENTITY.workspace_key,
        )
    assert timed_out.value.diagnostic.code == "mem0_timeout"

    client.delay = 10.0
    task = asyncio.create_task(
        Mem0CloudAdapter(client).search(
            "query",
            user_id=IDENTITY.user_id,
            workspace_key=IDENTITY.workspace_key,
        )
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"results": [_record("", "missing id")]},
        {"results": [{"id": "x"}]},
        {"results": [_record("same", "a"), _record("same", "b")]},
        {"unexpected": []},
    ],
)
async def test_malformed_search_payload_is_rejected(payload: object) -> None:
    client = FakeClient()
    client.search_result = payload
    with pytest.raises(Mem0CloudError) as raised:
        await Mem0CloudAdapter(client).search(
            "query",
            user_id=IDENTITY.user_id,
            workspace_key=IDENTITY.workspace_key,
        )
    assert raised.value.diagnostic.code == "mem0_malformed_response"


@pytest.mark.asyncio
async def test_add_uses_infer_false_and_only_allowed_metadata() -> None:
    client = FakeClient()
    candidate = MemoryCandidate(
        scope=MemoryScope.WORKSPACE,
        content="Project uses pytest.",
        fact_hash="a" * 64,
    )

    outcome = await Mem0CloudAdapter(client).add(candidate, IDENTITY)

    assert outcome.accepted is True
    assert outcome.queued is True
    [call] = client.add_calls
    assert call == {
        "messages": [{"role": "user", "content": "Project uses pytest."}],
        "user_id": IDENTITY.user_id,
        "metadata": {
            "app_id": "awesome-agent",
            "scope": "workspace",
            "workspace_key": IDENTITY.workspace_key,
            "fact_hash": "a" * 64,
        },
        "infer": False,
    }


@pytest.mark.asyncio
async def test_fact_hash_check_is_remote_and_scope_exact() -> None:
    client = FakeClient()
    client.search_result = {
        "results": [_record("memory_existing", "same fact", fact_hash="b" * 64)]
    }

    exists = await Mem0CloudAdapter(client).has_fact_hash(
        "b" * 64,
        user_id=IDENTITY.user_id,
        scope=MemoryScope.USER,
        workspace_key=None,
    )

    assert exists is True
    [call] = client.search_calls
    assert call["filters"] == {
        "AND": [
            {"app_id": "awesome-agent"},
            {"scope": "user"},
            {"fact_hash": "b" * 64},
        ]
    }


@pytest.mark.asyncio
async def test_remove_fetches_and_verifies_scope_before_delete() -> None:
    client = FakeClient()
    client.get_result = _record(
        "memory_workspace",
        "fact",
        scope="workspace",
        workspace_key=IDENTITY.workspace_key,
    )
    adapter = Mem0CloudAdapter(client)

    removed = await adapter.remove_scoped("memory_workspace", IDENTITY)

    assert removed.status is CloudDeleteStatus.REMOVED
    assert client.get_calls == ["memory_workspace"]
    assert client.delete_calls == ["memory_workspace"]

    client.get_result = _record(
        "other",
        "private",
        scope="workspace",
        workspace_key="ws_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    )
    rejected = await adapter.remove_scoped("other", IDENTITY)
    assert rejected.status is CloudDeleteStatus.NOT_FOUND
    assert client.delete_calls == ["memory_workspace"]

    client.get_result = _record(
        "other-user",
        "private",
        user_id="user_99999999999999999999999999999999",
    )
    rejected_user = await adapter.remove_scoped("other-user", IDENTITY)
    assert rejected_user.status is CloudDeleteStatus.NOT_FOUND
    assert client.delete_calls == ["memory_workspace"]

    client.get_result = _record("delete-fails", "fact")
    client.delete_result = {"status": "failed", "raw": "secret"}
    failed = await adapter.remove_scoped("delete-fails", IDENTITY)
    assert failed.status is CloudDeleteStatus.FAILED
    assert failed.diagnostic is not None
    assert failed.diagnostic.code == "mem0_delete_failed"
    assert "secret" not in failed.model_dump_json()


def test_missing_credential_is_safe_before_sdk_import() -> None:
    with pytest.raises(Mem0CloudError) as raised:
        create_mem0_client(None)
    assert raised.value.diagnostic.code == "mem0_credential_missing"


class _StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
