from pathlib import Path

import pytest

from awesome_agent.context import (
    ContextBuilder,
    ContextRequest,
    ContextSource,
    ContextSourceKind,
)
from awesome_agent.context.builder import local_memory_context_sources
from awesome_agent.context.tokens import calculate_context_budget
from awesome_agent.memory.local_file import LocalMemoryFile
from awesome_agent.memory.models import MemoryScope


def _source(kind: ContextSourceKind, content: str) -> ContextSource:
    return ContextSource(
        kind=kind,
        source_id=kind.value,
        content=content,
    )


@pytest.mark.asyncio
async def test_long_term_memory_uses_fixed_4k_8k_4k_and_16k_caps() -> None:
    prepared = await ContextBuilder().prepare(
        ContextRequest(
            sources=(
                _source(ContextSourceKind.USER_MEMORY, "user\n" * 20_000),
                _source(ContextSourceKind.WORKSPACE_MEMORY, "workspace\n" * 20_000),
                _source(ContextSourceKind.MEM0, "mem0\n" * 20_000),
            ),
            configured_total_tokens=262_144,
            model_context_limit=262_144,
        )
    )
    estimates = {item.kind: item.estimated_tokens for item in prepared.manifest}

    assert estimates[ContextSourceKind.USER_MEMORY] <= 4_096
    assert estimates[ContextSourceKind.WORKSPACE_MEMORY] <= 8_192
    assert estimates[ContextSourceKind.MEM0] <= 4_096
    assert sum(estimates.values()) <= 16_384
    assert all(item.truncated for item in prepared.manifest)


@pytest.mark.asyncio
async def test_small_context_uses_25_50_25_split_and_no_capacity_transfer() -> None:
    configured = 100_000
    effective = calculate_context_budget(configured, configured).effective_input_limit
    total = min(16_384, int(effective * 0.10))
    expected_user = int(total * 0.25)
    expected_workspace = int(total * 0.50)
    expected_mem0 = int(total * 0.25)
    prepared = await ContextBuilder().prepare(
        ContextRequest(
            sources=(
                _source(ContextSourceKind.USER_MEMORY, "tiny"),
                _source(ContextSourceKind.WORKSPACE_MEMORY, "workspace\n" * 20_000),
                _source(ContextSourceKind.MEM0, "mem0\n" * 20_000),
            ),
            configured_total_tokens=configured,
            model_context_limit=configured,
        )
    )
    estimates = {item.kind: item.estimated_tokens for item in prepared.manifest}

    assert estimates[ContextSourceKind.USER_MEMORY] < expected_user
    assert estimates[ContextSourceKind.WORKSPACE_MEMORY] <= expected_workspace
    assert estimates[ContextSourceKind.MEM0] <= expected_mem0
    assert sum(estimates.values()) <= total


def test_local_sources_label_untrusted_hash_and_deduplicate_managed_entries(
    tmp_path: Path,
) -> None:
    user_path = tmp_path / "USER.md"
    workspace_path = tmp_path / "MEMORY.md"
    duplicate = "Same stable preference"
    user_file = LocalMemoryFile(path=user_path, scope=MemoryScope.USER)
    workspace_file = LocalMemoryFile(
        path=workspace_path,
        scope=MemoryScope.WORKSPACE,
    )
    user = user_file.snapshot()
    user_result = user_file.add(duplicate, expected_hash=user.content_hash)
    assert user_result.document is not None
    workspace = workspace_file.snapshot()
    workspace_result = workspace_file.add(
        duplicate,
        expected_hash=workspace.content_hash,
    )
    assert workspace_result.document is not None

    sources = local_memory_context_sources(
        user=user_result.document,
        workspace=workspace_result.document,
    )
    rendered = "\n".join(source.content for source in sources)

    assert len(sources) == 2
    assert sources[0].source_id == f"local:user:{user_result.document.content_hash}"
    assert sources[1].source_id == (
        f"local:workspace:{workspace_result.document.content_hash}"
    )
    assert rendered.count(duplicate) == 1
    assert rendered.count("UNTRUSTED reference context") == 2
    assert all(str(tmp_path) not in source.source_id for source in sources)
