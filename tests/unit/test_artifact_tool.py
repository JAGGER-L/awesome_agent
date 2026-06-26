from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from awesome_agent.artifacts.repository import InMemoryArtifactMetadataRepository
from awesome_agent.artifacts.store import ArtifactMetadata
from awesome_agent.modeling import ToolCall
from awesome_agent.tools.repository import (
    build_modifying_executor,
    build_modifying_registry,
    execute_repository_call,
    model_tool_definitions,
)


def test_artifact_read_is_registered_only_when_repository_is_provided() -> None:
    without_artifacts = build_modifying_registry()
    with_artifacts = build_modifying_registry(InMemoryArtifactMetadataRepository())

    assert "artifact.read" not in {
        definition.name for definition in model_tool_definitions(without_artifacts)
    }
    assert "artifact.read" in {
        definition.name for definition in model_tool_definitions(with_artifacts)
    }


@pytest.mark.asyncio
async def test_artifact_read_returns_bounded_content(tmp_path: Path) -> None:
    artifact_path = tmp_path / "artifact.txt"
    artifact_path.write_text("hello artifact", encoding="utf-8")
    repository = InMemoryArtifactMetadataRepository()
    metadata = ArtifactMetadata(
        run_id=uuid4(),
        artifact_type="tool-output",
        path=artifact_path,
        sha256="sha",
        size=artifact_path.stat().st_size,
        mime_type="text/plain",
        summary="summary",
    )
    await repository.record(metadata)
    registry = build_modifying_registry(repository)

    result = await execute_repository_call(
        build_modifying_executor(registry),
        ToolCall(
            call_id="artifact",
            name="artifact.read",
            arguments_json=(f'{{"artifact_id":"{metadata.id}","max_bytes":5}}'),
        ),
        workspace=tmp_path,
        agent_id=uuid4(),
        capabilities={"artifact:read"},
    )

    assert not result.is_error
    assert "hello" in result.content
    assert '"truncated": true' in result.content
