from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from awesome_agent.artifacts.store import ArtifactMetadata
from awesome_agent.persistence.approvals import (
    DurableApproval,
    InMemoryApprovalRepository,
    PostgresApprovalRepository,
)
from awesome_agent.persistence.approvals import _from_record as approval_from_record
from awesome_agent.persistence.approvals import (
    _to_record as approval_to_record,
)
from awesome_agent.persistence.artifacts import (
    _from_record as artifact_from_record,
)
from awesome_agent.persistence.artifacts import (
    _to_record as artifact_to_record,
)
from awesome_agent.persistence.tool_invocations import (
    DurableToolInvocation,
    InMemoryToolInvocationRepository,
    PostgresToolInvocationRepository,
    _update_record,
)
from awesome_agent.persistence.tool_invocations import (
    _from_record as invocation_from_record,
)
from awesome_agent.persistence.tool_invocations import (
    _to_record as invocation_to_record,
)


def test_artifact_record_round_trips_metadata(tmp_path: Path) -> None:
    metadata = ArtifactMetadata(
        run_id=uuid4(),
        agent_id=uuid4(),
        artifact_type="tool-output",
        path=tmp_path / "artifact.txt",
        sha256="abc",
        size=3,
        mime_type="text/plain",
        summary="summary",
    )

    restored = artifact_from_record(artifact_to_record(metadata))

    assert restored == metadata


def test_tool_invocation_record_round_trips_and_updates() -> None:
    now = datetime.now(UTC)
    invocation = DurableToolInvocation(
        id=uuid4(),
        run_id=uuid4(),
        agent_id=uuid4(),
        tool_name="repo.apply_patch",
        tool_version="1",
        status="started",
        idempotency_key="key",
        arguments_hash="hash",
        risk_level="medium",
        path_refs=["README.md"],
        preimage_hashes={"README.md": "old"},
        expected_postimage_hashes={"README.md": "new"},
        created_at=now,
        updated_at=now,
    )
    record = invocation_to_record(invocation)

    restored = invocation_from_record(record)

    assert restored == invocation

    completed = DurableToolInvocation(
        **{
            **asdict(invocation),
            "status": "completed",
            "result_summary": "done",
            "artifact_refs": ["artifact-id"],
            "completed_at": now,
        }
    )
    _update_record(record, completed)

    assert record.status == "completed"
    assert record.result_summary == "done"
    assert record.artifact_refs == ["artifact-id"]
    assert invocation_from_record(record) == completed


def test_tool_invocation_list_for_run_signature() -> None:
    """Verify list_for_run is a callable method on the repository."""
    repository = PostgresToolInvocationRepository.__new__(
        PostgresToolInvocationRepository
    )
    assert callable(getattr(repository, "list_for_run", None))


@pytest.mark.asyncio
async def test_inmemory_tool_invocation_get_by_idempotency_key() -> None:
    """Exercise InMemoryToolInvocationRepository.idempotency lookup."""
    repo = InMemoryToolInvocationRepository()
    run_id = uuid4()
    inv = await repo.upsert(
        DurableToolInvocation(
            id=uuid4(),
            run_id=run_id,
            agent_id=uuid4(),
            tool_name="repo.apply_patch",
            tool_version="1",
            status="started",
            idempotency_key="key-abc",
            arguments_hash="h1",
            risk_level="medium",
        )
    )
    # Hit the idempotency lookup branch (exact match)
    found = await repo.get_by_idempotency_key(run_id, "key-abc")
    assert found is not None
    assert found.id == inv.id

    # Miss branch
    missing = await repo.get_by_idempotency_key(run_id, "no-such-key")
    assert missing is None

    # List branch
    listed = await repo.list_for_run(run_id)
    assert len(listed) == 1
    assert listed[0].id == inv.id


def test_approval_domain_record_round_trip() -> None:
    """Verify the approvals table and DurableApproval domain fields agree."""
    approval = DurableApproval(
        run_id=uuid4(),
        tool_invocation_id=uuid4(),
        tool_call_id="call-1",
        tool_name="shell.execute",
        tool_version="1",
        canonical_arguments={"argv": ["python", "script.py"]},
        arguments_hash="a" * 64,
        workspace_path="E:/workspace",
        workspace_fingerprint="b" * 64,
        capabilities=["shell:execute"],
        risk_level="medium",
        expires_at=datetime.now(UTC) + timedelta(minutes=60),
    )
    record = approval_to_record(approval)
    restored = approval_from_record(record)
    assert restored == approval

    repo = InMemoryApprovalRepository()
    assert callable(getattr(repo, "get_by_call", None))
    assert callable(getattr(repo, "list_for_run", None))
    assert callable(getattr(repo, "decide", None))
    assert callable(getattr(repo, "expire_expired", None))

    assert PostgresApprovalRepository is not None
