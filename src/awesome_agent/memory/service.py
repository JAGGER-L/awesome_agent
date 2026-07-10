from __future__ import annotations

from collections.abc import Callable

from awesome_agent.memory.local_file import LocalMemoryFile, MemoryDocumentInvalid
from awesome_agent.memory.models import (
    LocalMemoryScopeStatus,
    LocalMemoryStatus,
    MemoryDocument,
    MemoryEntry,
    MemoryMutationResult,
    MemoryMutationStatus,
    MemoryPolicyStatus,
    MemoryScope,
)
from awesome_agent.memory.policy import LocalMemoryPolicy
from awesome_agent.paths import AwesomePaths


class LocalMemoryService:
    def __init__(
        self,
        *,
        paths: AwesomePaths,
        workspace_key: str,
        enabled: bool = False,
        policy: LocalMemoryPolicy | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._enabled = enabled
        self._workspace_key = workspace_key
        self._policy = policy or LocalMemoryPolicy()
        self._files = {
            MemoryScope.USER: LocalMemoryFile(
                path=paths.user_memory_file,
                scope=MemoryScope.USER,
                id_factory=id_factory,
            ),
            MemoryScope.WORKSPACE: LocalMemoryFile(
                path=paths.workspace_memory_file(workspace_key),
                scope=MemoryScope.WORKSPACE,
                id_factory=id_factory,
            ),
        }
        self._labels = {
            MemoryScope.USER: "memory/USER.md",
            MemoryScope.WORKSPACE: f"workspaces/{workspace_key}/MEMORY.md",
        }

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def workspace_key(self) -> str:
        return self._workspace_key

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def status(self) -> LocalMemoryStatus:
        statuses: list[LocalMemoryScopeStatus] = []
        for scope in MemoryScope:
            file = self._files[scope]
            try:
                document = file.snapshot()
                count = len(document.entries)
                error_code = None
            except MemoryDocumentInvalid as error:
                count = 0
                error_code = error.code
            statuses.append(
                LocalMemoryScopeStatus(
                    scope=scope,
                    label=self._labels[scope],
                    exists=file.path.is_file(),
                    entry_count=count,
                    error_code=error_code,
                )
            )
        return LocalMemoryStatus(enabled=self._enabled, scopes=tuple(statuses))

    def snapshot(self, scope: MemoryScope) -> MemoryDocument:
        return self._files[scope].snapshot()

    def list(self, scope: MemoryScope) -> tuple[MemoryEntry, ...]:
        return self.snapshot(scope).entries

    def add(
        self,
        scope: MemoryScope,
        content: str,
        *,
        expected_hash: str,
    ) -> MemoryMutationResult:
        disabled = self._disabled(scope)
        if disabled is not None:
            return disabled
        eligible = self._policy.evaluate(content)
        if eligible.status is MemoryPolicyStatus.REJECTED or eligible.content is None:
            return self._rejected(scope, expected_hash, eligible.error_code)
        return self._files[scope].add(
            eligible.content,
            expected_hash=expected_hash,
        )

    def replace(
        self,
        scope: MemoryScope,
        entry_id: str,
        content: str,
        *,
        expected_hash: str,
    ) -> MemoryMutationResult:
        disabled = self._disabled(scope)
        if disabled is not None:
            return disabled
        eligible = self._policy.evaluate(content)
        if eligible.status is MemoryPolicyStatus.REJECTED or eligible.content is None:
            return self._rejected(scope, expected_hash, eligible.error_code)
        return self._files[scope].replace(
            entry_id,
            eligible.content,
            expected_hash=expected_hash,
        )

    def remove(
        self,
        scope: MemoryScope,
        entry_id: str,
        *,
        expected_hash: str,
    ) -> MemoryMutationResult:
        disabled = self._disabled(scope)
        if disabled is not None:
            return disabled
        return self._files[scope].remove(entry_id, expected_hash=expected_hash)

    def _disabled(self, scope: MemoryScope) -> MemoryMutationResult | None:
        if self._enabled:
            return None
        document = self.snapshot(scope)
        return MemoryMutationResult(
            status=MemoryMutationStatus.DISABLED,
            scope=scope,
            content_hash=document.content_hash,
            document=document,
            error_code=MemoryMutationStatus.DISABLED.value,
        )

    def _rejected(
        self,
        scope: MemoryScope,
        expected_hash: str,
        policy_code: str | None,
    ) -> MemoryMutationResult:
        current = self.snapshot(scope)
        content_hash = current.content_hash
        status = (
            MemoryMutationStatus.CONFLICT
            if content_hash != expected_hash
            else MemoryMutationStatus.REJECTED
        )
        return MemoryMutationResult(
            status=status,
            scope=scope,
            content_hash=content_hash,
            document=current if status is MemoryMutationStatus.REJECTED else None,
            error_code=(
                MemoryMutationStatus.CONFLICT.value
                if status is MemoryMutationStatus.CONFLICT
                else policy_code or MemoryMutationStatus.REJECTED.value
            ),
        )
