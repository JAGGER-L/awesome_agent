from __future__ import annotations

import hashlib
import os
from pathlib import Path

from awesome_agent.core.workspace.models import (
    WorkspaceErrorCode,
    WorkspaceIdentity,
    WorkspaceResolutionError,
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
