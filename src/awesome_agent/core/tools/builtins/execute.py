from __future__ import annotations

import os
from pathlib import Path
from typing import cast

from pydantic import BaseModel, Field, JsonValue

from awesome_agent.core.changes import ChangeJournal
from awesome_agent.core.tools.command_policy import (
    CommandPolicyAction,
    evaluate_command,
    host_shell_dialect,
)
from awesome_agent.core.tools.context import (
    ToolExecutionContext,
    ToolHandler,
)
from awesome_agent.core.tools.contracts import (
    ToolArguments,
    ToolErrorCode,
    ToolOutput,
    ToolPresentation,
)
from awesome_agent.core.tools.errors import ExpectedToolFailure, ToolInvariantError
from awesome_agent.core.tools.policy import (
    resolve_workspace_path,
    validate_workspace_path_syntax,
)
from awesome_agent.core.tools.process import ShellExecutionBackend
from awesome_agent.safety.redaction import redact_text

_EXECUTE_CLEANUP_BUDGET_SECONDS = 10.0


class ExecuteArguments(ToolArguments):
    command: str = Field(min_length=1, max_length=8_000)
    cwd: str = "."
    timeout_seconds: float = Field(default=60.0, gt=0, le=600.0)
    max_output_chars: int = Field(default=30_000, ge=1_000, le=200_000)


def resolve_execute_timeout(arguments: BaseModel) -> float:
    options = cast(ExecuteArguments, arguments)
    return options.timeout_seconds + _EXECUTE_CLEANUP_BUDGET_SECONDS


def admit_execute(
    arguments: BaseModel,
    context: ToolExecutionContext,
) -> None:
    """Bind cwd and apply the command circuit breaker before permission policy."""

    options = cast(ExecuteArguments, arguments)
    validate_workspace_path_syntax(options.cwd)
    cwd = resolve_workspace_path(
        context.workspace,
        options.cwd,
        must_exist=True,
        expected_kind="directory",
    )
    decision = evaluate_command(
        options.command,
        dialect=host_shell_dialect(),
        cwd=cwd.resolved,
        workspace=context.workspace.canonical_path,
    )
    if decision.action is CommandPolicyAction.DENY:
        raise ExpectedToolFailure(
            ToolErrorCode.PERMISSION_DENIED,
            decision.reason,
        )


def _sanitized_environment() -> dict[str, str]:
    denied_suffixes = ("_API_KEY", "_TOKEN", "_SECRET", "PASSWORD")
    return {
        name: value
        for name, value in os.environ.items()
        if not name.upper().endswith(denied_suffixes)
    }


def _shell_argv(command: str) -> list[str]:
    if os.name == "nt":
        return [
            os.environ.get("COMSPEC", "cmd.exe"),
            "/d",
            "/s",
            "/c",
            "%AWESOME_EXEC_COMMAND%",
        ]
    return ["/bin/sh", "-lc", command]


def create_execute_handler(
    journal: ChangeJournal,
    process_runner: ShellExecutionBackend,
) -> ToolHandler:
    async def execute(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolOutput:
        options = cast(ExecuteArguments, arguments)
        if context.change_set_id is None:
            raise ToolInvariantError("execute requires an open ChangeSet.")
        lexical_cwd = context.workspace.canonical_path / Path(options.cwd)
        if lexical_cwd.is_symlink():
            raise ExpectedToolFailure(
                ToolErrorCode.PERMISSION_DENIED,
                "Shell working directory cannot be a symlink.",
                metadata={"cwd": options.cwd},
            )
        cwd = resolve_workspace_path(
            context.workspace,
            options.cwd,
            must_exist=True,
            expected_kind="directory",
        )
        decision = evaluate_command(
            options.command,
            dialect=host_shell_dialect(),
            cwd=cwd.resolved,
            workspace=context.workspace.canonical_path,
        )
        if decision.action is CommandPolicyAction.DENY:
            raise ExpectedToolFailure(
                ToolErrorCode.PERMISSION_DENIED,
                decision.reason,
            )
        environment = _sanitized_environment()
        if os.name == "nt":
            environment["AWESOME_EXEC_COMMAND"] = options.command
        await journal.record_execute(
            change_set_id=context.change_set_id,
            command=redact_text(options.command).text,
            observed_paths=[],
        )
        result = await process_runner.run(
            argv=_shell_argv(options.command),
            cwd=cwd.resolved,
            environment=environment,
            timeout_seconds=options.timeout_seconds,
            max_output_chars=options.max_output_chars,
        )
        stdout = redact_text(result.stdout)
        stderr = redact_text(result.stderr)
        metadata: dict[str, JsonValue] = {
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "stdout_truncated": result.stdout_truncated,
            "stderr_truncated": result.stderr_truncated,
            "duration_ms": result.duration_ms,
            "redacted": stdout.redacted or stderr.redacted,
        }
        if result.timed_out:
            raise ExpectedToolFailure(
                ToolErrorCode.TIMEOUT,
                "Command execution timed out.",
                metadata=metadata,
            )
        content = stdout.text
        if stderr.text:
            content = f"{content}\n[stderr]\n{stderr.text}" if content else stderr.text
        return ToolOutput(
            content=content,
            metadata=metadata,
            presentation=ToolPresentation(
                verb="Run",
                target=options.command,
                outcome="Completed" if result.exit_code == 0 else "Failed",
                summary=f"Exit code {result.exit_code}",
                detail=content[:4_000] or None,
            ),
        )

    return execute
