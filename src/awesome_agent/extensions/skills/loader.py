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
    SkillIdentitySnapshot,
    _SkillBoundary,
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

    def descriptors(
        self,
        *,
        limit: int | None = None,
    ) -> tuple[SkillDescriptor, ...]:
        return self._catalog.descriptors(limit=limit)

    def identity_snapshot(self, name: str) -> SkillIdentitySnapshot:
        return self._catalog.identity_snapshot(name)

    def identity_snapshots(
        self,
        *,
        limit: int | None = None,
    ) -> tuple[SkillIdentitySnapshot, ...]:
        return self._catalog.identity_snapshots(limit=limit)

    def admit_load(self, name: str, *, expected_identity: str) -> None:
        _, boundary = self._target(name, expected_identity=expected_identity)
        try:
            with _pinned_skill_package(boundary) as pinned:
                _read_pinned_skill_file(pinned, boundary)
        except SkillResourceError:
            raise
        except FileNotFoundError as error:
            raise SkillResourceError(
                "Skill was not found.",
                kind=SkillResourceErrorKind.NOT_FOUND,
            ) from error
        except FileChangedError as error:
            raise SkillResourceError(
                "Skill changed after discovery.",
                kind=SkillResourceErrorKind.CONFLICT,
            ) from error
        except (OSError, UnsafePathError, ValueError) as error:
            raise SkillResourceError(
                "Skill could not be opened safely.",
                kind=SkillResourceErrorKind.PERMISSION_DENIED,
            ) from error

    def admit_resource(
        self,
        name: str,
        relative_path: str,
        *,
        expected_identity: str,
    ) -> None:
        _, boundary = self._target(name, expected_identity=expected_identity)
        requested = _resource_path(relative_path)
        try:
            with _pinned_skill_package(boundary) as pinned:
                _read_pinned_skill_file(pinned, boundary)
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
                "Skill changed after discovery.",
                kind=SkillResourceErrorKind.CONFLICT,
            ) from error
        except (OSError, UnsafePathError, ValueError) as error:
            raise SkillResourceError(
                "Skill resource could not be opened safely.",
                kind=SkillResourceErrorKind.PERMISSION_DENIED,
            ) from error

    def load(
        self,
        name: str,
        *,
        expected_identity: str,
        token_limit: int = 5_000,
    ) -> LoadedSkill:
        descriptor, boundary = self._target(
            name,
            expected_identity=expected_identity,
        )
        try:
            with _pinned_skill_package(boundary) as pinned:
                text = _decode_text(_read_pinned_skill_file(pinned, boundary))
        except SkillResourceError:
            raise
        except FileChangedError as error:
            raise SkillResourceError(
                "Skill changed after discovery.",
                kind=SkillResourceErrorKind.CONFLICT,
            ) from error
        except FileNotFoundError as error:
            raise SkillResourceError(
                "Skill was not found.",
                kind=SkillResourceErrorKind.NOT_FOUND,
            ) from error
        except (OSError, UnsafePathError) as error:
            raise SkillResourceError(
                "Skill could not be read safely.",
                kind=SkillResourceErrorKind.PERMISSION_DENIED,
            ) from error
        except (FileTooLargeError, UnicodeError, ValueError) as error:
            raise SkillResourceError("Skill content is invalid.") from error
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
        expected_identity: str,
        token_limit: int,
    ) -> SkillResource:
        _, boundary = self._target(name, expected_identity=expected_identity)
        requested = _resource_path(relative_path)
        try:
            with _pinned_skill_package(boundary) as pinned:
                _read_pinned_skill_file(pinned, boundary)
                content = _decode_text(
                    pinned.read_file(
                        requested,
                        max_bytes=_MAX_RESOURCE_BYTES,
                    ).data
                )
        except SkillResourceError:
            raise
        except FileChangedError as error:
            raise SkillResourceError(
                "Skill changed after discovery.",
                kind=SkillResourceErrorKind.CONFLICT,
            ) from error
        except FileTooLargeError as error:
            raise SkillResourceError(
                "Skill resource exceeds the 1 MiB limit."
            ) from error
        except UnicodeDecodeError as error:
            raise SkillResourceError("Skill resource is not UTF-8 text.") from error
        except (FileNotFoundError, NotADirectoryError) as error:
            raise SkillResourceError(
                "Skill resource was not found.",
                kind=SkillResourceErrorKind.NOT_FOUND,
            ) from error
        except UnsafePathError as error:
            raise SkillResourceError(
                "Skill resources cannot traverse links or reparse points.",
                kind=SkillResourceErrorKind.PERMISSION_DENIED,
            ) from error
        except OSError as error:
            raise SkillResourceError(
                "Skill resource could not be opened safely.",
                kind=SkillResourceErrorKind.PERMISSION_DENIED,
            ) from error
        bounded, truncated = _bounded(content, token_limit)
        return SkillResource(
            skill_name=name,
            relative_path=requested.as_posix(),
            content=bounded,
            estimated_tokens=estimate_text(bounded),
            truncated=truncated,
        )

    def _target(
        self,
        name: str,
        *,
        expected_identity: str,
    ) -> tuple[SkillDescriptor, _SkillBoundary]:
        descriptor = self._catalog.resolve(name)
        snapshot = self._catalog.identity_snapshot(name)
        if snapshot.identity != expected_identity:
            raise SkillResourceError(
                "Skill identity does not match the frozen Turn snapshot.",
                kind=SkillResourceErrorKind.CONFLICT,
            )
        boundary = self._catalog._boundary(name)
        if boundary is None:
            raise SkillResourceError(
                "Skill identity is unavailable.",
                kind=SkillResourceErrorKind.CONFLICT,
            )
        return descriptor, boundary


def _read_pinned_skill_file(
    pinned: PinnedPlainDirectory,
    boundary: _SkillBoundary,
) -> bytes:
    _preflight_regular_file(
        pinned,
        Path("SKILL.md"),
        expected=boundary.skill_file_fingerprint,
    )
    bounded = pinned.read_file(
        Path("SKILL.md"),
        max_bytes=_MAX_RESOURCE_BYTES,
        expected=boundary.skill_file_fingerprint,
    )
    if sha256(bounded.data).hexdigest() != boundary.skill_file_content_hash:
        raise FileChangedError("SKILL.md content changed after discovery.")
    return bounded.data


def _pinned_skill_package(boundary: _SkillBoundary) -> PinnedPlainDirectory:
    try:
        root_relative = boundary.source_root.relative_to(boundary.anchor)
        package_relative = boundary.package_root.relative_to(boundary.anchor)
        boundary.package_root.relative_to(boundary.source_root)
    except ValueError as error:
        raise UnsafePathError("Skill escapes its trusted source root.") from error
    identities = boundary.package_component_identities
    root_index = len(root_relative.parts)
    if (
        len(identities) != len(package_relative.parts) + 1
        or identities[0] != boundary.anchor_identity
        or identities[root_index] != boundary.source_root_identity
        or identities[-1] != boundary.package_root_identity
    ):
        raise FileChangedError("Skill boundary is inconsistent.")
    return PinnedPlainDirectory(
        boundary.anchor,
        boundary.package_root,
        expected_identities=identities,
        mount_boundary=boundary.source_root,
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
        try:
            info = pinned.child_status(parts[-1])
        except FileNotFoundError as error:
            if expected is not None:
                raise FileChangedError(
                    "Skill file disappeared after discovery."
                ) from error
            raise
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
        if expected is not None and (
            file_identity(info) != expected.identity
            or int(info.st_size) != expected.size
            or int(info.st_mtime_ns) != expected.modified_ns
        ):
            raise SkillResourceError(
                "Skill changed after discovery.",
                kind=SkillResourceErrorKind.CONFLICT,
            )
        if int(info.st_size) > _MAX_RESOURCE_BYTES:
            raise SkillResourceError("Skill resource exceeds the 1 MiB limit.")


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
