from __future__ import annotations

from pathlib import Path
from typing import cast

from pydantic import BaseModel, Field, JsonValue

from awesome_agent.core.tools.context import ToolExecutionContext
from awesome_agent.core.tools.contracts import (
    ToolErrorCode,
    ToolOutput,
    ToolPresentation,
)
from awesome_agent.core.tools.errors import ExpectedToolFailure
from awesome_agent.core.tools.policy import resolve_workspace_path


class LsArguments(BaseModel):
    path: str = "."
    max_entries: int = Field(default=200, ge=1, le=1_000)


def _relative_path(path: Path, context: ToolExecutionContext) -> str:
    return path.relative_to(context.workspace.canonical_path).as_posix()


async def list_directory(
    arguments: BaseModel,
    context: ToolExecutionContext,
) -> ToolOutput:
    options = cast(LsArguments, arguments)
    requested_path = context.workspace.canonical_path / Path(options.path)
    if requested_path.is_symlink():
        raise ExpectedToolFailure(
            ToolErrorCode.PERMISSION_DENIED,
            "Directory symlinks are not traversed.",
            metadata={"path": options.path},
        )
    safe = resolve_workspace_path(
        context.workspace,
        options.path,
        must_exist=True,
        expected_kind="directory",
    )

    entries: list[dict[str, str]] = []
    for entry in sorted(
        safe.resolved.iterdir(),
        key=lambda item: (item.name.casefold(), item.name),
    ):
        if entry.name == ".git":
            continue
        relative = _relative_path(entry, context)
        try:
            resolve_workspace_path(
                context.workspace,
                relative,
                must_exist=True,
            )
        except ExpectedToolFailure:
            continue
        if entry.is_symlink():
            node_type = "symlink"
        elif entry.is_dir():
            node_type = "directory"
        else:
            node_type = "file"
        entries.append(
            {
                "name": entry.name,
                "path": relative,
                "type": node_type,
            }
        )

    truncated = len(entries) > options.max_entries
    bounded = entries[: options.max_entries]
    content = "\n".join(f"{entry['type']}\t{entry['path']}" for entry in bounded)
    return ToolOutput(
        content=content,
        metadata={
            "entries": cast(JsonValue, bounded),
            "truncated": truncated,
        },
        presentation=ToolPresentation(
            verb="List",
            target=safe.relative.as_posix() or ".",
            outcome="Listed",
            summary=f"{len(bounded)} entries",
            detail=content[:4_000] or None,
        ),
    )
