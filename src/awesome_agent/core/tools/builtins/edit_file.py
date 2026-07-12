from __future__ import annotations

import stat
from pathlib import Path
from typing import cast

from pydantic import BaseModel, Field

from awesome_agent.core.changes import ChangeJournal, NodeSnapshot
from awesome_agent.core.changes.models import FileChangeKind, FileNodeType
from awesome_agent.core.tools.builtins.read_file import MAX_FILE_BYTES
from awesome_agent.core.tools.builtins.write_file import _atomic_write
from awesome_agent.core.tools.context import ToolExecutionContext, ToolHandler
from awesome_agent.core.tools.contracts import (
    ToolErrorCode,
    ToolOutput,
    ToolPresentation,
)
from awesome_agent.core.tools.errors import ExpectedToolFailure, ToolInvariantError
from awesome_agent.core.tools.policy import resolve_workspace_path


class EditFileArguments(BaseModel):
    path: str
    old_string: str = Field(min_length=1, max_length=200_000)
    new_string: str = Field(max_length=200_000)
    replace_all: bool = False


def create_edit_file_handler(journal: ChangeJournal) -> ToolHandler:
    async def edit_file(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolOutput:
        options = cast(EditFileArguments, arguments)
        if context.change_set_id is None:
            raise ToolInvariantError("edit_file requires an open ChangeSet.")
        lexical = context.workspace.canonical_path / Path(options.path)
        if lexical.is_symlink():
            raise ExpectedToolFailure(
                ToolErrorCode.PERMISSION_DENIED,
                "File symlinks cannot be modified.",
                metadata={"path": options.path},
            )
        safe = resolve_workspace_path(
            context.workspace,
            options.path,
            must_exist=True,
            expected_kind="file",
        )
        if safe.resolved.stat().st_size > MAX_FILE_BYTES:
            raise ExpectedToolFailure(
                ToolErrorCode.EXECUTION_FAILED,
                "File exceeds the 1 MiB edit limit.",
                metadata={"path": safe.relative.as_posix()},
            )
        data = safe.resolved.read_bytes()
        if b"\x00" in data:
            raise ExpectedToolFailure(
                ToolErrorCode.EXECUTION_FAILED,
                "Binary files cannot be edited as text.",
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

        occurrences = text.count(options.old_string)
        if occurrences == 0:
            raise ExpectedToolFailure(
                ToolErrorCode.NOT_FOUND,
                "Exact text was not found.",
                metadata={"path": safe.relative.as_posix()},
            )
        if occurrences > 1 and not options.replace_all:
            raise ExpectedToolFailure(
                ToolErrorCode.CONFLICT,
                "Exact text occurs more than once; set replace_all to continue.",
                metadata={"path": safe.relative.as_posix()},
            )

        count = -1 if options.replace_all else 1
        updated = text.replace(options.old_string, options.new_string, count)
        content = updated.encode("utf-8")
        mode = stat.S_IMODE(safe.resolved.stat().st_mode)
        change = journal.apply_file_mutation(
            change_set_id=context.change_set_id,
            path=safe.resolved,
            kind=FileChangeKind.UPDATED,
            intended_after=NodeSnapshot(FileNodeType.FILE, content, mode),
            mutate=lambda: _atomic_write(safe.resolved, content, mode),
        )
        return ToolOutput(
            content=f"Edited {change.path}.",
            metadata={
                "path": change.path,
                "replacements": occurrences if options.replace_all else 1,
                "change_set_id": context.change_set_id,
            },
            presentation=ToolPresentation(
                verb="Edit",
                target=change.path,
                outcome="Updated",
                summary=(
                    f"{occurrences if options.replace_all else 1} "
                    f"{'replacement' if occurrences == 1 else 'replacements'}"
                ),
            ),
        )

    return edit_file
