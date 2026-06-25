from __future__ import annotations

from datetime import timedelta
from typing import Protocol
from uuid import UUID

from awesome_agent.domain.enums import EventType, ExecutionKind
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


class RunDispatcher(Protocol):
    async def claim_next(
        self,
        *,
        worker_id: UUID,
        worker_name: str,
        lease_duration: timedelta,
        max_attempts: int,
        execution_kinds: frozenset[ExecutionKind] | None = None,
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
        graph_name: str,
        graph_version: int,
    ) -> None:
        """Move a claimed Run into fenced graph execution."""
        ...

    async def complete_execution(
        self,
        lease: RunLease,
        *,
        result_summary: str,
        recovered: bool = False,
    ) -> None:
        """Commit successful terminal projection and release ownership."""
        ...

    async def mark_recovery_required(
        self,
        lease: RunLease,
        *,
        reason: str,
    ) -> None:
        """Stop automatic execution for a permanently unsafe Run."""
        ...
