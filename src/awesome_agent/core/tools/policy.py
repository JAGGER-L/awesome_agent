from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from awesome_agent.core.filesystem import FileIdentity
from awesome_agent.core.filesystem import identity as filesystem_identity
from awesome_agent.core.tools.contracts import ToolErrorCode
from awesome_agent.core.tools.errors import ExpectedToolFailure
from awesome_agent.core.workspace import (
    WorkspaceIdentity,
    WorkspaceIdentityChanged,
    require_workspace_identity,
)
from awesome_agent.core.workspace.path_syntax import (
    WorkspacePathPlatform,
    WorkspacePathSyntaxError,
    WorkspacePathSyntaxKind,
    validate_workspace_relative_path_syntax,
)

type ExpectedPathKind = Literal["file", "directory"]


@dataclass(frozen=True, slots=True)
class SafeWorkspacePath:
    requested: str
    relative: Path
    resolved: Path
    workspace: Path
    workspace_root_identity: FileIdentity
    target_existed: bool
    target_identity: FileIdentity | None

    def __post_init__(self) -> None:
        if self.target_existed != (self.target_identity is not None):
            raise ValueError(
                "A safe workspace path must bind target existence and identity."
            )


def _is_sensitive(relative: Path) -> bool:
    parts = tuple(part.casefold() for part in relative.parts)
    for index, part in enumerate(parts):
        if part == ".ssh":
            return True
        if part == ".env" or (part.startswith(".env.") and part != ".env.example"):
            return True
        if part in {"id_rsa", "id_ed25519"}:
            return True
        if part.endswith((".pem", ".key")):
            return True
        if "credentials" in part or "secrets" in part:
            return True
        if (
            part == ".aws"
            and index + 1 < len(parts)
            and parts[index + 1] == "credentials"
        ):
            return True
    return False


def is_sensitive_workspace_path(relative: Path) -> bool:
    return _is_sensitive(relative)


def validate_workspace_path_syntax(
    requested: str,
    *,
    platform: WorkspacePathPlatform | None = None,
) -> None:
    try:
        validate_workspace_relative_path_syntax(requested, platform=platform)
    except WorkspacePathSyntaxError as error:
        if error.kind is WorkspacePathSyntaxKind.ESCAPE:
            raise ExpectedToolFailure(
                ToolErrorCode.WORKSPACE_ESCAPE,
                "Path is outside the workspace boundary.",
            ) from error
        raise ExpectedToolFailure(
            ToolErrorCode.INVALID_ARGUMENTS,
            "Path contains an ambiguous filesystem component.",
            metadata={"path": requested},
        ) from error


def resolve_workspace_path(
    identity: WorkspaceIdentity,
    requested: str,
    *,
    must_exist: bool,
    expected_kind: ExpectedPathKind | None = None,
    allow_sensitive: bool = False,
) -> SafeWorkspacePath:
    validate_workspace_path_syntax(requested)
    try:
        require_workspace_identity(identity)
    except WorkspaceIdentityChanged as error:
        raise ExpectedToolFailure(
            ToolErrorCode.CONFLICT,
            "Workspace root changed after this session started.",
            metadata={"path": requested},
        ) from error
    requested_path = Path(requested)

    workspace = identity.canonical_path
    relative = Path(*(part for part in requested_path.parts if part != "."))
    if not relative.parts:
        relative = Path(".")
    resolved = workspace / relative
    lexical_relative = requested_path
    if not allow_sensitive and (
        _is_sensitive(lexical_relative) or _is_sensitive(relative)
    ):
        raise ExpectedToolFailure(
            ToolErrorCode.PERMISSION_DENIED,
            "Sensitive paths are not available to tools.",
            metadata={"path": requested},
        )

    components = [workspace]
    current = workspace
    for part in relative.parts:
        if part == ".":
            continue
        current /= part
        components.append(current)

    final_status: os.stat_result | None = None
    for index, component in enumerate(components):
        final = index == len(components) - 1
        try:
            status = os.lstat(component)
        except FileNotFoundError as error:
            if must_exist or not final:
                raise ExpectedToolFailure(
                    ToolErrorCode.NOT_FOUND,
                    "Path was not found.",
                    metadata={"path": requested},
                ) from error
            break
        except OSError as error:
            raise ExpectedToolFailure(
                ToolErrorCode.PERMISSION_DENIED,
                "Path could not be inspected safely.",
                metadata={"path": requested},
            ) from error
        attributes = int(getattr(status, "st_file_attributes", 0))
        reparse_attribute = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        if stat.S_ISLNK(status.st_mode) or attributes & reparse_attribute:
            raise ExpectedToolFailure(
                ToolErrorCode.PERMISSION_DENIED,
                "Links and reparse points are not allowed in workspace paths.",
                metadata={"path": requested},
            )
        if index == 0 and filesystem_identity(status) != identity.root_identity:
            raise ExpectedToolFailure(
                ToolErrorCode.CONFLICT,
                "Workspace root changed after this session started.",
                metadata={"path": requested},
            )
        if not final and not stat.S_ISDIR(status.st_mode):
            raise ExpectedToolFailure(
                ToolErrorCode.INVALID_ARGUMENTS,
                "Path component is not a directory.",
                metadata={"path": requested},
            )
        if final:
            final_status = status

    if (
        must_exist
        and expected_kind == "file"
        and (final_status is None or not stat.S_ISREG(final_status.st_mode))
    ):
        raise ExpectedToolFailure(
            ToolErrorCode.INVALID_ARGUMENTS,
            "Path is not a file.",
            metadata={"path": requested},
        )
    if (
        must_exist
        and expected_kind == "directory"
        and (final_status is None or not stat.S_ISDIR(final_status.st_mode))
    ):
        raise ExpectedToolFailure(
            ToolErrorCode.INVALID_ARGUMENTS,
            "Path is not a directory.",
            metadata={"path": requested},
        )

    return SafeWorkspacePath(
        requested=requested,
        relative=relative,
        resolved=resolved,
        workspace=workspace,
        workspace_root_identity=identity.root_identity,
        target_existed=final_status is not None,
        target_identity=(
            filesystem_identity(final_status) if final_status is not None else None
        ),
    )
