import asyncio
import base64
import sqlite3
import sys
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from time import monotonic
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest

import awesome_agent.core.tools.builtins.execute as execute_module
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
    CommandPolicyDecision,
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
from awesome_agent.storage.application_sqlite import ApplicationSQLite
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


@pytest.fixture
async def application_database(tmp_path: Path) -> AsyncIterator[ApplicationSQLite]:
    database = ApplicationSQLite(tmp_path / "application.db")
    await database.initialize()
    try:
        yield database
    finally:
        await database.aclose()


async def execute_fixture(
    tmp_path: Path,
    application_database: ApplicationSQLite,
    runner: ShellExecutionBackend,
    *,
    origin: ToolExecutionOrigin = ToolExecutionOrigin.AGENT,
    executor_timeout_seconds: float = 30.0,
    workspace_name: str = "workspace",
) -> tuple[
    ToolExecutor,
    ToolExecutionContext,
    ChangeJournal,
    Path,
    CollectingEventSink,
]:
    workspace = tmp_path / workspace_name
    workspace.mkdir()
    identity = resolve_workspace(workspace)
    journal = ChangeJournal(
        SQLiteChangeSetStore(application_database),
        FileChangeBlobStore(tmp_path / "change-journal"),
        identity,
    )
    change_set = await journal.begin(
        session_id="session_1",
        turn_id="turn_1",
        workspace=identity,
    )
    registry = ToolRegistry()
    register_modifying_tools(registry, journal, runner, workspace=identity)
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
        activity_writer=AsyncMock(),
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


_DANGEROUS_POWERSHELL_SOURCE = "Remove-Item -Recurse -Force C:\\"
_DANGEROUS_POWERSHELL_ENCODED = base64.b64encode(
    _DANGEROUS_POWERSHELL_SOURCE.encode("utf-16-le")
).decode("ascii")
_BENIGN_POWERSHELL_SOURCE = "Write-Output 'Remove-Item -Recurse -Force C:\\'"
_BENIGN_POWERSHELL_ENCODED = base64.b64encode(
    _BENIGN_POWERSHELL_SOURCE.encode("utf-16-le")
).decode("ascii")


@pytest.mark.parametrize(
    ("dialect", "command"),
    [
        (
            ShellDialect.CMD,
            f"powershell.exe -EncodedCom {_DANGEROUS_POWERSHELL_ENCODED}",
        ),
        (ShellDialect.CMD, f"pwsh -EC {_DANGEROUS_POWERSHELL_ENCODED}"),
        (
            ShellDialect.POWERSHELL,
            f"PWSh.ExE -eNcOdEdCoM {_DANGEROUS_POWERSHELL_ENCODED}",
        ),
        (
            ShellDialect.CMD,
            f"powershell.exe /EncodedCom {_DANGEROUS_POWERSHELL_ENCODED}",
        ),
        (ShellDialect.CMD, f"pwsh /EC {_DANGEROUS_POWERSHELL_ENCODED}"),
        (
            ShellDialect.CMD,
            f"pwsh \u2013EC {_DANGEROUS_POWERSHELL_ENCODED}",
        ),
        (
            ShellDialect.POSIX,
            f"pwsh --EncodedCom {_DANGEROUS_POWERSHELL_ENCODED}",
        ),
        (
            ShellDialect.CMD,
            'powershell.exe -Com "Remove-Item -Recurse -Force C:\\"',
        ),
        (
            ShellDialect.POWERSHELL,
            "pwsh /cOm 'Remove-Item -Recurse -Force C:\\'",
        ),
        (
            ShellDialect.POSIX,
            "pwsh --Com 'Remove-Item -Recurse -Force C:\\'",
        ),
        (
            ShellDialect.CMD,
            f"pwsh -EncodedCommand:{_DANGEROUS_POWERSHELL_ENCODED}",
        ),
        (
            ShellDialect.CMD,
            f"pwsh -EC:{_DANGEROUS_POWERSHELL_ENCODED}",
        ),
        (
            ShellDialect.CMD,
            f"pwsh -EncodedCommand{_DANGEROUS_POWERSHELL_ENCODED}",
        ),
        (
            ShellDialect.CMD,
            f"pwsh -EC{_DANGEROUS_POWERSHELL_ENCODED}",
        ),
        (
            ShellDialect.CMD,
            "pwsh -Command:Remove-Item -Recurse -Force C:\\",
        ),
        (
            ShellDialect.CMD,
            "pwsh -Com:Remove-Item -Recurse -Force C:\\",
        ),
        (
            ShellDialect.CMD,
            "pwsh -CommandRemove-Item -Recurse -Force C:\\",
        ),
        (
            ShellDialect.CMD,
            "pwsh -cRemove-Item -Recurse -Force C:\\",
        ),
        (
            ShellDialect.CMD,
            "pwsh -CWA 'Remove-Item -Recurse -Force C:\\'",
        ),
        (
            ShellDialect.CMD,
            f"pwsh -EC:{_BENIGN_POWERSHELL_ENCODED}",
        ),
        (
            ShellDialect.CMD,
            "pwsh -Command:Write-Output safe",
        ),
        (ShellDialect.CMD, "pwsh -EncodedCom"),
        (ShellDialect.CMD, "pwsh -Com"),
    ],
)
def test_command_policy_denies_powershell_execution_option_bypasses(
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


@pytest.mark.parametrize(
    ("dialect", "command"),
    [
        (
            ShellDialect.CMD,
            f"powershell.exe -EncodedCom {_BENIGN_POWERSHELL_ENCODED}",
        ),
        (ShellDialect.CMD, f"pwsh -EC {_BENIGN_POWERSHELL_ENCODED}"),
        (
            ShellDialect.POWERSHELL,
            f"PWSh.ExE /eNcOdEdCoM {_BENIGN_POWERSHELL_ENCODED}",
        ),
        (
            ShellDialect.CMD,
            f"pwsh \u2014EC {_BENIGN_POWERSHELL_ENCODED}",
        ),
        (
            ShellDialect.POSIX,
            f"pwsh --EncodedCom {_BENIGN_POWERSHELL_ENCODED}",
        ),
        (
            ShellDialect.CMD,
            'powershell.exe -Com "Write-Output safe"',
        ),
        (
            ShellDialect.POWERSHELL,
            "pwsh /cOm 'Write-Output safe'",
        ),
        (
            ShellDialect.POSIX,
            "pwsh --Com 'Write-Output safe'",
        ),
        (
            ShellDialect.CMD,
            'pwsh -Ex Bypass -Com "Write-Output safe"',
        ),
        (
            ShellDialect.CMD,
            'pwsh -EP Bypass -Com "Write-Output safe"',
        ),
    ],
)
def test_command_policy_allows_benign_powershell_execution_prefixes(
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

    assert decision.action is CommandPolicyAction.ALLOW


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


_CWD_TRANSITION_DELETE_CASES = [
    (ShellDialect.CMD, "cd .. && rmdir /s /q awesome_agent"),
    (ShellDialect.CMD, "cd.. && rmdir /s /q awesome_agent"),
    (ShellDialect.CMD, "call cd .. && rd /s /q awesome_agent"),
    (ShellDialect.CMD, 'cmd /c "chdir .. && rd /s /q awesome_agent"'),
    (ShellDialect.POSIX, "cd .. && rm -rf awesome_agent"),
    (ShellDialect.POSIX, "builtin cd .. && rm -rf awesome_agent"),
    (ShellDialect.POSIX, "sh -c 'cd .. && rm -rf awesome_agent'"),
    (ShellDialect.POSIX, "env --chdir .. rm -rf awesome_agent"),
    (ShellDialect.POSIX, "env --chdir=.. rm -rf awesome_agent"),
    (ShellDialect.POSIX, "env -C .. rm -rf awesome_agent"),
    (ShellDialect.POSIX, "env -C.. rm -rf awesome_agent"),
    (
        ShellDialect.POWERSHELL,
        "Set-Location ..; Remove-Item -Recurse awesome_agent",
    ),
    (
        ShellDialect.POWERSHELL,
        "Set-Location .. | Remove-Item -Recurse awesome_agent",
    ),
    (
        ShellDialect.POWERSHELL,
        'pwsh -Command "Set-Location ..; Remove-Item -Recurse awesome_agent"',
    ),
    (
        ShellDialect.POWERSHELL,
        "Invoke-Expression 'Set-Location ..'; Remove-Item -Recurse awesome_agent",
    ),
]


@pytest.mark.parametrize(("dialect", "command"), _CWD_TRANSITION_DELETE_CASES)
def test_command_policy_tracks_cwd_across_compound_segments(
    tmp_path: Path,
    dialect: ShellDialect,
    command: str,
) -> None:
    workspace = tmp_path / "awesome_agent"
    workspace.mkdir()

    decision = evaluate_command(
        command,
        dialect=dialect,
        cwd=workspace,
        workspace=workspace,
    )

    assert decision.action is CommandPolicyAction.DENY


@pytest.mark.parametrize(
    ("dialect", "command"),
    (
        (ShellDialect.CMD, "cd child && echo ok"),
        (ShellDialect.POSIX, "cd child && echo ok"),
        (ShellDialect.POWERSHELL, "Set-Location child; Write-Output ok"),
        (
            ShellDialect.CMD,
            "echo cd .. ^&^& rmdir /s /q awesome_agent",
        ),
        (
            ShellDialect.POSIX,
            "printf '%s\\n' 'cd .. && rm -rf awesome_agent'",
        ),
        (
            ShellDialect.POWERSHELL,
            "Write-Output 'Set-Location ..; Remove-Item -Recurse awesome_agent'",
        ),
        (
            ShellDialect.POSIX,
            "rg 'cd .. && rm -rf awesome_agent' docs",
        ),
    ),
)
def test_command_policy_allows_benign_directory_changes_and_literal_data(
    tmp_path: Path,
    dialect: ShellDialect,
    command: str,
) -> None:
    workspace = tmp_path / "awesome_agent"
    workspace.mkdir()

    decision = evaluate_command(
        command,
        dialect=dialect,
        cwd=workspace,
        workspace=workspace,
    )

    assert decision.action is CommandPolicyAction.ALLOW


_UNINSPECTABLE_EXECUTION_CASES = (
    (ShellDialect.CMD, r"cd .. && del /s /q \*"),
    (ShellDialect.POSIX, "cd .. && rm -rf /*"),
    (
        ShellDialect.POWERSHELL,
        r"Set-Location .. && Remove-Item -Recurse -Force \*",
    ),
    (
        ShellDialect.CMD,
        "python -c\"import shutil; shutil.rmtree('.')\"",
    ),
    (
        ShellDialect.POSIX,
        "python -c\"import shutil; shutil.rmtree('.')\"",
    ),
    (
        ShellDialect.POWERSHELL,
        "python -c\"import shutil; shutil.rmtree('.')\"",
    ),
    (ShellDialect.POSIX, "echo $(rm -rf .)"),
    (ShellDialect.POSIX, "echo `rm -rf .`"),
    (
        ShellDialect.POWERSHELL,
        "Write-Output $(Remove-Item -Recurse -Force .)",
    ),
    (
        ShellDialect.POWERSHELL,
        "Start-Process cmd -ArgumentList '/c','rmdir /s /q .'",
    ),
    (
        ShellDialect.POWERSHELL,
        "Start-Process cmd '/c rmdir /s /q .'",
    ),
    (ShellDialect.POSIX, "env --argv0 harmless rm -rf /"),
    (ShellDialect.POSIX, "env -a harmless rm -rf /"),
    (ShellDialect.CMD, "cmd /cshutdown /s /t 0"),
    (ShellDialect.CMD, "%COMSPEC% /c shutdown /s /t 0"),
    (ShellDialect.POSIX, "$SHELL -c 'rm -rf /'"),
    (ShellDialect.POWERSHELL, "& $env:COMSPEC /c shutdown /s /t 0"),
)


@pytest.mark.parametrize(
    ("dialect", "command"),
    _UNINSPECTABLE_EXECUTION_CASES,
)
def test_command_policy_fails_closed_for_uninspectable_execution_forms(
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


@pytest.mark.parametrize(
    ("dialect", "command"),
    (
        (ShellDialect.CMD, r"echo del /s /q \*"),
        (ShellDialect.POSIX, "printf '%s\\n' 'rm -rf /*'"),
        (ShellDialect.POSIX, "printf '%s\\n' '$(rm -rf /)'"),
        (ShellDialect.POSIX, "printf '%s\\n' \"<(rm -rf /)\""),
        (
            ShellDialect.POWERSHELL,
            r"Write-Output 'Remove-Item -Recurse -Force \*'",
        ),
        (
            ShellDialect.POWERSHELL,
            r"Write-Output '$(Remove-Item -Recurse -Force C:\)'",
        ),
        (ShellDialect.CMD, "python -c\"print('shutdown /s')\""),
        (ShellDialect.POSIX, "python -c\"print('shutdown /s')\""),
        (ShellDialect.POWERSHELL, "python -c\"print('shutdown /s')\""),
        (ShellDialect.POWERSHELL, "Start-Process pytest"),
        (ShellDialect.POWERSHELL, "Start-Process pytest -Wait"),
        (ShellDialect.POSIX, "env --argv0 harmless echo shutdown"),
        (ShellDialect.CMD, "cmd /cecho shutdown /s"),
        (ShellDialect.CMD, "echo %COMSPEC% /c shutdown /s /t 0"),
        (ShellDialect.POSIX, "printf '%s\\n' '$SHELL -c rm -rf /'"),
        (
            ShellDialect.POWERSHELL,
            "Write-Output '$env:COMSPEC /c shutdown /s /t 0'",
        ),
    ),
)
def test_command_policy_keeps_literal_data_and_benign_attached_python(
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

    assert decision.action is CommandPolicyAction.ALLOW


@pytest.mark.parametrize(
    "command",
    (
        "saps pytest -Verb RunAs",
        "SAPS -FilePath pytest -Verb:RunAs",
        "start pytest -Verb RunAs",
    ),
)
def test_command_policy_denies_powershell_start_process_alias_elevation(
    tmp_path: Path,
    command: str,
) -> None:
    workspace = tmp_path / "awesome_agent"
    workspace.mkdir()

    decision = evaluate_command(
        command,
        dialect=ShellDialect.POWERSHELL,
        cwd=workspace,
        workspace=workspace,
    )

    assert decision.action is CommandPolicyAction.DENY


def _nested_powershell(payload: str, depth: int) -> str:
    for _ in range(depth):
        encoded = base64.b64encode(payload.encode("utf-16-le")).decode("ascii")
        payload = f"pwsh -EncodedCommand {encoded}"
    return payload


@pytest.mark.parametrize(
    ("dialect", "command", "reason"),
    (
        (
            ShellDialect.POSIX,
            " && ".join("echo ok" for _ in range(65)),
            "complexity",
        ),
        (
            ShellDialect.POWERSHELL,
            _nested_powershell("Write-Output ok", 9),
            "nesting",
        ),
    ),
    ids=("node-limit", "depth-limit"),
)
def test_command_policy_keeps_bounded_segment_and_wrapper_inspection(
    tmp_path: Path,
    dialect: ShellDialect,
    command: str,
    reason: str,
) -> None:
    workspace = tmp_path / "awesome_agent"
    workspace.mkdir()

    decision = evaluate_command(
        command,
        dialect=dialect,
        cwd=workspace,
        workspace=workspace,
    )

    assert decision.action is CommandPolicyAction.DENY
    assert reason in decision.reason.casefold()


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
    application_database: ApplicationSQLite,
) -> None:
    runner = RecordingProcessRunner()
    executor, context, _, _, sink = await execute_fixture(
        tmp_path, application_database, runner
    )

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
async def test_execute_rechecks_command_policy_immediately_before_runner(
    tmp_path: Path,
    application_database: ApplicationSQLite,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = RecordingProcessRunner()
    executor, context, journal, workspace, sink = await execute_fixture(
        tmp_path,
        application_database,
        runner,
    )
    observed_cwds: list[Path] = []
    decisions = iter(
        (
            CommandPolicyDecision(CommandPolicyAction.ALLOW, "admitted"),
            CommandPolicyDecision(CommandPolicyAction.DENY, "changed policy"),
        )
    )

    def staged_policy(
        command: str,
        *,
        dialect: ShellDialect,
        cwd: Path,
        workspace: Path,
    ) -> CommandPolicyDecision:
        del command, dialect
        observed_cwds.append(cwd)
        assert workspace == context.workspace.canonical_path
        return next(decisions)

    async def approve(_request: ToolApprovalRequest) -> ToolApprovalDecision:
        return ToolApprovalDecision.ALLOW_ONCE

    monkeypatch.setattr(execute_module, "evaluate_command", staged_policy)

    result = await executor.execute(
        ToolRequest(
            call_id="call_double_policy",
            tool_name="execute",
            arguments={"command": "pytest"},
        ),
        context=replace(context, approval_resolver=approve),
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.PERMISSION_DENIED
    assert observed_cwds == [workspace.resolve(), workspace.resolve()]
    assert runner.calls == []
    assert (await journal.seal(context.change_set_id or "")).execute == []
    _assert_one_terminal_event(sink)
    _assert_one_activity(context)


@pytest.mark.asyncio
async def test_execute_approval_preserves_8k_target_with_bounded_prompt_and_audit(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    command = f"echo {'x' * 7_995}"
    assert len(command) == 8_000
    runner = RecordingProcessRunner()
    executor, context, _, _, sink = await execute_fixture(
        tmp_path,
        application_database,
        runner,
    )
    approvals: list[ToolApprovalRequest] = []

    async def deny(request: ToolApprovalRequest) -> ToolApprovalDecision:
        approvals.append(request)
        return ToolApprovalDecision.DENY

    result = await executor.execute(
        ToolRequest(
            call_id="call_long_execute",
            tool_name="execute",
            arguments={"command": command},
        ),
        context=replace(context, approval_resolver=deny),
    )

    assert result.status is ToolStatus.ERROR
    assert len(approvals) == 1
    assert approvals[0].target == command
    assert len(approvals[0].prompt) == 2_000
    assert approvals[0].prompt.endswith("\u2026")
    assert sink.events[0].payload.target == command[:2_000]  # type: ignore[union-attr]
    writer = cast(AsyncMock, context.activity_writer)
    activity = writer.finalize.await_args.args[0]
    assert activity.input_summary == "arguments: command"
    assert command not in activity.input_summary
    assert runner.calls == []


@pytest.mark.asyncio
async def test_benign_powershell_prefix_uses_normal_approval_flow(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    command = 'pwsh -Com "Write-Output safe"'
    runner = RecordingProcessRunner()
    executor, context, _, _, _ = await execute_fixture(
        tmp_path, application_database, runner
    )
    approvals: list[ToolApprovalRequest] = []

    async def approve(request: ToolApprovalRequest) -> ToolApprovalDecision:
        assert runner.calls == []
        approvals.append(request)
        return ToolApprovalDecision.ALLOW_ONCE

    result = await executor.execute(
        ToolRequest(
            call_id="call_powershell_prefix",
            tool_name="execute",
            arguments={"command": command},
        ),
        context=replace(context, approval_resolver=approve),
    )

    assert result.status is ToolStatus.SUCCESS
    assert len(approvals) == 1
    assert approvals[0].target == command
    assert len(runner.calls) == 1


@pytest.mark.asyncio
async def test_direct_execute_is_already_explicit_user_authority(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    runner = RecordingProcessRunner()
    executor, context, _, _, _ = await execute_fixture(
        tmp_path,
        application_database,
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
    application_database: ApplicationSQLite,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SAFE_EXEC_VALUE", "safe-value")
    monkeypatch.setenv("OPENAI_API_KEY", "secret-api-key")
    monkeypatch.setenv("SERVICE_TOKEN", "secret-token")
    monkeypatch.setenv("DB_PASSWORD", "secret-password")
    executor, context, journal, workspace, _ = await execute_fixture(
        tmp_path, application_database, ProcessRunner()
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
    sealed = await journal.seal(context.change_set_id or "")

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
    (
        ShellDialect.CMD,
        f"pwsh -EC {_DANGEROUS_POWERSHELL_ENCODED}",
    ),
    (ShellDialect.POWERSHELL, "shut`down.exe /s /t 0"),
    (ShellDialect.POWERSHELL, "Invoke-Expression 'shutdown.exe /s /t 0'"),
    *_CWD_TRANSITION_DELETE_CASES,
    (ShellDialect.POWERSHELL, "saps pytest -Verb RunAs"),
    (ShellDialect.POWERSHELL, "start pytest -Verb RunAs"),
    *_UNINSPECTABLE_EXECUTION_CASES,
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("dialect", "command"), _HARD_DENY_REGRESSION_CASES)
@pytest.mark.parametrize("mode", list(PermissionMode))
async def test_execute_hard_denial_never_starts_process(
    tmp_path: Path,
    application_database: ApplicationSQLite,
    monkeypatch: pytest.MonkeyPatch,
    mode: PermissionMode,
    dialect: ShellDialect,
    command: str,
) -> None:
    monkeypatch.setattr(execute_module, "host_shell_dialect", lambda: dialect)
    runner = RecordingProcessRunner()
    executor, context, journal, _, sink = await execute_fixture(
        tmp_path,
        application_database,
        runner,
        workspace_name=(
            "awesome_agent"
            if (dialect, command) in _CWD_TRANSITION_DELETE_CASES
            else "workspace"
        ),
    )

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
    assert (await journal.seal(context.change_set_id or "")).execute == []
    _assert_one_terminal_event(sink)
    _assert_one_activity(context)


@pytest.mark.asyncio
@pytest.mark.parametrize(("dialect", "command"), _HARD_DENY_REGRESSION_CASES)
async def test_direct_execute_cannot_bypass_hard_denial(
    tmp_path: Path,
    application_database: ApplicationSQLite,
    monkeypatch: pytest.MonkeyPatch,
    dialect: ShellDialect,
    command: str,
) -> None:
    monkeypatch.setattr(execute_module, "host_shell_dialect", lambda: dialect)
    runner = RecordingProcessRunner()
    executor, context, journal, _, _ = await execute_fixture(
        tmp_path,
        application_database,
        runner,
        origin=ToolExecutionOrigin.DIRECT,
        workspace_name=(
            "awesome_agent"
            if (dialect, command) in _CWD_TRANSITION_DELETE_CASES
            else "workspace"
        ),
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
    assert (await journal.seal(context.change_set_id or "")).execute == []


@pytest.mark.asyncio
async def test_execute_invalid_arguments_do_not_record_an_attempt(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    runner = RecordingProcessRunner()
    executor, context, journal, _, sink = await execute_fixture(
        tmp_path, application_database, runner
    )

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
    assert (await journal.seal(context.change_set_id or "")).execute == []
    _assert_one_terminal_event(sink)
    _assert_one_activity(context)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("cwd", "error_code"),
    [
        ("missing", ToolErrorCode.NOT_FOUND),
        ("../outside", ToolErrorCode.WORKSPACE_ESCAPE),
    ],
)
async def test_execute_invalid_cwd_is_not_described_or_approved(
    tmp_path: Path,
    application_database: ApplicationSQLite,
    cwd: str,
    error_code: ToolErrorCode,
) -> None:
    runner = RecordingProcessRunner()
    executor, context, _, _, sink = await execute_fixture(
        tmp_path,
        application_database,
        runner,
    )
    approvals = 0

    async def approve(_request: ToolApprovalRequest) -> ToolApprovalDecision:
        nonlocal approvals
        approvals += 1
        return ToolApprovalDecision.ALLOW_ONCE

    result = await executor.execute(
        ToolRequest(
            call_id="call_invalid_cwd",
            tool_name="execute",
            arguments={"command": "pytest", "cwd": cwd},
        ),
        context=replace(context, approval_resolver=approve),
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is error_code
    assert approvals == 0
    assert runner.calls == []
    assert sink.events[0].payload.target is None  # type: ignore[union-attr]
    _assert_one_terminal_event(sink)
    _assert_one_activity(context)


@pytest.mark.asyncio
async def test_execute_symlink_cwd_is_not_described_or_approved(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    runner = RecordingProcessRunner()
    executor, context, _, workspace, sink = await execute_fixture(
        tmp_path,
        application_database,
        runner,
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (workspace / "linked").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlink creation is not available")
    approvals = 0

    async def approve(_request: ToolApprovalRequest) -> ToolApprovalDecision:
        nonlocal approvals
        approvals += 1
        return ToolApprovalDecision.ALLOW_ONCE

    result = await executor.execute(
        ToolRequest(
            call_id="call_linked_cwd",
            tool_name="execute",
            arguments={"command": "pytest", "cwd": "linked"},
        ),
        context=replace(context, approval_resolver=approve),
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.PERMISSION_DENIED
    assert approvals == 0
    assert runner.calls == []
    assert sink.events[0].payload.target is None  # type: ignore[union-attr]
    _assert_one_terminal_event(sink)
    _assert_one_activity(context)


@pytest.mark.asyncio
async def test_execute_permission_denial_does_not_record_an_attempt(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    runner = RecordingProcessRunner()
    executor, context, journal, _, sink = await execute_fixture(
        tmp_path, application_database, runner
    )

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
    assert (await journal.seal(context.change_set_id or "")).execute == []
    _assert_one_terminal_event(sink)
    _assert_one_activity(context)


@pytest.mark.asyncio
async def test_execute_uses_requested_timeout_instead_of_global_default(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    runner = DelayedProcessRunner()
    executor, context, _, _, _ = await execute_fixture(
        tmp_path,
        application_database,
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
    application_database: ApplicationSQLite,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(execute_module, "_EXECUTE_CLEANUP_BUDGET_SECONDS", 0.01)
    runner = CancellationSuppressingProcessRunner()
    executor, context, journal, _, sink = await execute_fixture(
        tmp_path, application_database, runner
    )

    started = monotonic()
    result = await executor.execute(
        ToolRequest(
            call_id="call_execute",
            tool_name="execute",
            arguments={"command": "pytest", "timeout_seconds": 0.05},
        ),
        context=replace(
            context,
            permission_session=PermissionSession(mode=PermissionMode.FULL_ACCESS),
        ),
    )

    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code is ToolErrorCode.TIMEOUT
    assert monotonic() - started < 0.2
    await asyncio.wait_for(runner.cancellation_seen.wait(), timeout=1)
    assert not runner.late_returned.is_set()
    await asyncio.wait_for(runner.late_returned.wait(), timeout=1)
    assert [
        item.command
        for item in (await journal.seal(context.change_set_id or "")).execute
    ] == ["pytest"]
    _assert_one_terminal_event(sink)
    _assert_one_activity(context)


@pytest.mark.asyncio
async def test_execute_records_attempt_before_backend_failure(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    runner = FailingProcessRunner()
    executor, context, journal, _, sink = await execute_fixture(
        tmp_path, application_database, runner
    )

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

    sealed = await journal.seal(context.change_set_id or "")
    assert [item.command for item in sealed.execute] == ["pytest"]
    assert len(runner.calls) == 1
    _assert_one_terminal_event(sink)
    _assert_one_activity(context)


@pytest.mark.asyncio
async def test_execute_redacts_persisted_command_but_runs_original(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    command = (
        "echo API_KEY=api-secret Authorization: Bearer bearer-secret token=token-secret"
    )
    runner = RecordingProcessRunner()
    executor, context, journal, _, _ = await execute_fixture(
        tmp_path,
        application_database,
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

    assert result.status is ToolStatus.SUCCESS
    assert len(runner.calls) == 1
    argv, _, environment = runner.calls[0]
    assert command in argv or environment.get("AWESOME_EXEC_COMMAND") == command
    sealed = await journal.seal(context.change_set_id or "")
    [observation] = sealed.execute
    assert observation.command.startswith("echo ")
    assert "[REDACTED:api_key]" in observation.command
    assert "[REDACTED:auth_header]" in observation.command
    assert "[REDACTED:token]" in observation.command

    with sqlite3.connect(tmp_path / "application.db") as connection:
        [payload] = connection.execute(
            "SELECT payload_json FROM change_sets WHERE change_set_id = ?",
            (context.change_set_id,),
        ).fetchone()
    for secret in ("api-secret", "bearer-secret", "token-secret"):
        assert secret not in payload


@pytest.mark.asyncio
async def test_execute_timeout_records_one_attempt_and_terminal_event(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    runner = TimedOutProcessRunner()
    executor, context, journal, _, sink = await execute_fixture(
        tmp_path, application_database, runner
    )

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
    sealed = await journal.seal(context.change_set_id or "")
    assert [item.command for item in sealed.execute] == ["pytest"]
    _assert_one_terminal_event(sink)
    _assert_one_activity(context)


@pytest.mark.asyncio
async def test_execute_records_cancelled_attempt_once(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    runner = WaitingProcessRunner()
    executor, context, journal, _, sink = await execute_fixture(
        tmp_path, application_database, runner
    )
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

    sealed = await journal.seal(context.change_set_id or "")
    assert [item.command for item in sealed.execute] == ["pytest"]
    event_types = [event.event_type.value for event in sink.events]
    assert event_types.count("tool.cancelled") == 1
    _assert_one_terminal_event(sink)
    _assert_one_activity(context)


@pytest.mark.asyncio
async def test_execute_preserves_user_cancellation_when_backend_returns_late(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    runner = CancellationSuppressingProcessRunner()
    executor, context, journal, _, sink = await execute_fixture(
        tmp_path, application_database, runner
    )
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
        item.command
        for item in (await journal.seal(context.change_set_id or "")).execute
    ] == ["pytest"]
    _assert_one_terminal_event(sink)
    _assert_one_activity(context)


@pytest.mark.asyncio
async def test_execute_outside_path_requires_matching_allow_once_scope(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    runner = RecordingProcessRunner()
    executor, context, _, _workspace, _ = await execute_fixture(
        tmp_path, application_database, runner
    )
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
