from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from awesome_agent.domain.enums import (
    DispatchStatus,
    EventType,
    ExecutionKind,
    RunIntent,
    RunStatus,
)
from awesome_agent.domain.models import Run, RunLease, RuntimeEvent
from awesome_agent.persistence.local_runtime import LocalRuntimeRepository
from awesome_agent.runtime.dispatch import DispatchConflict, LeaseLost, RunDispatcher


class LocalRunDispatcher(RunDispatcher):
    def __init__(self, runtime: LocalRuntimeRepository) -> None:
        self.runtime = runtime

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
        now = datetime.now(UTC)
        for run in await self.runtime.list_runs():
            if run.dispatch_status not in {
                DispatchStatus.QUEUED,
                DispatchStatus.RETRY_SCHEDULED,
            }:
                continue
            if run.available_at > now:
                continue
            if run.attempt >= max_attempts:
                await self._mark_run_recovery_required(
                    run,
                    reason="Local run exceeded maximum claim attempts.",
                )
                continue
            if (
                execution_kinds is not None
                and run.execution_kind not in execution_kinds
            ):
                continue
            if run_intents is not None and run.intent not in run_intents:
                continue
            if runtime_routes is not None and run.runtime_route not in runtime_routes:
                continue

            lease = RunLease(
                run_id=run.id,
                worker_id=worker_id,
                worker_name=worker_name,
                fencing_token=run.fencing_token + 1,
                attempt=run.attempt + 1,
                lease_acquired_at=now,
                lease_expires_at=now + lease_duration,
                heartbeat_at=now,
            )
            claimed = run.model_copy(
                update={
                    "dispatch_status": DispatchStatus.CLAIMED,
                    "current_worker_id": lease.worker_id,
                    "current_worker_name": lease.worker_name,
                    "fencing_token": lease.fencing_token,
                    "attempt": lease.attempt,
                    "lease_acquired_at": lease.lease_acquired_at,
                    "lease_expires_at": lease.lease_expires_at,
                    "heartbeat_at": lease.heartbeat_at,
                    "last_release_reason": None,
                    "last_dispatch_error": None,
                }
            )
            await self.runtime.update_run(claimed)
            await self.runtime.append_event(
                run_id=run.id,
                event_type=EventType.DISPATCH_CLAIMED,
                payload={
                    "dispatch_status": claimed.dispatch_status.value,
                    "worker_id": str(worker_id),
                    "worker_name": worker_name,
                    "attempt": lease.attempt,
                    "fencing_token": lease.fencing_token,
                    "lease_expires_at": lease.lease_expires_at.isoformat(),
                },
            )
            return lease
        return None

    async def heartbeat(
        self,
        lease: RunLease,
        *,
        lease_duration: timedelta,
    ) -> RunLease:
        run = await self._owned_run(lease)
        now = datetime.now(UTC)
        updated_lease = lease.model_copy(
            update={
                "lease_expires_at": now + lease_duration,
                "heartbeat_at": now,
            }
        )
        await self.runtime.update_run(
            run.model_copy(
                update={
                    "lease_expires_at": updated_lease.lease_expires_at,
                    "heartbeat_at": updated_lease.heartbeat_at,
                }
            )
        )
        return updated_lease

    async def append_fenced_event(
        self,
        lease: RunLease,
        *,
        event_type: EventType,
        payload: dict[str, object],
        transition_id: str | None = None,
    ) -> RuntimeEvent:
        await self._owned_run(lease)
        event = await self.runtime.append_event(
            run_id=lease.run_id,
            event_type=event_type,
            payload=payload,
            transition_id=transition_id,
        )
        return event

    async def release_to_queue(
        self,
        lease: RunLease,
        *,
        reason: str,
        max_attempts: int,
    ) -> None:
        run = await self._owned_run(lease)
        if run.attempt >= max_attempts:
            await self.mark_recovery_required(lease, reason=reason)
            return
        updated = self._released_run(
            run,
            dispatch_status=DispatchStatus.QUEUED,
            reason=reason,
        )
        await self.runtime.update_run(updated)
        await self.runtime.append_event(
            run_id=lease.run_id,
            event_type=EventType.DISPATCH_RELEASED,
            payload={
                "dispatch_status": updated.dispatch_status.value,
                "reason": reason,
            },
        )

    async def request_cancellation(
        self,
        *,
        run_id: UUID,
        requested_by: str | None,
        reason: str | None,
    ) -> RuntimeEvent | None:
        run = await self.runtime.get_run(run_id)
        if run.status is RunStatus.CANCELLED:
            return None
        if run.dispatch_status is DispatchStatus.TERMINAL:
            raise DispatchConflict("Terminal Runs cannot be cancelled.")
        if run.status in {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.RECOVERY_REQUIRED,
        }:
            return None
        now = datetime.now(UTC)
        if run.dispatch_status in {
            DispatchStatus.CLAIMED,
            DispatchStatus.EXECUTING,
        }:
            updated = run.model_copy(
                update={
                    "cancel_requested_at": run.cancel_requested_at or now,
                    "cancel_requested_by": requested_by,
                    "cancel_reason": reason,
                }
            )
            await self.runtime.update_run(updated)
            return await self.runtime.append_event(
                run_id=run_id,
                event_type=EventType.CANCELLATION_REQUESTED,
                payload={
                    "requested_by": requested_by,
                    "reason": reason,
                    "dispatch_status": updated.dispatch_status.value,
                },
                transition_id=f"cancel-requested:{run_id}",
            )
        updated = run.model_copy(
            update={
                "cancel_requested_at": run.cancel_requested_at or now,
                "cancel_requested_by": requested_by,
                "cancel_reason": reason,
            }
        )
        await self.runtime.update_run(updated)
        _cancelled, event = await self.runtime.cancel_run(run_id)
        return event

    async def is_cancel_requested(self, lease: RunLease) -> bool:
        run = await self._owned_run(lease)
        return run.cancel_requested_at is not None

    async def mark_cancelled(self, lease: RunLease, *, reason: str) -> None:
        run = await self._owned_run(lease)
        updated = run.model_copy(
            update={
                "status": RunStatus.CANCELLED,
                "dispatch_status": DispatchStatus.TERMINAL,
                "cancel_reason": reason,
            }
        )
        await self.runtime.update_run(self._clear_lease(updated))
        await self.runtime.append_event(
            run_id=lease.run_id,
            event_type=EventType.RUN_STATUS_CHANGED,
            payload={
                "status": updated.status.value,
                "dispatch_status": updated.dispatch_status.value,
                "reason": reason,
            },
        )

    async def release_for_approval_wait(
        self,
        lease: RunLease,
        *,
        approval_id: UUID,
        reason: str,
    ) -> None:
        run = await self._owned_run(lease)
        updated = self._released_run(
            run.model_copy(update={"status": RunStatus.PAUSED}),
            dispatch_status=DispatchStatus.WAITING,
            reason=reason,
        )
        await self.runtime.update_run(updated)
        await self.runtime.append_event(
            run_id=lease.run_id,
            event_type=EventType.DISPATCH_RELEASED,
            payload={
                "dispatch_status": updated.dispatch_status.value,
                "approval_id": str(approval_id),
                "reason": reason,
            },
        )
        await self.runtime.append_event(
            run_id=lease.run_id,
            event_type=EventType.RUN_STATUS_CHANGED,
            payload={
                "status": updated.status.value,
                "dispatch_status": updated.dispatch_status.value,
                "approval_id": str(approval_id),
                "reason": reason,
            },
        )

    async def release_for_child_wait(self, lease: RunLease, *, reason: str) -> None:
        run = await self._owned_run(lease)
        updated = self._released_run(
            run.model_copy(update={"status": RunStatus.WAITING}),
            dispatch_status=DispatchStatus.WAITING,
            reason=reason,
        )
        await self.runtime.update_run(updated)
        await self.runtime.append_event(
            run_id=lease.run_id,
            event_type=EventType.DISPATCH_RELEASED,
            payload={
                "dispatch_status": updated.dispatch_status.value,
                "reason": reason,
            },
        )

    async def requeue_after_approval(
        self,
        *,
        run_id: UUID,
        approval_id: UUID,
        reason: str,
    ) -> None:
        await self.runtime.requeue_waiting_run(run_id, reason=reason)

    async def expire_pending_approvals(self, *, batch_size: int = 100) -> int:
        return 0

    async def release_for_retry(
        self,
        lease: RunLease,
        *,
        delay: timedelta,
        reason: str,
        max_attempts: int,
        error: str | None = None,
    ) -> None:
        run = await self._owned_run(lease)
        if run.attempt >= max_attempts:
            await self.mark_recovery_required(lease, reason=error or reason)
            return
        updated = self._released_run(
            run,
            dispatch_status=DispatchStatus.RETRY_SCHEDULED,
            reason=reason,
        ).model_copy(update={"available_at": datetime.now(UTC) + delay})
        await self.runtime.update_run(updated)
        await self.runtime.append_event(
            run_id=lease.run_id,
            event_type=EventType.DISPATCH_RETRY_SCHEDULED,
            payload={
                "dispatch_status": updated.dispatch_status.value,
                "reason": reason,
                "error": error,
            },
        )

    async def recover_expired(
        self,
        *,
        max_attempts: int,
        batch_size: int = 100,
    ) -> int:
        now = datetime.now(UTC)
        processed = 0
        for run in await self.runtime.list_runs():
            if processed >= batch_size:
                break
            if run.dispatch_status not in {
                DispatchStatus.CLAIMED,
                DispatchStatus.EXECUTING,
            }:
                continue
            if run.lease_expires_at is None or run.lease_expires_at > now:
                continue
            expired_worker = run.current_worker_id
            expired_token = run.fencing_token
            if run.attempt >= max_attempts:
                await self._mark_run_recovery_required(
                    run,
                    reason="maximum attempts exceeded",
                )
            else:
                updated = self._clear_lease(
                    run.model_copy(
                        update={
                            "dispatch_status": DispatchStatus.QUEUED,
                            "available_at": now,
                            "last_release_reason": "lease expired",
                        }
                    )
                )
                await self.runtime.update_run(updated)
                await self.runtime.append_event(
                    run_id=run.id,
                    event_type=EventType.DISPATCH_LEASE_EXPIRED,
                    payload={
                        "expired_worker_id": str(expired_worker),
                        "fencing_token": expired_token,
                        "attempt": run.attempt,
                    },
                )
            processed += 1
        return processed

    async def start_execution(self, lease: RunLease, *, runtime_route: str) -> None:
        run = await self._owned_run(lease)
        if run.runtime_route != runtime_route:
            raise ValueError("Run runtime route does not match the executor.")
        updated = run.model_copy(
            update={
                "status": RunStatus.RUNNING,
                "dispatch_status": DispatchStatus.EXECUTING,
                "runtime_route": runtime_route,
            }
        )
        await self.runtime.update_run(updated)
        await self.runtime.append_event(
            run_id=lease.run_id,
            event_type=EventType.RUN_STATUS_CHANGED,
            payload={
                "status": updated.status.value,
                "dispatch_status": updated.dispatch_status.value,
                "runtime_route": runtime_route,
            },
        )
        await self.runtime.append_event(
            run_id=lease.run_id,
            event_type=EventType.GRAPH_STARTED,
            payload={
                "runtime_route": runtime_route,
                "fencing_token": lease.fencing_token,
            },
        )

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
        run = await self._owned_run(lease)
        updated = run.model_copy(
            update={
                "status": RunStatus.COMPLETED,
                "dispatch_status": DispatchStatus.TERMINAL,
                "result_text": result_text
                if result_text is not None
                else run.result_text,
                "last_release_reason": result_summary,
            }
        )
        await self.runtime.update_run(self._clear_lease(updated))
        await self.runtime.append_event(
            run_id=lease.run_id,
            event_type=EventType.RUN_STATUS_CHANGED,
            payload={
                "status": updated.status.value,
                "dispatch_status": updated.dispatch_status.value,
                "summary": result_summary,
                "completion_kind": completion_kind,
                "goal_executed": goal_executed,
                "recovered": recovered,
            },
        )
        await self.runtime.append_event(
            run_id=lease.run_id,
            event_type=EventType.GRAPH_RECOVERED
            if recovered
            else EventType.GRAPH_COMPLETED,
            payload={
                "result_summary": result_summary,
                "completion_kind": completion_kind,
                "goal_executed": goal_executed,
                "validation_complete": completion_kind != "modifying_unvalidated",
            },
        )

    async def fail_execution(self, lease: RunLease, *, reason: str) -> None:
        run = await self._owned_run(lease)
        updated = run.model_copy(
            update={
                "status": RunStatus.FAILED,
                "dispatch_status": DispatchStatus.TERMINAL,
                "last_dispatch_error": reason,
            }
        )
        await self.runtime.update_run(self._clear_lease(updated))
        await self.runtime.append_event(
            run_id=lease.run_id,
            event_type=EventType.RUN_STATUS_CHANGED,
            payload={
                "status": updated.status.value,
                "dispatch_status": updated.dispatch_status.value,
                "error": reason,
            },
        )

    async def mark_recovery_required(self, lease: RunLease, *, reason: str) -> None:
        run = await self._owned_run(lease)
        await self._mark_run_recovery_required(run, reason=reason)

    async def _owned_run(self, lease: RunLease) -> Run:
        run = await self.runtime.get_run(lease.run_id)
        if run.dispatch_status not in {
            DispatchStatus.CLAIMED,
            DispatchStatus.EXECUTING,
        }:
            raise LeaseLost(f"Lease lost for Run {lease.run_id}.")
        if run.current_worker_id != lease.worker_id:
            raise LeaseLost(f"Lease lost for Run {lease.run_id}.")
        if run.fencing_token != lease.fencing_token:
            raise LeaseLost(f"Lease lost for Run {lease.run_id}.")
        if run.lease_expires_at is None:
            raise LeaseLost(f"Lease lost for Run {lease.run_id}.")
        if run.lease_expires_at <= datetime.now(UTC):
            raise LeaseLost(f"Lease expired for Run {lease.run_id}.")
        return run

    async def _mark_run_recovery_required(self, run: Run, *, reason: str) -> None:
        updated = self._clear_lease(
            run.model_copy(
                update={
                    "status": RunStatus.RECOVERY_REQUIRED,
                    "dispatch_status": DispatchStatus.TERMINAL,
                    "last_dispatch_error": reason,
                }
            )
        )
        await self.runtime.update_run(updated)
        await self.runtime.append_event(
            run_id=run.id,
            event_type=EventType.RUN_STATUS_CHANGED,
            payload={
                "status": updated.status.value,
                "dispatch_status": updated.dispatch_status.value,
                "error": reason,
            },
        )
        await self.runtime.append_event(
            run_id=run.id,
            event_type=EventType.DISPATCH_RECOVERY_REQUIRED,
            payload={
                "status": updated.status.value,
                "dispatch_status": updated.dispatch_status.value,
                "reason": reason,
            },
        )

    @staticmethod
    def _released_run(
        run: Run,
        *,
        dispatch_status: DispatchStatus,
        reason: str,
    ) -> Run:
        return LocalRunDispatcher._clear_lease(
            run.model_copy(
                update={
                    "dispatch_status": dispatch_status,
                    "last_release_reason": reason,
                }
            )
        )

    @staticmethod
    def _clear_lease(run: Run) -> Run:
        return run.model_copy(
            update={
                "current_worker_id": None,
                "current_worker_name": None,
                "lease_acquired_at": None,
                "lease_expires_at": None,
                "heartbeat_at": None,
            }
        )
