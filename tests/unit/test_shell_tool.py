from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from awesome_agent.sandbox.base import CommandResult
from awesome_agent.tools.models import ToolInvocation
from awesome_agent.tools.shell import _execute, classify_command


def test_shell_policy_classifies_allow_ask_and_deny() -> None:
    assert classify_command(["pytest"]) == "allow"
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
async def test_shell_execute_runs_allowed_command_in_docker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_run_process(
        arguments: list[str],
        *,
        command_label: str,
        workspace: Path,
        timeout_seconds: float,
    ) -> CommandResult:
        calls.append(
            {
                "arguments": arguments,
                "command_label": command_label,
                "workspace": workspace,
                "timeout_seconds": timeout_seconds,
            }
        )
        return CommandResult(
            command=command_label,
            exit_code=0,
            stdout="ok",
            stderr="",
        )

    monkeypatch.setattr("awesome_agent.tools.shell.run_process", fake_run_process)

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
    )

    assert result.output["status"] == "completed"
    assert calls[0]["arguments"][:5] == [
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
    ]
    assert calls[0]["arguments"][-1] == "pytest"
    assert calls[0]["timeout_seconds"] == 5


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
        )


@pytest.mark.asyncio
async def test_shell_execute_runs_approved_ambiguous_command(
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
            stdout="approved",
            stderr="",
        )

    monkeypatch.setattr("awesome_agent.tools.shell.run_process", fake_run_process)

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
        )


@pytest.mark.asyncio
async def test_shell_execute_reports_failed_and_truncated_output(
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
            stdout="x" * 1_100,
            stderr="y" * 1_200,
            timed_out=True,
        )

    monkeypatch.setattr("awesome_agent.tools.shell.run_process", fake_run_process)

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
        )
