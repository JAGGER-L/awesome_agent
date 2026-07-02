from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from awesome_agent.persistence.validation import InMemoryValidationRepository
from awesome_agent.runtime.validation.executor import execute_validation_plan
from awesome_agent.runtime.validation.models import ValidationGate, ValidationPlan
from awesome_agent.sandbox.base import CommandRequest, CommandResult


class RecordingSandbox:
    name = "recording"

    def __init__(
        self,
        *,
        exit_code: int = 0,
        stdout: str = "",
        stderr: str = "",
        timed_out: bool = False,
    ) -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out

    async def execute(self, request: CommandRequest) -> CommandResult:
        return CommandResult(
            command=request.command_label,
            exit_code=self.exit_code,
            stdout=self.stdout,
            stderr=self.stderr,
            timed_out=self.timed_out,
            sandbox=self.name,
        )


@pytest.mark.asyncio
async def test_validation_executor_records_passing_gate(
    tmp_path: Path,
) -> None:
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
        sandbox=RecordingSandbox(stdout="passed\n"),
    )

    assert result.report.status == "passed"
    assert result.gates[0].status == "passed"
    assert result.gates[0].exit_code == 0
    assert result.gates[0].stdout_summary == "passed\n"
    assert await repository.list_for_run(run_id) == [result]


@pytest.mark.asyncio
async def test_validation_executor_classifies_command_failure(
    tmp_path: Path,
) -> None:
    result = await execute_validation_plan(
        _plan(["pytest", "-q"]),
        run_id=uuid4(),
        agent_id=uuid4(),
        workspace=tmp_path,
        sandbox=RecordingSandbox(
            exit_code=1,
            stdout="failed\n",
            stderr="assertion error\n",
        ),
    )

    assert result.report.status == "failed"
    assert result.gates[0].status == "failed"
    assert result.gates[0].failure_kind == "command_failed"
    assert result.gates[0].stderr_summary == "assertion error\n"


@pytest.mark.asyncio
async def test_validation_executor_classifies_timeout(
    tmp_path: Path,
) -> None:
    result = await execute_validation_plan(
        _plan(["pytest", "-q"]),
        run_id=uuid4(),
        agent_id=uuid4(),
        workspace=tmp_path,
        sandbox=RecordingSandbox(
            exit_code=124,
            stderr="timed out",
            timed_out=True,
        ),
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
