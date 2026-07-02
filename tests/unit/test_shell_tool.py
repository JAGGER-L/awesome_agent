from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from awesome_agent.sandbox.base import CommandRequest, CommandResult
from awesome_agent.tools.models import ToolInvocation
from awesome_agent.tools.registry import ToolRegistry
from awesome_agent.tools.shell import _execute, classify_command, register_shell_tools


class RecordingSandbox:
    name = "recording"

    def __init__(
        self,
        *,
        exit_code: int = 0,
        stdout: str = "ok",
        stderr: str = "",
        timed_out: bool = False,
    ) -> None:
        self.requests: list[CommandRequest] = []
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out

    async def execute(self, request: CommandRequest) -> CommandResult:
        self.requests.append(request)
        return CommandResult(
            command=request.command_label,
            exit_code=self.exit_code,
            stdout=self.stdout,
            stderr=self.stderr,
            timed_out=self.timed_out,
            sandbox=self.name,
        )


def test_shell_policy_classifies_allow_ask_and_deny() -> None:
    assert classify_command(["pytest"]) == "allow"
    assert classify_command(["python", "-m", "unittest", "discover"]) == "allow"
    assert classify_command(["git", "diff"]) == "allow"
    assert classify_command(["git", "push"]) == "deny"
    assert classify_command(["git", "status"]) == "allow"
    assert classify_command(["git", "reset", "--hard"]) == "deny"
    assert classify_command(["ruff", "check", "."]) == "allow"
    assert classify_command(["mypy", "src"]) == "allow"
    assert classify_command(["npm", "publish"]) == "deny"
    assert classify_command(["npm", "run", "lint"]) == "allow"
    assert classify_command(["npm", "test"]) == "allow"
    assert classify_command(["cargo", "test"]) == "allow"
    assert classify_command(["go", "test", "./..."]) == "allow"
    assert classify_command(["pwsh", "-Command", "Remove-Item"]) == "deny"
    assert classify_command(["python", "script.py"]) == "ask"


@pytest.mark.asyncio
async def test_shell_execute_uses_injected_sandbox(tmp_path: Path) -> None:
    registry = ToolRegistry()
    sandbox = RecordingSandbox()
    register_shell_tools(registry, sandbox=sandbox)
    _, handler = registry.resolve("shell.execute")
    result = await _execute(
        ToolInvocation(
            tool_name="shell.execute",
            agent_id=uuid4(),
            profile="leader",
            capabilities={"shell:execute"},
            arguments={"argv": ["pytest"], "timeout_seconds": 5},
            workspace=tmp_path,
        ),
        None,
        sandbox=sandbox,
    )
    registry_result = await handler(
        ToolInvocation(
            tool_name="shell.execute",
            agent_id=uuid4(),
            profile="leader",
            capabilities={"shell:execute"},
            arguments={"argv": ["pytest"], "timeout_seconds": 5},
            workspace=tmp_path,
        ),
        None,
    )

    assert result.output["status"] == "completed"
    assert result.output["sandbox"] == "recording"
    assert registry_result.output["stdout"] == "ok"
    assert sandbox.requests[0].argv == ["pytest"]
    assert sandbox.requests[0].workspace == tmp_path.resolve()
    assert sandbox.requests[0].timeout_seconds == 5


@pytest.mark.asyncio
async def test_shell_execute_requires_grant_for_ambiguous_command(
    tmp_path: Path,
) -> None:
    with pytest.raises(RuntimeError, match="approval"):
        await _execute(
            ToolInvocation(
                tool_name="shell.execute",
                agent_id=uuid4(),
                profile="leader",
                capabilities={"shell:execute"},
                arguments={"argv": ["python", "script.py"]},
                workspace=tmp_path,
            ),
            None,
            sandbox=RecordingSandbox(),
        )


@pytest.mark.asyncio
async def test_shell_execute_runs_approved_ambiguous_command(
    tmp_path: Path,
) -> None:
    sandbox = RecordingSandbox(stdout="approved")
    result = await _execute(
        ToolInvocation(
            tool_name="shell.execute",
            agent_id=uuid4(),
            profile="leader",
            capabilities={"shell:execute"},
            arguments={"argv": ["python", "script.py"]},
            workspace=tmp_path,
            approval_granted=True,
        ),
        None,
        sandbox=sandbox,
    )

    assert result.output["status"] == "completed"
    assert result.output["stdout"] == "approved"


@pytest.mark.asyncio
async def test_shell_execute_denies_dangerous_command(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="denied"):
        await _execute(
            ToolInvocation(
                tool_name="shell.execute",
                agent_id=uuid4(),
                profile="leader",
                capabilities={"shell:execute"},
                arguments={"argv": ["git", "push"]},
                workspace=tmp_path,
            ),
            None,
            sandbox=RecordingSandbox(),
        )


@pytest.mark.asyncio
async def test_shell_execute_reports_failed_and_truncated_output(
    tmp_path: Path,
) -> None:
    result = await _execute(
        ToolInvocation(
            tool_name="shell.execute",
            agent_id=uuid4(),
            profile="leader",
            capabilities={"shell:execute"},
            arguments={
                "argv": ["pytest"],
                "max_output_chars": 1_000,
            },
            workspace=tmp_path,
        ),
        None,
        sandbox=RecordingSandbox(
            exit_code=1,
            stdout="x" * 1_100,
            stderr="y" * 1_200,
            timed_out=True,
        ),
    )

    assert result.output["status"] == "failed"
    assert result.output["timed_out"] is True
    assert result.output["stdout_truncated"] is True
    assert result.output["stderr_truncated"] is True
    assert result.output["stdout"] == "x" * 1_000
    assert result.output["stderr"] == "y" * 1_000


@pytest.mark.asyncio
async def test_shell_execute_requires_workspace() -> None:
    with pytest.raises(RuntimeError, match="workspace"):
        await _execute(
            ToolInvocation(
                tool_name="shell.execute",
                agent_id=uuid4(),
                profile="leader",
                capabilities={"shell:execute"},
                arguments={"argv": ["pytest"]},
            ),
            None,
            sandbox=RecordingSandbox(),
        )
