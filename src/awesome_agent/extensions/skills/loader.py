from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from awesome_agent.context import estimate_text
from awesome_agent.context._safe_files import (
    FileChangedError,
    FileTooLargeError,
    UnsafePathError,
    ensure_identity,
    read_bounded_file,
    validate_plain_components,
)
from awesome_agent.extensions.skills.models import (
    SkillCatalog,
    SkillDescriptor,
    _WorkspaceSkillBoundary,
)

_MAX_RESOURCE_BYTES = 1024 * 1024


class SkillResourceError(ValueError):
    pass


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

    def load(self, name: str, *, token_limit: int = 5_000) -> LoadedSkill:
        descriptor = self._catalog.resolve(name)
        boundary = self._catalog._workspace_boundary(name)
        if boundary is None:
            text = _read_text(descriptor.root / "SKILL.md", descriptor.root)
        else:
            try:
                _validate_workspace_boundary(boundary)
                bounded = read_bounded_file(
                    boundary.skill_file,
                    max_bytes=_MAX_RESOURCE_BYTES,
                    expected=boundary.skill_file_fingerprint,
                )
                if sha256(bounded.data).hexdigest() != boundary.skill_file_content_hash:
                    raise FileChangedError("SKILL.md content changed after discovery.")
                _validate_workspace_boundary(boundary)
                text = _decode_text(bounded.data)
            except FileChangedError as error:
                raise SkillResourceError(
                    "Workspace Skill changed after discovery."
                ) from error
            except (
                FileNotFoundError,
                FileTooLargeError,
                OSError,
                UnsafePathError,
                UnicodeError,
                ValueError,
            ) as error:
                raise SkillResourceError(
                    "Workspace Skill could not be read safely."
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
        requested = Path(relative_path)
        if requested.is_absolute() or ".." in requested.parts:
            raise SkillResourceError("Skill resource escapes its package.")
        target = descriptor.root / requested
        boundary = self._catalog._workspace_boundary(name)
        if boundary is None:
            content = _read_text(target, descriptor.root)
        else:
            content = _read_workspace_resource(target, boundary)
        bounded, truncated = _bounded(content, token_limit)
        return SkillResource(
            skill_name=name,
            relative_path=requested.as_posix(),
            content=bounded,
            estimated_tokens=estimate_text(bounded),
            truncated=truncated,
        )


def _read_text(path: Path, root: Path) -> str:
    current = root
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise SkillResourceError("Skill resource escapes its package.") from error
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise SkillResourceError("Skill resources cannot traverse symlinks.")
    try:
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, OSError) as error:
        raise SkillResourceError("Skill resource was not found.") from error
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise SkillResourceError("Skill resource is outside its package.")
    if resolved.stat().st_size > _MAX_RESOURCE_BYTES:
        raise SkillResourceError("Skill resource exceeds the 1 MiB limit.")
    data = resolved.read_bytes()
    if b"\x00" in data:
        raise SkillResourceError("Binary Skill resources are not supported.")
    try:
        return _decode_text(data)
    except UnicodeDecodeError as error:
        raise SkillResourceError("Skill resource is not UTF-8 text.") from error


def _read_workspace_resource(
    path: Path,
    boundary: _WorkspaceSkillBoundary,
) -> str:
    try:
        _validate_workspace_boundary(boundary)
        before = validate_plain_components(
            boundary.package_root,
            path,
            target_kind="file",
        )
        bounded = read_bounded_file(path, max_bytes=_MAX_RESOURCE_BYTES)
        after = validate_plain_components(
            boundary.package_root,
            path,
            target_kind="file",
        )
        if after != before:
            raise FileChangedError("Skill resource changed while being read.")
        _validate_workspace_boundary(boundary)
        return _decode_text(bounded.data)
    except FileChangedError as error:
        raise SkillResourceError("Workspace Skill changed after discovery.") from error
    except FileTooLargeError as error:
        raise SkillResourceError("Skill resource exceeds the 1 MiB limit.") from error
    except UnicodeDecodeError as error:
        raise SkillResourceError("Skill resource is not UTF-8 text.") from error
    except (FileNotFoundError, NotADirectoryError, OSError) as error:
        raise SkillResourceError("Skill resource was not found.") from error
    except UnsafePathError as error:
        raise SkillResourceError(
            "Skill resources cannot traverse links or reparse points."
        ) from error


def _validate_workspace_boundary(boundary: _WorkspaceSkillBoundary) -> None:
    try:
        validate_plain_components(
            boundary.workspace_anchor,
            boundary.package_root,
            target_kind="directory",
        )
        ensure_identity(
            boundary.workspace_anchor,
            boundary.workspace_anchor_identity,
        )
        ensure_identity(boundary.skills_root, boundary.skills_root_identity)
        ensure_identity(boundary.package_root, boundary.package_root_identity)
    except (FileNotFoundError, NotADirectoryError, OSError, UnsafePathError) as error:
        raise FileChangedError("Workspace Skill changed after discovery.") from error


def _decode_text(data: bytes) -> str:
    if b"\x00" in data:
        raise SkillResourceError("Binary Skill resources are not supported.")
    return (
        data.decode("utf-8", errors="strict").replace("\r\n", "\n").replace("\r", "\n")
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
