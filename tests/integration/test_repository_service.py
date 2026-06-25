from pathlib import Path

import pytest

from awesome_agent.repositories.config import LocalRepositoryConfigStore
from awesome_agent.repositories.policy import RepositoryPathDenied
from awesome_agent.repositories.registry import InMemoryRepositoryRegistry
from awesome_agent.repositories.service import RepositoryService
from awesome_agent.sandbox.process import run_process

pytestmark = pytest.mark.integration


async def _git(path: Path, *arguments: str) -> None:
    result = await run_process(
        ["git", *arguments],
        command_label="git fixture",
        workspace=path,
        timeout_seconds=30,
    )
    assert result.exit_code == 0, result.stderr


async def _repository(path: Path) -> None:
    path.mkdir(parents=True)
    await _git(path, "init")
    await _git(path, "config", "user.email", "test@example.com")
    await _git(path, "config", "user.name", "Test")
    (path / "README.md").write_text("fixture\n", encoding="utf-8")
    await _git(path, "add", "README.md")
    await _git(path, "commit", "-m", "Initial")


@pytest.mark.asyncio
async def test_registration_is_allowed_and_idempotent(tmp_path: Path) -> None:
    allowed = tmp_path / "projects"
    repository_path = allowed / "repository"
    await _repository(repository_path)
    config = LocalRepositoryConfigStore(tmp_path / "config.toml")
    config.add_root(allowed)
    registry = InMemoryRepositoryRegistry()
    service = RepositoryService(registry=registry, config=config)

    first = await service.register(repository_path)
    second = await service.register(repository_path)

    assert second.id == first.id
    assert len(await registry.list()) == 1


@pytest.mark.asyncio
async def test_registration_rejects_path_outside_allowed_roots(
    tmp_path: Path,
) -> None:
    repository_path = tmp_path / "repository"
    await _repository(repository_path)
    config = LocalRepositoryConfigStore(tmp_path / "config.toml")
    service = RepositoryService(
        registry=InMemoryRepositoryRegistry(),
        config=config,
    )

    with pytest.raises(RepositoryPathDenied):
        await service.register(repository_path)


@pytest.mark.asyncio
async def test_root_removal_requires_force_for_enabled_repository(
    tmp_path: Path,
) -> None:
    allowed = tmp_path / "projects"
    repository_path = allowed / "repository"
    await _repository(repository_path)
    config = LocalRepositoryConfigStore(tmp_path / "config.toml")
    config.add_root(allowed)
    registry = InMemoryRepositoryRegistry()
    service = RepositoryService(registry=registry, config=config)
    repository = await service.register(repository_path)

    with pytest.raises(ValueError, match="enabled repositories"):
        await service.remove_allowed_root(allowed)

    disabled = await service.remove_allowed_root(allowed, force=True)

    assert [item.id for item in disabled] == [repository.id]
    assert config.load().allowed_roots == []
    assert not (await registry.get(repository.id)).enabled
