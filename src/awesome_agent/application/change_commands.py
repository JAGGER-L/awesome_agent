from __future__ import annotations

from typing import Literal

from awesome_agent.application.command_results import (
    ChangeCommandPayload,
    CommandOutcome,
    DiffCommandPayload,
    error,
    result,
)
from awesome_agent.application.commands import CommandIntent
from awesome_agent.core.changes import ChangeOperations
from awesome_agent.core.changes.errors import (
    ChangeConflict,
    ChangeLifecycleError,
    ChangeNotReversible,
    ChangeSetNotFound,
)
from awesome_agent.storage.changes import SQLiteChangeSetStore


class ChangeCommandService:
    """Own deterministic Change Journal commands and exact domain errors."""

    def __init__(
        self,
        *,
        operations: ChangeOperations,
        store: SQLiteChangeSetStore,
        workspace_key: str,
    ) -> None:
        self._operations = operations
        self._store = store
        self._workspace_key = workspace_key

    async def diff(self, intent: CommandIntent) -> CommandOutcome:
        if len(intent.arguments) > 1:
            return error("invalid_arguments", "Usage: /diff [change_set_id]")
        explicit = intent.arguments[0] if intent.arguments else None
        identifier = explicit or await self._latest_identifier()
        if identifier is None:
            return result(DiffCommandPayload())
        try:
            content = await self._operations.diff(identifier)
        except ChangeSetNotFound:
            return error("change_set_not_found", "ChangeSet was not found.")
        except ChangeLifecycleError:
            return error(
                "invalid_change_lifecycle",
                "ChangeSet is not in the required lifecycle.",
            )
        return result(DiffCommandPayload(change_set_id=identifier, content=content))

    async def undo(self, intent: CommandIntent) -> CommandOutcome:
        return await self._change(intent, action="undo")

    async def redo(self, intent: CommandIntent) -> CommandOutcome:
        return await self._change(intent, action="redo")

    async def _change(
        self,
        intent: CommandIntent,
        *,
        action: Literal["undo", "redo"],
    ) -> CommandOutcome:
        if len(intent.arguments) > 1:
            return error("invalid_arguments", f"Usage: /{action} [change_set_id]")
        identifier = (
            intent.arguments[0] if intent.arguments else await self._latest_identifier()
        )
        if identifier is None:
            return error("change_set_not_found", "ChangeSet was not found.")
        try:
            changed = (
                await self._operations.undo(identifier)
                if action == "undo"
                else await self._operations.redo(identifier)
            )
        except ChangeSetNotFound:
            return error("change_set_not_found", "ChangeSet was not found.")
        except ChangeConflict:
            return error(
                "workspace_conflict",
                "Workspace content conflicts with the recorded change.",
            )
        except ChangeNotReversible:
            return error("change_not_reversible", "ChangeSet is not reversible.")
        except ChangeLifecycleError:
            return error(
                "invalid_change_lifecycle",
                "ChangeSet is not in the required lifecycle.",
            )
        return result(
            ChangeCommandPayload(
                action=action,
                change_set_id=changed.change_set_id,
                lifecycle=changed.lifecycle.value,
                restored_paths=changed.restored_paths,
                warning=changed.warning,
            )
        )

    async def _latest_identifier(self) -> str | None:
        latest = await self._store.latest(self._workspace_key)
        return latest.id if latest is not None else None
