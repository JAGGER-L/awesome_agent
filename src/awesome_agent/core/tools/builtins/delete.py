from __future__ import annotations

import os
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import cast

from pydantic import BaseModel

from awesome_agent.core.changes import ChangeJournal
from awesome_agent.core.changes.errors import ChangeCapacityExceeded
from awesome_agent.core.changes.models import FileChangeKind, FileNodeType
from awesome_agent.core.tools.context import ToolExecutionContext, ToolHandler
from awesome_agent.core.tools.contracts import (
    ToolErrorCode,
    ToolOutput,
    ToolPresentation,
)
from awesome_agent.core.tools.errors import ExpectedToolFailure, ToolInvariantError
from awesome_agent.core.tools.policy import (
    is_sensitive_workspace_path,
    resolve_workspace_path,
)


class DeleteArguments(BaseModel):
    path: str


@dataclass(frozen=True, slots=True)
class _DeleteNode:
    path: Path
    node_type: FileNodeType
    content_bytes: int


def _deny_protected(relative: Path) -> None:
    parts = tuple(part.casefold() for part in relative.parts)
    if ".git" in parts or is_sensitive_workspace_path(relative):
        raise ExpectedToolFailure(
            ToolErrorCode.PERMISSION_DENIED,
            "Protected workspace paths cannot be deleted.",
            metadata={"path": relative.as_posix()},
        )


def _inventory(path: Path, workspace: Path) -> list[_DeleteNode]:
    relative = path.relative_to(workspace)
    _deny_protected(relative)
    if path.is_symlink():
        return [
            _DeleteNode(
                path,
                FileNodeType.SYMLINK,
                len(os.fsencode(os.readlink(path))),
            )
        ]
    if path.is_file():
        return [_DeleteNode(path, FileNodeType.FILE, path.stat().st_size)]
    if not path.is_dir():
        raise ExpectedToolFailure(
            ToolErrorCode.INVALID_ARGUMENTS,
            "Unsupported filesystem node.",
            metadata={"path": relative.as_posix()},
        )

    nodes: list[_DeleteNode] = []
    with os.scandir(path) as entries:
        children = sorted(
            entries, key=lambda entry: (entry.name.casefold(), entry.name)
        )
    for child in children:
        nodes.extend(_inventory(Path(child.path), workspace))
    nodes.append(_DeleteNode(path, FileNodeType.DIRECTORY, 0))
    return nodes


def _delete_node(node: _DeleteNode) -> None:
    if node.node_type is FileNodeType.DIRECTORY:
        node.path.rmdir()
    else:
        node.path.unlink()


def create_delete_handler(journal: ChangeJournal) -> ToolHandler:
    async def delete(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolOutput:
        options = cast(DeleteArguments, arguments)
        if context.change_set_id is None:
            raise ToolInvariantError("delete requires an open ChangeSet.")
        requested = Path(options.path)
        if requested.is_absolute() or ".." in requested.parts:
            raise ExpectedToolFailure(
                ToolErrorCode.WORKSPACE_ESCAPE,
                "Delete path escapes the workspace boundary.",
                metadata={"path": options.path},
            )
        if requested in {Path("."), Path()}:
            raise ExpectedToolFailure(
                ToolErrorCode.PERMISSION_DENIED,
                "The workspace root cannot be deleted.",
            )

        parent = resolve_workspace_path(
            context.workspace,
            requested.parent.as_posix(),
            must_exist=True,
            expected_kind="directory",
        )
        target = parent.resolved / requested.name
        if not target.exists() and not target.is_symlink():
            raise ExpectedToolFailure(
                ToolErrorCode.NOT_FOUND,
                "Path was not found.",
                metadata={"path": options.path},
            )
        relative = target.relative_to(context.workspace.canonical_path)
        _deny_protected(relative)
        nodes = _inventory(target, context.workspace.canonical_path)
        try:
            journal.preflight_batch(
                change_set_id=context.change_set_id,
                additional_nodes=len(nodes),
                additional_bytes=sum(node.content_bytes for node in nodes),
            )
        except ChangeCapacityExceeded as error:
            raise ExpectedToolFailure(
                ToolErrorCode.EXECUTION_FAILED,
                str(error),
                metadata={"path": relative.as_posix()},
            ) from error

        for node in nodes:
            journal.apply_file_mutation(
                change_set_id=context.change_set_id,
                path=node.path,
                kind=FileChangeKind.DELETED,
                intended_after=None,
                mutate=partial(_delete_node, node),
            )
        return ToolOutput(
            content=f"Deleted {relative.as_posix()}.",
            metadata={
                "path": relative.as_posix(),
                "deleted_nodes": len(nodes),
                "change_set_id": context.change_set_id,
            },
            presentation=ToolPresentation(
                verb="Delete",
                target=relative.as_posix(),
                outcome="Deleted",
                summary=f"{len(nodes)} {'node' if len(nodes) == 1 else 'nodes'}",
            ),
        )

    return delete
