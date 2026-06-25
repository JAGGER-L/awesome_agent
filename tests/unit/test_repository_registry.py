from pathlib import Path

import pytest

from awesome_agent.domain.models import Repository
from awesome_agent.repositories.registry import InMemoryRepositoryRegistry


@pytest.mark.asyncio
async def test_registry_refreshes_duplicate_git_identity(tmp_path: Path) -> None:
    registry = InMemoryRepositoryRegistry()
    common_dir = tmp_path / "repository" / ".git"
    original = Repository(
        root=tmp_path / "repository",
        display_name="original",
        git_common_dir=common_dir,
    )
    created = await registry.upsert(original)

    refreshed = await registry.upsert(
        Repository(
            root=tmp_path / "relocated",
            display_name="renamed",
            git_common_dir=common_dir,
        )
    )

    assert refreshed.id == created.id
    assert refreshed.display_name == "renamed"
    assert len(await registry.list()) == 1


@pytest.mark.asyncio
async def test_registry_can_filter_and_disable_repositories(tmp_path: Path) -> None:
    registry = InMemoryRepositoryRegistry()
    repository = await registry.upsert(
        Repository(
            root=tmp_path / "repository",
            display_name="repository",
            git_common_dir=tmp_path / "repository" / ".git",
        )
    )

    disabled = await registry.disable(repository.id)

    assert not disabled.enabled
    assert await registry.list(enabled_only=True) == []
