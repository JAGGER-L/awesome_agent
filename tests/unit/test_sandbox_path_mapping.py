from pathlib import Path

import pytest

from awesome_agent.sandbox.path_mapping import WorkspacePathMapper


def test_local_mapper_translates_logical_workspace(tmp_path: Path) -> None:
    mapper = WorkspacePathMapper(thread_workspace=tmp_path / "workspace")

    assert mapper.to_host_path("/mnt/user-data/workspace/index.html") == (
        tmp_path / "workspace" / "index.html"
    )


def test_local_mapper_maps_workspace_root(tmp_path: Path) -> None:
    mapper = WorkspacePathMapper(thread_workspace=tmp_path / "workspace")

    assert mapper.to_host_path("/mnt/user-data/workspace") == (
        tmp_path / "workspace"
    )


def test_local_mapper_rejects_paths_outside_workspace(tmp_path: Path) -> None:
    mapper = WorkspacePathMapper(thread_workspace=tmp_path / "workspace")

    with pytest.raises(ValueError):
        mapper.to_host_path("/etc/passwd")
