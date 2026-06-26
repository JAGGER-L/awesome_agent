from pathlib import Path
from uuid import uuid4

import pytest

from awesome_agent.agents.profiles import RoleModelResolver
from awesome_agent.artifacts.repository import InMemoryArtifactMetadataRepository
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.runtime.events import EventStream
from awesome_agent.runtime.repository import InMemoryRuntimeRepository
from awesome_agent.runtime.service import RuntimeService


def _models() -> RoleModelResolver:
    return RoleModelResolver(
        leader_model="deepseek-v4-pro",
        teammate_model="deepseek-v4-flash",
        verifier_model="deepseek-v4-flash",
        subagent_model="deepseek-v4-flash",
    )


def test_artifact_store_writes_hashes_and_deletes_run(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    run_id = uuid4()

    metadata = store.write(
        run_id=run_id,
        artifact_type="logs",
        filename="../test.log",
        content=b"hello",
        mime_type="text/plain",
    )

    assert metadata.path.name.endswith("-test.log")
    assert metadata.sha256 == (
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )
    assert metadata.path.exists()

    store.delete_run(run_id)
    assert not metadata.path.exists()


@pytest.mark.asyncio
async def test_runtime_service_persists_artifact_metadata(tmp_path: Path) -> None:
    metadata_repository = InMemoryArtifactMetadataRepository()
    service = RuntimeService(
        repository=InMemoryRuntimeRepository(),
        events=EventStream(),
        artifacts=LocalArtifactStore(tmp_path),
        model_resolver=_models(),
        artifact_repository=metadata_repository,
    )
    run = await service.create_run("Capture artifact")

    metadata = await service.write_artifact(
        run_id=run.id,
        artifact_type="tool-output",
        filename="output.txt",
        content=b"large output",
        mime_type="text/plain",
        summary="tool output",
    )

    listed = await service.list_artifacts(run.id)
    fetched = await service.get_artifact(metadata.id)

    assert listed == [metadata]
    assert fetched.path.exists()
    assert fetched.summary == "tool output"
