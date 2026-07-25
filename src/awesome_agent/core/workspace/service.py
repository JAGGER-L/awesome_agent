from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Protocol

from awesome_agent.core.filesystem import (
    MutationTargetChanged,
    UnsafeWorkspacePath,
    is_link_or_reparse,
    open_directory,
)
from awesome_agent.core.filesystem import (
    identity as filesystem_identity,
)
from awesome_agent.core.workspace.models import (
    TrustStatus,
    WorkspaceErrorCode,
    WorkspaceIdentity,
    WorkspaceIdentityChanged,
    WorkspaceResolutionError,
    WorkspaceTrust,
)


def resolve_workspace(path: Path) -> WorkspaceIdentity:
    display_path = Path(path).expanduser()
    try:
        canonical = display_path.resolve(strict=True)
    except FileNotFoundError:
        raise WorkspaceResolutionError(
            WorkspaceErrorCode.NOT_FOUND, display_path
        ) from None
    except OSError as error:
        raise WorkspaceResolutionError(
            WorkspaceErrorCode.UNRESOLVABLE,
            display_path,
        ) from error
    try:
        canonical_status = os.lstat(canonical)
    except OSError as error:
        raise WorkspaceResolutionError(
            WorkspaceErrorCode.UNRESOLVABLE,
            display_path,
        ) from error
    if is_link_or_reparse(canonical_status):
        raise WorkspaceResolutionError(
            WorkspaceErrorCode.UNRESOLVABLE,
            display_path,
        )
    if not stat.S_ISDIR(canonical_status.st_mode):
        raise WorkspaceResolutionError(
            WorkspaceErrorCode.NOT_DIRECTORY,
            display_path,
        )
    try:
        root = open_directory(
            canonical,
            expected_identity=filesystem_identity(canonical_status),
        )
    except (MutationTargetChanged, UnsafeWorkspacePath, OSError) as error:
        raise WorkspaceResolutionError(
            WorkspaceErrorCode.UNRESOLVABLE,
            display_path,
        ) from error
    try:
        root_identity = root.identity
    finally:
        root.close()
    normalized = os.path.normcase(str(canonical))
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]
    return WorkspaceIdentity(
        key=f"ws_{digest}",
        canonical_path=canonical,
        display_path=display_path,
        root_identity=root_identity,
    )


def require_workspace_identity(workspace: WorkspaceIdentity) -> None:
    try:
        root = open_directory(
            workspace.canonical_path,
            expected_identity=workspace.root_identity,
        )
    except (
        FileNotFoundError,
        MutationTargetChanged,
        UnsafeWorkspacePath,
        OSError,
    ) as error:
        raise WorkspaceIdentityChanged(
            "The workspace root changed after the session was composed."
        ) from error
    root.close()


def workspace_runtime_key(workspace: WorkspaceIdentity) -> str:
    root = workspace.root_identity
    payload = f"{root.device}:{root.inode}:{root.file_type}".encode("ascii")
    digest = hashlib.sha256(payload).hexdigest()[:32]
    return f"entity_{digest}"


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
