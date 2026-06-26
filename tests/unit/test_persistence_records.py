from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from awesome_agent.artifacts.store import ArtifactMetadata
from awesome_agent.persistence.artifacts import (
    _from_record as artifact_from_record,
)
from awesome_agent.persistence.artifacts import (
    _to_record as artifact_to_record,
)
from awesome_agent.persistence.tool_invocations import (
    DurableToolInvocation,
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
