from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest

from awesome_agent.persistence.cwd_context import PostgresCwdContextSnapshotRepository
from awesome_agent.persistence.database import create_engine, create_session_factory
from awesome_agent.persistence.models import Base
from awesome_agent.runtime.cwd_context import CwdContextFileSnapshot, CwdContextSnapshot

pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    "AWESOME_AGENT_TEST_DATABASE_URL" not in os.environ,
    reason="Integration database is not configured.",
)
@pytest.mark.asyncio
async def test_postgres_repository_persists_latest_snapshot(tmp_path: Path) -> None:
    engine = create_engine(os.environ["AWESOME_AGENT_TEST_DATABASE_URL"])
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = create_session_factory(engine)
    repository = PostgresCwdContextSnapshotRepository(sessions)
    thread_id = uuid4()
    snapshot = CwdContextSnapshot(
        id="snap_pg",
        thread_id=thread_id,
        working_directory=str(tmp_path),
        status="created",
        files=[
            CwdContextFileSnapshot(
                filename="CLAUDE.md",
                path=str(tmp_path / "CLAUDE.md"),
                exists=True,
                size_bytes=18,
                mtime_ns=456,
                sha256="b" * 64,
                included=True,
            )
        ],
    )

    try:
        await repository.save(snapshot)
        loaded = await repository.latest_for_thread(thread_id, str(tmp_path))
    finally:
        await engine.dispose()

    assert loaded is not None
    assert loaded.id == "snap_pg"
    assert loaded.files[0].sha256 == "b" * 64
