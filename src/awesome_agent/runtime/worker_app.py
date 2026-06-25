from __future__ import annotations

import signal
from collections.abc import Callable
from datetime import timedelta
from types import FrameType
from typing import Any

from awesome_agent.persistence.checkpoints import checkpoint_saver
from awesome_agent.persistence.database import create_engine, create_session_factory
from awesome_agent.persistence.dispatch import PostgresRunDispatcher
from awesome_agent.persistence.runtime_repository import PostgresRuntimeRepository
from awesome_agent.runtime.probe_graph import RuntimeProbeGraph
from awesome_agent.runtime.worker import DurableWorker, WorkerConfig
from awesome_agent.settings import Settings


async def run_worker(*, once: bool = False, settings: Settings | None = None) -> bool:
    configured = settings or Settings()
    engine = create_engine(configured.database_url)
    sessions = create_session_factory(engine)
    async with checkpoint_saver(configured.checkpoint_database_url) as saver:
        await saver.setup()
        worker = DurableWorker(
            dispatcher=PostgresRunDispatcher(sessions),
            repository=PostgresRuntimeRepository(sessions),
            probe_graph=RuntimeProbeGraph(saver),
            config=WorkerConfig(
                lease_duration=timedelta(seconds=configured.lease_duration_seconds),
                heartbeat_interval=timedelta(
                    seconds=configured.heartbeat_interval_seconds
                ),
                poll_interval=configured.worker_poll_interval_seconds,
                recovery_interval=configured.worker_recovery_interval_seconds,
                shutdown_grace=configured.worker_shutdown_grace_seconds,
                retry_delay=timedelta(seconds=configured.worker_retry_delay_seconds),
                max_attempts=configured.max_claim_attempts,
            ),
        )
        restore = _install_signal_handlers(worker)
        try:
            if once:
                await worker.dispatcher.recover_expired(
                    max_attempts=worker.config.max_attempts
                )
                return await worker.run_once()
            await worker.run_forever()
            return True
        finally:
            restore()
            await engine.dispose()


def _install_signal_handlers(worker: DurableWorker) -> Callable[[], None]:
    previous: dict[signal.Signals, Any] = {}

    def request_stop(_: int, __: FrameType | None) -> None:
        worker.request_stop()

    supported = [signal.SIGINT, signal.SIGTERM]
    for current in supported:
        previous[current] = signal.getsignal(current)
        signal.signal(current, request_stop)

    def restore() -> None:
        for current, handler in previous.items():
            signal.signal(current, handler)

    return restore
