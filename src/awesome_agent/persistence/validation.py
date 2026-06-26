from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from awesome_agent.persistence.models import (
    ValidationGateResultRecord,
    ValidationReportRecord,
)


@dataclass(frozen=True, slots=True)
class DurableValidationReport:
    run_id: UUID
    agent_id: UUID | None
    attempt: int
    status: str
    summary: str
    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class DurableValidationGateResult:
    report_id: UUID
    run_id: UUID
    gate_id: str
    name: str
    command: list[str]
    required: bool
    status: str
    id: UUID = field(default_factory=uuid4)
    exit_code: int | None = None
    duration_ms: int | None = None
    stdout_summary: str = ""
    stderr_summary: str = ""
    artifact_refs: list[str] = field(default_factory=list)
    failure_kind: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class ValidationReportWithGates:
    report: DurableValidationReport
    gates: list[DurableValidationGateResult]


class ValidationRepository(Protocol):
    async def record_report(
        self,
        report: DurableValidationReport,
        *,
        gates: list[DurableValidationGateResult],
    ) -> DurableValidationReport:
        """Persist a validation report and its gate results."""
        ...

    async def list_for_run(self, run_id: UUID) -> list[ValidationReportWithGates]:
        """Load validation reports for a run in creation order."""
        ...


class InMemoryValidationRepository:
    def __init__(self) -> None:
        self._reports: dict[UUID, DurableValidationReport] = {}
        self._gates: dict[UUID, list[DurableValidationGateResult]] = {}

    async def record_report(
        self,
        report: DurableValidationReport,
        *,
        gates: list[DurableValidationGateResult],
    ) -> DurableValidationReport:
        self._reports[report.id] = report
        self._gates[report.id] = list(gates)
        return report

    async def list_for_run(self, run_id: UUID) -> list[ValidationReportWithGates]:
        reports = sorted(
            (report for report in self._reports.values() if report.run_id == run_id),
            key=lambda report: (report.created_at, report.id),
        )
        return [
            ValidationReportWithGates(
                report=report,
                gates=sorted(
                    self._gates.get(report.id, []),
                    key=lambda gate: (gate.created_at, gate.id),
                ),
            )
            for report in reports
        ]


class PostgresValidationRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def record_report(
        self,
        report: DurableValidationReport,
        *,
        gates: list[DurableValidationGateResult],
    ) -> DurableValidationReport:
        async with self._sessions.begin() as session:
            existing = await session.get(ValidationReportRecord, report.id)
            if existing is None:
                session.add(_to_report_record(report))
            else:
                existing.status = report.status
                existing.summary = report.summary
            for gate in gates:
                gate_record = await session.get(ValidationGateResultRecord, gate.id)
                if gate_record is None:
                    session.add(_to_gate_record(gate))
                else:
                    _update_gate_record(gate_record, gate)
        return report

    async def list_for_run(self, run_id: UUID) -> list[ValidationReportWithGates]:
        async with self._sessions() as session:
            reports = list(
                await session.scalars(
                    select(ValidationReportRecord)
                    .where(ValidationReportRecord.run_id == run_id)
                    .order_by(
                        ValidationReportRecord.created_at,
                        ValidationReportRecord.id,
                    )
                )
            )
            gates = list(
                await session.scalars(
                    select(ValidationGateResultRecord)
                    .where(ValidationGateResultRecord.run_id == run_id)
                    .order_by(
                        ValidationGateResultRecord.created_at,
                        ValidationGateResultRecord.id,
                    )
                )
            )
        gates_by_report: dict[UUID, list[DurableValidationGateResult]] = {}
        for gate in gates:
            restored = _from_gate_record(gate)
            gates_by_report.setdefault(restored.report_id, []).append(restored)
        return [
            ValidationReportWithGates(
                report=_from_report_record(report),
                gates=gates_by_report.get(report.id, []),
            )
            for report in reports
        ]


def _to_report_record(report: DurableValidationReport) -> ValidationReportRecord:
    return ValidationReportRecord(
        id=report.id,
        run_id=report.run_id,
        agent_id=report.agent_id,
        attempt=report.attempt,
        status=report.status,
        summary=report.summary,
        created_at=report.created_at,
    )


def _from_report_record(record: ValidationReportRecord) -> DurableValidationReport:
    return DurableValidationReport(
        id=record.id,
        run_id=record.run_id,
        agent_id=record.agent_id,
        attempt=record.attempt,
        status=record.status,
        summary=record.summary,
        created_at=record.created_at,
    )


def _to_gate_record(
    gate: DurableValidationGateResult,
) -> ValidationGateResultRecord:
    return ValidationGateResultRecord(
        id=gate.id,
        report_id=gate.report_id,
        run_id=gate.run_id,
        gate_id=gate.gate_id,
        name=gate.name,
        command=gate.command,
        required=gate.required,
        status=gate.status,
        exit_code=gate.exit_code,
        duration_ms=gate.duration_ms,
        stdout_summary=gate.stdout_summary,
        stderr_summary=gate.stderr_summary,
        artifact_refs=gate.artifact_refs,
        failure_kind=gate.failure_kind,
        created_at=gate.created_at,
    )


def _update_gate_record(
    record: ValidationGateResultRecord,
    gate: DurableValidationGateResult,
) -> None:
    record.status = gate.status
    record.exit_code = gate.exit_code
    record.duration_ms = gate.duration_ms
    record.stdout_summary = gate.stdout_summary
    record.stderr_summary = gate.stderr_summary
    record.artifact_refs = gate.artifact_refs
    record.failure_kind = gate.failure_kind


def _from_gate_record(
    record: ValidationGateResultRecord,
) -> DurableValidationGateResult:
    return DurableValidationGateResult(
        id=record.id,
        report_id=record.report_id,
        run_id=record.run_id,
        gate_id=record.gate_id,
        name=record.name,
        command=list(record.command),
        required=record.required,
        status=record.status,
        exit_code=record.exit_code,
        duration_ms=record.duration_ms,
        stdout_summary=record.stdout_summary,
        stderr_summary=record.stderr_summary,
        artifact_refs=list(record.artifact_refs),
        failure_kind=record.failure_kind,
        created_at=record.created_at,
    )
