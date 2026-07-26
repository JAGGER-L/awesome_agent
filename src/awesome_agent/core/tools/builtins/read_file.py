from __future__ import annotations

from typing import cast

from pydantic import BaseModel, Field

from awesome_agent.core.filesystem import (
    MutationTargetChanged,
    WorkspaceFileTooLarge,
)
from awesome_agent.core.tools.context import ToolExecutionContext
from awesome_agent.core.tools.contracts import (
    ToolArguments,
    ToolErrorCode,
    ToolOutput,
    ToolPresentation,
)
from awesome_agent.core.tools.errors import ExpectedToolFailure
from awesome_agent.core.tools.filesystem import WorkspaceFileTransaction
from awesome_agent.core.tools.policy import resolve_workspace_path

MAX_FILE_BYTES = 1024 * 1024
MAX_READ_LINES = 500
MAX_CONTENT_CHARS = 30_000


class ReadFileArguments(ToolArguments):
    path: str
    start_line: int = Field(default=1, ge=1)
    end_line: int | None = Field(default=None, ge=1)


async def read_file(
    arguments: BaseModel,
    context: ToolExecutionContext,
) -> ToolOutput:
    options = cast(ReadFileArguments, arguments)
    safe = resolve_workspace_path(
        context.workspace,
        options.path,
        must_exist=True,
        expected_kind="file",
    )
    try:
        with WorkspaceFileTransaction(safe) as transaction:
            opened = transaction.read_regular(max_bytes=MAX_FILE_BYTES)
    except WorkspaceFileTooLarge as error:
        raise ExpectedToolFailure(
            ToolErrorCode.EXECUTION_FAILED,
            "File exceeds the 1 MiB read limit.",
            metadata={"path": safe.relative.as_posix()},
        ) from error
    except MutationTargetChanged as error:
        raise ExpectedToolFailure(
            ToolErrorCode.CONFLICT,
            "Workspace path changed while the file was being read.",
            metadata={"path": safe.relative.as_posix()},
        ) from error
    assert opened is not None
    data = opened.data
    if b"\x00" in data:
        raise ExpectedToolFailure(
            ToolErrorCode.EXECUTION_FAILED,
            "Binary files cannot be read as text.",
            metadata={"path": safe.relative.as_posix()},
        )
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ExpectedToolFailure(
            ToolErrorCode.EXECUTION_FAILED,
            "File is not valid UTF-8 text.",
            metadata={"path": safe.relative.as_posix()},
        ) from error

    lines = text.splitlines()
    total_lines = len(lines)
    start_index = min(options.start_line - 1, total_lines)
    requested_end = options.end_line or total_lines
    end_index = min(requested_end, start_index + MAX_READ_LINES, total_lines)

    rendered: list[str] = []
    rendered_chars = 0
    last_line = start_index
    content_truncated = False
    for index in range(start_index, end_index):
        line = f"{index + 1}: {lines[index]}"
        separator = "\n" if rendered else ""
        remaining = MAX_CONTENT_CHARS - rendered_chars
        addition = f"{separator}{line}"
        if len(addition) > remaining:
            if remaining > 0:
                rendered.append(addition[:remaining])
                rendered_chars += remaining
                last_line = index + 1
            content_truncated = True
            break
        rendered.append(addition)
        rendered_chars += len(addition)
        last_line = index + 1

    truncated = content_truncated or end_index < total_lines
    content = "".join(rendered)
    line_count = max(0, last_line - start_index)
    return ToolOutput(
        content=content,
        metadata={
            "path": safe.relative.as_posix(),
            "start_line": options.start_line,
            "end_line": last_line,
            "total_lines": total_lines,
            "truncated": truncated,
        },
        presentation=ToolPresentation(
            verb="Read",
            target=safe.relative.as_posix(),
            outcome="Read",
            summary=f"{line_count} {'line' if line_count == 1 else 'lines'}",
            detail=content[:4_000] or None,
        ),
    )
