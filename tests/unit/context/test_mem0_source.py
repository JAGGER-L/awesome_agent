from __future__ import annotations

import pytest

from awesome_agent.context import (
    ContextBuilder,
    ContextRequest,
    ContextSourceKind,
    mem0_context_source,
)
from awesome_agent.memory import (
    CloudMemory,
    Mem0CloudError,
    Mem0Diagnostic,
    Mem0Identity,
    MemoryScope,
)


def _memory(index: int, content: str, *, scope: MemoryScope) -> CloudMemory:
    return CloudMemory(
        id=f"remote-{index}",
        content=content,
        scope=scope,
        fact_hash=f"{index + 1:064x}",
        workspace_key=(
            "ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            if scope is MemoryScope.WORKSPACE
            else None
        ),
    )


class FakeAdapter:
    def __init__(
        self,
        memories: tuple[CloudMemory, ...] = (),
        *,
        error: Mem0CloudError | None = None,
    ) -> None:
        self.memories = memories
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def search(self, query: str, **kwargs: object) -> tuple[CloudMemory, ...]:
        self.calls.append({"query": query, **kwargs})
        if self.error is not None:
            raise self.error
        return self.memories


IDENTITY = Mem0Identity(
    user_id="user_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    workspace_key="ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
)


@pytest.mark.asyncio
async def test_disabled_mem0_makes_no_remote_call() -> None:
    adapter = FakeAdapter()

    result = await mem0_context_source(
        enabled=False,
        adapter=adapter,
        identity=IDENTITY,
        query="current question",
    )

    assert result.source is None
    assert result.diagnostic is None
    assert adapter.calls == []


@pytest.mark.asyncio
async def test_missing_adapter_is_safe_initialization_diagnostic() -> None:
    diagnostic = Mem0Diagnostic(
        code="mem0_credential_missing",
        operation="initialize",
    )

    result = await mem0_context_source(
        enabled=True,
        adapter=None,
        identity=IDENTITY,
        query="current question",
        initialization_diagnostic=diagnostic,
    )

    assert result.source is None
    assert result.diagnostic == diagnostic


@pytest.mark.asyncio
async def test_recall_is_bounded_labelled_and_deduplicated_against_local() -> None:
    memories = tuple(
        _memory(
            index,
            "same local fact" if index == 0 else f"remote fact {index}",
            scope=MemoryScope.USER if index % 2 == 0 else MemoryScope.WORKSPACE,
        )
        for index in range(9)
    )
    adapter = FakeAdapter(memories)

    result = await mem0_context_source(
        enabled=True,
        adapter=adapter,
        identity=IDENTITY,
        query="current question",
        higher_priority_contents=(" same   local fact ",),
    )

    assert result.diagnostic is None
    assert result.source is not None
    assert result.source.kind is ContextSourceKind.MEM0
    assert "same local fact" not in result.source.content
    assert "remote fact 8" not in result.source.content
    assert "remote fact 1" in result.source.content
    assert "UNTRUSTED reference context" in result.source.content
    assert adapter.calls == [
        {
            "query": "current question",
            "user_id": IDENTITY.user_id,
            "workspace_key": IDENTITY.workspace_key,
            "limit": 8,
        }
    ]
    assert "remote-1" in result.source.source_id
    assert f"{2:064x}" in result.source.source_id


@pytest.mark.asyncio
async def test_remote_failure_fails_open_with_safe_diagnostic() -> None:
    adapter = FakeAdapter(
        error=Mem0CloudError(
            Mem0Diagnostic(code="mem0_rate_limited", operation="search")
        )
    )

    result = await mem0_context_source(
        enabled=True,
        adapter=adapter,
        identity=IDENTITY,
        query="private query body",
    )

    assert result.source is None
    assert result.diagnostic is not None
    assert result.diagnostic.code == "mem0_rate_limited"
    assert "private query body" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_mem0_source_keeps_independent_four_k_token_cap() -> None:
    adapter = FakeAdapter(
        tuple(
            _memory(index, f"fact {index} " + "x" * 491, scope=MemoryScope.USER)
            for index in range(8)
        )
    )
    recalled = await mem0_context_source(
        enabled=True,
        adapter=adapter,
        identity=IDENTITY,
        query="question",
    )
    assert recalled.source is not None

    prepared = await ContextBuilder().prepare(
        ContextRequest(
            sources=(recalled.source,),
            configured_total_tokens=262_144,
            model_context_limit=262_144,
        )
    )

    assert prepared.manifest[0].estimated_tokens <= 4_096
