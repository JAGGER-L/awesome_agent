from pathlib import Path
from uuid import uuid4

import pytest
from tests.fakes import FakeModelProvider

from awesome_agent.memory.builtin import BuiltinMemoryStore
from awesome_agent.memory.compression import ContextCompressor
from awesome_agent.memory.models import (
    ContextItem,
    MemoryCandidate,
    MemoryKind,
    MemorySource,
)
from awesome_agent.memory.pipeline import MemoryPipeline
from awesome_agent.memory.policy import MemoryPolicy


def _candidate(
    content: str,
    *,
    source: MemorySource = MemorySource.USER_STATEMENT,
) -> MemoryCandidate:
    return MemoryCandidate(
        kind=MemoryKind.USER,
        content=content,
        source=source,
    )


def test_builtin_memory_is_bounded_and_deduplicated(tmp_path: Path) -> None:
    store = BuiltinMemoryStore(root=tmp_path, policy=MemoryPolicy())
    candidate = _candidate("Prefer concise engineering updates.")

    assert store.write(candidate)
    assert not store.write(candidate)
    assert "Prefer concise" in store.snapshot()[MemoryKind.USER]


def test_memory_policy_blocks_secrets_source_and_retrieval() -> None:
    policy = MemoryPolicy()

    assert not policy.accept(_candidate("API_KEY=secret-value"))
    assert not policy.accept(_candidate("```python\nprint('source')\n```"))
    assert not policy.accept(
        _candidate("retrieved fact", source=MemorySource.MEMORY_RETRIEVAL)
    )


@pytest.mark.asyncio
async def test_pipeline_defaults_can_disable_both_layers(tmp_path: Path) -> None:
    policy = MemoryPolicy()
    pipeline = MemoryPipeline(
        policy=policy,
        builtin=BuiltinMemoryStore(root=tmp_path, policy=policy),
        external=None,
        builtin_enabled=False,
        external_enabled=False,
    )

    result = await pipeline.process(
        _candidate("Remember this preference."),
        user_id="user",
        project_id="project",
    )

    assert result == {"builtin": False, "external": False}


@pytest.mark.asyncio
async def test_context_compression_preserves_source_lineage() -> None:
    event_id = uuid4()
    compressor = ContextCompressor(FakeModelProvider(["summary"]))

    summary = await compressor.compress(
        [ContextItem(event_id=event_id, content="Tests failed once.")]
    )

    assert summary.text == "summary"
    assert summary.source_event_ids == [event_id]
