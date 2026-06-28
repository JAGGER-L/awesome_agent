from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from awesome_agent.health import CheckSeverity, HealthCheck, HealthStatus
from awesome_agent.settings import Settings


@dataclass(frozen=True, slots=True)
class RuntimeRoute:
    route: str

    def label(self) -> str:
        return self.route


class WorkerHeartbeatStatus(StrEnum):
    ONLINE = "online"
    STOPPING = "stopping"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class WorkerHeartbeat:
    worker_id: UUID
    worker_name: str
    started_at: datetime
    heartbeat_at: datetime
    supported_runtime_routes: list[RuntimeRoute]
    status: WorkerHeartbeatStatus


class WorkerHeartbeatRepository(Protocol):
    async def upsert(self, heartbeat: WorkerHeartbeat) -> None:
        pass

    async def list_recent(self, *, stale_after: datetime) -> list[WorkerHeartbeat]:
        pass

    async def mark_stopping(self, worker_id: UUID) -> None:
        pass


class InMemoryWorkerHeartbeatRepository:
    def __init__(self) -> None:
        self._records: dict[UUID, WorkerHeartbeat] = {}

    async def upsert(self, heartbeat: WorkerHeartbeat) -> None:
        self._records[heartbeat.worker_id] = heartbeat

    async def list_recent(self, *, stale_after: datetime) -> list[WorkerHeartbeat]:
        return [
            heartbeat
            for heartbeat in self._records.values()
            if heartbeat.heartbeat_at >= stale_after
        ]

    async def mark_stopping(self, worker_id: UUID) -> None:
        heartbeat = self._records.get(worker_id)
        if heartbeat is None:
            return
        self._records[worker_id] = WorkerHeartbeat(
            worker_id=heartbeat.worker_id,
            worker_name=heartbeat.worker_name,
            started_at=heartbeat.started_at,
            heartbeat_at=datetime.now(UTC),
            supported_runtime_routes=heartbeat.supported_runtime_routes,
            status=WorkerHeartbeatStatus.STOPPING,
        )


async def worker_heartbeat_check(
    repository: WorkerHeartbeatRepository,
    settings: Settings,
    *,
    required_runtime_routes: list[RuntimeRoute],
    now: datetime | None = None,
) -> HealthCheck:
    current_time = now or datetime.now(UTC)
    stale_after = current_time - timedelta(
        seconds=settings.worker_heartbeat_stale_seconds
    )
    try:
        recent_workers = await repository.list_recent(stale_after=stale_after)
    except Exception as error:
        return HealthCheck(
            "worker_heartbeat",
            HealthStatus.UNHEALTHY,
            f"worker heartbeat check failed: {error}",
            remediation="Verify PostgreSQL connectivity for worker heartbeats.",
        )

    online_workers = [
        worker
        for worker in recent_workers
        if worker.status is WorkerHeartbeatStatus.ONLINE
    ]
    supported = {
        route for worker in online_workers for route in worker.supported_runtime_routes
    }
    missing = [route for route in required_runtime_routes if route not in supported]
    metadata = {
        "workers": len(online_workers),
        "required_runtime_routes": [route.label() for route in required_runtime_routes],
        "supported_runtime_routes": sorted(route.label() for route in supported),
        "stale_after": stale_after.isoformat(),
    }
    if missing:
        return HealthCheck(
            "worker_heartbeat",
            HealthStatus.UNHEALTHY,
            "no fresh online worker heartbeat supports "
            f"{', '.join(route.label() for route in missing)}",
            severity=CheckSeverity.REQUIRED,
            remediation="Start an awesome-agent Worker for the runtime profile.",
            metadata=metadata,
        )
    return HealthCheck(
        "worker_heartbeat",
        HealthStatus.HEALTHY,
        f"{len(online_workers)} fresh online worker(s)",
        metadata=metadata,
    )
