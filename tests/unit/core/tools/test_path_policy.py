from pathlib import Path

import pytest

from awesome_agent.core.tools.errors import ExpectedToolFailure
from awesome_agent.core.tools.policy import resolve_workspace_path
from awesome_agent.core.workspace import resolve_workspace


def test_absolute_and_parent_paths_are_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    identity = resolve_workspace(workspace)

    for requested in (str(tmp_path / "outside.txt"), "../outside.txt"):
        with pytest.raises(ExpectedToolFailure) as captured:
            resolve_workspace_path(identity, requested, must_exist=False)
        assert captured.value.code.value == "workspace_escape"


def test_prefix_collision_is_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "workspace-private"
    workspace.mkdir()
    outside.mkdir()

    with pytest.raises(ExpectedToolFailure) as captured:
        resolve_workspace_path(
            resolve_workspace(workspace),
            "../workspace-private/new.txt",
            must_exist=False,
        )

    assert captured.value.code.value == "workspace_escape"


def test_sensitive_file_is_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".env").write_text("SECRET=value", encoding="utf-8")

    with pytest.raises(ExpectedToolFailure) as captured:
        resolve_workspace_path(
            resolve_workspace(workspace),
            ".env",
            must_exist=True,
            allow_sensitive=False,
        )
    assert captured.value.code.value == "permission_denied"


def test_file_symlink_escape_is_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = workspace / "linked.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("File symlinks are not available on this platform.")

    with pytest.raises(ExpectedToolFailure) as captured:
        resolve_workspace_path(
            resolve_workspace(workspace),
            "linked.txt",
            must_exist=True,
        )

    assert captured.value.code.value == "workspace_escape"
