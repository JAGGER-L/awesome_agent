from pathlib import Path

import pytest

from awesome_agent.application import composition
from awesome_agent.application.contracts import (
    ApplicationResult,
    InitializeStatus,
    ThreadListQuery,
)
from awesome_agent.core.events import CollectingEventSink
from awesome_agent.core.workspace import (
    TrustStatus,
    WorkspaceTrustService,
    resolve_workspace,
)
from awesome_agent.storage.trust import SQLiteWorkspaceTrustStore


def _unwrap[T](result: ApplicationResult[T]) -> T:
    assert result.ok is True
    assert result.value is not None
    return result.value


def test_accept_survives_reopen_and_revoke(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = tmp_path / "home" / "state" / "application.db"
    identity = resolve_workspace(workspace)

    first = WorkspaceTrustService(SQLiteWorkspaceTrustStore(database))
    assert first.status(identity) is TrustStatus.UNKNOWN
    first.accept(identity)

    reopened = WorkspaceTrustService(SQLiteWorkspaceTrustStore(database))
    assert reopened.status(identity) is TrustStatus.TRUSTED
    assert reopened.revoke(identity) is True
    assert reopened.status(identity) is TrustStatus.UNKNOWN


def test_decline_is_represented_by_not_calling_accept(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    database = tmp_path / "application.db"
    identity = resolve_workspace(workspace)

    service = WorkspaceTrustService(SQLiteWorkspaceTrustStore(database))
    assert service.status(identity) is TrustStatus.UNKNOWN

    reopened = WorkspaceTrustService(SQLiteWorkspaceTrustStore(database))
    assert reopened.status(identity) is TrustStatus.UNKNOWN


def test_moved_workspace_requires_new_trust(tmp_path: Path) -> None:
    original = tmp_path / "original"
    moved = tmp_path / "moved"
    original.mkdir()
    database = tmp_path / "application.db"
    service = WorkspaceTrustService(SQLiteWorkspaceTrustStore(database))
    service.accept(resolve_workspace(original))

    original.rename(moved)

    assert service.status(resolve_workspace(moved)) is TrustStatus.UNKNOWN


def test_symlink_and_real_path_share_identity_and_trust(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    link = tmp_path / "workspace-link"
    try:
        link.symlink_to(workspace, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are not available on this platform.")

    real_identity = resolve_workspace(workspace)
    link_identity = resolve_workspace(link)
    service = WorkspaceTrustService(
        SQLiteWorkspaceTrustStore(tmp_path / "application.db")
    )
    service.accept(real_identity)

    assert link_identity.key == real_identity.key
    assert service.status(link_identity) is TrustStatus.TRUSTED


@pytest.mark.asyncio
async def test_branch_is_not_read_before_trust_and_is_cached_afterward(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    calls: list[Path] = []

    def branch_reader(path: Path) -> str:
        calls.append(path)
        return "feature/auth"

    monkeypatch.setattr(composition, "_git_branch", branch_reader)
    application = await composition.compose_local_application(
        home=tmp_path / "home",
        workspace=workspace,
        event_sink=CollectingEventSink(),
        environ={},
    )

    pending = _unwrap(await application.initialize())
    assert pending.status is InitializeStatus.TRUST_REQUIRED
    assert pending.workspace.display_path == str(workspace)
    assert pending.workspace.branch is None
    assert calls == []

    assert pending.interaction_id is not None
    _unwrap(await application.respond_interaction(pending.interaction_id, "trust"))
    ready = _unwrap(await application.initialize())
    state = _unwrap(await application.get_state())

    assert ready.workspace.branch == "feature/auth"
    assert state.workspace.branch == "feature/auth"
    assert state.current_thread_id is None
    assert _unwrap(await application.list_threads(ThreadListQuery())).threads == ()
    assert calls == [workspace.resolve()]
    _unwrap(await application.shutdown())
