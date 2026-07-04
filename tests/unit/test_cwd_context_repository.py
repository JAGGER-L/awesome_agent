from pathlib import Path
from uuid import uuid4

import pytest

from awesome_agent.persistence.local_cwd_context import LocalCwdContextSnapshotRepository
from awesome_agent.runtime.cwd_context import CwdContextFileSnapshot, CwdContextSnapshot


@pytest.mark.asyncio
async def test_local_repository_persists_latest_snapshot(tmp_path: Path) -> None:
    repository = LocalCwdContextSnapshotRepository(tmp_path / "state.db")
    thread_id = uuid4()
    snapshot = CwdContextSnapshot(
        id="snap_abc",
        thread_id=thread_id,
        working_directory=str(tmp_path),
        status="created",
        files=[
            CwdContextFileSnapshot(
                filename="AGENTS.md",
                path=str(tmp_path / "AGENTS.md"),
                exists=True,
                size_bytes=12,
                mtime_ns=123,
                sha256="a" * 64,
                included=True,
            )
        ],
    )

    await repository.save(snapshot)
    loaded = await repository.latest_for_thread(thread_id, str(tmp_path))

    assert loaded is not None
    assert loaded.id == "snap_abc"
    assert loaded.files[0].filename == "AGENTS.md"
    repository.close()
