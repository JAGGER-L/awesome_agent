from __future__ import annotations

from uuid import UUID

from awesome_agent.domain.enums import DispatchStatus, RunStatus
from awesome_agent.runtime.repository import RuntimeRepository
from awesome_agent.runtime.worker import DurableWorker

_STOP_RUN_STATUSES = {
    RunStatus.COMPLETED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
    RunStatus.RECOVERY_REQUIRED,
    RunStatus.WAITING,
    RunStatus.PAUSED,
}

_STOP_DISPATCH_STATUSES = {
    DispatchStatus.TERMINAL,
    DispatchStatus.WAITING,
}


class LocalWorkerPump:
    def __init__(
        self,
        *,
        worker: DurableWorker,
        runtime: RuntimeRepository,
        max_iterations: int = 100,
    ) -> None:
        self.worker = worker
        self.runtime = runtime
        self.max_iterations = max_iterations

    async def drain_until_idle(self) -> int:
        for processed in range(self.max_iterations):
            if not await self.worker.run_once():
                return processed
        raise RuntimeError("Local worker pump exceeded max_iterations while draining.")

    async def drain_until_run_terminal_or_waiting(self, run_id: str) -> int:
        target = UUID(run_id)
        for processed in range(self.max_iterations):
            run = await self.runtime.get_run(target)
            if (
                run.status in _STOP_RUN_STATUSES
                or run.dispatch_status in _STOP_DISPATCH_STATUSES
            ):
                return processed
            if not await self.worker.run_once():
                return processed
        raise RuntimeError(
            "Local worker pump did not reach a terminal or waiting state "
            f"for Run {run_id}."
        )
