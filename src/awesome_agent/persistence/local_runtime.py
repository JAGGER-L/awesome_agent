from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from awesome_agent.domain.enums import (
    DispatchStatus,
    EventType,
    IntakeReservationStatus,
    RunStatus,
    WorkspaceRetentionStatus,
)
from awesome_agent.domain.models import Agent, Run, RuntimeEvent, TodoItem
from awesome_agent.repositories.reservations import IntakeReservationStore
from awesome_agent.runtime.dispatch import DispatchConflict
from awesome_agent.runtime.repository import RuntimeRepository
from awesome_agent.safety.redaction import redact_runtime_payload

_SCHEMA_VERSION = "1"


class LocalRuntimeRepository(RuntimeRepository):
    def __init__(
        self,
        path: Path,
        reservations: IntakeReservationStore | None = None,
    ) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._reservations = reservations
        self._ensure_schema()

    def close(self) -> None:
        self._connection.close()

    async def create_run(self, run: Run, leader: Agent) -> None:
        with self._connection:
            self._upsert_run(run)
            self._upsert_agent(leader)

    async def publish_intake(
        self,
        *,
        run: Run,
        leader: Agent,
        todo: TodoItem | None,
        events: list[RuntimeEvent],
        reservation_id: UUID,
    ) -> None:
        if self._reservations is None:
            raise RuntimeError("Intake reservation store is not configured.")
        reservation = await self._reservations.get(reservation_id)
        if reservation.run_id != run.id:
            raise ValueError("Reservation does not belong to this Run.")
        published = reservation.model_copy(
            update={"status": IntakeReservationStatus.PUBLISHED}
        )
        with self._connection:
            self._upsert_run(run)
            self._upsert_agent(leader)
            if todo is not None:
                self._upsert_todo(todo)
            for event in events:
                self._insert_event(_redacted_event(event))
        await self._reservations.update(published)

    async def get_run(self, run_id: UUID) -> Run:
        row = self._connection.execute(
            "SELECT payload_json FROM runtime_runs WHERE id = ?",
            (str(run_id),),
        ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return Run.model_validate_json(row["payload_json"])

    async def list_runs(self) -> list[Run]:
        rows = self._connection.execute(
            """
            SELECT payload_json FROM runtime_runs
            ORDER BY created_at ASC, id ASC
            """
        ).fetchall()
        return [Run.model_validate_json(row["payload_json"]) for row in rows]

    async def list_child_runs(self, parent_run_id: UUID) -> list[Run]:
        return [
            run for run in await self.list_runs() if run.parent_run_id == parent_run_id
        ]

    async def list_descendant_runs(self, root_run_id: UUID) -> list[Run]:
        return [
            run
            for run in await self.list_runs()
            if run.root_run_id == root_run_id and run.id != root_run_id
        ]

    async def update_run(self, run: Run) -> None:
        with self._connection:
            self._upsert_run(run)

    async def requeue_waiting_run(self, run_id: UUID, *, reason: str) -> Run:
        current = await self.get_run(run_id)
        if current.status is not RunStatus.WAITING:
            return current
        updated = current.model_copy(
            update={
                "status": RunStatus.RUNNING,
                "dispatch_status": DispatchStatus.QUEUED,
                "last_release_reason": reason,
            }
        )
        await self.update_run(updated)
        await self.append_event(
            run_id=run_id,
            event_type=EventType.RUN_STATUS_CHANGED,
            payload={
                "status": updated.status.value,
                "dispatch_status": updated.dispatch_status.value,
                "reason": reason,
            },
        )
        return updated

    async def update_workspace_retention(
        self,
        run_id: UUID,
        *,
        status: WorkspaceRetentionStatus,
        reason: str | None,
        cleaned_at: datetime | None = None,
    ) -> Run:
        current = await self.get_run(run_id)
        updated = current.model_copy(
            update={
                "workspace_retention_status": status,
                "workspace_cleanup_reason": reason,
                "workspace_cleaned_at": cleaned_at,
            }
        )
        await self.update_run(updated)
        return updated

    async def cancel_run(self, run_id: UUID) -> tuple[Run, RuntimeEvent | None]:
        current = await self.get_run(run_id)
        if current.dispatch_status in {
            DispatchStatus.CLAIMED,
            DispatchStatus.EXECUTING,
        }:
            raise DispatchConflict("Claimed or executing Runs cannot be cancelled yet.")
        if current.status is RunStatus.CANCELLED:
            return current, None
        updated = current.model_copy(
            update={
                "status": RunStatus.CANCELLED,
                "dispatch_status": DispatchStatus.TERMINAL,
            }
        )
        await self.update_run(updated)
        event = await self.append_event(
            run_id=run_id,
            event_type=EventType.RUN_STATUS_CHANGED,
            payload={
                "status": updated.status.value,
                "dispatch_status": updated.dispatch_status.value,
            },
        )
        for child in await self.list_descendant_runs(run_id):
            await self._cancel_descendant(child, reason="parent_cancelled")
        return updated, event

    async def list_agents(self, run_id: UUID) -> list[Agent]:
        rows = self._connection.execute(
            """
            SELECT payload_json FROM runtime_agents
            WHERE run_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (str(run_id),),
        ).fetchall()
        return [Agent.model_validate_json(row["payload_json"]) for row in rows]

    async def add_agent(self, agent: Agent) -> None:
        with self._connection:
            self._upsert_agent(agent)

    async def list_todos(self, run_id: UUID) -> list[TodoItem]:
        rows = self._connection.execute(
            """
            SELECT payload_json FROM runtime_todos
            WHERE run_id = ?
            ORDER BY created_at ASC, id ASC
            """,
            (str(run_id),),
        ).fetchall()
        return [TodoItem.model_validate_json(row["payload_json"]) for row in rows]

    async def add_todo(self, todo: TodoItem) -> None:
        with self._connection:
            self._upsert_todo(todo)

    async def update_todo(self, todo: TodoItem) -> None:
        await self.add_todo(todo)

    async def append_event(
        self,
        *,
        run_id: UUID,
        event_type: EventType,
        payload: dict[str, object],
        agent_id: UUID | None = None,
    ) -> RuntimeEvent:
        sequence = self._next_sequence(run_id)
        event = RuntimeEvent(
            run_id=run_id,
            sequence=sequence,
            event_type=event_type,
            payload=redact_runtime_payload(payload),
            agent_id=agent_id,
            trace_id=run_id.hex,
        )
        with self._connection:
            self._insert_event(event)
        return event

    async def list_events(
        self,
        run_id: UUID,
        *,
        after_sequence: int = 0,
    ) -> list[RuntimeEvent]:
        rows = self._connection.execute(
            """
            SELECT payload_json FROM runtime_events
            WHERE run_id = ? AND sequence > ?
            ORDER BY sequence ASC
            """,
            (str(run_id), after_sequence),
        ).fetchall()
        return [RuntimeEvent.model_validate_json(row["payload_json"]) for row in rows]

    async def _cancel_descendant(self, run: Run, *, reason: str) -> None:
        if run.status in {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.RECOVERY_REQUIRED,
        }:
            return
        if run.dispatch_status in {
            DispatchStatus.CLAIMED,
            DispatchStatus.EXECUTING,
        }:
            updated = run.model_copy(
                update={
                    "cancel_requested_at": datetime.now(UTC),
                    "cancel_reason": reason,
                }
            )
        else:
            updated = run.model_copy(
                update={
                    "status": RunStatus.CANCELLED,
                    "dispatch_status": DispatchStatus.TERMINAL,
                    "cancel_reason": reason,
                }
            )
        await self.update_run(updated)
        await self.append_event(
            run_id=run.id,
            event_type=EventType.RUN_STATUS_CHANGED,
            payload={
                "status": updated.status.value,
                "dispatch_status": updated.dispatch_status.value,
                "reason": reason,
            },
        )

    def _ensure_schema(self) -> None:
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS local_runtime_metadata (
                  key TEXT PRIMARY KEY,
                  value TEXT NOT NULL
                )
                """
            )
            existing_version = self._connection.execute(
                """
                SELECT value FROM local_runtime_metadata
                WHERE key = 'schema_version'
                """
            ).fetchone()
            if (
                existing_version is not None
                and existing_version["value"] != _SCHEMA_VERSION
            ):
                raise RuntimeError(
                    "Local runtime database is from a newer awesome_agent version."
                )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_runs (
                  id TEXT PRIMARY KEY,
                  status TEXT NOT NULL,
                  dispatch_status TEXT NOT NULL,
                  execution_kind TEXT NOT NULL,
                  runtime_route TEXT,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  payload_json TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_agents (
                  id TEXT PRIMARY KEY,
                  run_id TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  FOREIGN KEY(run_id) REFERENCES runtime_runs(id)
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_todos (
                  id TEXT PRIMARY KEY,
                  run_id TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  FOREIGN KEY(run_id) REFERENCES runtime_runs(id)
                )
                """
            )
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_events (
                  id TEXT PRIMARY KEY,
                  run_id TEXT NOT NULL,
                  sequence INTEGER NOT NULL,
                  event_type TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  UNIQUE(run_id, sequence),
                  FOREIGN KEY(run_id) REFERENCES runtime_runs(id)
                )
                """
            )
            self._connection.execute(
                """
                INSERT OR IGNORE INTO local_runtime_metadata (key, value)
                VALUES ('schema_version', ?)
                """,
                (_SCHEMA_VERSION,),
            )

    def _upsert_run(self, run: Run) -> None:
        self._connection.execute(
            """
            INSERT OR REPLACE INTO runtime_runs
              (
                id,
                status,
                dispatch_status,
                execution_kind,
                runtime_route,
                created_at,
                updated_at,
                payload_json
              )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(run.id),
                run.status.value,
                run.dispatch_status.value,
                run.execution_kind.value,
                run.runtime_route,
                run.created_at.isoformat(),
                datetime.now(UTC).isoformat(),
                run.model_dump_json(),
            ),
        )

    def _upsert_agent(self, agent: Agent) -> None:
        self._connection.execute(
            """
            INSERT OR REPLACE INTO runtime_agents
              (id, run_id, created_at, updated_at, payload_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(agent.id),
                str(agent.run_id),
                agent.created_at.isoformat(),
                datetime.now(UTC).isoformat(),
                agent.model_dump_json(),
            ),
        )

    def _upsert_todo(self, todo: TodoItem) -> None:
        self._connection.execute(
            """
            INSERT OR REPLACE INTO runtime_todos
              (id, run_id, created_at, updated_at, payload_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(todo.id),
                str(todo.run_id),
                todo.created_at.isoformat(),
                datetime.now(UTC).isoformat(),
                todo.model_dump_json(),
            ),
        )

    def _insert_event(self, event: RuntimeEvent) -> None:
        self._connection.execute(
            """
            INSERT INTO runtime_events
              (id, run_id, sequence, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(event.id),
                str(event.run_id),
                event.sequence,
                event.event_type.value,
                event.model_dump_json(),
                event.created_at.isoformat(),
            ),
        )

    def _next_sequence(self, run_id: UUID) -> int:
        row = self._connection.execute(
            """
            SELECT COALESCE(MAX(sequence), 0) AS current_sequence
            FROM runtime_events
            WHERE run_id = ?
            """,
            (str(run_id),),
        ).fetchone()
        return int(row["current_sequence"]) + 1


def _redacted_event(event: RuntimeEvent) -> RuntimeEvent:
    return event.model_copy(update={"payload": redact_runtime_payload(event.payload)})
