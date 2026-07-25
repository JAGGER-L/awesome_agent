from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from awesome_agent.core.filesystem import FileIdentity


class WorkspaceErrorCode(StrEnum):
    NOT_FOUND = "workspace_not_found"
    NOT_DIRECTORY = "workspace_not_directory"
    UNRESOLVABLE = "workspace_unresolvable"


class WorkspaceResolutionError(ValueError):
    def __init__(self, code: WorkspaceErrorCode, path: Path) -> None:
        super().__init__(code.value)
        self.code = code
        self.path = path


class WorkspaceIdentityChanged(RuntimeError):
    """The path no longer names the filesystem object bound at composition."""


class WorkspaceIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    canonical_path: Path
    display_path: Path
    root_identity: FileIdentity


class TrustStatus(StrEnum):
    UNKNOWN = "unknown"
    TRUSTED = "trusted"


class WorkspaceTrust(BaseModel):
    model_config = ConfigDict(frozen=True)

    workspace_key: str
    canonical_path: Path
    trusted_at: datetime
