from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from awesome_agent.persistence.validation import (
    DurableValidationGateResult,
    DurableValidationReport,
    InMemoryValidationRepository,
    _from_gate_record,
    _from_report_record,
    _to_gate_record,
    _to_report_record,
)


def test_validation_report_and_gate_records_round_trip() -> None:
    now = datetime.now(UTC)
    run_id = uuid4()
    agent_id = uuid4()
    report = DurableValidationReport(
        id=uuid4(),
        run_id=run_id,
        agent_id=agent_id,
        attempt=1,
        status="failed",
        summary="pytest failed",
        created_at=now,
    )
    gate = DurableValidationGateResult(
        id=uuid4(),
        report_id=report.id,
        run_id=run_id,
        gate_id="pytest",
        name="Pytest",
        command=["pytest", "-q"],
        required=True,
        status="failed",
        exit_code=1,
        duration_ms=1234,
        stdout_summary="1 failed",
        stderr_summary="",
        artifact_refs=["artifact-id"],
        failure_kind="command_failed",
        created_at=now,
    )

    assert _from_report_record(_to_report_record(report)) == report
    assert _from_gate_record(_to_gate_record(gate)) == gate


@pytest.mark.asyncio
async def test_inmemory_validation_repository_lists_reports_with_gates() -> None:
    repository = InMemoryValidationRepository()
    run_id = uuid4()
    report = DurableValidationReport(
        run_id=run_id,
        agent_id=uuid4(),
        attempt=0,
        status="passed",
        summary="all gates passed",
    )
    gate = DurableValidationGateResult(
        report_id=report.id,
        run_id=run_id,
        gate_id="ruff",
        name="Ruff lint",
        command=["ruff", "check", "."],
        required=True,
        status="passed",
    )

    stored = await repository.record_report(report, gates=[gate])
    listed = await repository.list_for_run(run_id)

    assert stored == report
    assert [item.report for item in listed] == [report]
    assert [item.gates for item in listed] == [[gate]]
    assert asdict(listed[0].report)["summary"] == "all gates passed"
