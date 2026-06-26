from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from awesome_agent.domain.enums import ApprovalStatus
from awesome_agent.persistence.approvals import (
    ApprovalExpired,
    DurableApproval,
    InMemoryApprovalRepository,
    _from_record,
    _to_record,
)


def _approval(*, expires_at: datetime | None = None) -> DurableApproval:
    return DurableApproval(
        run_id=uuid4(),
        agent_id=uuid4(),
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
        expires_at=expires_at or datetime.now(UTC) + timedelta(minutes=60),
    )


def test_approval_record_round_trips() -> None:
    approval = _approval()

    restored = _from_record(_to_record(approval))

    assert restored == approval


@pytest.mark.asyncio
async def test_approval_repository_decide_is_cas_and_idempotent() -> None:
    repository = InMemoryApprovalRepository()
    approval = await repository.upsert(_approval())
    now = datetime.now(UTC)

    approved = await repository.decide(
        approval.id,
        approved=True,
        decided_by="cli",
        reason="approved by test",
        now=now,
    )
    repeated = await repository.decide(
        approval.id,
        approved=False,
        decided_by="cli",
        reason="ignored",
        now=now + timedelta(seconds=1),
    )

    assert approved.status is ApprovalStatus.APPROVED
    assert repeated == approved


@pytest.mark.asyncio
async def test_approval_repository_expires_pending_approvals() -> None:
    repository = InMemoryApprovalRepository()
    now = datetime.now(UTC)
    expired = await repository.upsert(_approval(expires_at=now - timedelta(seconds=1)))
    live = await repository.upsert(_approval(expires_at=now + timedelta(minutes=1)))

    expired_items = await repository.expire_expired(now)

    assert [item.id for item in expired_items] == [expired.id]
    assert (await repository.get(expired.id)).status is ApprovalStatus.EXPIRED
    assert (await repository.get(live.id)).status is ApprovalStatus.PENDING


@pytest.mark.asyncio
async def test_approval_repository_rejects_decision_after_expiry() -> None:
    repository = InMemoryApprovalRepository()
    now = datetime.now(UTC)
    approval = await repository.upsert(_approval(expires_at=now - timedelta(seconds=1)))

    with pytest.raises(ApprovalExpired):
        await repository.decide(
            approval.id,
            approved=True,
            decided_by="cli",
            reason=None,
            now=now,
        )

    assert (await repository.get(approval.id)).status is ApprovalStatus.EXPIRED


@pytest.mark.asyncio
async def test_approval_repository_get_by_call() -> None:
    repository = InMemoryApprovalRepository()
    approval = await repository.upsert(_approval())

    found = await repository.get_by_call(approval.run_id, approval.tool_call_id)

    assert found is not None
    assert found.id == approval.id

    missing = await repository.get_by_call(uuid4(), "no-such-call")
    assert missing is None


@pytest.mark.asyncio
async def test_approval_repository_list_for_run_with_status_filter() -> None:
    repository = InMemoryApprovalRepository()
    run_id = uuid4()
    approved = await repository.upsert(
        DurableApproval(
            run_id=run_id,
            tool_invocation_id=uuid4(),
            tool_call_id="call-approved",
            tool_name="shell.execute",
            tool_version="1",
            canonical_arguments={"argv": ["pytest"]},
            arguments_hash="a" * 64,
            workspace_path="E:/workspace",
            workspace_fingerprint="b" * 64,
            capabilities=["shell:execute"],
            risk_level="low",
            expires_at=datetime.now(UTC) + timedelta(minutes=60),
            status=ApprovalStatus.APPROVED,
        )
    )
    pending = await repository.upsert(
        _approval(expires_at=datetime.now(UTC) + timedelta(minutes=30))
    )

    all_for_approved = await repository.list_for_run(
        run_id, status=ApprovalStatus.APPROVED
    )
    assert [item.id for item in all_for_approved] == [approved.id]

    all_for_run = await repository.list_for_run(pending.run_id)
    assert pending.id in [item.id for item in all_for_run]

    empty = await repository.list_for_run(uuid4())
    assert empty == []
