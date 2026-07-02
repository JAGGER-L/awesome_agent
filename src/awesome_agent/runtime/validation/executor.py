from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import UUID

from awesome_agent.domain.enums import ExecutionOrigin
from awesome_agent.persistence.validation import (
    DurableValidationGateResult,
    DurableValidationReport,
    ValidationReportWithGates,
    ValidationRepository,
)
from awesome_agent.runtime.validation.models import ValidationGate, ValidationPlan
from awesome_agent.runtime.validation.summarize import summarize_output
from awesome_agent.sandbox.base import SandboxBackend
from awesome_agent.sandbox.factory import create_sandbox
from awesome_agent.settings import Settings
from awesome_agent.tools.approval import ApprovalPolicy
from awesome_agent.tools.executor import ToolExecutor
from awesome_agent.tools.models import ApprovalRequired, ToolDenied, ToolInvocation
from awesome_agent.tools.registry import ToolRegistry
from awesome_agent.tools.shell import ShellToolError, register_shell_tools


async def execute_validation_plan(
    plan: ValidationPlan,
    *,
    run_id: UUID,
    agent_id: UUID,
    workspace: Path,
    repository: ValidationRepository | None = None,
    executor: ToolExecutor | None = None,
    sandbox: SandboxBackend | None = None,
) -> ValidationReportWithGates:
    started_at = datetime.now(UTC)
    shell_executor = executor or _default_executor(sandbox=sandbox)
    report = DurableValidationReport(
        run_id=run_id,
        agent_id=agent_id,
        attempt=0,
        status="running",
        summary="Validation is running.",
        created_at=started_at,
    )
    gates = [
        await _execute_gate(
            gate,
            run_id=run_id,
            report_id=report.id,
            agent_id=agent_id,
            workspace=workspace,
            executor=shell_executor,
        )
        for gate in plan.gates
    ]
    passed = all(gate.status == "passed" for gate in gates if gate.required)
    final_report = DurableValidationReport(
        id=report.id,
        run_id=report.run_id,
        agent_id=report.agent_id,
        attempt=report.attempt,
        status="passed" if passed else "failed",
        summary=_report_summary(gates, passed=passed),
        created_at=report.created_at,
    )
    if repository is not None:
        await repository.record_report(final_report, gates=gates)
    return ValidationReportWithGates(report=final_report, gates=gates)


async def _execute_gate(
    gate: ValidationGate,
    *,
    run_id: UUID,
    report_id: UUID,
    agent_id: UUID,
    workspace: Path,
    executor: ToolExecutor,
) -> DurableValidationGateResult:
    start = perf_counter()
    try:
        result = await executor.execute(
            ToolInvocation(
                tool_name="shell.execute",
                agent_id=agent_id,
                profile="leader",
                capabilities={"shell:execute"},
                arguments={
                    "argv": gate.command,
                    "timeout_seconds": gate.timeout_seconds,
                    "max_output_chars": 30_000,
                },
                workspace=workspace,
            ),
            progress=None,
        )
    except ToolDenied:
        return _failed_gate(
            gate,
            run_id=run_id,
            report_id=report_id,
            duration_ms=_duration_ms(start),
            failure_kind="policy_denied",
            stderr_summary="Command was denied by policy.",
        )
    except ApprovalRequired:
        return _failed_gate(
            gate,
            run_id=run_id,
            report_id=report_id,
            duration_ms=_duration_ms(start),
            status="approval_required",
            failure_kind="approval_required",
            stderr_summary="Command requires approval before execution.",
        )
    except (ShellToolError, TimeoutError) as error:
        return _failed_gate(
            gate,
            run_id=run_id,
            report_id=report_id,
            duration_ms=_duration_ms(start),
            failure_kind="internal_error",
            stderr_summary=str(error),
        )

    output = result.output
    exit_code = _int_or_none(output.get("exit_code"))
    timed_out = output.get("timed_out") is True
    stdout = str(output.get("stdout", ""))
    stderr = str(output.get("stderr", ""))
    if timed_out:
        status = "failed"
        failure_kind = "timeout"
    elif exit_code == 0:
        status = "passed"
        failure_kind = None
    else:
        status = "failed"
        failure_kind = "command_failed"
    return DurableValidationGateResult(
        report_id=report_id,
        run_id=run_id,
        gate_id=gate.id,
        name=gate.name,
        command=gate.command,
        required=gate.required,
        status=status,
        exit_code=exit_code,
        duration_ms=_duration_ms(start),
        stdout_summary=summarize_output(stdout),
        stderr_summary=summarize_output(stderr),
        failure_kind=failure_kind,
    )


def _failed_gate(
    gate: ValidationGate,
    *,
    run_id: UUID,
    report_id: UUID,
    duration_ms: int,
    failure_kind: str,
    stderr_summary: str,
    status: str = "failed",
) -> DurableValidationGateResult:
    return DurableValidationGateResult(
        report_id=report_id,
        run_id=run_id,
        gate_id=gate.id,
        name=gate.name,
        command=gate.command,
        required=gate.required,
        status=status,
        duration_ms=duration_ms,
        stderr_summary=stderr_summary,
        failure_kind=failure_kind,
    )


def _default_executor(*, sandbox: SandboxBackend | None = None) -> ToolExecutor:
    registry = ToolRegistry()
    register_shell_tools(
        registry,
        sandbox=sandbox
        or create_sandbox(origin=ExecutionOrigin.API, settings=Settings()),
    )
    return ToolExecutor(registry, ApprovalPolicy())


def _duration_ms(start: float) -> int:
    return max(0, int((perf_counter() - start) * 1000))


def _int_or_none(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _report_summary(
    gates: list[DurableValidationGateResult],
    *,
    passed: bool,
) -> str:
    failed = [
        gate.gate_id for gate in gates if gate.required and gate.status != "passed"
    ]
    if passed:
        return f"Validation passed with {len(gates)} gate(s)."
    return "Validation failed: " + ", ".join(failed)
