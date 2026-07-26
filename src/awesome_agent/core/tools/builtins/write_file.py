from __future__ import annotations

from typing import cast

from pydantic import BaseModel, Field

from awesome_agent.core.changes import ChangeJournal, NodeSnapshot
from awesome_agent.core.changes.journal import MAX_CHANGESET_BYTES
from awesome_agent.core.changes.models import FileChangeKind, FileNodeType
from awesome_agent.core.filesystem import (
    MutationTargetChanged,
    WorkspaceFileTooLarge,
)
from awesome_agent.core.tools.context import ToolExecutionContext, ToolHandler
from awesome_agent.core.tools.contracts import (
    ToolArguments,
    ToolErrorCode,
    ToolOutput,
    ToolPresentation,
)
from awesome_agent.core.tools.errors import ExpectedToolFailure, ToolInvariantError
from awesome_agent.core.tools.filesystem import WorkspaceFileTransaction
from awesome_agent.core.tools.policy import resolve_workspace_path


class WriteFileArguments(ToolArguments):
    path: str
    content: str = Field(max_length=1_000_000)


def create_write_file_handler(journal: ChangeJournal) -> ToolHandler:
    async def write_file(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolOutput:
        options = cast(WriteFileArguments, arguments)
        if context.change_set_id is None:
            raise ToolInvariantError("write_file requires an open ChangeSet.")
        safe = resolve_workspace_path(
            context.workspace,
            options.path,
            must_exist=False,
        )
        try:
            with WorkspaceFileTransaction(safe) as transaction:
                before = transaction.read_regular(
                    max_bytes=MAX_CHANGESET_BYTES,
                    allow_missing=True,
                )
                existed = before is not None
                mode = before.snapshot.mode if before is not None else None
                content = options.content.encode("utf-8")
                change = journal.apply_file_mutation(
                    change_set_id=context.change_set_id,
                    kind=(
                        FileChangeKind.UPDATED if existed else FileChangeKind.CREATED
                    ),
                    intended_after=NodeSnapshot(FileNodeType.FILE, content, mode),
                    target=transaction.replace_mutation(
                        before=before,
                        content=content,
                        mode=mode,
                    ),
                )
        except WorkspaceFileTooLarge as error:
            raise ExpectedToolFailure(
                ToolErrorCode.EXECUTION_FAILED,
                "Existing file exceeds the ChangeSet byte limit.",
                metadata={"path": safe.relative.as_posix()},
            ) from error
        except MutationTargetChanged as error:
            raise ExpectedToolFailure(
                ToolErrorCode.CONFLICT,
                "Workspace path changed before the file could be written.",
                metadata={"path": safe.relative.as_posix()},
            ) from error
        return ToolOutput(
            content=f"Wrote {change.path}.",
            metadata={"path": change.path, "change_set_id": context.change_set_id},
            presentation=ToolPresentation(
                verb="Write",
                target=change.path,
                outcome="Updated" if existed else "Created",
                summary=_line_summary(options.content),
            ),
        )

    return write_file


def _line_summary(content: str) -> str:
    count = len(content.splitlines())
    return f"{count} {'line' if count == 1 else 'lines'}"
