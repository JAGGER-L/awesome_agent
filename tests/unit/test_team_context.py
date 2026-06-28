from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from awesome_agent.artifacts.repository import InMemoryArtifactMetadataRepository
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.persistence.budget import InMemoryBudgetRepository
from awesome_agent.runtime.team_context import compact_team_payload


@pytest.mark.asyncio
async def test_compact_team_payload_offloads_large_payload_and_records_compaction(
    tmp_path: Path,
) -> None:
    artifacts = InMemoryArtifactMetadataRepository()
    budgets = InMemoryBudgetRepository()
    run_id = uuid4()
    agent_id = uuid4()

    result = await compact_team_payload(
        run_id=run_id,
        agent_id=agent_id,
        graph_name="team-role",
        payload_kind="child-result",
        payload={"summary": "important evidence " * 200},
        artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
        artifact_repository=artifacts,
        budget_repository=budgets,
        max_inline_tokens=20,
    )

    assert result.compacted
    assert result.artifact_refs
    assert result.inline_payload["compacted"] is True
    metadata = await artifacts.get(result.artifact_refs[0])
    assert metadata.artifact_type == "team-context"
    assert metadata.path.read_text(encoding="utf-8")
    compactions = await budgets.list_compactions(run_id)
    assert compactions[0].agent_id == agent_id
    assert compactions[0].artifact_refs == result.artifact_refs


@pytest.mark.asyncio
async def test_compact_team_payload_keeps_small_payload_inline(
    tmp_path: Path,
) -> None:
    result = await compact_team_payload(
        run_id=uuid4(),
        agent_id=None,
        graph_name="team-role",
        payload_kind="handoff",
        payload={"summary": "short"},
        artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
        artifact_repository=InMemoryArtifactMetadataRepository(),
        budget_repository=InMemoryBudgetRepository(),
        max_inline_tokens=200,
    )

    assert not result.compacted
    assert result.inline_payload == {"summary": "short"}
    assert result.artifact_refs == []
