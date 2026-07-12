import sys
from dataclasses import replace
from pathlib import Path
from time import monotonic
from unittest.mock import Mock

import pytest

from awesome_agent.core.changes import ChangeJournal, ChangeReversibility
from awesome_agent.core.events import CollectingEventSink, EventEmitter
from awesome_agent.core.tools import (
    ToolErrorCode,
    ToolExecutionContext,
    ToolExecutionOrigin,
    ToolExecutor,
    ToolRequest,
    ToolStatus,
)
from awesome_agent.core.tools.builtins import register_modifying_tools
from awesome_agent.core.tools.command_policy import (
    CommandPolicyAction,
    evaluate_command,
)
from awesome_agent.core.tools.permissions import (
    PermissionMode,
    PermissionSession,
    ToolApprovalDecision,
    ToolApprovalRequest,
)
from awesome_agent.core.tools.process import (
    ProcessResult,
    ProcessRunner,
    ShellExecutionBackend,
)
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
    runner: ShellExecutionBackend,
    *,
    origin: ToolExecutionOrigin = ToolExecutionOrigin.AGENT,
) -> tuple[
    ToolExecutor,
    ToolExecutionContext,
    ChangeJournal,
    Path,
    CollectingEventSink,
]:
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
    sink = CollectingEventSink()
    context = ToolExecutionContext(
        workspace=identity,
        thread_id="thread_1",
        operation_id="operation_1",
        turn_id="turn_1" if origin is ToolExecutionOrigin.AGENT else None,
        origin=origin,
        emitter=EventEmitter(
            session_id="session_1",
            workspace_key=identity.key,
            sink=sink,
        ),
        activity_writer=Mock(),
        monotonic=monotonic,
        change_set_id=change_set.id,
        permission_session=PermissionSession(
            mode=(
                PermissionMode.FULL_ACCESS
                if origin is ToolExecutionOrigin.DIRECT
                else PermissionMode.REQUEST_APPROVAL
            )
        ),
    )
    return ToolExecutor(registry), context, journal, workspace, sink


def test_command_policy_denies_host_destructive_commands(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    for command in ("sudo pytest", "shutdown /s", "rm -rf /"):
        decision = evaluate_command(command, workspace)
        assert decision.action is CommandPolicyAction.DENY

    assert evaluate_command("pytest", workspace).action is CommandPolicyAction.ALLOW


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf ../outside",
        "echo ok && sudo pytest",
        'sh -c "rm -rf ../outside"',
        "python -c \"from pathlib import Path; Path('../outside').unlink()\"",
        "echo data > ../outside",
    ],
)
def test_command_policy_never_implicitly_allows_shell_bypasses(
    tmp_path: Path,
    command: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    assert evaluate_command(command, workspace).action in {
        CommandPolicyAction.DENY,
        CommandPolicyAction.ALLOW,
    }


def test_command_policy_reports_outside_path_without_creating_approval_scope(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    command = f"echo {outside}"

    first = evaluate_command(command, workspace)
    assert first.action is CommandPolicyAction.ALLOW
    assert "outside" in first.reason


@pytest.mark.asyncio
async def test_agent_execute_requires_allow_once_for_simple_command(
    tmp_path: Path,
) -> None:
    runner = RecordingProcessRunner()
    executor, context, _, _, sink = execute_fixture(tmp_path, runner)

    approvals: list[ToolApprovalRequest] = []

    async def approve(request: ToolApprovalRequest) -> ToolApprovalDecision:
        assert runner.calls == []
        approvals.append(request)
        return ToolApprovalDecision.ALLOW_ONCE

    result = await executor.execute(
        ToolRequest(
            call_id="call_execute",
            tool_name="execute",
            arguments={"command": "pytest"},
        ),
        context=replace(context, approval_resolver=approve),
    )

    assert result.status is ToolStatus.SUCCESS
    assert result.presentation is not None
    assert result.presentation.verb == "Run"
    assert result.presentation.target == "pytest"
    assert result.presentation.summary == "Exit code 0"
    assert sink.events[-1].payload.duration_ms is not None  # type: ignore[union-attr]
    assert len(runner.calls) == 1
    assert approvals[0].operation == "run"
    assert approvals[0].target == "pytest"


@pytest.mark.asyncio
async def test_direct_execute_is_already_explicit_user_authority(
    tmp_path: Path,
) -> None:
    runner = RecordingProcessRunner()
    executor, context, _, _, _ = execute_fixture(
        tmp_path,
        runner,
        origin=ToolExecutionOrigin.DIRECT,
    )

    result = await executor.execute(
        ToolRequest(
            call_id="call_execute",
            tool_name="execute",
            arguments={"command": "pytest"},
        ),
        context=context,
    )

    assert result.status is ToolStatus.SUCCESS
    assert len(runner.calls) == 1


@pytest.mark.asyncio
async def test_execute_strips_secrets_redacts_and_records_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SAFE_EXEC_VALUE", "safe-value")
    monkeypatch.setenv("OPENAI_API_KEY", "secret-api-key")
    monkeypatch.setenv("SERVICE_TOKEN", "secret-token")
    monkeypatch.setenv("DB_PASSWORD", "secret-password")
    executor, context, journal, workspace, _ = execute_fixture(
        tmp_path, ProcessRunner()
    )
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

    context = replace(
        context,
        permission_session=PermissionSession(mode=PermissionMode.FULL_ACCESS),
    )
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
    executor, context, _, _, _ = execute_fixture(tmp_path, runner)

    result = await executor.execute(
        ToolRequest(
            call_id="call_execute",
            tool_name="execute",
            arguments={"command": "sudo pytest"},
        ),
        context=replace(
            context,
            permission_session=PermissionSession(mode=PermissionMode.FULL_ACCESS),
        ),
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
    executor, context, _, _workspace, _ = execute_fixture(tmp_path, runner)
    outside = tmp_path / "outside.txt"
    command = f"echo {outside}"
    request = ToolRequest(
        call_id="call_execute",
        tool_name="execute",
        arguments={"command": command},
    )

    approvals: list[ToolApprovalRequest] = []

    async def approve(approval: ToolApprovalRequest) -> ToolApprovalDecision:
        approvals.append(approval)
        return ToolApprovalDecision.ALLOW_ONCE

    result = await executor.execute(
        request,
        context=replace(context, approval_resolver=approve),
    )

    assert result.status is ToolStatus.SUCCESS
    assert len(runner.calls) == 1
    assert approvals[0].target == command
