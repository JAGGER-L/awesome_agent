from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from awesome_agent.core.tools.contracts import ToolErrorCode
from awesome_agent.core.tools.errors import ExpectedToolFailure
from awesome_agent.core.workspace import WorkspaceIdentity

type ExpectedPathKind = Literal["file", "directory"]


@dataclass(frozen=True, slots=True)
class SafeWorkspacePath:
    requested: str
    relative: Path
    resolved: Path


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


def resolve_workspace_path(
    identity: WorkspaceIdentity,
    requested: str,
    *,
    must_exist: bool,
    expected_kind: ExpectedPathKind | None = None,
    allow_sensitive: bool = False,
) -> SafeWorkspacePath:
    requested_path = Path(requested)
    if requested_path.is_absolute():
        raise ExpectedToolFailure(
            ToolErrorCode.WORKSPACE_ESCAPE,
            "Absolute paths are outside the workspace boundary.",
        )

    workspace = identity.canonical_path
    try:
        resolved = (workspace / requested_path).resolve(strict=must_exist)
    except FileNotFoundError as error:
        raise ExpectedToolFailure(
            ToolErrorCode.NOT_FOUND,
            "Path was not found.",
            metadata={"path": requested},
        ) from error
    except OSError as error:
        raise ExpectedToolFailure(
            ToolErrorCode.PERMISSION_DENIED,
            "Path could not be resolved.",
            metadata={"path": requested},
        ) from error

    if not resolved.is_relative_to(workspace):
        raise ExpectedToolFailure(
            ToolErrorCode.WORKSPACE_ESCAPE,
            "Path escapes the workspace boundary.",
            metadata={"path": requested},
        )

    relative = resolved.relative_to(workspace)
    lexical_relative = requested_path
    if not allow_sensitive and (
        _is_sensitive(lexical_relative) or _is_sensitive(relative)
    ):
        raise ExpectedToolFailure(
            ToolErrorCode.PERMISSION_DENIED,
            "Sensitive paths are not available to tools.",
            metadata={"path": requested},
        )

    if must_exist and expected_kind == "file" and not resolved.is_file():
        raise ExpectedToolFailure(
            ToolErrorCode.INVALID_ARGUMENTS,
            "Path is not a file.",
            metadata={"path": requested},
        )
    if must_exist and expected_kind == "directory" and not resolved.is_dir():
        raise ExpectedToolFailure(
            ToolErrorCode.INVALID_ARGUMENTS,
            "Path is not a directory.",
            metadata={"path": requested},
        )

    return SafeWorkspacePath(
        requested=requested,
        relative=relative,
        resolved=resolved,
    )
