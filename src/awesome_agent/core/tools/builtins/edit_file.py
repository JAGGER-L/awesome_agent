from __future__ import annotations

from typing import cast

from pydantic import BaseModel, Field

from awesome_agent.core.changes import ChangeJournal, NodeSnapshot
from awesome_agent.core.changes.models import FileChangeKind, FileNodeType
from awesome_agent.core.filesystem import (
    MutationTargetChanged,
    WorkspaceFileTooLarge,
)
from awesome_agent.core.tools.builtins.read_file import MAX_FILE_BYTES
from awesome_agent.core.tools.context import ToolExecutionContext, ToolHandler
from awesome_agent.core.tools.contracts import (
    ToolErrorCode,
    ToolOutput,
    ToolPresentation,
)
from awesome_agent.core.tools.errors import ExpectedToolFailure, ToolInvariantError
from awesome_agent.core.tools.filesystem import WorkspaceFileTransaction
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
        safe = resolve_workspace_path(
            context.workspace,
            options.path,
            must_exist=True,
            expected_kind="file",
        )
        try:
            with WorkspaceFileTransaction(safe) as transaction:
                opened = transaction.read_regular(max_bytes=MAX_FILE_BYTES)
                assert opened is not None
                content, occurrences = _edited_content(
                    opened.data,
                    options,
                    safe.relative.as_posix(),
                )
                mode = opened.snapshot.mode
                change = journal.apply_file_mutation(
                    change_set_id=context.change_set_id,
                    kind=FileChangeKind.UPDATED,
                    intended_after=NodeSnapshot(FileNodeType.FILE, content, mode),
                    target=transaction.replace_mutation(
                        before=opened,
                        content=content,
                        mode=mode,
                    ),
                )
        except WorkspaceFileTooLarge as error:
            raise ExpectedToolFailure(
                ToolErrorCode.EXECUTION_FAILED,
                "File exceeds the 1 MiB edit limit.",
                metadata={"path": safe.relative.as_posix()},
            ) from error
        except MutationTargetChanged as error:
            raise ExpectedToolFailure(
                ToolErrorCode.CONFLICT,
                "Workspace path changed while the file was being opened.",
                metadata={"path": safe.relative.as_posix()},
            ) from error
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


def _edited_content(
    data: bytes,
    options: EditFileArguments,
    relative_path: str,
) -> tuple[bytes, int]:
    if b"\x00" in data:
        raise ExpectedToolFailure(
            ToolErrorCode.EXECUTION_FAILED,
            "Binary files cannot be edited as text.",
            metadata={"path": relative_path},
        )
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ExpectedToolFailure(
            ToolErrorCode.EXECUTION_FAILED,
            "File is not valid UTF-8 text.",
            metadata={"path": relative_path},
        ) from error

    occurrences = text.count(options.old_string)
    if occurrences == 0:
        raise ExpectedToolFailure(
            ToolErrorCode.NOT_FOUND,
            "Exact text was not found.",
            metadata={"path": relative_path},
        )
    if occurrences > 1 and not options.replace_all:
        raise ExpectedToolFailure(
            ToolErrorCode.CONFLICT,
            "Exact text occurs more than once; set replace_all to continue.",
            metadata={"path": relative_path},
        )

    count = -1 if options.replace_all else 1
    updated = text.replace(options.old_string, options.new_string, count)
    return updated.encode("utf-8"), occurrences
