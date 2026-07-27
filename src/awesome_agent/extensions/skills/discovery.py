from __future__ import annotations

import os
import stat
from hashlib import sha256
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError
from yaml.events import (
    AliasEvent,
    CollectionEndEvent,
    CollectionStartEvent,
    ScalarEvent,
)

from awesome_agent.core.safe_files import (
    FileChangedError,
    FileTooLargeError,
    PinnedPlainDirectory,
    UnsafePathError,
    file_identity,
    lexical_absolute,
)
from awesome_agent.extensions.skills.models import (
    SkillCatalog,
    SkillDescriptor,
    SkillDiagnostic,
    SkillSource,
    _identity_snapshot,
    _SkillBoundary,
)

_MAX_SKILL_FILE_BYTES = 1024 * 1024
_MAX_YAML_DEPTH = 64
_MAX_YAML_NODES = 4_096
_MAX_YAML_ALIASES = 64
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
    boundaries: dict[str, _SkillBoundary] = {}

    for source, root in (
        (SkillSource.BUNDLED, bundled_root),
        (SkillSource.USER, user_root),
    ):
        if root is None:
            continue
        _discover_standard_skills(
            root=root,
            source=source,
            disabled_names=disabled_names,
            discovered=discovered,
            diagnostics=diagnostics,
            boundaries=boundaries,
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
        if name in discovered and discovered[name].source == boundary.snapshot.source
    }
    return SkillCatalog(
        tuple(sorted(discovered.values(), key=lambda item: item.name)),
        tuple(diagnostics),
        retained_boundaries,
    )


def _discover_standard_skills(
    *,
    root: Path,
    source: SkillSource,
    disabled_names: set[str],
    discovered: dict[str, SkillDescriptor],
    diagnostics: list[SkillDiagnostic],
    boundaries: dict[str, _SkillBoundary],
) -> None:
    source_root = lexical_absolute(root)
    try:
        with PinnedPlainDirectory(source_root, source_root) as pinned:
            root_identity = pinned.identities[0]
            try:
                names = pinned.names()
            except OSError:
                return
            for name in names:
                directory = source_root / name
                try:
                    entry_info = pinned.child_status(name)
                    if _is_link_or_reparse(entry_info):
                        raise UnsafePathError("Skill package is a reparse point.")
                    if not stat.S_ISDIR(entry_info.st_mode):
                        continue
                    package_identity = file_identity(entry_info)
                    with pinned.descend(
                        Path(name),
                        expected_identities=(package_identity,),
                    ):
                        bounded = pinned.read_file(
                            Path("SKILL.md"),
                            max_bytes=_MAX_SKILL_FILE_BYTES,
                        )
                        component_identities = pinned.identities
                        descriptor = _descriptor_from_text(
                            directory,
                            source,
                            _decode_skill_text(bounded.data),
                            root_path=directory,
                        )
                except UnsafePathError:
                    diagnostics.append(
                        _diagnostic(
                            "unsafe_skill_path",
                            source,
                            directory,
                            name,
                            "Skill package uses a link or reparse point.",
                        )
                    )
                    continue
                except (
                    FileChangedError,
                    FileNotFoundError,
                    FileTooLargeError,
                    NotADirectoryError,
                    OSError,
                    RecursionError,
                    UnicodeError,
                    ValueError,
                    ValidationError,
                ) as error:
                    diagnostics.append(
                        _diagnostic(
                            "invalid_skill",
                            source,
                            directory,
                            name,
                            f"Invalid Skill metadata: {type(error).__name__}",
                        )
                    )
                    continue

                boundary = _SkillBoundary(
                    anchor=source_root,
                    anchor_identity=root_identity,
                    source_root=source_root,
                    source_root_identity=root_identity,
                    package_root=directory,
                    package_root_identity=package_identity,
                    package_component_identities=component_identities,
                    skill_file_fingerprint=bounded.fingerprint,
                    skill_file_content_hash=sha256(bounded.data).hexdigest(),
                    snapshot=_identity_snapshot(
                        descriptor,
                        fingerprint=bounded.fingerprint,
                        content=bounded.data,
                    ),
                )
                if _retain_descriptor(
                    descriptor,
                    disabled_names,
                    discovered,
                    diagnostics,
                ):
                    boundaries[descriptor.name] = boundary
    except FileNotFoundError:
        return
    except (FileChangedError, NotADirectoryError, OSError, UnsafePathError):
        return


def _discover_workspace_skills(
    *,
    workspace_root: Path,
    workspace_anchor: Path,
    disabled_names: set[str],
    discovered: dict[str, SkillDescriptor],
    diagnostics: list[SkillDiagnostic],
    boundaries: dict[str, _SkillBoundary],
) -> None:
    anchor = lexical_absolute(workspace_anchor)
    root = lexical_absolute(workspace_root)
    try:
        with PinnedPlainDirectory(anchor, root) as pinned:
            identities = pinned.identities
            anchor_identity = identities[0]
            root_identity = identities[-1]
            try:
                names = pinned.names()
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

            for name in names:
                directory = root / name
                try:
                    entry_info = pinned.child_status(name)
                    if _is_link_or_reparse(entry_info):
                        raise UnsafePathError(
                            "Workspace Skill package is a reparse point."
                        )
                    if not stat.S_ISDIR(entry_info.st_mode):
                        continue
                    package_identity = file_identity(entry_info)
                    with pinned.descend(
                        Path(name),
                        expected_identities=(package_identity,),
                    ):
                        bounded = pinned.read_file(
                            Path("SKILL.md"),
                            max_bytes=_MAX_SKILL_FILE_BYTES,
                        )
                        component_identities = pinned.identities
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
                            name,
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
                    RecursionError,
                    UnicodeError,
                    ValueError,
                    ValidationError,
                ) as error:
                    diagnostics.append(
                        _diagnostic(
                            "invalid_skill",
                            SkillSource.WORKSPACE,
                            directory,
                            name,
                            f"Invalid Skill metadata: {type(error).__name__}",
                        )
                    )
                    continue

                boundary = _SkillBoundary(
                    anchor=anchor,
                    anchor_identity=anchor_identity,
                    source_root=root,
                    source_root_identity=root_identity,
                    package_root=directory,
                    package_root_identity=package_identity,
                    package_component_identities=component_identities,
                    skill_file_fingerprint=bounded.fingerprint,
                    skill_file_content_hash=sha256(bounded.data).hexdigest(),
                    snapshot=_identity_snapshot(
                        descriptor,
                        fingerprint=bounded.fingerprint,
                        content=bounded.data,
                    ),
                )
                retained = _retain_descriptor(
                    descriptor,
                    disabled_names,
                    discovered,
                    diagnostics,
                )
                if retained:
                    boundaries[descriptor.name] = boundary
    except FileNotFoundError:
        return
    except (FileChangedError, NotADirectoryError, OSError, UnsafePathError):
        diagnostics.append(
            _diagnostic(
                "unsafe_workspace_skill_path",
                SkillSource.WORKSPACE,
                workspace_root,
                None,
                "Workspace Skill root uses an unsafe path component.",
            )
        )


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
    source = parts[1]
    try:
        _validate_yaml_events(source)
        parsed = yaml.safe_load(source)
        _validate_yaml_value(parsed)
    except (RecursionError, yaml.YAMLError) as error:
        raise ValueError("Skill frontmatter is not bounded valid YAML") from error
    if not isinstance(parsed, dict):
        raise ValueError("Skill frontmatter must be a mapping")
    return {str(key): value for key, value in parsed.items()}


def _validate_yaml_events(source: str) -> None:
    depth = 0
    nodes = 0
    aliases = 0
    for event in yaml.parse(source, Loader=yaml.SafeLoader):
        if isinstance(event, CollectionStartEvent):
            depth += 1
            nodes += 1
            if depth > _MAX_YAML_DEPTH:
                raise ValueError("Skill frontmatter exceeds the YAML depth limit")
        elif isinstance(event, CollectionEndEvent):
            depth -= 1
        elif isinstance(event, ScalarEvent):
            nodes += 1
        elif isinstance(event, AliasEvent):
            aliases += 1
            nodes += 1
            if aliases > _MAX_YAML_ALIASES:
                raise ValueError("Skill frontmatter exceeds the YAML alias limit")
        if nodes > _MAX_YAML_NODES:
            raise ValueError("Skill frontmatter exceeds the YAML node limit")
    if depth != 0:
        raise ValueError("Skill frontmatter has unbalanced YAML collections")


def _validate_yaml_value(value: object) -> None:
    nodes = 0
    active: set[int] = set()

    def walk(current: object, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > _MAX_YAML_NODES:
            raise ValueError("Skill frontmatter exceeds the YAML node limit")
        if depth > _MAX_YAML_DEPTH:
            raise ValueError("Skill frontmatter exceeds the YAML depth limit")
        if isinstance(current, dict):
            identity = id(current)
            if identity in active:
                raise ValueError("Skill frontmatter contains a recursive YAML alias")
            active.add(identity)
            try:
                for key, item in current.items():
                    walk(key, depth + 1)
                    walk(item, depth + 1)
            finally:
                active.remove(identity)
        elif isinstance(current, (list, set, tuple)):
            identity = id(current)
            if identity in active:
                raise ValueError("Skill frontmatter contains a recursive YAML alias")
            active.add(identity)
            try:
                for item in current:
                    walk(item, depth + 1)
            finally:
                active.remove(identity)

    walk(value, 0)


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
