from __future__ import annotations

import builtins
import importlib
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from awesome_agent.domain.enums import ApprovalStatus
from awesome_agent.persistence.approval_contracts import (
    ApprovalExpired,
    DurableApproval,
)


def _local_approval_repository() -> type:
    from awesome_agent.persistence.local_approvals import LocalApprovalRepository

    return LocalApprovalRepository


def _approval(
    tmp_path: Path,
    *,
    approval_id: UUID | None = None,
    run_id: UUID | None = None,
    tool_call_id: str = "call_shell",
    status: ApprovalStatus = ApprovalStatus.PENDING,
    created_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> DurableApproval:
    resolved_id = approval_id or uuid4()
    return DurableApproval(
        id=resolved_id,
        run_id=run_id or uuid4(),
        agent_id=uuid4(),
        tool_invocation_id=resolved_id,
        tool_call_id=tool_call_id,
        tool_name="shell.execute",
        tool_version="1",
        canonical_arguments={"argv": ["git", "status"], "cwd": str(tmp_path)},
        arguments_hash="hash",
        workspace_path=str(tmp_path),
        workspace_fingerprint="fingerprint",
        capabilities=["shell:execute"],
        risk_level="medium",
        status=status,
        expires_at=expires_at or datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
        created_at=created_at or datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=created_at or datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_local_approvals_imports_without_sqlalchemy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def guarded_import(
        name: str,
        globals: dict[str, object] | None = None,
        locals: dict[str, object] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name.startswith("sqlalchemy"):
            raise AssertionError(f"unexpected SQLAlchemy import: {name}")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    sys.modules.pop("awesome_agent.persistence.local_approvals", None)

    importlib.import_module("awesome_agent.persistence.local_approvals")


@pytest.mark.asyncio
async def test_local_approval_repository_round_trips_and_lists_by_run(
    tmp_path: Path,
) -> None:
    LocalApprovalRepository = _local_approval_repository()
    repository = LocalApprovalRepository(tmp_path / "state.db")
    run_id = uuid4()
    same_created_at = datetime(2026, 1, 1, tzinfo=UTC)
    second = _approval(
        tmp_path,
        approval_id=UUID("00000000-0000-0000-0000-000000000002"),
        run_id=run_id,
        tool_call_id="call_second",
        created_at=same_created_at,
    )
    first = _approval(
        tmp_path,
        approval_id=UUID("00000000-0000-0000-0000-000000000001"),
        run_id=run_id,
        tool_call_id="call_first",
        created_at=same_created_at,
    )
    other_status = _approval(
        tmp_path,
        run_id=run_id,
        tool_call_id="call_approved",
        status=ApprovalStatus.APPROVED,
        created_at=same_created_at + timedelta(seconds=1),
    )
    unrelated = _approval(tmp_path)

    await repository.upsert(second)
    await repository.upsert(first)
    await repository.upsert(other_status)
    await repository.upsert(unrelated)

    assert await repository.get(first.id) == first
    assert [item.id for item in await repository.list_for_run(run_id)] == [
        first.id,
        second.id,
        other_status.id,
    ]
    assert [
        item.id
        for item in await repository.list_for_run(
            run_id, status=ApprovalStatus.APPROVED
        )
    ] == [other_status.id]
    repository.close()


@pytest.mark.asyncio
async def test_local_approval_repository_get_by_call(tmp_path: Path) -> None:
    LocalApprovalRepository = _local_approval_repository()
    repository = LocalApprovalRepository(tmp_path / "state.db")
    approval = await repository.upsert(_approval(tmp_path, tool_call_id="call_1"))

    found = await repository.get_by_call(approval.run_id, "call_1")

    assert found == approval
    assert await repository.get_by_call(approval.run_id, "missing") is None
    assert await repository.get_by_call(uuid4(), "call_1") is None
    repository.close()


@pytest.mark.asyncio
async def test_local_approval_repository_decide_is_idempotent(
    tmp_path: Path,
) -> None:
    LocalApprovalRepository = _local_approval_repository()
    repository = LocalApprovalRepository(tmp_path / "state.db")
    approval = await repository.upsert(_approval(tmp_path))
    now = datetime(2026, 1, 1, 0, 1, tzinfo=UTC)

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
    assert approved.decided_at == now
    assert approved.decided_by == "cli"
    assert approved.decision_reason == "approved by test"
    assert repeated == approved
    assert await repository.get(approval.id) == approved
    repository.close()


@pytest.mark.asyncio
async def test_local_approval_repository_decide_denies_pending_approval(
    tmp_path: Path,
) -> None:
    LocalApprovalRepository = _local_approval_repository()
    repository = LocalApprovalRepository(tmp_path / "state.db")
    approval = await repository.upsert(_approval(tmp_path))
    now = datetime(2026, 1, 1, 0, 1, tzinfo=UTC)

    denied = await repository.decide(
        approval.id,
        approved=False,
        decided_by="cli",
        reason="not safe",
        now=now,
    )

    assert denied.status is ApprovalStatus.DENIED
    assert denied.decided_at == now
    assert denied.decided_by == "cli"
    assert denied.decision_reason == "not safe"
    assert denied.updated_at == now
    repository.close()


@pytest.mark.asyncio
async def test_local_approval_repository_expired_decision_raises_and_persists(
    tmp_path: Path,
) -> None:
    LocalApprovalRepository = _local_approval_repository()
    repository = LocalApprovalRepository(tmp_path / "state.db")
    now = datetime(2026, 1, 1, 0, 10, tzinfo=UTC)
    approval = await repository.upsert(
        _approval(tmp_path, expires_at=now - timedelta(seconds=1))
    )

    with pytest.raises(ApprovalExpired) as exc_info:
        await repository.decide(
            approval.id,
            approved=True,
            decided_by="cli",
            reason=None,
            now=now,
        )

    assert exc_info.value.approval.status is ApprovalStatus.EXPIRED
    assert exc_info.value.approval.updated_at == now
    assert (await repository.get(approval.id)).status is ApprovalStatus.EXPIRED
    repository.close()


@pytest.mark.asyncio
async def test_local_approval_repository_expire_expired_returns_and_persists(
    tmp_path: Path,
) -> None:
    LocalApprovalRepository = _local_approval_repository()
    repository = LocalApprovalRepository(tmp_path / "state.db")
    now = datetime(2026, 1, 1, 0, 10, tzinfo=UTC)
    expired = await repository.upsert(
        _approval(tmp_path, expires_at=now - timedelta(seconds=1))
    )
    live = await repository.upsert(
        _approval(tmp_path, expires_at=now + timedelta(seconds=1))
    )
    already_decided = await repository.upsert(
        replace(
            _approval(tmp_path, expires_at=now - timedelta(seconds=1)),
            status=ApprovalStatus.APPROVED,
        )
    )

    expired_items = await repository.expire_expired(now)

    assert [item.id for item in expired_items] == [expired.id]
    assert expired_items[0].status is ApprovalStatus.EXPIRED
    assert expired_items[0].updated_at == now
    assert (await repository.get(expired.id)).status is ApprovalStatus.EXPIRED
    assert (await repository.get(live.id)).status is ApprovalStatus.PENDING
    assert (await repository.get(already_decided.id)).status is ApprovalStatus.APPROVED
    repository.close()


@pytest.mark.asyncio
async def test_local_approval_repository_persists_after_close_and_reopen(
    tmp_path: Path,
) -> None:
    LocalApprovalRepository = _local_approval_repository()
    database_path = tmp_path / "state.db"
    repository = LocalApprovalRepository(database_path)
    approval = await repository.upsert(_approval(tmp_path))
    decided = await repository.decide(
        approval.id,
        approved=False,
        decided_by="cli",
        reason="deny once",
        now=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
    )
    repository.close()

    reopened = LocalApprovalRepository(database_path)

    assert await reopened.get(approval.id) == decided
    reopened.close()
