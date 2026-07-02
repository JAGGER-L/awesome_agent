from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from awesome_agent.domain.enums import RiskLevel
from awesome_agent.sandbox.base import CommandRequest, SandboxBackend
from awesome_agent.tools.models import ToolInvocation, ToolResult, ToolSpec
from awesome_agent.tools.registry import ToolRegistry


class ShellExecuteArguments(BaseModel):
    argv: list[str] = Field(min_length=1, max_length=64)
    timeout_seconds: float = Field(default=60, gt=0, le=600)
    max_output_chars: int = Field(default=30_000, ge=1_000, le=200_000)


class ShellToolError(RuntimeError):
    pass


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
    decision = classify_command(arguments.argv)
    if decision == "deny":
        raise ShellToolError("Command is denied by policy.")
    if decision == "ask" and not invocation.approval_granted:
        raise ShellToolError("Command requires durable approval before execution.")
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
        },
    )


def classify_command(argv: list[str]) -> Literal["allow", "ask", "deny"]:
    executable = Path(argv[0]).name.lower()
    lowered = [item.lower() for item in argv]
    if executable in {
        "rm",
        "curl",
        "wget",
        "ssh",
        "docker",
        "docker-compose",
        "powershell",
        "powershell.exe",
        "pwsh",
        "cmd",
        "cmd.exe",
        "sh",
        "bash",
    }:
        return "deny"
    if executable == "git" and len(lowered) > 1:
        if lowered[1] in {"push", "reset", "clean", "checkout", "switch", "commit"}:
            return "deny"
        if lowered[1] in {"status", "diff", "grep"}:
            return "allow"
    if executable == "pytest":
        return "allow"
    if executable == "python" and len(lowered) > 2 and lowered[1:3] == [
        "-m",
        "unittest",
    ]:
        return "allow"
    if executable == "ruff" and len(lowered) > 1 and lowered[1] == "check":
        return "allow"
    if executable == "mypy":
        return "allow"
    if executable == "npm" and len(lowered) > 1:
        if lowered[1] == "publish":
            return "deny"
        if lowered[1:] == ["run", "lint"] or lowered[1] == "test":
            return "allow"
    if executable == "cargo" and len(lowered) > 1 and lowered[1] == "test":
        return "allow"
    if executable == "go" and len(lowered) > 1 and lowered[1] == "test":
        return "allow"
    return "ask"


def _workspace(invocation: ToolInvocation) -> Path:
    if invocation.workspace is None:
        raise ShellToolError("Tool invocation has no Run workspace.")
    return invocation.workspace.resolve()


def _bound(limit: int, value: str) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    return value[:limit], True
