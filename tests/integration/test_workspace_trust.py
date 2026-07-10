from pathlib import Path

import pytest

from awesome_agent.core.workspace import (
    TrustStatus,
    WorkspaceTrustService,
    resolve_workspace,
)
from awesome_agent.storage.trust import SQLiteWorkspaceTrustStore


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
