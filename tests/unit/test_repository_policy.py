from pathlib import Path

import pytest

from awesome_agent.repositories.policy import (
    RepositoryPathDenied,
    ensure_allowed_path,
)


def test_allowed_path_rejects_prefix_collision(tmp_path: Path) -> None:
    allowed = tmp_path / "projects"
    denied = tmp_path / "projects-private"
    allowed.mkdir()
    denied.mkdir()

    with pytest.raises(RepositoryPathDenied):
        ensure_allowed_path(denied, [allowed])


def test_allowed_path_accepts_descendant(tmp_path: Path) -> None:
    allowed = tmp_path / "projects"
    repository = allowed / "repo"
    repository.mkdir(parents=True)

    assert ensure_allowed_path(repository, [allowed]) == repository.resolve()


def test_allowed_path_rejects_symlink_escape(tmp_path: Path) -> None:
    allowed = tmp_path / "projects"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    link = allowed / "linked-repository"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks are not available on this platform.")

    with pytest.raises(RepositoryPathDenied):
        ensure_allowed_path(link, [allowed])
