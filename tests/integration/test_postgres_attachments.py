from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from awesome_agent.attachments.models import (
    AttachmentMediaType,
    AttachmentScope,
    AttachmentSource,
    AttachmentStatus,
    ThreadAttachment,
)
from awesome_agent.persistence.attachments import PostgresAttachmentRepository
from awesome_agent.persistence.models import ThreadRecord


def _database_url() -> str | None:
    return os.environ.get("AWESOME_AGENT_TEST_DATABASE_URL")


@pytest.mark.asyncio
async def test_postgres_attachment_repository_round_trip() -> None:
    url = _database_url()
    if not url:
        pytest.skip("AWESOME_AGENT_TEST_DATABASE_URL is not configured.")
    engine = create_async_engine(url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    repository = PostgresAttachmentRepository(sessions)
    thread_id = uuid4()
    now = datetime.now(UTC)
    attachment = ThreadAttachment(
        thread_id=thread_id,
        scope=AttachmentScope.NEXT_TURN,
        status=AttachmentStatus.PENDING,
        filename="spec.md",
        mime_type="text/markdown",
        media_type=AttachmentMediaType.TEXT,
        size=12,
        sha256="a" * 64,
        storage_path=Path("attachments/thread/att/content"),
        source=AttachmentSource.API,
    )

    try:
        async with sessions.begin() as session:
            session.add(
                ThreadRecord(
                    id=thread_id,
                    title="Attachment test",
                    context_kind="workspace",
                    context_path=None,
                    repository_id=None,
                    default_model=None,
                    sandbox_profile=None,
                    created_at=now,
                    updated_at=now,
                )
            )
        created = await repository.create(attachment)
        listed = await repository.list_for_thread(thread_id)
    finally:
        await engine.dispose()

    assert listed[0].id == created.id
