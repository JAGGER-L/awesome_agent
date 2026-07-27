from __future__ import annotations

import asyncio
from collections.abc import Callable
from uuid import uuid4

from awesome_agent.application.change_scope import ChangeScope
from awesome_agent.application.command_results import (
    CommandOutcome,
    ThreadExportCommandPayload,
    error,
    result,
)
from awesome_agent.application.commands import CommandIntent
from awesome_agent.conversation import (
    AssistantEntryMetadata,
    ConversationService,
    ThreadEntry,
    ThreadEntryKind,
    ThreadExportFormat,
    ThreadNotFound,
    ThreadView,
    render_thread_export,
)
from awesome_agent.core.changes import ChangeJournal, NodeSnapshot
from awesome_agent.core.changes.errors import (
    ChangeCapacityExceeded,
    ChangeLifecycleError,
    PendingMutationConflict,
)
from awesome_agent.core.changes.models import FileChangeKind, FileNodeType
from awesome_agent.core.filesystem import MutationTargetChanged, WorkspaceFileTooLarge
from awesome_agent.core.tools.contracts import ToolErrorCode
from awesome_agent.core.tools.errors import ExpectedToolFailure
from awesome_agent.core.tools.filesystem import WorkspaceFileTransaction
from awesome_agent.core.tools.policy import resolve_workspace_path
from awesome_agent.core.workspace import WorkspaceIdentity

# Export has a deliberately lower bound than the 50 MiB Change Journal ceiling so
# rendering and workspace I/O remain suitable for an interactive command.
MAX_THREAD_EXPORT_BYTES = 5 * 1024 * 1024


class ThreadExportService:
    def __init__(
        self,
        *,
        conversation: ConversationService,
        workspace: WorkspaceIdentity,
        current_thread_id: Callable[[], str | None],
        journal: ChangeJournal,
        change_scope: ChangeScope,
    ) -> None:
        self._conversation = conversation
        self._workspace = workspace
        self._current_thread_id = current_thread_id
        self._journal = journal
        self._change_scope = change_scope

    async def export(self, intent: CommandIntent) -> CommandOutcome:
        parsed = _parse_export_arguments(intent.arguments)
        if parsed is None:
            return error(
                "invalid_arguments",
                "Usage: /export <workspace-relative-path> [markdown|json]",
            )
        path, format = parsed
        thread_id = self._current_thread_id()
        if thread_id is None:
            return error("thread_not_found", "Select a Thread first.")
        try:
            view = await self._conversation.read_thread(thread_id)
        except ThreadNotFound:
            return error("thread_not_found", "Selected Thread was not found.")
        if view.thread.workspace_key != self._workspace.key:
            return error("thread_not_found", "Selected Thread was not found.")
        if _entry_content_exceeds_limit(view.entries):
            return error(
                "export_too_large",
                "Thread export exceeds the 5 MiB output limit.",
            )
        content = await asyncio.to_thread(_render_export_bytes, view, format)
        if len(content) > MAX_THREAD_EXPORT_BYTES:
            return error(
                "export_too_large",
                "Thread export exceeds the 5 MiB output limit.",
            )
        try:
            payload = await self._write(
                thread_id=thread_id,
                requested_path=path,
                format=format,
                content=content,
            )
        except ExpectedToolFailure as failure:
            return error(failure.code.value, failure.message)
        except MutationTargetChanged:
            return error(
                "conflict",
                "Workspace path changed before the export could be written.",
            )
        except WorkspaceFileTooLarge:
            return error(
                "export_too_large",
                "Existing export exceeds the 5 MiB output limit.",
            )
        except ChangeCapacityExceeded:
            return error(
                "export_too_large",
                "Thread export exceeds the Change Journal byte limit.",
            )
        except (ChangeLifecycleError, PendingMutationConflict):
            return error(
                "change_conflict",
                "Thread export could not be recorded safely.",
            )
        except OSError:
            return error(
                "permission_denied",
                "Thread export could not be written safely.",
            )
        return result(payload)

    async def _write(
        self,
        *,
        thread_id: str,
        requested_path: str,
        format: ThreadExportFormat,
        content: bytes,
    ) -> ThreadExportCommandPayload:
        safe = resolve_workspace_path(
            self._workspace,
            requested_path,
            must_exist=False,
        )
        normalized_path = safe.relative.as_posix()
        if not 1 <= len(normalized_path) <= 1_000:
            raise ExpectedToolFailure(
                ToolErrorCode.INVALID_ARGUMENTS,
                "Export path must be 1 to 1000 characters after normalization.",
            )
        owner: str | None = None
        change_set_id: str | None = None
        try:
            with WorkspaceFileTransaction(safe) as transaction:
                before = transaction.read_regular(
                    max_bytes=MAX_THREAD_EXPORT_BYTES,
                    allow_missing=True,
                )
                if before is not None and before.data == content:
                    return ThreadExportCommandPayload(
                        thread_id=thread_id,
                        path=normalized_path,
                        format=format,
                        write_status="unchanged",
                        byte_count=len(content),
                    )
                owner = f"thread_export_{uuid4().hex}"
                change_set_id = await self._change_scope.acquire(owner, turn_id=None)
                existed = before is not None
                mode = before.snapshot.mode if before is not None else None
                change = await self._journal.apply_file_mutation(
                    change_set_id=change_set_id,
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
            await self._change_scope.seal(owner)
            owner = None
            assert change_set_id is not None
            return ThreadExportCommandPayload(
                thread_id=thread_id,
                path=change.path,
                format=format,
                write_status="updated" if existed else "created",
                byte_count=len(content),
                change_set_id=change_set_id,
            )
        finally:
            if owner is not None:
                await self._change_scope.finalize_failed(owner)


def _parse_export_arguments(
    arguments: tuple[str, ...],
) -> tuple[str, ThreadExportFormat] | None:
    if len(arguments) not in {1, 2}:
        return None
    path = arguments[0].strip()
    if not path:
        return None
    if len(arguments) == 1 or arguments[1] == "markdown":
        return path, "markdown"
    if arguments[1] == "json":
        return path, "json"
    return None


def _entry_content_exceeds_limit(entries: tuple[ThreadEntry, ...]) -> bool:
    total = 0
    for entry in entries:
        exported_values = (
            entry.id,
            entry.kind.value,
            str(entry.sequence),
            entry.created_at.isoformat(),
            entry.content,
        )
        total += sum(len(value.encode("utf-8")) for value in exported_values)
        if entry.kind is ThreadEntryKind.ASSISTANT_MESSAGE:
            citations = AssistantEntryMetadata.model_validate(entry.metadata).citations
            total += sum(
                len(value.encode("utf-8"))
                for citation in citations
                for value in (citation.id, citation.title, citation.url)
            )
        if total > MAX_THREAD_EXPORT_BYTES:
            return True
    return False


def _render_export_bytes(view: ThreadView, format: ThreadExportFormat) -> bytes:
    return render_thread_export(view, format=format).encode("utf-8")


__all__ = ["MAX_THREAD_EXPORT_BYTES", "ThreadExportService"]
