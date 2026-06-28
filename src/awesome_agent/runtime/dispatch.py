from __future__ import annotations

from datetime import timedelta
from typing import Protocol
from uuid import UUID

from awesome_agent.domain.enums import EventType, ExecutionKind, RunIntent
from awesome_agent.domain.models import RunLease, RuntimeEvent


class LeaseLost(RuntimeError):
    pass


class DispatchConflict(RuntimeError):
    pass


class TransientExecutionError(RuntimeError):
    pass


class PermanentExecutionError(RuntimeError):
    pass


class IncompatibleGraphError(PermanentExecutionError):
    pass


class CorruptRuntimeStateError(PermanentExecutionError):
    pass


class ApprovalInterrupt(RuntimeError):
    def __init__(self, approval_id: UUID) -> None:
        self.approval_id = approval_id
        super().__init__(f"Run is waiting for approval {approval_id}.")


class RunCancelled(RuntimeError):
    pass


class ChildRunWait(PermanentExecutionError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class RunDispatcher(Protocol):
    async def claim_next(
        self,
        *,
        worker_id: UUID,
        worker_name: str,
        lease_duration: timedelta,
        max_attempts: int,
        execution_kinds: frozenset[ExecutionKind] | None = None,
        run_intents: frozenset[RunIntent] | None = None,
        runtime_routes: frozenset[str] | None = None,
    ) -> RunLease | None:
        """Claim the next eligible Run without waiting on competing claims."""
        ...

    async def heartbeat(
        self,
        lease: RunLease,
        *,
        lease_duration: timedelta,
    ) -> RunLease:
        """Extend a live lease or raise LeaseLost."""
        ...

    async def append_fenced_event(
        self,
        lease: RunLease,
        *,
        event_type: EventType,
        payload: dict[str, object],
        transition_id: str | None = None,
    ) -> RuntimeEvent:
        """Append an event only while the supplied lease is current."""
        ...

    async def release_to_queue(
        self,
        lease: RunLease,
        *,
        reason: str,
        max_attempts: int,
    ) -> None:
        """Release current ownership and make the Run immediately claimable."""
        ...

    async def request_cancellation(
        self,
        *,
        run_id: UUID,
        requested_by: str | None,
        reason: str | None,
    ) -> RuntimeEvent | None:
        """Durably request or complete cancellation for a Run."""
        ...

    async def is_cancel_requested(self, lease: RunLease) -> bool:
        """Return whether the leased Run has a pending cancellation request."""
        ...

    async def mark_cancelled(
        self,
        lease: RunLease,
        *,
        reason: str,
    ) -> None:
        """Commit a fenced active cancellation terminal projection."""
        ...

    async def release_for_approval_wait(
        self,
        lease: RunLease,
        *,
        approval_id: UUID,
        reason: str,
    ) -> None:
        """Release current ownership while the Run waits for approval."""
        ...

    async def release_for_child_wait(
        self,
        lease: RunLease,
        *,
        reason: str,
    ) -> None:
        """Release current ownership while a parent Run waits for child Runs."""
        ...

    async def requeue_after_approval(
        self,
        *,
        run_id: UUID,
        approval_id: UUID,
        reason: str,
    ) -> None:
        """Make a waiting Run claimable after an approval decision."""
        ...

    async def expire_pending_approvals(
        self,
        *,
        batch_size: int = 100,
    ) -> int:
        """Expire pending approvals and requeue their waiting Runs."""
        ...

    async def release_for_retry(
        self,
        lease: RunLease,
        *,
        delay: timedelta,
        reason: str,
        max_attempts: int,
        error: str | None = None,
    ) -> None:
        """Release current ownership and delay the next claim."""
        ...

    async def recover_expired(
        self,
        *,
        max_attempts: int,
        batch_size: int = 100,
    ) -> int:
        """Recover expired leases and return the number processed."""
        ...

    async def start_execution(
        self,
        lease: RunLease,
        *,
        runtime_route: str,
    ) -> None:
        """Move a claimed Run into fenced graph execution."""
        ...

    async def complete_execution(
        self,
        lease: RunLease,
        *,
        result_summary: str,
        recovered: bool = False,
        completion_kind: str = "runtime_probe",
        goal_executed: bool = False,
        result_text: str | None = None,
    ) -> None:
        """Commit successful terminal projection and release ownership."""
        ...

    async def fail_execution(
        self,
        lease: RunLease,
        *,
        reason: str,
    ) -> None:
        """Commit a normal permanent execution failure."""
        ...

    async def mark_recovery_required(
        self,
        lease: RunLease,
        *,
        reason: str,
    ) -> None:
        """Stop automatic execution for a permanently unsafe Run."""
        ...
