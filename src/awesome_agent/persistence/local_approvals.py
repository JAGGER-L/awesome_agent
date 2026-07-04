from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from awesome_agent.domain.enums import ApprovalStatus
from awesome_agent.persistence.approval_contracts import (
    ApprovalExpired,
    DurableApproval,
)


class LocalApprovalRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._ensure_schema()

    def close(self) -> None:
        self._connection.close()

    async def upsert(self, approval: DurableApproval) -> DurableApproval:
        stored = self._approval_for_upsert(approval)
        with self._connection:
            self._upsert(stored)
        return stored

    async def get(self, approval_id: UUID) -> DurableApproval:
        row = self._connection.execute(
            """
            SELECT payload_json FROM local_approvals
            WHERE id = ?
            """,
            (str(approval_id),),
        ).fetchone()
        if row is None:
            raise KeyError(approval_id)
        return _approval_from_json(str(row["payload_json"]))

    async def get_by_call(
        self,
        run_id: UUID,
        tool_call_id: str,
    ) -> DurableApproval | None:
        row = self._connection.execute(
            """
            SELECT payload_json FROM local_approvals
            WHERE run_id = ? AND tool_call_id = ?
            """,
            (str(run_id), tool_call_id),
        ).fetchone()
        if row is None:
            return None
        return _approval_from_json(str(row["payload_json"]))

    async def list_for_run(
        self,
        run_id: UUID,
        *,
        status: ApprovalStatus | None = None,
    ) -> list[DurableApproval]:
        parameters: tuple[str, ...]
        if status is None:
            query = """
                SELECT payload_json FROM local_approvals
                WHERE run_id = ?
                ORDER BY created_at ASC, id ASC
                """
            parameters = (str(run_id),)
        else:
            query = """
                SELECT payload_json FROM local_approvals
                WHERE run_id = ? AND status = ?
                ORDER BY created_at ASC, id ASC
                """
            parameters = (str(run_id), status.value)
        rows = self._connection.execute(query, parameters).fetchall()
        return [_approval_from_json(str(row["payload_json"])) for row in rows]

    async def decide(
        self,
        approval_id: UUID,
        *,
        approved: bool,
        decided_by: str | None,
        reason: str | None,
        now: datetime,
    ) -> DurableApproval:
        expired: DurableApproval | None = None
        with self._connection:
            row = self._connection.execute(
                """
                SELECT payload_json FROM local_approvals
                WHERE id = ?
                """,
                (str(approval_id),),
            ).fetchone()
            if row is None:
                raise KeyError(approval_id)
            approval = _approval_from_json(str(row["payload_json"]))
            if approval.status is not ApprovalStatus.PENDING:
                return approval
            if approval.expires_at <= now:
                expired = replace(
                    approval,
                    status=ApprovalStatus.EXPIRED,
                    updated_at=now,
                )
                self._upsert(expired)
            else:
                decided = replace(
                    approval,
                    status=(
                        ApprovalStatus.APPROVED if approved else ApprovalStatus.DENIED
                    ),
                    decided_at=now,
                    decided_by=decided_by,
                    decision_reason=reason,
                    updated_at=now,
                )
                self._upsert(decided)
                return decided
        if expired is not None:
            raise ApprovalExpired(expired)
        raise RuntimeError("Approval decision transaction ended without a result.")

    async def expire_expired(
        self,
        now: datetime,
        *,
        batch_size: int | None = None,
    ) -> list[DurableApproval]:
        if batch_size is not None and batch_size < 1:
            raise ValueError("Batch size must be positive.")
        expired: list[DurableApproval] = []
        with self._connection:
            rows = self._connection.execute(
                """
                SELECT payload_json FROM local_approvals
                WHERE status = ?
                ORDER BY expires_at ASC, created_at ASC, id ASC
                """,
                (ApprovalStatus.PENDING.value,),
            ).fetchall()
            for row in rows:
                if batch_size is not None and len(expired) >= batch_size:
                    break
                approval = _approval_from_json(str(row["payload_json"]))
                if approval.expires_at > now:
                    continue
                updated = replace(
                    approval,
                    status=ApprovalStatus.EXPIRED,
                    updated_at=now,
                )
                self._upsert(updated)
                expired.append(updated)
        return expired

    def _ensure_schema(self) -> None:
        with self._connection:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS local_approvals (
                  id TEXT PRIMARY KEY,
                  run_id TEXT NOT NULL,
                  tool_call_id TEXT NOT NULL,
                  status TEXT NOT NULL,
                  expires_at TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  payload_json TEXT NOT NULL
                )
                """
            )
            self._connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_local_approvals_run_call
                ON local_approvals (run_id, tool_call_id)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_local_approvals_run_status_created
                ON local_approvals (run_id, status, created_at, id)
                """
            )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_local_approvals_pending_expires
                ON local_approvals (status, expires_at)
                """
            )

    def _approval_for_upsert(self, approval: DurableApproval) -> DurableApproval:
        existing = self._connection.execute(
            """
            SELECT id FROM local_approvals
            WHERE id = ?
            """,
            (str(approval.id),),
        ).fetchone()
        if existing is not None:
            return approval

        existing_by_call = self._connection.execute(
            """
            SELECT id FROM local_approvals
            WHERE run_id = ? AND tool_call_id = ?
            """,
            (str(approval.run_id), approval.tool_call_id),
        ).fetchone()
        if existing_by_call is None:
            return approval
        return replace(approval, id=UUID(str(existing_by_call["id"])))

    def _upsert(self, approval: DurableApproval) -> None:
        self._connection.execute(
            """
            INSERT INTO local_approvals
              (
                id,
                run_id,
                tool_call_id,
                status,
                expires_at,
                created_at,
                updated_at,
                payload_json
              )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              run_id = excluded.run_id,
              tool_call_id = excluded.tool_call_id,
              status = excluded.status,
              expires_at = excluded.expires_at,
              created_at = excluded.created_at,
              updated_at = excluded.updated_at,
              payload_json = excluded.payload_json
            """,
            (
                str(approval.id),
                str(approval.run_id),
                approval.tool_call_id,
                approval.status.value,
                approval.expires_at.isoformat(),
                approval.created_at.isoformat(),
                approval.updated_at.isoformat(),
                _approval_to_json(approval),
            ),
        )


def _approval_to_json(approval: DurableApproval) -> str:
    return json.dumps(asdict(approval), default=_json_default, sort_keys=True)


def _approval_from_json(payload_json: str) -> DurableApproval:
    data = json.loads(payload_json)
    for key in ("id", "run_id", "tool_invocation_id"):
        data[key] = UUID(data[key])
    if data["agent_id"] is not None:
        data["agent_id"] = UUID(data["agent_id"])
    data["status"] = ApprovalStatus(data["status"])
    for key in ("expires_at", "decided_at", "created_at", "updated_at"):
        if data[key] is not None:
            data[key] = datetime.fromisoformat(data[key])
    return DurableApproval(**data)


def _json_default(value: Any) -> str:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, ApprovalStatus):
        return value.value
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")
