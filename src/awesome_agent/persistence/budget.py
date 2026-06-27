from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from awesome_agent.persistence.models import (
    ContextCompactionRecord as ContextCompactionRow,
)
from awesome_agent.persistence.models import (
    RunBudgetLedgerRecord as RunBudgetLedgerRow,
)


@dataclass(frozen=True, slots=True)
class RunBudgetLedgerRecord:
    run_id: UUID
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_reasoning_tokens: int = 0
    active_seconds: int = 0
    model_call_count: int = 0
    threshold_status: str = "within_budget"
    active_window_started_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class ContextCompactionRecord:
    run_id: UUID
    agent_id: UUID | None
    graph_name: str
    graph_version: int
    before_estimated_tokens: int
    after_estimated_tokens: int
    summary: str
    artifact_refs: list[UUID]
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class BudgetRepository(Protocol):
    async def upsert_ledger(
        self,
        ledger: RunBudgetLedgerRecord,
    ) -> RunBudgetLedgerRecord:
        pass

    async def get_ledger(self, run_id: UUID) -> RunBudgetLedgerRecord:
        pass

    async def record_compaction(
        self,
        compaction: ContextCompactionRecord,
    ) -> ContextCompactionRecord:
        pass

    async def list_compactions(self, run_id: UUID) -> list[ContextCompactionRecord]:
        pass


class InMemoryBudgetRepository:
    def __init__(self) -> None:
        self._ledgers: dict[UUID, RunBudgetLedgerRecord] = {}
        self._compactions: list[ContextCompactionRecord] = []

    async def upsert_ledger(
        self,
        ledger: RunBudgetLedgerRecord,
    ) -> RunBudgetLedgerRecord:
        self._ledgers[ledger.run_id] = ledger
        return ledger

    async def get_ledger(self, run_id: UUID) -> RunBudgetLedgerRecord:
        return self._ledgers.get(run_id, RunBudgetLedgerRecord(run_id=run_id))

    async def record_compaction(
        self,
        compaction: ContextCompactionRecord,
    ) -> ContextCompactionRecord:
        self._compactions.append(compaction)
        return compaction

    async def list_compactions(self, run_id: UUID) -> list[ContextCompactionRecord]:
        return [
            compaction
            for compaction in self._compactions
            if compaction.run_id == run_id
        ]


class PostgresBudgetRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def upsert_ledger(
        self,
        ledger: RunBudgetLedgerRecord,
    ) -> RunBudgetLedgerRecord:
        now = datetime.now(UTC)
        saved = RunBudgetLedgerRecord(
            run_id=ledger.run_id,
            total_input_tokens=ledger.total_input_tokens,
            total_output_tokens=ledger.total_output_tokens,
            total_reasoning_tokens=ledger.total_reasoning_tokens,
            active_seconds=ledger.active_seconds,
            model_call_count=ledger.model_call_count,
            threshold_status=ledger.threshold_status,
            active_window_started_at=ledger.active_window_started_at,
            created_at=ledger.created_at,
            updated_at=now,
        )
        statement = insert(RunBudgetLedgerRow).values(
            run_id=saved.run_id,
            total_input_tokens=saved.total_input_tokens,
            total_output_tokens=saved.total_output_tokens,
            total_reasoning_tokens=saved.total_reasoning_tokens,
            active_seconds=saved.active_seconds,
            model_call_count=saved.model_call_count,
            threshold_status=saved.threshold_status,
            active_window_started_at=saved.active_window_started_at,
            created_at=saved.created_at,
            updated_at=saved.updated_at,
        )
        statement = statement.on_conflict_do_update(
            index_elements=[RunBudgetLedgerRow.run_id],
            set_={
                "total_input_tokens": statement.excluded.total_input_tokens,
                "total_output_tokens": statement.excluded.total_output_tokens,
                "total_reasoning_tokens": statement.excluded.total_reasoning_tokens,
                "active_seconds": statement.excluded.active_seconds,
                "model_call_count": statement.excluded.model_call_count,
                "threshold_status": statement.excluded.threshold_status,
                "active_window_started_at": (
                    statement.excluded.active_window_started_at
                ),
                "updated_at": statement.excluded.updated_at,
            },
        )
        async with self._sessions.begin() as session:
            await session.execute(statement)
        return saved

    async def get_ledger(self, run_id: UUID) -> RunBudgetLedgerRecord:
        async with self._sessions() as session:
            record = await session.get(RunBudgetLedgerRow, run_id)
        if record is None:
            return RunBudgetLedgerRecord(run_id=run_id)
        return _ledger_from_row(record)

    async def record_compaction(
        self,
        compaction: ContextCompactionRecord,
    ) -> ContextCompactionRecord:
        async with self._sessions.begin() as session:
            existing = await session.get(ContextCompactionRow, compaction.id)
            if existing is None:
                session.add(_compaction_to_row(compaction))
        return compaction

    async def list_compactions(self, run_id: UUID) -> list[ContextCompactionRecord]:
        async with self._sessions() as session:
            records = list(
                await session.scalars(
                    select(ContextCompactionRow)
                    .where(ContextCompactionRow.run_id == run_id)
                    .order_by(
                        ContextCompactionRow.created_at,
                        ContextCompactionRow.id,
                    )
                )
            )
        return [_compaction_from_row(record) for record in records]


def _ledger_from_row(row: RunBudgetLedgerRow) -> RunBudgetLedgerRecord:
    return RunBudgetLedgerRecord(
        run_id=row.run_id,
        total_input_tokens=row.total_input_tokens,
        total_output_tokens=row.total_output_tokens,
        total_reasoning_tokens=row.total_reasoning_tokens,
        active_seconds=row.active_seconds,
        model_call_count=row.model_call_count,
        threshold_status=row.threshold_status,
        active_window_started_at=row.active_window_started_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _compaction_to_row(compaction: ContextCompactionRecord) -> ContextCompactionRow:
    return ContextCompactionRow(
        id=compaction.id,
        run_id=compaction.run_id,
        agent_id=compaction.agent_id,
        graph_name=compaction.graph_name,
        graph_version=compaction.graph_version,
        before_estimated_tokens=compaction.before_estimated_tokens,
        after_estimated_tokens=compaction.after_estimated_tokens,
        summary=compaction.summary,
        artifact_refs=[str(ref) for ref in compaction.artifact_refs],
        created_at=compaction.created_at,
    )


def _compaction_from_row(row: ContextCompactionRow) -> ContextCompactionRecord:
    return ContextCompactionRecord(
        id=row.id,
        run_id=row.run_id,
        agent_id=row.agent_id,
        graph_name=row.graph_name,
        graph_version=row.graph_version,
        before_estimated_tokens=row.before_estimated_tokens,
        after_estimated_tokens=row.after_estimated_tokens,
        summary=row.summary,
        artifact_refs=[UUID(ref) for ref in row.artifact_refs],
        created_at=row.created_at,
    )
