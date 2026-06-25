from __future__ import annotations

from typing import Protocol
from uuid import UUID

from awesome_agent.domain.models import Repository


class RepositoryRegistry(Protocol):
    async def upsert(self, repository: Repository) -> Repository:
        """Create a repository identity or refresh its mutable metadata."""
        ...

    async def get(self, repository_id: UUID) -> Repository:
        """Load one repository identity."""
        ...

    async def list(self, *, enabled_only: bool = False) -> list[Repository]:
        """List registered repositories."""
        ...

    async def disable(self, repository_id: UUID) -> Repository:
        """Disable a repository without deleting its historical identity."""
        ...

    async def relocate(
        self,
        repository_id: UUID,
        repository: Repository,
    ) -> Repository:
        """Move an existing identity to an explicitly validated checkout."""
        ...


class InMemoryRepositoryRegistry(RepositoryRegistry):
    def __init__(self) -> None:
        self._repositories: dict[UUID, Repository] = {}

    async def upsert(self, repository: Repository) -> Repository:
        duplicate = next(
            (
                current
                for current in self._repositories.values()
                if current.git_common_dir == repository.git_common_dir
            ),
            None,
        )
        if duplicate is not None:
            refreshed = repository.model_copy(
                update={
                    "id": duplicate.id,
                    "created_at": duplicate.created_at,
                }
            )
            self._repositories[duplicate.id] = refreshed
            return refreshed
        self._repositories[repository.id] = repository
        return repository

    async def get(self, repository_id: UUID) -> Repository:
        return self._repositories[repository_id]

    async def list(self, *, enabled_only: bool = False) -> list[Repository]:
        repositories = sorted(
            self._repositories.values(),
            key=lambda repository: (repository.display_name, str(repository.id)),
        )
        if enabled_only:
            return [repository for repository in repositories if repository.enabled]
        return repositories

    async def disable(self, repository_id: UUID) -> Repository:
        repository = await self.get(repository_id)
        disabled = repository.model_copy(update={"enabled": False})
        self._repositories[repository_id] = disabled
        return disabled

    async def relocate(
        self,
        repository_id: UUID,
        repository: Repository,
    ) -> Repository:
        current = await self.get(repository_id)
        if any(
            candidate.id != repository_id
            and candidate.git_common_dir == repository.git_common_dir
            for candidate in self._repositories.values()
        ):
            raise ValueError("Git checkout is already registered.")
        relocated = repository.model_copy(
            update={
                "id": repository_id,
                "created_at": current.created_at,
            }
        )
        self._repositories[repository_id] = relocated
        return relocated
