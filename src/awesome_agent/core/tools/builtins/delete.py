from __future__ import annotations

from pathlib import Path
from typing import cast

from pydantic import BaseModel

from awesome_agent.core.changes import ChangeJournal
from awesome_agent.core.changes.errors import ChangeCapacityExceeded
from awesome_agent.core.changes.journal import (
    MAX_CHANGESET_BYTES,
    MAX_CHANGESET_FILES,
)
from awesome_agent.core.changes.models import FileChangeKind
from awesome_agent.core.filesystem import MutationTargetChanged, lstat_child
from awesome_agent.core.filesystem import identity as filesystem_identity
from awesome_agent.core.tools.context import ToolExecutionContext, ToolHandler
from awesome_agent.core.tools.contracts import (
    ToolArguments,
    ToolErrorCode,
    ToolOutput,
    ToolPresentation,
)
from awesome_agent.core.tools.errors import ExpectedToolFailure, ToolInvariantError
from awesome_agent.core.tools.filesystem import (
    WorkspaceDeleteTransaction,
    WorkspaceDirectoryTransaction,
)
from awesome_agent.core.tools.policy import (
    SafeWorkspacePath,
    is_sensitive_workspace_path,
    resolve_workspace_path,
    validate_workspace_path_syntax,
)


class DeleteArguments(ToolArguments):
    path: str


def _deny_protected(relative: Path) -> None:
    parts = tuple(part.casefold() for part in relative.parts)
    if ".git" in parts or is_sensitive_workspace_path(relative):
        raise ExpectedToolFailure(
            ToolErrorCode.PERMISSION_DENIED,
            "Protected workspace paths cannot be deleted.",
            metadata={"path": relative.as_posix()},
        )


def create_delete_handler(journal: ChangeJournal) -> ToolHandler:
    async def delete(
        arguments: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolOutput:
        options = cast(DeleteArguments, arguments)
        if context.change_set_id is None:
            raise ToolInvariantError("delete requires an open ChangeSet.")
        validate_workspace_path_syntax(options.path)
        requested = Path(options.path)
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
        relative = parent.relative / requested.name
        _deny_protected(relative)
        try:
            with WorkspaceDirectoryTransaction(parent) as parent_transaction:
                target_status = lstat_child(
                    parent_transaction.directory,
                    requested.name,
                )
        except FileNotFoundError as error:
            raise ExpectedToolFailure(
                ToolErrorCode.NOT_FOUND,
                "Path was not found.",
                metadata={"path": relative.as_posix()},
            ) from error
        except OSError as error:
            raise ExpectedToolFailure(
                ToolErrorCode.PERMISSION_DENIED,
                "Workspace path could not be inspected safely.",
                metadata={"path": relative.as_posix()},
            ) from error
        target = SafeWorkspacePath(
            requested=options.path,
            relative=relative,
            resolved=context.workspace.canonical_path / relative,
            workspace=context.workspace.canonical_path,
            workspace_root_identity=context.workspace.root_identity,
            target_existed=True,
            target_identity=filesystem_identity(target_status),
        )
        try:
            with WorkspaceDeleteTransaction(target) as transaction:
                nodes = transaction.inventory(
                    validate_relative=_deny_protected,
                    max_nodes=MAX_CHANGESET_FILES,
                    max_bytes=MAX_CHANGESET_BYTES,
                )
                try:
                    await journal.preflight_batch(
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
                    await journal.apply_file_mutation(
                        change_set_id=context.change_set_id,
                        kind=FileChangeKind.DELETED,
                        intended_after=None,
                        target=node.mutation,
                    )
        except MutationTargetChanged as error:
            raise ExpectedToolFailure(
                ToolErrorCode.CONFLICT,
                "Workspace path changed before deletion could complete.",
                metadata={"path": relative.as_posix()},
            ) from error
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
