from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from awesome_agent.domain.enums import ApprovalStatus


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

    async def expire_expired(
        self,
        now: datetime,
        *,
        batch_size: int | None = None,
    ) -> list[DurableApproval]:
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

    async def expire_expired(
        self,
        now: datetime,
        *,
        batch_size: int | None = None,
    ) -> list[DurableApproval]:
        if batch_size is not None and batch_size < 1:
            raise ValueError("Batch size must be positive.")
        expired: list[DurableApproval] = []
        candidates = sorted(
            self._records.values(),
            key=lambda approval: (
                approval.expires_at,
                approval.created_at,
                approval.id,
            ),
        )
        for approval in candidates:
            if batch_size is not None and len(expired) >= batch_size:
                break
            if approval.status is ApprovalStatus.PENDING and approval.expires_at <= now:
                updated = replace(
                    approval,
                    status=ApprovalStatus.EXPIRED,
                    updated_at=now,
                )
                self._records[approval.id] = updated
                expired.append(updated)
        return expired
