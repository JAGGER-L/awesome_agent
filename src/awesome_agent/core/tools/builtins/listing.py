from __future__ import annotations

from typing import cast

from pydantic import BaseModel, Field, JsonValue

from awesome_agent.core.tools.context import ToolExecutionContext
from awesome_agent.core.tools.contracts import (
    ToolArguments,
    ToolOutput,
    ToolPresentation,
)
from awesome_agent.core.tools.filesystem import WorkspaceDirectoryTransaction
from awesome_agent.core.tools.policy import (
    is_sensitive_workspace_path,
    resolve_workspace_path,
)


class LsArguments(ToolArguments):
    path: str = "."
    max_entries: int = Field(default=200, ge=1, le=1_000)


async def list_directory(
    arguments: BaseModel,
    context: ToolExecutionContext,
) -> ToolOutput:
    options = cast(LsArguments, arguments)
    safe = resolve_workspace_path(
        context.workspace,
        options.path,
        must_exist=True,
        expected_kind="directory",
    )

    entries: list[dict[str, str]] = []
    with WorkspaceDirectoryTransaction(safe) as transaction:
        for entry in transaction.entries():
            relative_path = safe.relative / entry.name
            if entry.name == ".git" or is_sensitive_workspace_path(relative_path):
                continue
            entries.append(
                {
                    "name": entry.name,
                    "path": relative_path.as_posix(),
                    "type": entry.kind,
                }
            )

    truncated = len(entries) > options.max_entries
    bounded = entries[: options.max_entries]
    truncated_count = len(entries) - len(bounded)
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
            detail_truncated_count=truncated_count or None,
        ),
    )
