from __future__ import annotations

import stat
from enum import StrEnum
from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from awesome_agent.context import estimate_text
from awesome_agent.core.safe_files import (
    FileChangedError,
    FileFingerprint,
    FileTooLargeError,
    PinnedPlainDirectory,
    UnsafePathError,
    file_identity,
)
from awesome_agent.core.workspace.path_syntax import (
    WorkspacePathSyntaxError,
    validate_workspace_relative_path_syntax,
)
from awesome_agent.extensions.skills.models import (
    SkillCatalog,
    SkillDescriptor,
    _WorkspaceSkillBoundary,
)

_MAX_RESOURCE_BYTES = 1024 * 1024


class SkillResourceErrorKind(StrEnum):
    INVALID_ARGUMENTS = "invalid_arguments"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    PERMISSION_DENIED = "permission_denied"
    EXECUTION_FAILED = "execution_failed"


class SkillResourceError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        kind: SkillResourceErrorKind = SkillResourceErrorKind.EXECUTION_FAILED,
    ) -> None:
        super().__init__(message)
        self.kind = kind


class LoadedSkill(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    descriptor: SkillDescriptor
    body: str
    estimated_tokens: int = Field(ge=0)
    truncated: bool


class SkillResource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    skill_name: str
    relative_path: str
    content: str
    estimated_tokens: int = Field(ge=0)
    truncated: bool


class SkillLoader:
    def __init__(self, catalog: SkillCatalog) -> None:
        self._catalog = catalog

    def admit_load(self, name: str) -> None:
        descriptor = self._catalog.resolve(name)
        boundary = self._catalog._workspace_boundary(name)
        if boundary is None:
            try:
                with PinnedPlainDirectory(descriptor.root, descriptor.root) as pinned:
                    _preflight_regular_file(pinned, Path("SKILL.md"))
            except SkillResourceError:
                raise
            except FileNotFoundError as error:
                raise SkillResourceError(
                    "Skill was not found.",
                    kind=SkillResourceErrorKind.NOT_FOUND,
                ) from error
            except (FileChangedError, OSError, UnsafePathError, ValueError) as error:
                raise SkillResourceError(
                    "Skill could not be opened safely.",
                    kind=SkillResourceErrorKind.PERMISSION_DENIED,
                ) from error
            return
        try:
            with _pinned_workspace_package(boundary) as pinned:
                _preflight_regular_file(
                    pinned,
                    Path("SKILL.md"),
                    expected=boundary.skill_file_fingerprint,
                )
        except SkillResourceError:
            raise
        except FileNotFoundError as error:
            raise SkillResourceError(
                "Skill was not found.",
                kind=SkillResourceErrorKind.NOT_FOUND,
            ) from error
        except FileChangedError as error:
            raise SkillResourceError(
                "Workspace Skill changed after discovery.",
                kind=SkillResourceErrorKind.CONFLICT,
            ) from error
        except (OSError, UnsafePathError, ValueError) as error:
            raise SkillResourceError(
                "Workspace Skill could not be opened safely.",
                kind=SkillResourceErrorKind.PERMISSION_DENIED,
            ) from error

    def admit_resource(self, name: str, relative_path: str) -> None:
        descriptor = self._catalog.resolve(name)
        requested = _resource_path(relative_path)
        boundary = self._catalog._workspace_boundary(name)
        try:
            if boundary is None:
                with PinnedPlainDirectory(descriptor.root, descriptor.root) as pinned:
                    _preflight_regular_file(pinned, requested)
            else:
                with _pinned_workspace_package(boundary) as pinned:
                    _preflight_regular_file(pinned, requested)
        except SkillResourceError:
            raise
        except FileNotFoundError as error:
            raise SkillResourceError(
                "Skill resource was not found.",
                kind=SkillResourceErrorKind.NOT_FOUND,
            ) from error
        except FileChangedError as error:
            raise SkillResourceError(
                "Workspace Skill changed after discovery.",
                kind=SkillResourceErrorKind.CONFLICT,
            ) from error
        except (OSError, UnsafePathError, ValueError) as error:
            raise SkillResourceError(
                "Skill resource could not be opened safely.",
                kind=SkillResourceErrorKind.PERMISSION_DENIED,
            ) from error

    def load(self, name: str, *, token_limit: int = 5_000) -> LoadedSkill:
        descriptor = self._catalog.resolve(name)
        boundary = self._catalog._workspace_boundary(name)
        if boundary is None:
            text = _read_standard_resource(Path("SKILL.md"), descriptor.root)
        else:
            try:
                with _pinned_workspace_package(boundary) as pinned:
                    bounded = pinned.read_file(
                        Path("SKILL.md"),
                        max_bytes=_MAX_RESOURCE_BYTES,
                        expected=boundary.skill_file_fingerprint,
                    )
                    if (
                        sha256(bounded.data).hexdigest()
                        != boundary.skill_file_content_hash
                    ):
                        raise FileChangedError(
                            "SKILL.md content changed after discovery."
                        )
                    text = _decode_text(bounded.data)
            except FileChangedError as error:
                raise SkillResourceError(
                    "Workspace Skill changed after discovery.",
                    kind=SkillResourceErrorKind.CONFLICT,
                ) from error
            except FileNotFoundError as error:
                raise SkillResourceError(
                    "Skill was not found.",
                    kind=SkillResourceErrorKind.NOT_FOUND,
                ) from error
            except (OSError, UnsafePathError) as error:
                raise SkillResourceError(
                    "Workspace Skill could not be read safely.",
                    kind=SkillResourceErrorKind.PERMISSION_DENIED,
                ) from error
            except (FileTooLargeError, UnicodeError, ValueError) as error:
                raise SkillResourceError(
                    "Workspace Skill content is invalid."
                ) from error
        parts = text.split("---", 2)
        if len(parts) != 3:
            raise SkillResourceError("SKILL.md frontmatter is incomplete.")
        body, truncated = _bounded(parts[2].lstrip("\r\n"), token_limit)
        return LoadedSkill(
            descriptor=descriptor,
            body=body,
            estimated_tokens=estimate_text(body),
            truncated=truncated,
        )

    def read_resource(
        self,
        name: str,
        relative_path: str,
        *,
        token_limit: int,
    ) -> SkillResource:
        descriptor = self._catalog.resolve(name)
        requested = _resource_path(relative_path)
        boundary = self._catalog._workspace_boundary(name)
        if boundary is None:
            content = _read_standard_resource(requested, descriptor.root)
        else:
            content = _read_workspace_resource(requested, boundary)
        bounded, truncated = _bounded(content, token_limit)
        return SkillResource(
            skill_name=name,
            relative_path=requested.as_posix(),
            content=bounded,
            estimated_tokens=estimate_text(bounded),
            truncated=truncated,
        )


def _read_standard_resource(relative: Path, root: Path) -> str:
    try:
        with PinnedPlainDirectory(root, root) as pinned:
            bounded = pinned.read_file(relative, max_bytes=_MAX_RESOURCE_BYTES)
            return _decode_text(bounded.data)
    except SkillResourceError:
        raise
    except FileChangedError as error:
        raise SkillResourceError(
            "Skill resource changed while it was being read.",
            kind=SkillResourceErrorKind.CONFLICT,
        ) from error
    except FileTooLargeError as error:
        raise SkillResourceError("Skill resource exceeds the 1 MiB limit.") from error
    except UnicodeDecodeError as error:
        raise SkillResourceError("Skill resource is not UTF-8 text.") from error
    except (FileNotFoundError, NotADirectoryError) as error:
        raise SkillResourceError(
            "Skill resource was not found.",
            kind=SkillResourceErrorKind.NOT_FOUND,
        ) from error
    except (OSError, UnsafePathError, ValueError) as error:
        raise SkillResourceError(
            "Skill resource could not be opened safely.",
            kind=SkillResourceErrorKind.PERMISSION_DENIED,
        ) from error


def _read_workspace_resource(
    relative: Path,
    boundary: _WorkspaceSkillBoundary,
) -> str:
    try:
        with _pinned_workspace_package(boundary) as pinned:
            bounded = pinned.read_file(
                relative,
                max_bytes=_MAX_RESOURCE_BYTES,
            )
            return _decode_text(bounded.data)
    except FileChangedError as error:
        raise SkillResourceError(
            "Workspace Skill changed after discovery.",
            kind=SkillResourceErrorKind.CONFLICT,
        ) from error
    except FileTooLargeError as error:
        raise SkillResourceError("Skill resource exceeds the 1 MiB limit.") from error
    except UnicodeDecodeError as error:
        raise SkillResourceError("Skill resource is not UTF-8 text.") from error
    except (FileNotFoundError, NotADirectoryError) as error:
        raise SkillResourceError(
            "Skill resource was not found.",
            kind=SkillResourceErrorKind.NOT_FOUND,
        ) from error
    except OSError as error:
        raise SkillResourceError(
            "Skill resource could not be opened safely.",
            kind=SkillResourceErrorKind.PERMISSION_DENIED,
        ) from error
    except UnsafePathError as error:
        raise SkillResourceError(
            "Skill resources cannot traverse links or reparse points.",
            kind=SkillResourceErrorKind.PERMISSION_DENIED,
        ) from error


def _pinned_workspace_package(
    boundary: _WorkspaceSkillBoundary,
) -> PinnedPlainDirectory:
    try:
        root_relative = boundary.skills_root.relative_to(boundary.workspace_anchor)
        package_relative = boundary.package_root.relative_to(boundary.workspace_anchor)
        boundary.package_root.relative_to(boundary.skills_root)
    except ValueError as error:
        raise UnsafePathError("Workspace Skill escapes its trusted anchor.") from error
    identities = boundary.package_component_identities
    root_index = len(root_relative.parts)
    if (
        len(identities) != len(package_relative.parts) + 1
        or identities[0] != boundary.workspace_anchor_identity
        or identities[root_index] != boundary.skills_root_identity
        or identities[-1] != boundary.package_root_identity
    ):
        raise FileChangedError("Workspace Skill boundary is inconsistent.")
    return PinnedPlainDirectory(
        boundary.workspace_anchor,
        boundary.package_root,
        expected_identities=identities,
    )


def _decode_text(data: bytes) -> str:
    if b"\x00" in data:
        raise SkillResourceError("Binary Skill resources are not supported.")
    return (
        data.decode("utf-8", errors="strict").replace("\r\n", "\n").replace("\r", "\n")
    )


def _resource_path(relative_path: str) -> Path:
    try:
        validate_workspace_relative_path_syntax(relative_path, platform="posix")
        validate_workspace_relative_path_syntax(relative_path, platform="windows")
    except WorkspacePathSyntaxError as error:
        raise SkillResourceError(
            "Skill resource path is invalid.",
            kind=SkillResourceErrorKind.INVALID_ARGUMENTS,
        ) from error
    requested = Path(relative_path)
    parts = tuple(part for part in requested.parts if part != ".")
    if (
        requested.is_absolute()
        or bool(requested.drive)
        or ".." in requested.parts
        or not parts
    ):
        raise SkillResourceError(
            "Skill resource path is invalid.",
            kind=SkillResourceErrorKind.INVALID_ARGUMENTS,
        )
    return Path(*parts)


def _preflight_regular_file(
    pinned: PinnedPlainDirectory,
    relative: Path,
    *,
    expected: FileFingerprint | None = None,
) -> None:
    parts = relative.parts
    if not parts:
        raise SkillResourceError(
            "Skill resource path is invalid.",
            kind=SkillResourceErrorKind.INVALID_ARGUMENTS,
        )
    with pinned.descend(Path(*parts[:-1])):
        info = pinned.child_status(parts[-1])
        attributes = int(getattr(info, "st_file_attributes", 0))
        reparse = bool(
            attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        )
        if (
            stat.S_ISLNK(info.st_mode)
            or reparse
            or not stat.S_ISREG(info.st_mode)
            or int(info.st_nlink) != 1
        ):
            raise SkillResourceError(
                "Skill resource is not a plain regular file.",
                kind=SkillResourceErrorKind.PERMISSION_DENIED,
            )
        if int(info.st_size) > _MAX_RESOURCE_BYTES:
            raise SkillResourceError("Skill resource exceeds the 1 MiB limit.")
        if expected is not None and (
            file_identity(info) != expected.identity
            or int(info.st_size) != expected.size
            or int(info.st_mtime_ns) != expected.modified_ns
        ):
            raise SkillResourceError(
                "Workspace Skill changed after discovery.",
                kind=SkillResourceErrorKind.CONFLICT,
            )


def _bounded(content: str, token_limit: int) -> tuple[str, bool]:
    if token_limit <= 0:
        raise ValueError("token_limit must be positive")
    if estimate_text(content) <= token_limit:
        return content, False
    low = 0
    high = len(content)
    while low < high:
        midpoint = (low + high + 1) // 2
        if estimate_text(content[:midpoint]) <= token_limit:
            low = midpoint
        else:
            high = midpoint - 1
    return content[:low], True
