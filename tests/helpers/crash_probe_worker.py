from __future__ import annotations

import asyncio
import os
from datetime import timedelta

from awesome_agent.persistence.checkpoints import checkpoint_saver
from awesome_agent.persistence.database import create_engine, create_session_factory
from awesome_agent.persistence.dispatch import PostgresRunDispatcher
from awesome_agent.persistence.runtime_repository import PostgresRuntimeRepository
from awesome_agent.runtime.asyncio import configure_event_loop_policy
from awesome_agent.runtime.probe_graph import RuntimeProbeGraph, RuntimeProbeState
from awesome_agent.runtime.worker import DurableWorker, WorkerConfig

configure_event_loop_policy()


async def main() -> None:
    engine = create_engine(os.environ["AWESOME_AGENT_TEST_DATABASE_URL"])
    sessions = create_session_factory(engine)

    async def crash(node: str, _: RuntimeProbeState) -> None:
        if node == "checkpoint_probe":
            os._exit(91)

    async with checkpoint_saver(
        os.environ["AWESOME_AGENT_TEST_CHECKPOINT_DATABASE_URL"]
    ) as saver:
        await saver.setup()
        worker = DurableWorker(
            dispatcher=PostgresRunDispatcher(sessions),
            repository=PostgresRuntimeRepository(sessions),
            probe_graph=RuntimeProbeGraph(saver, fault_hook=crash),
            config=WorkerConfig(
                lease_duration=timedelta(seconds=2),
                heartbeat_interval=timedelta(milliseconds=500),
                poll_interval=0.1,
                recovery_interval=1,
                shutdown_grace=1,
                retry_delay=timedelta(seconds=1),
                max_attempts=3,
            ),
        )
        await worker.run_once()


if __name__ == "__main__":
    asyncio.run(main())
