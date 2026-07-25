import os
import stat
from pathlib import Path

import pytest

from awesome_agent.core.workspace import (
    WorkspaceErrorCode,
    WorkspaceResolutionError,
    resolve_workspace,
)


def test_resolve_workspace_keeps_startup_directory(tmp_path: Path) -> None:
    nested = tmp_path / "repo" / "nested"
    nested.mkdir(parents=True)
    (tmp_path / "repo" / ".git").mkdir()

    identity = resolve_workspace(nested)

    assert identity.canonical_path == nested.resolve(strict=True)
    assert identity.display_path == nested
    assert identity.key.startswith("ws_")
    root_status = os.lstat(nested)
    assert identity.root_identity.device == root_status.st_dev
    assert identity.root_identity.inode == root_status.st_ino
    assert identity.root_identity.file_type == stat.S_IFMT(root_status.st_mode)
    assert identity.root_identity.reparse is False


def test_missing_workspace_fails_with_typed_code(tmp_path: Path) -> None:
    with pytest.raises(WorkspaceResolutionError) as captured:
        resolve_workspace(tmp_path / "missing")

    assert captured.value.code is WorkspaceErrorCode.NOT_FOUND


def test_file_is_not_a_workspace(tmp_path: Path) -> None:
    path = tmp_path / "file.txt"
    path.write_text("x", encoding="utf-8")

    with pytest.raises(WorkspaceResolutionError) as captured:
        resolve_workspace(path)

    assert captured.value.code is WorkspaceErrorCode.NOT_DIRECTORY
