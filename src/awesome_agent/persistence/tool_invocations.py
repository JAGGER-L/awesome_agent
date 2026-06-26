from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from awesome_agent.persistence.models import ToolInvocationRecord


@dataclass(frozen=True, slots=True)
class DurableToolInvocation:
    id: UUID
    run_id: UUID
    agent_id: UUID | None
    tool_name: str
    tool_version: str
    status: str
    idempotency_key: str
    arguments_hash: str
    risk_level: str
    path_refs: list[str] = field(default_factory=list)
    preimage_hashes: dict[str, str] = field(default_factory=dict)
    expected_postimage_hashes: dict[str, str] = field(default_factory=dict)
    result_summary: str | None = None
    artifact_refs: list[str] = field(default_factory=list)
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class PostgresToolInvocationRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def upsert(self, invocation: DurableToolInvocation) -> DurableToolInvocation:
        async with self._sessions.begin() as session:
            record = await session.get(ToolInvocationRecord, invocation.id)
            if record is None:
                session.add(_to_record(invocation))
            else:
                _update_record(record, invocation)
        return invocation

    async def get(self, invocation_id: UUID) -> DurableToolInvocation:
        async with self._sessions() as session:
            record = await session.get(ToolInvocationRecord, invocation_id)
        if record is None:
            raise KeyError(invocation_id)
        return _from_record(record)

    async def get_by_idempotency_key(
        self,
        run_id: UUID,
        idempotency_key: str,
    ) -> DurableToolInvocation | None:
        async with self._sessions() as session:
            record = await session.scalar(
                select(ToolInvocationRecord).where(
                    ToolInvocationRecord.run_id == run_id,
                    ToolInvocationRecord.idempotency_key == idempotency_key,
                )
            )
        return _from_record(record) if record is not None else None

    async def list_for_run(self, run_id: UUID) -> list[DurableToolInvocation]:
        async with self._sessions() as session:
            records = list(
                await session.scalars(
                    select(ToolInvocationRecord)
                    .where(ToolInvocationRecord.run_id == run_id)
                    .order_by(ToolInvocationRecord.created_at, ToolInvocationRecord.id)
                )
            )
        return [_from_record(record) for record in records]


def _to_record(invocation: DurableToolInvocation) -> ToolInvocationRecord:
    return ToolInvocationRecord(
        id=invocation.id,
        run_id=invocation.run_id,
        agent_id=invocation.agent_id,
        tool_name=invocation.tool_name,
        tool_version=invocation.tool_version,
        status=invocation.status,
        idempotency_key=invocation.idempotency_key,
        arguments_hash=invocation.arguments_hash,
        risk_level=invocation.risk_level,
        path_refs=invocation.path_refs,
        preimage_hashes=invocation.preimage_hashes,
        expected_postimage_hashes=invocation.expected_postimage_hashes,
        result_summary=invocation.result_summary,
        artifact_refs=invocation.artifact_refs,
        error=invocation.error,
        started_at=invocation.started_at,
        completed_at=invocation.completed_at,
        created_at=invocation.created_at,
        updated_at=invocation.updated_at,
    )


def _update_record(
    record: ToolInvocationRecord,
    invocation: DurableToolInvocation,
) -> None:
    record.status = invocation.status
    record.path_refs = invocation.path_refs
    record.preimage_hashes = invocation.preimage_hashes
    record.expected_postimage_hashes = invocation.expected_postimage_hashes
    record.result_summary = invocation.result_summary
    record.artifact_refs = invocation.artifact_refs
    record.error = invocation.error
    record.started_at = invocation.started_at
    record.completed_at = invocation.completed_at
    record.updated_at = invocation.updated_at


def _from_record(record: ToolInvocationRecord) -> DurableToolInvocation:
    return DurableToolInvocation(
        id=record.id,
        run_id=record.run_id,
        agent_id=record.agent_id,
        tool_name=record.tool_name,
        tool_version=record.tool_version,
        status=record.status,
        idempotency_key=record.idempotency_key,
        arguments_hash=record.arguments_hash,
        risk_level=record.risk_level,
        path_refs=list(record.path_refs),
        preimage_hashes={
            str(key): value for key, value in record.preimage_hashes.items()
        },
        expected_postimage_hashes={
            str(key): value for key, value in record.expected_postimage_hashes.items()
        },
        result_summary=record.result_summary,
        artifact_refs=list(record.artifact_refs),
        error=record.error,
        started_at=record.started_at,
        completed_at=record.completed_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
