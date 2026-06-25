from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from awesome_agent.domain.models import Repository
from awesome_agent.repositories.config import LocalRepositoryConfigStore
from awesome_agent.repositories.git import require_primary_clean_repository
from awesome_agent.repositories.policy import ensure_allowed_path, normalize_path
from awesome_agent.repositories.registry import RepositoryRegistry


class RepositoryService:
    def __init__(
        self,
        *,
        registry: RepositoryRegistry,
        config: LocalRepositoryConfigStore,
    ) -> None:
        self.registry = registry
        self.config = config

    async def register(self, path: Path) -> Repository:
        configured = self.config.load()
        allowed = ensure_allowed_path(path, configured.allowed_roots)
        snapshot = await require_primary_clean_repository(allowed)
        now = datetime.now(UTC)
        return await self.registry.upsert(
            Repository(
                root=snapshot.root,
                display_name=snapshot.root.name,
                git_common_dir=snapshot.git_common_dir,
                default_branch=snapshot.branch,
                created_at=now,
                updated_at=now,
                last_seen_at=now,
            )
        )

    async def relocate(
        self,
        repository_id: UUID,
        path: Path,
    ) -> Repository:
        configured = self.config.load()
        allowed = ensure_allowed_path(path, configured.allowed_roots)
        snapshot = await require_primary_clean_repository(allowed)
        current = await self.registry.get(repository_id)
        now = datetime.now(UTC)
        return await self.registry.relocate(
            repository_id,
            Repository(
                root=snapshot.root,
                display_name=snapshot.root.name,
                git_common_dir=snapshot.git_common_dir,
                default_branch=snapshot.branch,
                enabled=current.enabled,
                created_at=current.created_at,
                updated_at=now,
                last_seen_at=now,
            ),
        )

    async def remove_allowed_root(
        self,
        root: Path,
        *,
        force: bool = False,
    ) -> list[Repository]:
        normalized = normalize_path(root)
        dependent = [
            repository
            for repository in await self.registry.list(enabled_only=True)
            if repository.root == normalized
            or repository.root.is_relative_to(normalized)
        ]
        if dependent and not force:
            raise ValueError(
                "Allowed root has enabled repositories; use --force to disable them."
            )
        disabled = [
            await self.registry.disable(repository.id) for repository in dependent
        ]
        self.config.remove_root(normalized)
        return disabled
