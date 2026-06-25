from __future__ import annotations

import asyncio
import os
import socket
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from time import monotonic
from uuid import UUID, uuid4

from awesome_agent.domain.enums import ExecutionKind
from awesome_agent.domain.models import Run, RunLease
from awesome_agent.runtime.dispatch import (
    CorruptRuntimeStateError,
    IncompatibleGraphError,
    LeaseLost,
    RunDispatcher,
)
from awesome_agent.runtime.probe_graph import RuntimeProbeGraph, RuntimeProbeState
from awesome_agent.runtime.repository import RuntimeRepository


@dataclass(frozen=True, slots=True)
class WorkerConfig:
    lease_duration: timedelta
    heartbeat_interval: timedelta
    poll_interval: float
    recovery_interval: float
    shutdown_grace: float
    retry_delay: timedelta
    max_attempts: int


class DurableWorker:
    def __init__(
        self,
        *,
        dispatcher: RunDispatcher,
        repository: RuntimeRepository,
        probe_graph: RuntimeProbeGraph,
        config: WorkerConfig,
        worker_id: UUID | None = None,
        worker_name: str | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.dispatcher = dispatcher
        self.repository = repository
        self.probe_graph = probe_graph
        self.config = config
        self.worker_id = worker_id or uuid4()
        self.worker_name = worker_name or default_worker_name()
        self.sleep = sleep
        self.stop_requested = asyncio.Event()

    def request_stop(self) -> None:
        self.stop_requested.set()

    async def run_forever(self) -> None:
        next_recovery = 0.0
        while not self.stop_requested.is_set():
            if monotonic() >= next_recovery:
                await self.dispatcher.recover_expired(
                    max_attempts=self.config.max_attempts
                )
                next_recovery = monotonic() + self.config.recovery_interval
            processed = await self.run_once()
            if not processed and not self.stop_requested.is_set():
                await self.sleep(self.config.poll_interval)

    async def run_once(self) -> bool:
        lease = await self.dispatcher.claim_next(
            worker_id=self.worker_id,
            worker_name=self.worker_name,
            lease_duration=self.config.lease_duration,
            max_attempts=self.config.max_attempts,
            execution_kinds=frozenset({ExecutionKind.RUNTIME_PROBE}),
        )
        if lease is None:
            return False
        await self._execute_claim(lease)
        return True

    async def _execute_claim(self, lease: RunLease) -> None:
        run = await self.repository.get_run(lease.run_id)
        try:
            self._validate_probe_run(run)
            await self.dispatcher.start_execution(
                lease,
                graph_name=run.graph_name or "",
                graph_version=run.graph_version or 0,
            )
            state, recovered = await self._execute_with_heartbeat(run, lease)
            await self.dispatcher.complete_execution(
                lease,
                result_summary=state.get(
                    "result_summary",
                    "Durable runtime probe completed.",
                ),
                recovered=recovered,
            )
        except LeaseLost:
            return
        except (IncompatibleGraphError, CorruptRuntimeStateError) as error:
            await self._mark_recovery_if_owned(lease, str(error))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self._retry_if_owned(lease, error)

    async def _execute_with_heartbeat(
        self,
        run: Run,
        lease: RunLease,
    ) -> tuple[RuntimeProbeState, bool]:
        lease_lost = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._heartbeat_loop(lease, lease_lost),
            name=f"heartbeat:{lease.run_id}",
        )
        graph_task = asyncio.create_task(
            self.probe_graph.execute(run),
            name=f"graph:{lease.run_id}",
        )
        stop_task = asyncio.create_task(
            self.stop_requested.wait(),
            name=f"stop:{lease.run_id}",
        )
        lost_task = asyncio.create_task(
            lease_lost.wait(),
            name=f"lease-lost:{lease.run_id}",
        )
        try:
            done, _ = await asyncio.wait(
                {graph_task, stop_task, lost_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if graph_task in done:
                return await graph_task
            if lost_task in done and lease_lost.is_set():
                graph_task.cancel()
                await _consume_cancel(graph_task)
                raise LeaseLost(f"Lease lost for Run {lease.run_id}.")
            try:
                return await asyncio.wait_for(
                    asyncio.shield(graph_task),
                    timeout=self.config.shutdown_grace,
                )
            except TimeoutError as error:
                graph_task.cancel()
                await _consume_cancel(graph_task)
                raise LeaseLost(
                    f"Worker stopped before Run {lease.run_id} reached a safe boundary."
                ) from error
        finally:
            heartbeat.cancel()
            stop_task.cancel()
            lost_task.cancel()
            await asyncio.gather(
                heartbeat,
                stop_task,
                lost_task,
                return_exceptions=True,
            )

    async def _heartbeat_loop(
        self,
        lease: RunLease,
        lease_lost: asyncio.Event,
    ) -> None:
        safety_window = (
            self.config.lease_duration - self.config.heartbeat_interval
        ).total_seconds()
        last_confirmed = monotonic()
        while not lease_lost.is_set():
            await self.sleep(self.config.heartbeat_interval.total_seconds())
            try:
                lease = await self.dispatcher.heartbeat(
                    lease,
                    lease_duration=self.config.lease_duration,
                )
                last_confirmed = monotonic()
            except LeaseLost:
                lease_lost.set()
            except Exception:
                if monotonic() - last_confirmed >= safety_window:
                    lease_lost.set()
                else:
                    await self.sleep(1)

    async def _retry_if_owned(self, lease: RunLease, error: Exception) -> None:
        try:
            await self.dispatcher.release_for_retry(
                lease,
                delay=self.config.retry_delay,
                reason=type(error).__name__,
                error=str(error),
                max_attempts=self.config.max_attempts,
            )
        except LeaseLost:
            return

    async def _mark_recovery_if_owned(
        self,
        lease: RunLease,
        reason: str,
    ) -> None:
        try:
            await self.dispatcher.mark_recovery_required(lease, reason=reason)
        except LeaseLost:
            return

    @staticmethod
    def _validate_probe_run(run: Run) -> None:
        if run.execution_kind is not ExecutionKind.RUNTIME_PROBE:
            raise IncompatibleGraphError(
                f"Worker cannot execute kind {run.execution_kind.value}."
            )


async def _consume_cancel(task: asyncio.Task[object]) -> None:
    with suppress(asyncio.CancelledError):
        await task


def default_worker_name() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"
