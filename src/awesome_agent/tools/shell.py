from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from awesome_agent.domain.enums import RiskLevel
from awesome_agent.sandbox.base import CommandRequest, SandboxBackend
from awesome_agent.tools.guardrails import evaluate_command
from awesome_agent.tools.models import ToolInvocation, ToolResult, ToolSpec
from awesome_agent.tools.registry import ToolRegistry


class ShellExecuteArguments(BaseModel):
    argv: list[str] = Field(min_length=1, max_length=64)
    timeout_seconds: float = Field(default=60, gt=0, le=600)
    max_output_chars: int = Field(default=30_000, ge=1_000, le=200_000)


class ShellToolError(RuntimeError):
    pass


def classify_command(argv: list[str]) -> Literal["allow", "ask", "deny"]:
    return evaluate_command(argv).action


def register_shell_tools(registry: ToolRegistry, *, sandbox: SandboxBackend) -> None:
    async def execute(invocation: ToolInvocation, progress: object) -> ToolResult:
        return await _execute(invocation, progress, sandbox=sandbox)

    registry.register(
        ToolSpec(
            name="shell.execute",
            description=(
                "Execute an approved argv-only command through the configured "
                "sandbox provider."
            ),
            risk_level=RiskLevel.MEDIUM,
            sandbox_required=True,
            required_capabilities={"shell:execute"},
            input_schema=ShellExecuteArguments.model_json_schema(),
        ),
        execute,
    )


async def _execute(
    invocation: ToolInvocation,
    _: object,
    *,
    sandbox: SandboxBackend,
) -> ToolResult:
    arguments = ShellExecuteArguments.model_validate(invocation.arguments)
    guardrail = evaluate_command(arguments.argv)
    if guardrail.action == "deny":
        raise ShellToolError(guardrail.reason)
    if guardrail.action == "ask" and not invocation.approval_granted:
        raise ShellToolError(guardrail.reason)
    workspace = _workspace(invocation)
    result = await sandbox.execute(
        CommandRequest(
            argv=arguments.argv,
            workspace=workspace,
            timeout_seconds=arguments.timeout_seconds,
            max_output_chars=arguments.max_output_chars,
        )
    )
    stdout, stdout_truncated = _bound(arguments.max_output_chars, result.stdout)
    stderr, stderr_truncated = _bound(arguments.max_output_chars, result.stderr)
    return ToolResult(
        invocation_id=invocation.id,
        output={
            "status": "completed" if result.exit_code == 0 else "failed",
            "argv": arguments.argv,
            "exit_code": result.exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": result.timed_out,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "sandbox": result.sandbox or sandbox.name,
            "guardrail": {
                "action": guardrail.action,
                "reason": guardrail.reason,
                "name": guardrail.guardrail,
            },
        },
    )


def _workspace(invocation: ToolInvocation) -> Path:
    if invocation.workspace is None:
        raise ShellToolError("Tool invocation has no Run workspace.")
    return invocation.workspace.resolve()


def _bound(limit: int, value: str) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    return value[:limit], True
