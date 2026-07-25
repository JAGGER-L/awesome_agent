from pathlib import Path

import pytest

from awesome_agent.core.tools.errors import ExpectedToolFailure
from awesome_agent.core.tools.policy import (
    resolve_workspace_path,
    validate_workspace_path_syntax,
)
from awesome_agent.core.workspace import resolve_workspace
from awesome_agent.core.workspace.path_syntax import WorkspacePathPlatform


def test_absolute_and_parent_paths_are_rejected(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    identity = resolve_workspace(workspace)

    for requested in (
        str(tmp_path / "outside.txt"),
        "../outside.txt",
        "/outside.txt",
    ):
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


def test_resolved_path_binds_target_existence_and_identity(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    existing = workspace / "existing.txt"
    existing.write_text("content", encoding="utf-8")
    identity = resolve_workspace(workspace)

    bound_existing = resolve_workspace_path(
        identity,
        "existing.txt",
        must_exist=True,
        expected_kind="file",
    )
    bound_missing = resolve_workspace_path(
        identity,
        "missing.txt",
        must_exist=False,
    )

    assert bound_existing.target_existed is True
    assert bound_existing.target_identity is not None
    assert bound_missing.target_existed is False
    assert bound_missing.target_identity is None


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

    assert captured.value.code.value == "permission_denied"


@pytest.mark.parametrize(
    "requested",
    [
        "file.txt:stream",
        "dir/name:stream:$DATA",
        ".env. ",
        "file.",
        "CON",
        "con.txt",
        "NUL.json",
        "dir/COM1.log",
        "LPT9",
        "COM¹.txt",
        "CONIN$",
        "ENV~1",
        "dir/AWESOM~1.TXT",
        "name?.txt",
    ],
)
def test_windows_ambiguous_components_are_rejected_on_any_host(
    requested: str,
) -> None:
    with pytest.raises(ExpectedToolFailure) as captured:
        validate_workspace_path_syntax(requested, platform="windows")

    assert captured.value.code.value == "invalid_arguments"


@pytest.mark.parametrize(
    "requested",
    ["file.txt:stream", ".env. ", "CON", "dir/NUL.txt"],
)
def test_posix_legal_names_are_not_rejected_by_windows_alias_rules(
    requested: str,
) -> None:
    validate_workspace_path_syntax(requested, platform="posix")


def test_leading_backslash_is_only_rooted_on_windows() -> None:
    with pytest.raises(ExpectedToolFailure) as captured:
        validate_workspace_path_syntax(r"\name", platform="windows")

    assert captured.value.code.value == "workspace_escape"
    validate_workspace_path_syntax(r"\name", platform="posix")


@pytest.mark.parametrize(
    "requested",
    ["COM10.txt", "NUL-safe.txt", "company.txt", ".env.example"],
)
def test_unambiguous_windows_components_remain_valid(requested: str) -> None:
    validate_workspace_path_syntax(requested, platform="windows")


@pytest.mark.parametrize("platform", ["windows", "posix"])
def test_nul_is_rejected_on_every_platform(
    platform: WorkspacePathPlatform,
) -> None:
    with pytest.raises(ExpectedToolFailure) as captured:
        validate_workspace_path_syntax(
            "file\x00name.txt",
            platform=platform,
        )

    assert captured.value.code.value == "invalid_arguments"
