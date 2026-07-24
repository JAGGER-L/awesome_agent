import asyncio
import base64
import sys
from dataclasses import replace
from pathlib import Path
from time import monotonic
from typing import cast
from unittest.mock import Mock

import pytest

import awesome_agent.core.tools.builtins.execute as execute_module
import awesome_agent.core.tools.executor as executor_module
from awesome_agent.core.changes import ChangeJournal, ChangeReversibility
from awesome_agent.core.events import CollectingEventSink, EventEmitter, EventType
from awesome_agent.core.tools import (
    ToolErrorCode,
    ToolExecutionContext,
    ToolExecutionOrigin,
    ToolExecutor,
    ToolInvariantError,
    ToolRequest,
    ToolStatus,
)
from awesome_agent.core.tools.builtins import register_modifying_tools
from awesome_agent.core.tools.builtins.execute import (
    ExecuteArguments,
    resolve_execute_timeout,
)
from awesome_agent.core.tools.command_policy import (
    CommandPolicyAction,
    ShellDialect,
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


def _assert_one_activity(context: ToolExecutionContext) -> None:
    writer = cast(Mock, context.activity_writer)
    assert writer.finalize.call_count == 1


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


class DelayedProcessRunner(RecordingProcessRunner):
    async def run(
        self,
        *,
        argv: list[str],
        cwd: Path,
        environment: dict[str, str],
        timeout_seconds: float,
        max_output_chars: int,
    ) -> ProcessResult:
        await asyncio.sleep(0.05)
        return await super().run(
            argv=argv,
            cwd=cwd,
            environment=environment,
            timeout_seconds=timeout_seconds,
            max_output_chars=max_output_chars,
        )


class FailingProcessRunner(RecordingProcessRunner):
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
        raise RuntimeError("backend failed after execution was attempted")


class TimedOutProcessRunner(RecordingProcessRunner):
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
            exit_code=-1,
            stdout="partial",
            stderr="",
            timed_out=True,
            stdout_truncated=False,
            stderr_truncated=False,
            duration_ms=timeout_seconds * 1_000,
        )


class WaitingProcessRunner(RecordingProcessRunner):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

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
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("cancelled process backend resumed")


class CancellationSuppressingProcessRunner(RecordingProcessRunner):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.cancellation_seen = asyncio.Event()
        self.late_returned = asyncio.Event()

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
        self.started.set()
        try:
            await asyncio.Event().wait()
            raise AssertionError("cancellation-suppressing backend resumed")
        except asyncio.CancelledError:
            self.cancellation_seen.set()
            await asyncio.sleep(0.05)
            self.late_returned.set()
            return ProcessResult(
                exit_code=0,
                stdout="late success",
                stderr="",
                timed_out=False,
                stdout_truncated=False,
                stderr_truncated=False,
                duration_ms=50.0,
            )


def execute_fixture(
    tmp_path: Path,
    runner: ShellExecutionBackend,
    *,
    origin: ToolExecutionOrigin = ToolExecutionOrigin.AGENT,
    executor_timeout_seconds: float = 30.0,
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
    return (
        ToolExecutor(registry, timeout_seconds=executor_timeout_seconds),
        context,
        journal,
        workspace,
        sink,
    )


def _assert_one_terminal_event(sink: CollectingEventSink) -> None:
    terminal = [
        event for event in sink.events if event.event_type is not EventType.TOOL_STARTED
    ]
    assert len(terminal) == 1


def test_command_policy_denies_host_destructive_commands(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    for command in ("sudo pytest", "shutdown /s", "rm -rf /"):
        decision = evaluate_command(
            command,
            dialect=ShellDialect.POSIX,
            cwd=workspace,
            workspace=workspace,
        )
        assert decision.action is CommandPolicyAction.DENY

    assert (
        evaluate_command(
            "pytest",
            dialect=ShellDialect.POSIX,
            cwd=workspace,
            workspace=workspace,
        ).action
        is CommandPolicyAction.ALLOW
    )


def test_execute_total_timeout_includes_bounded_cleanup_budget() -> None:
    arguments = ExecuteArguments(command="pytest", timeout_seconds=600)

    assert resolve_execute_timeout(arguments) == 610


@pytest.mark.parametrize(
    ("dialect", "command"),
    [
        (ShellDialect.CMD, "cmd /c shutdown.exe /s /t 0"),
        (
            ShellDialect.CMD,
            'C:\\Windows\\System32\\cmd.exe /d /s /c "shutdown /s /t 0"',
        ),
        (
            ShellDialect.CMD,
            'powershell.exe -NoProfile -Command "Remove-Item -Recurse -Force C:\\\\"',
        ),
        (ShellDialect.POSIX, "echo ok && /usr/bin/sudo pytest"),
        (ShellDialect.POSIX, "sh -c 'rm -rf /'"),
        (ShellDialect.POSIX, "/bin/bash -lc 'rm -rf .'"),
        (ShellDialect.POSIX, "exec rm -rf ."),
        (ShellDialect.POSIX, "rm -rf /tmp/.."),
        (ShellDialect.POSIX, "time -f elapsed sudo pytest"),
        (ShellDialect.POSIX, "xargs -p shutdown -h now"),
        (ShellDialect.POSIX, "xargs -a commands.txt shutdown -h now"),
        (ShellDialect.POSIX, "eval 'rm -rf .'"),
        (ShellDialect.POSIX, "env --split-string='exec rm -rf .'"),
        (
            ShellDialect.POSIX,
            "python -c \"import os; os.system('shutdown /s /t 0')\"",
        ),
        (
            ShellDialect.POSIX,
            "python3 -c \"import subprocess; subprocess.run(['shutdown', '/s'])\"",
        ),
        (
            ShellDialect.POSIX,
            "python3 -c \"import subprocess; subprocess.run(args=['shutdown', '/s'])\"",
        ),
        (
            ShellDialect.POSIX,
            "python3 -c \"import shutil; shutil.rmtree(path='.')\"",
        ),
        (
            ShellDialect.CMD,
            'python -c "import subprocess; '
            "subprocess.run([r'C:\\Program Files\\shutdown.exe', '/s'])\"",
        ),
        (ShellDialect.CMD, "echo 'safe & shutdown /s /t 0'"),
        (ShellDialect.CMD, "echo safe & (shutdown /s /t 0)"),
        (ShellDialect.CMD, "call shutdown /s /t 0"),
        (ShellDialect.CMD, 'start "title" shutdown.exe /s /t 0'),
        (ShellDialect.CMD, "^s^h^u^t^d^o^w^n.exe /s /t 0"),
        (ShellDialect.CMD, "cmd /c echo safe ^& shutdown.exe /s /t 0"),
        (ShellDialect.CMD, "del /s /q C:\\Windows\\.."),
        (ShellDialect.POSIX, "echo data >/dev/sda"),
        (ShellDialect.POSIX, "{ shutdown -h now; }"),
        (ShellDialect.POWERSHELL, "Start-Process pytest -Verb RunAs"),
        (
            ShellDialect.POWERSHELL,
            "& { Remove-Item -Recurse -Force C:\\ }",
        ),
        (ShellDialect.POWERSHELL, "shut`down.exe /s /t 0"),
        (ShellDialect.POWERSHELL, "Invoke-Expression 'shutdown.exe /s /t 0'"),
    ],
)
def test_command_policy_denies_shell_wrappers_and_compound_bypasses(
    tmp_path: Path,
    dialect: ShellDialect,
    command: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    assert (
        evaluate_command(
            command,
            dialect=dialect,
            cwd=workspace,
            workspace=workspace,
        ).action
        is CommandPolicyAction.DENY
    )


def test_command_policy_decodes_powershell_encoded_command(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    payload = "Remove-Item -Recurse -Force C:\\"
    encoded = base64.b64encode(payload.encode("utf-16-le")).decode("ascii")

    decision = evaluate_command(
        f"pwsh -NoProfile -EncodedCommand {encoded}",
        dialect=ShellDialect.CMD,
        cwd=workspace,
        workspace=workspace,
    )

    assert decision.action is CommandPolicyAction.DENY


@pytest.mark.parametrize(
    "command",
    [
        "pytest -k shutdown",
        'rg "rm -rf /" docs',
        "python -c \"print('shutdown /s')\"",
        'echo "sudo pytest"',
        "echo data > ../outside",
        "python -c \"from pathlib import Path; Path('../outside').unlink()\"",
        "eval 'echo \"rm -rf /\"'",
        "env --split-string='echo \"shutdown /s\"'",
        "time -f elapsed pytest -k shutdown",
        "xargs -p echo shutdown",
        "python -c \"import subprocess; subprocess.run(args=['echo', 'shutdown'])\"",
    ],
)
def test_command_policy_does_not_confuse_data_with_executable_code(
    tmp_path: Path,
    command: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    decision = evaluate_command(
        command,
        dialect=ShellDialect.POSIX,
        cwd=workspace,
        workspace=workspace,
    )

    assert decision.action is CommandPolicyAction.ALLOW


def test_command_policy_honors_cmd_escape_for_literal_data(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    decision = evaluate_command(
        "echo safe ^& shutdown /s",
        dialect=ShellDialect.CMD,
        cwd=workspace,
        workspace=workspace,
    )

    assert decision.action is CommandPolicyAction.ALLOW


def test_command_policy_resolves_relative_delete_targets_from_actual_cwd(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    child = workspace / "child"
    child.mkdir()

    assert (
        evaluate_command(
            "rm -rf .",
            dialect=ShellDialect.POSIX,
            cwd=workspace,
            workspace=workspace,
        ).action
        is CommandPolicyAction.DENY
    )
    assert (
        evaluate_command(
            "rm -rf .",
            dialect=ShellDialect.POSIX,
            cwd=child,
            workspace=workspace,
        ).action
        is CommandPolicyAction.ALLOW
    )


def test_python_subprocess_policy_uses_literal_child_cwd(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    child = workspace / "child"
    child.mkdir()
    command = (
        "python -c \"import subprocess; subprocess.run(['rm', '-rf', '.'], cwd='..')\""
    )

    decision = evaluate_command(
        command,
        dialect=ShellDialect.POSIX,
        cwd=child,
        workspace=workspace,
    )

    assert decision.action is CommandPolicyAction.DENY


@pytest.mark.parametrize(
    ("dialect", "command"),
    [
        (ShellDialect.POSIX, "rm -rf $PWD"),
        (ShellDialect.CMD, "rmdir /s /q %CD%"),
        (ShellDialect.POWERSHELL, "Remove-Item -Recurse -Force $PWD"),
    ],
)
def test_command_policy_denies_known_current_directory_expansions(
    tmp_path: Path,
    dialect: ShellDialect,
    command: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    decision = evaluate_command(
        command,
        dialect=dialect,
        cwd=workspace,
        workspace=workspace,
    )

    assert decision.action is CommandPolicyAction.DENY


def test_command_policy_reports_outside_path_without_creating_approval_scope(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    command = f"echo {outside.as_posix()}"

    first = evaluate_command(
        command,
        dialect=ShellDialect.POSIX,
        cwd=workspace,
        workspace=workspace,
    )
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


_HARD_DENY_REGRESSION_CASES = [
    (ShellDialect.POSIX, "exec rm -rf ."),
    (ShellDialect.POSIX, "rm -rf /tmp/.."),
    (ShellDialect.POSIX, "time -f elapsed sudo pytest"),
    (ShellDialect.POSIX, "xargs -p shutdown -h now"),
    (ShellDialect.POSIX, "xargs -a commands.txt shutdown -h now"),
    (ShellDialect.POSIX, "eval 'rm -rf .'"),
    (ShellDialect.POSIX, "env --split-string='exec rm -rf .'"),
    (
        ShellDialect.POSIX,
        "python3 -c \"import shutil; shutil.rmtree(path='.')\"",
    ),
    (ShellDialect.CMD, 'start "title" shutdown.exe /s /t 0'),
    (ShellDialect.CMD, "^s^h^u^t^d^o^w^n.exe /s /t 0"),
    (ShellDialect.CMD, "cmd /c echo safe ^& shutdown.exe /s /t 0"),
    (ShellDialect.POWERSHELL, "shut`down.exe /s /t 0"),
    (ShellDialect.POWERSHELL, "Invoke-Expression 'shutdown.exe /s /t 0'"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("dialect", "command"), _HARD_DENY_REGRESSION_CASES)
@pytest.mark.parametrize("mode", list(PermissionMode))
async def test_execute_hard_denial_never_starts_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: PermissionMode,
    dialect: ShellDialect,
    command: str,
) -> None:
    monkeypatch.setattr(executor_module, "host_shell_dialect", lambda: dialect)
    runner = RecordingProcessRunner()
    executor, context, journal, _, sink = execute_fixture(tmp_path, runner)

    result = await executor.execute(
        ToolRequest(
            call_id="call_execute",
            tool_name="execute",
            arguments={"command": command},
        ),
        context=replace(
            context,
            permission_session=PermissionSession(mode=mode),
        ),
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.PERMISSION_DENIED
    assert runner.calls == []
    assert journal.seal(context.change_set_id or "").execute == []
    _assert_one_terminal_event(sink)
    _assert_one_activity(context)


@pytest.mark.asyncio
@pytest.mark.parametrize(("dialect", "command"), _HARD_DENY_REGRESSION_CASES)
async def test_direct_execute_cannot_bypass_hard_denial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dialect: ShellDialect,
    command: str,
) -> None:
    monkeypatch.setattr(executor_module, "host_shell_dialect", lambda: dialect)
    runner = RecordingProcessRunner()
    executor, context, journal, _, _ = execute_fixture(
        tmp_path,
        runner,
        origin=ToolExecutionOrigin.DIRECT,
    )

    result = await executor.execute(
        ToolRequest(
            call_id="call_execute",
            tool_name="execute",
            arguments={"command": command},
        ),
        context=context,
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.PERMISSION_DENIED
    assert runner.calls == []
    assert journal.seal(context.change_set_id or "").execute == []


@pytest.mark.asyncio
async def test_execute_invalid_arguments_do_not_record_an_attempt(
    tmp_path: Path,
) -> None:
    runner = RecordingProcessRunner()
    executor, context, journal, _, sink = execute_fixture(tmp_path, runner)

    result = await executor.execute(
        ToolRequest(
            call_id="call_execute",
            tool_name="execute",
            arguments={"command": "pytest", "timeout_seconds": 0},
        ),
        context=context,
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.INVALID_ARGUMENTS
    assert runner.calls == []
    assert journal.seal(context.change_set_id or "").execute == []
    _assert_one_terminal_event(sink)
    _assert_one_activity(context)


@pytest.mark.asyncio
async def test_execute_permission_denial_does_not_record_an_attempt(
    tmp_path: Path,
) -> None:
    runner = RecordingProcessRunner()
    executor, context, journal, _, sink = execute_fixture(tmp_path, runner)

    async def deny(_: ToolApprovalRequest) -> ToolApprovalDecision:
        return ToolApprovalDecision.DENY

    result = await executor.execute(
        ToolRequest(
            call_id="call_execute",
            tool_name="execute",
            arguments={"command": "pytest"},
        ),
        context=replace(context, approval_resolver=deny),
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.PERMISSION_DENIED
    assert runner.calls == []
    assert journal.seal(context.change_set_id or "").execute == []
    _assert_one_terminal_event(sink)
    _assert_one_activity(context)


@pytest.mark.asyncio
async def test_execute_uses_requested_timeout_instead_of_global_default(
    tmp_path: Path,
) -> None:
    runner = DelayedProcessRunner()
    executor, context, _, _, _ = execute_fixture(
        tmp_path,
        runner,
        executor_timeout_seconds=0.01,
    )

    result = await executor.execute(
        ToolRequest(
            call_id="call_execute",
            tool_name="execute",
            arguments={"command": "pytest", "timeout_seconds": 0.02},
        ),
        context=replace(
            context,
            permission_session=PermissionSession(mode=PermissionMode.FULL_ACCESS),
        ),
    )

    assert result.status is ToolStatus.SUCCESS
    assert len(runner.calls) == 1


@pytest.mark.asyncio
async def test_execute_outer_deadline_rejects_backend_late_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(execute_module, "_EXECUTE_CLEANUP_BUDGET_SECONDS", 0.01)
    runner = CancellationSuppressingProcessRunner()
    executor, context, journal, _, sink = execute_fixture(tmp_path, runner)

    started = monotonic()
    result = await executor.execute(
        ToolRequest(
            call_id="call_execute",
            tool_name="execute",
            arguments={"command": "pytest", "timeout_seconds": 0.01},
        ),
        context=replace(
            context,
            permission_session=PermissionSession(mode=PermissionMode.FULL_ACCESS),
        ),
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.TIMEOUT
    assert monotonic() - started < 0.1
    await asyncio.wait_for(runner.cancellation_seen.wait(), timeout=1)
    assert not runner.late_returned.is_set()
    await asyncio.wait_for(runner.late_returned.wait(), timeout=1)
    assert [
        item.command for item in journal.seal(context.change_set_id or "").execute
    ] == ["pytest"]
    _assert_one_terminal_event(sink)
    _assert_one_activity(context)


@pytest.mark.asyncio
async def test_execute_records_attempt_before_backend_failure(tmp_path: Path) -> None:
    runner = FailingProcessRunner()
    executor, context, journal, _, sink = execute_fixture(tmp_path, runner)

    with pytest.raises(ToolInvariantError, match="Unexpected tool handler failure"):
        await executor.execute(
            ToolRequest(
                call_id="call_execute",
                tool_name="execute",
                arguments={"command": "pytest"},
            ),
            context=replace(
                context,
                permission_session=PermissionSession(mode=PermissionMode.FULL_ACCESS),
            ),
        )

    sealed = journal.seal(context.change_set_id or "")
    assert [item.command for item in sealed.execute] == ["pytest"]
    assert len(runner.calls) == 1
    _assert_one_terminal_event(sink)
    _assert_one_activity(context)


@pytest.mark.asyncio
async def test_execute_timeout_records_one_attempt_and_terminal_event(
    tmp_path: Path,
) -> None:
    runner = TimedOutProcessRunner()
    executor, context, journal, _, sink = execute_fixture(tmp_path, runner)

    result = await executor.execute(
        ToolRequest(
            call_id="call_execute",
            tool_name="execute",
            arguments={"command": "pytest", "timeout_seconds": 0.01},
        ),
        context=replace(
            context,
            permission_session=PermissionSession(mode=PermissionMode.FULL_ACCESS),
        ),
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.TIMEOUT
    sealed = journal.seal(context.change_set_id or "")
    assert [item.command for item in sealed.execute] == ["pytest"]
    _assert_one_terminal_event(sink)
    _assert_one_activity(context)


@pytest.mark.asyncio
async def test_execute_records_cancelled_attempt_once(tmp_path: Path) -> None:
    runner = WaitingProcessRunner()
    executor, context, journal, _, sink = execute_fixture(tmp_path, runner)
    task = asyncio.create_task(
        executor.execute(
            ToolRequest(
                call_id="call_execute",
                tool_name="execute",
                arguments={"command": "pytest"},
            ),
            context=replace(
                context,
                permission_session=PermissionSession(mode=PermissionMode.FULL_ACCESS),
            ),
        )
    )
    await runner.started.wait()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    sealed = journal.seal(context.change_set_id or "")
    assert [item.command for item in sealed.execute] == ["pytest"]
    event_types = [event.event_type.value for event in sink.events]
    assert event_types.count("tool.cancelled") == 1
    _assert_one_terminal_event(sink)
    _assert_one_activity(context)


@pytest.mark.asyncio
async def test_execute_preserves_user_cancellation_when_backend_returns_late(
    tmp_path: Path,
) -> None:
    runner = CancellationSuppressingProcessRunner()
    executor, context, journal, _, sink = execute_fixture(tmp_path, runner)
    task = asyncio.create_task(
        executor.execute(
            ToolRequest(
                call_id="call_execute",
                tool_name="execute",
                arguments={"command": "pytest"},
            ),
            context=replace(
                context,
                permission_session=PermissionSession(mode=PermissionMode.FULL_ACCESS),
            ),
        )
    )
    await runner.started.wait()

    task.cancel()
    await runner.cancellation_seen.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert runner.cancellation_seen.is_set()
    assert runner.late_returned.is_set()
    assert [
        item.command for item in journal.seal(context.change_set_id or "").execute
    ] == ["pytest"]
    _assert_one_terminal_event(sink)
    _assert_one_activity(context)


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
