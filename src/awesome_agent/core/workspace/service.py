from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Protocol

from awesome_agent.core.workspace.models import (
    TrustStatus,
    WorkspaceErrorCode,
    WorkspaceIdentity,
    WorkspaceResolutionError,
    WorkspaceTrust,
)


def resolve_workspace(path: Path) -> WorkspaceIdentity:
    display_path = Path(path).expanduser()
    if not display_path.exists():
        raise WorkspaceResolutionError(WorkspaceErrorCode.NOT_FOUND, display_path)
    if not display_path.is_dir():
        raise WorkspaceResolutionError(
            WorkspaceErrorCode.NOT_DIRECTORY,
            display_path,
        )
    try:
        canonical = display_path.resolve(strict=True)
    except OSError as error:
        raise WorkspaceResolutionError(
            WorkspaceErrorCode.UNRESOLVABLE,
            display_path,
        ) from error
    normalized = os.path.normcase(str(canonical))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]
    return WorkspaceIdentity(
        key=f"ws_{digest}",
        canonical_path=canonical,
        display_path=display_path,
    )


class WorkspaceTrustStore(Protocol):
    def get(self, workspace_key: str) -> WorkspaceTrust | None: ...

    def accept(self, identity: WorkspaceIdentity) -> WorkspaceTrust: ...

    def revoke(self, workspace_key: str) -> bool: ...


class WorkspaceTrustService:
    def __init__(self, store: WorkspaceTrustStore) -> None:
        self._store = store

    def status(self, identity: WorkspaceIdentity) -> TrustStatus:
        record = self._store.get(identity.key)
        if record is None or record.canonical_path != identity.canonical_path:
            return TrustStatus.UNKNOWN
        return TrustStatus.TRUSTED

    def accept(self, identity: WorkspaceIdentity) -> WorkspaceTrust:
        return self._store.accept(identity)

    def revoke(self, identity: WorkspaceIdentity) -> bool:
        return self._store.revoke(identity.key)
