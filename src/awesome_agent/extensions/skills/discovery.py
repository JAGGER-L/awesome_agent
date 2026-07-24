from __future__ import annotations

import os
import stat
from hashlib import sha256
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from awesome_agent.context._safe_files import (
    FileChangedError,
    FileTooLargeError,
    UnsafePathError,
    ensure_identity,
    lexical_absolute,
    plain_directory_identity,
    plain_file_fingerprint,
    read_bounded_file,
    validate_plain_components,
)
from awesome_agent.extensions.skills.models import (
    SkillCatalog,
    SkillDescriptor,
    SkillDiagnostic,
    SkillSource,
    _WorkspaceSkillBoundary,
)

_MAX_SKILL_FILE_BYTES = 1024 * 1024
_ALLOWED_FIELDS = frozenset(
    {
        "name",
        "description",
        "allowed-tools",
        "license",
        "compatibility",
        "metadata",
    }
)


def discover_skills(
    *,
    bundled_root: Path | None,
    user_root: Path | None,
    workspace_root: Path | None,
    workspace_trusted: bool,
    workspace_anchor: Path | None = None,
    disabled: set[str] | None = None,
) -> SkillCatalog:
    disabled_names = disabled or set()
    discovered: dict[str, SkillDescriptor] = {}
    diagnostics: list[SkillDiagnostic] = []
    boundaries: dict[str, _WorkspaceSkillBoundary] = {}

    for source, root in (
        (SkillSource.BUNDLED, bundled_root),
        (SkillSource.USER, user_root),
    ):
        if root is None or not root.is_dir():
            continue
        for directory in sorted(root.iterdir(), key=lambda path: path.name):
            if not directory.is_dir():
                continue
            descriptor = _standard_descriptor_or_diagnostic(
                directory,
                source,
                diagnostics,
            )
            if descriptor is not None:
                _retain_descriptor(
                    descriptor,
                    disabled_names,
                    discovered,
                    diagnostics,
                )

    if workspace_trusted and workspace_root is not None:
        anchor = workspace_anchor or workspace_root.parent.parent
        _discover_workspace_skills(
            workspace_root=workspace_root,
            workspace_anchor=anchor,
            disabled_names=disabled_names,
            discovered=discovered,
            diagnostics=diagnostics,
            boundaries=boundaries,
        )

    retained_boundaries = {
        name: boundary
        for name, boundary in boundaries.items()
        if name in discovered and discovered[name].source is SkillSource.WORKSPACE
    }
    return SkillCatalog(
        tuple(sorted(discovered.values(), key=lambda item: item.name)),
        tuple(diagnostics),
        retained_boundaries,
    )


def _discover_workspace_skills(
    *,
    workspace_root: Path,
    workspace_anchor: Path,
    disabled_names: set[str],
    discovered: dict[str, SkillDescriptor],
    diagnostics: list[SkillDiagnostic],
    boundaries: dict[str, _WorkspaceSkillBoundary],
) -> None:
    anchor = lexical_absolute(workspace_anchor)
    root = lexical_absolute(workspace_root)
    try:
        validate_plain_components(anchor, root, target_kind="directory")
        anchor_identity = plain_directory_identity(anchor)
        root_identity = plain_directory_identity(root)
    except FileNotFoundError:
        return
    except (NotADirectoryError, OSError, UnsafePathError):
        diagnostics.append(
            _diagnostic(
                "unsafe_workspace_skill_path",
                SkillSource.WORKSPACE,
                workspace_root,
                None,
                "Workspace Skill root uses an unsafe path component.",
            )
        )
        return

    try:
        with os.scandir(root) as scanner:
            entries = sorted(scanner, key=lambda entry: entry.name)
    except OSError:
        diagnostics.append(
            _diagnostic(
                "invalid_skill",
                SkillSource.WORKSPACE,
                root,
                None,
                "Workspace Skill root could not be enumerated.",
            )
        )
        return

    for entry in entries:
        directory = root / entry.name
        try:
            entry_info = entry.stat(follow_symlinks=False)
            if not stat.S_ISDIR(entry_info.st_mode):
                if _is_link_or_reparse(entry_info):
                    raise UnsafePathError("Workspace Skill package is a reparse point.")
                continue
            validate_plain_components(anchor, directory, target_kind="directory")
            package_identity = plain_directory_identity(directory)
            skill_file = directory / "SKILL.md"
            skill_components = validate_plain_components(
                anchor,
                skill_file,
                target_kind="file",
            )
            skill_fingerprint = plain_file_fingerprint(skill_file)
            if (
                not skill_components
                or skill_fingerprint.identity != skill_components[-1]
            ):
                raise FileChangedError("SKILL.md changed before its trusted snapshot.")
            bounded = read_bounded_file(
                skill_file,
                max_bytes=_MAX_SKILL_FILE_BYTES,
                expected=skill_fingerprint,
            )
            if bounded.fingerprint != skill_fingerprint:
                raise FileChangedError("SKILL.md changed before it was opened.")
            ensure_identity(anchor, anchor_identity)
            ensure_identity(root, root_identity)
            ensure_identity(directory, package_identity)
            text = _decode_skill_text(bounded.data)
            descriptor = _descriptor_from_text(
                directory,
                SkillSource.WORKSPACE,
                text,
                root_path=directory,
            )
        except UnsafePathError:
            diagnostics.append(
                _diagnostic(
                    "unsafe_workspace_skill_path",
                    SkillSource.WORKSPACE,
                    directory,
                    entry.name,
                    "Workspace Skill package uses a link or reparse point.",
                )
            )
            continue
        except (
            FileChangedError,
            FileNotFoundError,
            FileTooLargeError,
            NotADirectoryError,
            OSError,
            UnicodeError,
            ValueError,
            ValidationError,
        ) as error:
            diagnostics.append(
                _diagnostic(
                    "invalid_skill",
                    SkillSource.WORKSPACE,
                    directory,
                    entry.name,
                    f"Invalid Skill metadata: {type(error).__name__}",
                )
            )
            continue

        boundary = _WorkspaceSkillBoundary(
            workspace_anchor=anchor,
            workspace_anchor_identity=anchor_identity,
            skills_root=root,
            skills_root_identity=root_identity,
            package_root=directory,
            package_root_identity=package_identity,
            skill_file=skill_file,
            skill_file_fingerprint=bounded.fingerprint,
            skill_file_content_hash=sha256(bounded.data).hexdigest(),
        )
        retained = _retain_descriptor(
            descriptor,
            disabled_names,
            discovered,
            diagnostics,
        )
        if retained:
            boundaries[descriptor.name] = boundary


def _standard_descriptor_or_diagnostic(
    directory: Path,
    source: SkillSource,
    diagnostics: list[SkillDiagnostic],
) -> SkillDescriptor | None:
    try:
        return _descriptor(directory, source)
    except (OSError, UnicodeError, ValueError, ValidationError) as error:
        diagnostics.append(
            _diagnostic(
                "invalid_skill",
                source,
                directory,
                directory.name,
                f"Invalid Skill metadata: {type(error).__name__}",
            )
        )
        return None


def _retain_descriptor(
    descriptor: SkillDescriptor,
    disabled_names: set[str],
    discovered: dict[str, SkillDescriptor],
    diagnostics: list[SkillDiagnostic],
) -> bool:
    if descriptor.name in disabled_names:
        diagnostics.append(
            _diagnostic(
                "disabled",
                descriptor.source,
                descriptor.root,
                descriptor.name,
                "Skill is disabled by user configuration.",
            )
        )
        return False
    previous = discovered.get(descriptor.name)
    if previous is not None:
        diagnostics.append(
            _diagnostic(
                "shadowed",
                previous.source,
                previous.root,
                previous.name,
                f"Skill is shadowed by {descriptor.source.value} source.",
            )
        )
    discovered[descriptor.name] = descriptor
    return True


def _descriptor(directory: Path, source: SkillSource) -> SkillDescriptor:
    path = directory / "SKILL.md"
    if path.stat().st_size > _MAX_SKILL_FILE_BYTES:
        raise FileTooLargeError("SKILL.md exceeds the 1 MiB limit")
    with path.open("rb") as stream:
        data = stream.read(_MAX_SKILL_FILE_BYTES + 1)
    if len(data) > _MAX_SKILL_FILE_BYTES:
        raise FileTooLargeError("SKILL.md exceeds the 1 MiB limit")
    text = _decode_skill_text(data)
    return _descriptor_from_text(
        directory,
        source,
        text,
        root_path=directory.resolve(),
    )


def _descriptor_from_text(
    directory: Path,
    source: SkillSource,
    text: str,
    *,
    root_path: Path,
) -> SkillDescriptor:
    metadata = _frontmatter(text)
    unknown = metadata.keys() - _ALLOWED_FIELDS
    if unknown:
        raise ValueError(f"Unsupported Skill fields: {sorted(unknown)}")
    name = str(metadata.get("name") or "")
    if name != directory.name:
        raise ValueError("Skill name must match its directory")
    allowed = metadata.get("allowed-tools", [])
    allowed_tools: tuple[str, ...]
    if isinstance(allowed, str):
        allowed_tools = (allowed,)
    elif isinstance(allowed, list):
        allowed_tools = tuple(str(item) for item in allowed)
    else:
        raise ValueError("allowed-tools must be a string or list")
    raw_metadata = metadata.get("metadata", {})
    if not isinstance(raw_metadata, dict):
        raise ValueError("metadata must be a mapping")
    return SkillDescriptor(
        name=name,
        description=str(metadata.get("description") or ""),
        source=source,
        root=root_path,
        license=_optional_string(metadata.get("license")),
        compatibility=_optional_string(metadata.get("compatibility")),
        metadata={str(key): value for key, value in raw_metadata.items()},
        allowed_tools=allowed_tools,
    )


def _frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md requires YAML frontmatter")
    parts = text.split("---", 2)
    if len(parts) != 3:
        raise ValueError("SKILL.md frontmatter is incomplete")
    parsed = yaml.safe_load(parts[1])
    if not isinstance(parsed, dict):
        raise ValueError("Skill frontmatter must be a mapping")
    return {str(key): value for key, value in parsed.items()}


def _decode_skill_text(data: bytes) -> str:
    if b"\x00" in data:
        raise ValueError("Binary Skill files are not supported")
    return (
        data.decode("utf-8", errors="strict").replace("\r\n", "\n").replace("\r", "\n")
    )


def _is_link_or_reparse(info: os.stat_result) -> bool:
    attributes = int(getattr(info, "st_file_attributes", 0))
    reparse_attribute = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    return stat.S_ISLNK(info.st_mode) or bool(
        reparse_attribute and attributes & reparse_attribute
    )


def _optional_string(value: object) -> str | None:
    return None if value is None else str(value)


def _diagnostic(
    code: str,
    source: SkillSource,
    path: Path,
    name: str | None,
    message: str,
) -> SkillDiagnostic:
    return SkillDiagnostic(
        code=code,
        source=source,
        path=str(path)[:2_000],
        name=name[:64] if name is not None else None,
        message=message[:1_000],
    )
