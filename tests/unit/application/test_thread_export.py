from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

import pytest

import awesome_agent.application.thread_export as thread_export_module
from awesome_agent.application.change_scope import ChangeScope
from awesome_agent.application.command_results import (
    CommandError,
    CommandResult,
    ThreadExportCommandPayload,
)
from awesome_agent.application.commands import CommandIntent, CommandName
from awesome_agent.application.thread_export import ThreadExportService
from awesome_agent.conversation import ConversationService
from awesome_agent.core.changes import (
    BoundFileMutation,
    ChangeJournal,
    ChangeLifecycle,
    ChangeOperations,
)
from awesome_agent.core.changes.models import FileChangeKind
from awesome_agent.core.filesystem import MutationTargetChanged
from awesome_agent.core.tools.contracts import ToolErrorCode
from awesome_agent.core.tools.errors import ExpectedToolFailure
from awesome_agent.core.tools.filesystem import (
    BoundRegularFile,
    WorkspaceFileTransaction,
)
from awesome_agent.core.tools.policy import resolve_workspace_path
from awesome_agent.core.tools.registry import ToolRegistry
from awesome_agent.core.workspace import WorkspaceIdentity, resolve_workspace
from awesome_agent.storage.application_sqlite import ApplicationSQLite
from awesome_agent.storage.changes import FileChangeBlobStore, SQLiteChangeSetStore
from awesome_agent.storage.conversations import SQLiteConversationRepositories

pytestmark = pytest.mark.asyncio


class ExportFixture:
    def __init__(
        self,
        *,
        database: ApplicationSQLite,
        workspace: WorkspaceIdentity,
        conversation: ConversationService,
        store: SQLiteChangeSetStore,
        blobs: FileChangeBlobStore,
        service: ThreadExportService,
        thread_id: str,
    ) -> None:
        self.database = database
        self.workspace = workspace
        self.conversation = conversation
        self.store = store
        self.blobs = blobs
        self.service = service
        self.thread_id = thread_id


@pytest.fixture
async def export_fixture(tmp_path: Path) -> AsyncIterator[ExportFixture]:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = resolve_workspace(workspace_path)
    database = ApplicationSQLite(tmp_path / "application.db")
    await database.initialize()
    repositories = SQLiteConversationRepositories(database)
    conversation = ConversationService(store=repositories)
    thread = await conversation.create_thread(workspace.key, "Export title")
    store = SQLiteChangeSetStore(database)
    blobs = FileChangeBlobStore(tmp_path / "change-journal")
    journal = ChangeJournal(store, blobs, workspace)
    scope = ChangeScope(
        journal=journal,
        store=store,
        registry=ToolRegistry(),
        session_id="session_export",
        workspace=workspace,
    )
    fixture = ExportFixture(
        database=database,
        workspace=workspace,
        conversation=conversation,
        store=store,
        blobs=blobs,
        service=ThreadExportService(
            conversation=conversation,
            workspace=workspace,
            current_thread_id=lambda: thread.id,
            journal=journal,
            change_scope=scope,
        ),
        thread_id=thread.id,
    )
    try:
        yield fixture
    finally:
        await database.aclose()


def _payload(outcome: object) -> ThreadExportCommandPayload:
    assert isinstance(outcome, CommandResult)
    assert isinstance(outcome.payload, ThreadExportCommandPayload)
    return outcome.payload


async def test_export_create_unchanged_update_and_undo_are_journaled(
    export_fixture: ExportFixture,
) -> None:
    fixture = export_fixture
    target = fixture.workspace.canonical_path / "exports" / "thread.json"
    target.parent.mkdir()
    intent = CommandIntent(
        name=CommandName.EXPORT,
        arguments=("exports/thread.json", "json"),
    )

    created = _payload(await fixture.service.export(intent))
    created_content = target.read_bytes()
    assert created.write_status == "created"
    assert created.change_set_id is not None
    assert created.byte_count == len(created_content)
    created_change = await fixture.store.get(created.change_set_id)
    assert created_change is not None
    assert created_change.lifecycle is ChangeLifecycle.APPLIED
    assert [change.kind for change in created_change.files] == [FileChangeKind.CREATED]

    unchanged = _payload(await fixture.service.export(intent))
    assert unchanged.write_status == "unchanged"
    assert unchanged.change_set_id is None
    assert unchanged.byte_count == len(created_content)
    latest = await fixture.store.latest(fixture.workspace.key)
    assert latest is not None
    assert latest.id == created.change_set_id

    await fixture.conversation.rename_thread(fixture.thread_id, "Updated title")
    updated = _payload(await fixture.service.export(intent))
    updated_content = target.read_bytes()
    assert updated.write_status == "updated"
    assert updated.change_set_id is not None
    assert updated.byte_count == len(updated_content)
    assert updated_content != created_content
    updated_change = await fixture.store.get(updated.change_set_id)
    assert updated_change is not None
    assert updated_change.lifecycle is ChangeLifecycle.APPLIED
    assert [change.kind for change in updated_change.files] == [FileChangeKind.UPDATED]

    await ChangeOperations(
        fixture.store,
        fixture.blobs,
        fixture.workspace,
    ).undo(updated.change_set_id)
    assert target.read_bytes() == created_content


@pytest.mark.parametrize(
    "arguments",
    [
        (),
        ("",),
        ("export.md", "yaml"),
        ("export.md", "markdown", "extra"),
    ],
)
async def test_export_rejects_invalid_grammar_without_writing(
    export_fixture: ExportFixture,
    arguments: tuple[str, ...],
) -> None:
    outcome = await export_fixture.service.export(
        CommandIntent(name=CommandName.EXPORT, arguments=arguments)
    )

    assert isinstance(outcome, CommandError)
    assert outcome.code == "invalid_arguments"
    assert await export_fixture.store.latest(export_fixture.workspace.key) is None


async def test_export_rejects_workspace_escape_before_opening_changeset(
    export_fixture: ExportFixture,
) -> None:
    outcome = await export_fixture.service.export(
        CommandIntent(name=CommandName.EXPORT, arguments=("../outside.md",))
    )

    assert isinstance(outcome, CommandError)
    assert outcome.code == "workspace_escape"
    assert await export_fixture.store.latest(export_fixture.workspace.key) is None


async def test_export_rejects_normalized_path_over_wire_limit_before_mutation(
    export_fixture: ExportFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = export_fixture.workspace.canonical_path / "export.md"
    safe = resolve_workspace_path(
        export_fixture.workspace,
        "export.md",
        must_exist=False,
    )

    def overlong_relative(*_args: object, **_kwargs: object) -> object:
        return replace(safe, relative=Path("x" * 1_001))

    monkeypatch.setattr(
        "awesome_agent.application.thread_export.resolve_workspace_path",
        overlong_relative,
    )

    outcome = await export_fixture.service.export(
        CommandIntent(name=CommandName.EXPORT, arguments=("export.md",))
    )

    assert isinstance(outcome, CommandError)
    assert outcome.code == "invalid_arguments"
    assert not target.exists()
    assert await export_fixture.store.list_pending() == []
    assert await export_fixture.store.latest(export_fixture.workspace.key) is None


async def test_export_rejects_oversized_entry_before_render_or_mutation(
    export_fixture: ExportFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await export_fixture.conversation.append_direct_command(
        export_fixture.thread_id,
        "content over the test limit",
        {},
    )
    monkeypatch.setattr(thread_export_module, "MAX_THREAD_EXPORT_BYTES", 10)

    def fail_render(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("oversized content must be rejected before rendering")

    monkeypatch.setattr(thread_export_module, "render_thread_export", fail_render)

    outcome = await export_fixture.service.export(
        CommandIntent(name=CommandName.EXPORT, arguments=("export.md",))
    )

    assert isinstance(outcome, CommandError)
    assert outcome.code == "export_too_large"
    assert not (export_fixture.workspace.canonical_path / "export.md").exists()
    assert await export_fixture.store.list_pending() == []
    assert await export_fixture.store.latest(export_fixture.workspace.key) is None


async def test_export_permission_failure_is_typed_and_has_no_changeset(
    export_fixture: ExportFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def deny(*_args: object, **_kwargs: object) -> object:
        raise ExpectedToolFailure(
            ToolErrorCode.PERMISSION_DENIED,
            "Path could not be opened safely.",
        )

    monkeypatch.setattr(
        "awesome_agent.application.thread_export.resolve_workspace_path",
        deny,
    )

    outcome = await export_fixture.service.export(
        CommandIntent(name=CommandName.EXPORT, arguments=("export.md",))
    )

    assert isinstance(outcome, CommandError)
    assert outcome.code == "permission_denied"
    assert await export_fixture.store.latest(export_fixture.workspace.key) is None


async def test_export_target_race_is_error_and_reconciles_changeset(
    export_fixture: ExportFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = WorkspaceFileTransaction.replace_mutation

    def race(
        transaction: WorkspaceFileTransaction,
        *,
        before: BoundRegularFile | None,
        content: bytes,
        mode: int | None,
    ) -> BoundFileMutation:
        mutation = original(
            transaction,
            before=before,
            content=content,
            mode=mode,
        )

        def changed() -> None:
            raise MutationTargetChanged("replaced")

        return replace(mutation, mutate=changed)

    monkeypatch.setattr(
        WorkspaceFileTransaction,
        "replace_mutation",
        race,
    )

    outcome = await export_fixture.service.export(
        CommandIntent(name=CommandName.EXPORT, arguments=("export.md",))
    )

    assert isinstance(outcome, CommandError)
    assert outcome.code == "conflict"
    assert not (export_fixture.workspace.canonical_path / "export.md").exists()
    assert await export_fixture.store.list_pending() == []
    assert await export_fixture.store.list_open(export_fixture.workspace.key) == []
    assert await export_fixture.store.latest(export_fixture.workspace.key) is None
