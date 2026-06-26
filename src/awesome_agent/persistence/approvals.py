from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from awesome_agent.domain.enums import ApprovalStatus
from awesome_agent.persistence.models import ApprovalRecord as ApprovalTable


class ApprovalExpired(Exception):
    def __init__(self, approval: DurableApproval) -> None:
        self.approval = approval
        super().__init__(f"Approval {approval.id} has expired.")


@dataclass(frozen=True, slots=True)
class DurableApproval:
    run_id: UUID
    tool_invocation_id: UUID
    tool_call_id: str
    tool_name: str
    tool_version: str
    canonical_arguments: dict[str, object]
    arguments_hash: str
    workspace_path: str
    workspace_fingerprint: str
    capabilities: list[str]
    risk_level: str
    expires_at: datetime
    id: UUID = field(default_factory=uuid4)
    agent_id: UUID | None = None
    status: ApprovalStatus = ApprovalStatus.PENDING
    decided_at: datetime | None = None
    decided_by: str | None = None
    decision_reason: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class ApprovalRepository(Protocol):
    async def upsert(self, approval: DurableApproval) -> DurableApproval:
        """Create or update a durable approval."""
        ...

    async def get(self, approval_id: UUID) -> DurableApproval:
        """Load one approval."""
        ...

    async def get_by_call(
        self,
        run_id: UUID,
        tool_call_id: str,
    ) -> DurableApproval | None:
        """Load an approval by run and model tool-call id."""
        ...

    async def list_for_run(
        self,
        run_id: UUID,
        *,
        status: ApprovalStatus | None = None,
    ) -> list[DurableApproval]:
        """Load approvals for one run."""
        ...

    async def decide(
        self,
        approval_id: UUID,
        *,
        approved: bool,
        decided_by: str | None,
        reason: str | None,
        now: datetime,
    ) -> DurableApproval:
        """Compare-and-set a pending approval to approved or denied."""
        ...

    async def expire_expired(self, now: datetime) -> list[DurableApproval]:
        """Expire pending approvals whose deadline has passed."""
        ...


class InMemoryApprovalRepository:
    def __init__(self) -> None:
        self._records: dict[UUID, DurableApproval] = {}

    async def upsert(self, approval: DurableApproval) -> DurableApproval:
        self._records[approval.id] = approval
        return approval

    async def get(self, approval_id: UUID) -> DurableApproval:
        return self._records[approval_id]

    async def get_by_call(
        self,
        run_id: UUID,
        tool_call_id: str,
    ) -> DurableApproval | None:
        return next(
            (
                approval
                for approval in self._records.values()
                if approval.run_id == run_id and approval.tool_call_id == tool_call_id
            ),
            None,
        )

    async def list_for_run(
        self,
        run_id: UUID,
        *,
        status: ApprovalStatus | None = None,
    ) -> list[DurableApproval]:
        approvals = [
            approval
            for approval in self._records.values()
            if approval.run_id == run_id
            and (status is None or approval.status is status)
        ]
        return sorted(
            approvals, key=lambda approval: (approval.created_at, approval.id)
        )

    async def decide(
        self,
        approval_id: UUID,
        *,
        approved: bool,
        decided_by: str | None,
        reason: str | None,
        now: datetime,
    ) -> DurableApproval:
        approval = self._records[approval_id]
        if approval.status is not ApprovalStatus.PENDING:
            return approval
        if approval.expires_at <= now:
            expired = replace(
                approval,
                status=ApprovalStatus.EXPIRED,
                updated_at=now,
            )
            self._records[approval_id] = expired
            raise ApprovalExpired(expired)
        decided = replace(
            approval,
            status=(ApprovalStatus.APPROVED if approved else ApprovalStatus.DENIED),
            decided_at=now,
            decided_by=decided_by,
            decision_reason=reason,
            updated_at=now,
        )
        self._records[approval_id] = decided
        return decided

    async def expire_expired(self, now: datetime) -> list[DurableApproval]:
        expired: list[DurableApproval] = []
        for approval in list(self._records.values()):
            if approval.status is ApprovalStatus.PENDING and approval.expires_at <= now:
                updated = replace(
                    approval,
                    status=ApprovalStatus.EXPIRED,
                    updated_at=now,
                )
                self._records[approval.id] = updated
                expired.append(updated)
        return expired


class PostgresApprovalRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def upsert(self, approval: DurableApproval) -> DurableApproval:
        async with self._sessions.begin() as session:
            record = await session.get(ApprovalTable, approval.id)
            if record is None:
                existing = await session.scalar(
                    select(ApprovalTable).where(
                        ApprovalTable.run_id == approval.run_id,
                        ApprovalTable.tool_call_id == approval.tool_call_id,
                    )
                )
                if existing is None:
                    session.add(_to_record(approval))
                else:
                    _update_record(existing, approval)
            else:
                _update_record(record, approval)
        return approval

    async def get(self, approval_id: UUID) -> DurableApproval:
        async with self._sessions() as session:
            record = await session.get(ApprovalTable, approval_id)
        if record is None:
            raise KeyError(approval_id)
        return _from_record(record)

    async def get_by_call(
        self,
        run_id: UUID,
        tool_call_id: str,
    ) -> DurableApproval | None:
        async with self._sessions() as session:
            record = await session.scalar(
                select(ApprovalTable).where(
                    ApprovalTable.run_id == run_id,
                    ApprovalTable.tool_call_id == tool_call_id,
                )
            )
        return _from_record(record) if record is not None else None

    async def list_for_run(
        self,
        run_id: UUID,
        *,
        status: ApprovalStatus | None = None,
    ) -> list[DurableApproval]:
        async with self._sessions() as session:
            query = select(ApprovalTable).where(ApprovalTable.run_id == run_id)
            if status is not None:
                query = query.where(ApprovalTable.status == status.value)
            records = list(
                await session.scalars(
                    query.order_by(ApprovalTable.created_at, ApprovalTable.id)
                )
            )
        return [_from_record(record) for record in records]

    async def decide(
        self,
        approval_id: UUID,
        *,
        approved: bool,
        decided_by: str | None,
        reason: str | None,
        now: datetime,
    ) -> DurableApproval:
        async with self._sessions.begin() as session:
            record = await session.get(ApprovalTable, approval_id, with_for_update=True)
            if record is None:
                raise KeyError(approval_id)
            approval = _from_record(record)
            if approval.status is not ApprovalStatus.PENDING:
                return approval
            if approval.expires_at <= now:
                expired = replace(
                    approval,
                    status=ApprovalStatus.EXPIRED,
                    updated_at=now,
                )
                _update_record(record, expired)
                raise ApprovalExpired(expired)
            decided = replace(
                approval,
                status=(ApprovalStatus.APPROVED if approved else ApprovalStatus.DENIED),
                decided_at=now,
                decided_by=decided_by,
                decision_reason=reason,
                updated_at=now,
            )
            _update_record(record, decided)
            return decided

    async def expire_expired(self, now: datetime) -> list[DurableApproval]:
        expired: list[DurableApproval] = []
        async with self._sessions.begin() as session:
            records = list(
                await session.scalars(
                    select(ApprovalTable)
                    .where(
                        ApprovalTable.status == ApprovalStatus.PENDING.value,
                        ApprovalTable.expires_at <= now,
                    )
                    .with_for_update(skip_locked=True)
                )
            )
            for record in records:
                approval = replace(
                    _from_record(record),
                    status=ApprovalStatus.EXPIRED,
                    updated_at=now,
                )
                _update_record(record, approval)
                expired.append(approval)
        return expired


def _to_record(approval: DurableApproval) -> ApprovalTable:
    return ApprovalTable(
        id=approval.id,
        run_id=approval.run_id,
        agent_id=approval.agent_id,
        tool_invocation_id=approval.tool_invocation_id,
        tool_call_id=approval.tool_call_id,
        tool_name=approval.tool_name,
        tool_version=approval.tool_version,
        canonical_arguments=approval.canonical_arguments,
        arguments_hash=approval.arguments_hash,
        workspace_path=approval.workspace_path,
        workspace_fingerprint=approval.workspace_fingerprint,
        capabilities=approval.capabilities,
        risk_level=approval.risk_level,
        status=approval.status.value,
        expires_at=approval.expires_at,
        decided_at=approval.decided_at,
        decided_by=approval.decided_by,
        decision_reason=approval.decision_reason,
        created_at=approval.created_at,
        updated_at=approval.updated_at,
    )


def _update_record(record: ApprovalTable, approval: DurableApproval) -> None:
    record.agent_id = approval.agent_id
    record.tool_invocation_id = approval.tool_invocation_id
    record.tool_name = approval.tool_name
    record.tool_version = approval.tool_version
    record.canonical_arguments = approval.canonical_arguments
    record.arguments_hash = approval.arguments_hash
    record.workspace_path = approval.workspace_path
    record.workspace_fingerprint = approval.workspace_fingerprint
    record.capabilities = approval.capabilities
    record.risk_level = approval.risk_level
    record.status = approval.status.value
    record.expires_at = approval.expires_at
    record.decided_at = approval.decided_at
    record.decided_by = approval.decided_by
    record.decision_reason = approval.decision_reason
    record.updated_at = approval.updated_at


def _from_record(record: ApprovalTable) -> DurableApproval:
    return DurableApproval(
        id=record.id,
        run_id=record.run_id,
        agent_id=record.agent_id,
        tool_invocation_id=record.tool_invocation_id,
        tool_call_id=record.tool_call_id,
        tool_name=record.tool_name,
        tool_version=record.tool_version,
        canonical_arguments=dict(record.canonical_arguments),
        arguments_hash=record.arguments_hash,
        workspace_path=record.workspace_path,
        workspace_fingerprint=record.workspace_fingerprint,
        capabilities=[str(capability) for capability in record.capabilities],
        risk_level=record.risk_level,
        status=ApprovalStatus(record.status),
        expires_at=record.expires_at,
        decided_at=record.decided_at,
        decided_by=record.decided_by,
        decision_reason=record.decision_reason,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
