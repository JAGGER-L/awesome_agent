from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from awesome_agent.persistence.validation import InMemoryValidationRepository
from awesome_agent.runtime.validation.executor import execute_validation_plan
from awesome_agent.runtime.validation.models import ValidationGate, ValidationPlan
from awesome_agent.sandbox.base import CommandResult


@pytest.mark.asyncio
async def test_validation_executor_records_passing_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_process(
        arguments: list[str],
        *,
        command_label: str,
        workspace: Path,
        timeout_seconds: float,
    ) -> CommandResult:
        return CommandResult(
            command=command_label,
            exit_code=0,
            stdout="passed\n",
            stderr="",
        )

    monkeypatch.setattr("awesome_agent.tools.shell.run_process", fake_run_process)
    repository = InMemoryValidationRepository()
    run_id = uuid4()

    result = await execute_validation_plan(
        ValidationPlan(
            gates=[
                ValidationGate(
                    id="pytest",
                    name="Pytest",
                    command=["pytest", "-q"],
                    required=True,
                    timeout_seconds=30,
                )
            ],
            source="detected",
        ),
        run_id=run_id,
        agent_id=uuid4(),
        workspace=tmp_path,
        repository=repository,
    )

    assert result.report.status == "passed"
    assert result.gates[0].status == "passed"
    assert result.gates[0].exit_code == 0
    assert result.gates[0].stdout_summary == "passed\n"
    assert await repository.list_for_run(run_id) == [result]


@pytest.mark.asyncio
async def test_validation_executor_classifies_command_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_process(
        arguments: list[str],
        *,
        command_label: str,
        workspace: Path,
        timeout_seconds: float,
    ) -> CommandResult:
        return CommandResult(
            command=command_label,
            exit_code=1,
            stdout="failed\n",
            stderr="assertion error\n",
        )

    monkeypatch.setattr("awesome_agent.tools.shell.run_process", fake_run_process)

    result = await execute_validation_plan(
        _plan(["pytest", "-q"]),
        run_id=uuid4(),
        agent_id=uuid4(),
        workspace=tmp_path,
    )

    assert result.report.status == "failed"
    assert result.gates[0].status == "failed"
    assert result.gates[0].failure_kind == "command_failed"
    assert result.gates[0].stderr_summary == "assertion error\n"


@pytest.mark.asyncio
async def test_validation_executor_classifies_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_process(
        arguments: list[str],
        *,
        command_label: str,
        workspace: Path,
        timeout_seconds: float,
    ) -> CommandResult:
        return CommandResult(
            command=command_label,
            exit_code=124,
            stdout="",
            stderr="timed out",
            timed_out=True,
        )

    monkeypatch.setattr("awesome_agent.tools.shell.run_process", fake_run_process)

    result = await execute_validation_plan(
        _plan(["pytest", "-q"]),
        run_id=uuid4(),
        agent_id=uuid4(),
        workspace=tmp_path,
    )

    assert result.report.status == "failed"
    assert result.gates[0].status == "failed"
    assert result.gates[0].failure_kind == "timeout"


@pytest.mark.asyncio
async def test_validation_executor_classifies_policy_denial(tmp_path: Path) -> None:
    result = await execute_validation_plan(
        _plan(["docker", "ps"]),
        run_id=uuid4(),
        agent_id=uuid4(),
        workspace=tmp_path,
    )

    assert result.report.status == "failed"
    assert result.gates[0].status == "failed"
    assert result.gates[0].failure_kind == "policy_denied"


def _plan(command: list[str]) -> ValidationPlan:
    return ValidationPlan(
        gates=[
            ValidationGate(
                id="gate",
                name="Gate",
                command=command,
                required=True,
                timeout_seconds=30,
            )
        ],
        source="configured",
    )
