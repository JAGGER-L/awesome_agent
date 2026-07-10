import sys
from dataclasses import replace
from pathlib import Path

import pytest

from awesome_agent.core.changes import ChangeJournal, ChangeReversibility
from awesome_agent.core.events import CollectingEventSink, EventEmitter
from awesome_agent.core.tools import (
    ToolErrorCode,
    ToolExecutionContext,
    ToolExecutor,
    ToolRequest,
    ToolStatus,
)
from awesome_agent.core.tools.builtins import register_modifying_tools
from awesome_agent.core.tools.command_policy import (
    CommandPolicyAction,
    InteractionRequired,
    evaluate_command,
)
from awesome_agent.core.tools.process import ProcessResult, ProcessRunner
from awesome_agent.core.tools.registry import ToolRegistry
from awesome_agent.core.workspace import resolve_workspace
from awesome_agent.storage.changes import FileChangeBlobStore, SQLiteChangeSetStore


class RecordingProcessRunner(ProcessRunner):
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], Path, dict[str, str]]] = []

    async def run(
        self,
        *,
        argv: list[str],
        cwd: Path,
        environment: dict[str, str],
        timeout_seconds: float,
        max_output_chars: int,
    ) -> ProcessResult:
        self.calls.append((argv, cwd, environment))
        return ProcessResult(
            exit_code=0,
            stdout="ok",
            stderr="",
            timed_out=False,
            stdout_truncated=False,
            stderr_truncated=False,
            duration_ms=1.0,
        )


def execute_fixture(
    tmp_path: Path,
    runner: ProcessRunner,
) -> tuple[ToolExecutor, ToolExecutionContext, ChangeJournal, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    identity = resolve_workspace(workspace)
    journal = ChangeJournal(
        SQLiteChangeSetStore(tmp_path / "application.db"),
        FileChangeBlobStore(tmp_path / "change-journal"),
        identity,
    )
    change_set = journal.begin(
        session_id="session_1",
        turn_id="turn_1",
        workspace=identity,
    )
    registry = ToolRegistry()
    register_modifying_tools(registry, journal, runner)
    context = ToolExecutionContext(
        workspace=identity,
        operation_id="operation_1",
        turn_id="turn_1",
        emitter=EventEmitter(session_id="session_1", sink=CollectingEventSink()),
        change_set_id=change_set.id,
    )
    return ToolExecutor(registry), context, journal, workspace


def test_command_policy_denies_host_destructive_commands(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    for command in ("sudo pytest", "shutdown /s", "rm -rf /"):
        decision = evaluate_command(command, workspace)
        assert decision.action is CommandPolicyAction.DENY

    assert evaluate_command("pytest", workspace).action is CommandPolicyAction.ALLOW


def test_command_policy_returns_stable_scope_for_outside_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    command = f"echo {outside}"

    first = evaluate_command(command, workspace)
    second = evaluate_command(command, workspace)

    assert first.action is CommandPolicyAction.INTERACTION_REQUIRED
    assert first.scope == second.scope
    assert first.scope is not None


@pytest.mark.asyncio
async def test_execute_strips_secrets_redacts_and_records_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SAFE_EXEC_VALUE", "safe-value")
    monkeypatch.setenv("OPENAI_API_KEY", "secret-api-key")
    monkeypatch.setenv("SERVICE_TOKEN", "secret-token")
    monkeypatch.setenv("DB_PASSWORD", "secret-password")
    executor, context, journal, workspace = execute_fixture(tmp_path, ProcessRunner())
    subdirectory = workspace / "subdirectory"
    subdirectory.mkdir()
    script = (
        "import os;"
        "print(os.getcwd());"
        "print(os.getenv('SAFE_EXEC_VALUE'));"
        "print(os.getenv('OPENAI_API_KEY'));"
        "print(os.getenv('SERVICE_TOKEN'));"
        "print(os.getenv('DB_PASSWORD'));"
        "print('TOKEN=abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG')"
    )
    command = f'"{sys.executable}" -c "{script}"'

    result = await executor.execute(
        ToolRequest(
            call_id="call_execute",
            tool_name="execute",
            arguments={"command": command, "cwd": "subdirectory"},
        ),
        context=context,
    )
    sealed = journal.seal(context.change_set_id or "")

    assert result.status is ToolStatus.SUCCESS
    assert str(subdirectory) in result.content
    assert "safe-value" in result.content
    assert "secret-api-key" not in result.content
    assert "secret-token" not in result.content
    assert "secret-password" not in result.content
    assert "[REDACTED:token]" in result.content
    assert result.metadata["exit_code"] == 0
    assert result.metadata["duration_ms"] is not None
    assert sealed.reversibility is ChangeReversibility.NONE


@pytest.mark.asyncio
async def test_execute_hard_denial_never_starts_process(tmp_path: Path) -> None:
    runner = RecordingProcessRunner()
    executor, context, _, _ = execute_fixture(tmp_path, runner)

    result = await executor.execute(
        ToolRequest(
            call_id="call_execute",
            tool_name="execute",
            arguments={"command": "sudo pytest"},
        ),
        context=context,
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.PERMISSION_DENIED
    assert runner.calls == []


@pytest.mark.asyncio
async def test_execute_outside_path_requires_matching_allow_once_scope(
    tmp_path: Path,
) -> None:
    runner = RecordingProcessRunner()
    executor, context, _, workspace = execute_fixture(tmp_path, runner)
    outside = tmp_path / "outside.txt"
    command = f"echo {outside}"
    decision = evaluate_command(command, workspace)
    assert decision.scope is not None
    request = ToolRequest(
        call_id="call_execute",
        tool_name="execute",
        arguments={"command": command},
    )

    with pytest.raises(InteractionRequired) as captured:
        await executor.execute(request, context=context)
    assert captured.value.scope == decision.scope
    assert runner.calls == []

    allowed = replace(
        context,
        allowed_interaction_scopes=frozenset({decision.scope}),
    )
    result = await executor.execute(request, context=allowed)

    assert result.status is ToolStatus.SUCCESS
    assert len(runner.calls) == 1
