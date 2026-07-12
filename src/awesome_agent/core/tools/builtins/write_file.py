from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path
from typing import cast

from pydantic import BaseModel, Field

from awesome_agent.core.changes import ChangeJournal, NodeSnapshot
from awesome_agent.core.changes.models import FileChangeKind, FileNodeType
from awesome_agent.core.tools.context import ToolExecutionContext, ToolHandler
from awesome_agent.core.tools.contracts import ToolErrorCode, ToolOutput
from awesome_agent.core.tools.errors import ExpectedToolFailure, ToolInvariantError
from awesome_agent.core.tools.policy import resolve_workspace_path


class WriteFileArguments(BaseModel):
    path: str
    content: str = Field(max_length=1_000_000)


def _atomic_write(path: Path, content: bytes, mode: int | None) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        if mode is not None:
            os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def create_write_file_handler(journal: ChangeJournal) -> ToolHandler:
    async def write_file(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolOutput:
        options = cast(WriteFileArguments, arguments)
        if context.change_set_id is None:
            raise ToolInvariantError("write_file requires an open ChangeSet.")
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
            must_exist=False,
        )
        if not safe.resolved.parent.is_dir():
            raise ExpectedToolFailure(
                ToolErrorCode.NOT_FOUND,
                "Parent directory was not found.",
                metadata={"path": options.path},
            )
        if safe.resolved.exists() and not safe.resolved.is_file():
            raise ExpectedToolFailure(
                ToolErrorCode.INVALID_ARGUMENTS,
                "Path is not a file.",
                metadata={"path": options.path},
            )

        existed = safe.resolved.exists()
        mode = stat.S_IMODE(safe.resolved.stat().st_mode) if existed else None
        content = options.content.encode("utf-8")
        change = journal.apply_file_mutation(
            change_set_id=context.change_set_id,
            path=safe.resolved,
            kind=FileChangeKind.UPDATED if existed else FileChangeKind.CREATED,
            intended_after=NodeSnapshot(FileNodeType.FILE, content, mode),
            mutate=lambda: _atomic_write(safe.resolved, content, mode),
        )
        return ToolOutput(
            content=f"Wrote {change.path}.",
            metadata={"path": change.path, "change_set_id": context.change_set_id},
        )

    return write_file
