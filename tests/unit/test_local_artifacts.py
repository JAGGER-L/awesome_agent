from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from awesome_agent.artifacts.store import ArtifactMetadata
from awesome_agent.persistence.local_artifacts import LocalArtifactMetadataRepository


@pytest.mark.asyncio
async def test_local_artifact_repository_persists_metadata_after_reopen(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "nested" / "state.db"
    metadata = _metadata(tmp_path, summary="Answer")

    repository = LocalArtifactMetadataRepository(database_path)
    recorded = await repository.record(metadata)
    repository.close()

    reopened = LocalArtifactMetadataRepository(database_path)
    try:
        assert recorded == metadata
        assert await reopened.get(metadata.id) == metadata
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_local_artifact_repository_lists_only_matching_run_in_stable_order(
    tmp_path: Path,
) -> None:
    repository = LocalArtifactMetadataRepository(tmp_path / "state.db")
    run_id = uuid4()
    other_run_id = uuid4()
    second = _metadata(
        tmp_path,
        run_id=run_id,
        path=tmp_path / "b.txt",
        created_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC),
    )
    other = _metadata(tmp_path, run_id=other_run_id)
    first = _metadata(
        tmp_path,
        run_id=run_id,
        path=tmp_path / "a.txt",
        created_at=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
    )

    try:
        await repository.record(second)
        await repository.record(other)
        await repository.record(first)

        assert await repository.list_for_run(run_id) == [first, second]
    finally:
        repository.close()


@pytest.mark.asyncio
async def test_local_artifact_repository_upserts_existing_metadata(
    tmp_path: Path,
) -> None:
    repository = LocalArtifactMetadataRepository(tmp_path / "state.db")
    metadata = _metadata(tmp_path, summary="before")
    updated = metadata.model_copy(update={"summary": "after"})

    try:
        await repository.record(metadata)
        recorded = await repository.record(updated)

        assert recorded == updated
        assert await repository.get(metadata.id) == updated
        assert await repository.list_for_run(metadata.run_id) == [updated]
    finally:
        repository.close()


@pytest.mark.asyncio
async def test_local_artifact_repository_get_missing_id_raises_key_error(
    tmp_path: Path,
) -> None:
    repository = LocalArtifactMetadataRepository(tmp_path / "state.db")
    artifact_id = uuid4()

    try:
        with pytest.raises(KeyError) as exc_info:
            await repository.get(artifact_id)
    finally:
        repository.close()

    assert exc_info.value.args == (artifact_id,)


def test_local_artifact_repository_rejects_unknown_schema_version(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state.db"
    connection = sqlite3.connect(database_path)
    with connection:
        connection.execute(
            """
            CREATE TABLE artifact_metadata (
              key TEXT PRIMARY KEY,
              value TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO artifact_metadata (key, value) VALUES ('schema_version', ?)",
            ("999",),
        )
    connection.close()

    with pytest.raises(RuntimeError, match="Local artifact database"):
        LocalArtifactMetadataRepository(database_path)


def _metadata(
    tmp_path: Path,
    *,
    run_id: UUID | None = None,
    path: Path | None = None,
    summary: str = "",
    created_at: datetime | None = None,
) -> ArtifactMetadata:
    created_at = created_at or datetime(2026, 1, 1, tzinfo=UTC)
    return ArtifactMetadata(
        run_id=run_id or uuid4(),
        artifact_type="text",
        path=path or tmp_path / f"{uuid4()}.txt",
        sha256="abc",
        size=12,
        mime_type="text/plain",
        summary=summary,
        created_at=created_at + timedelta(microseconds=0),
    )
