from __future__ import annotations

import os
import stat
from hashlib import sha256
from pathlib import Path

from pydantic import ValidationError

from awesome_agent.core.safe_files import (
    FileChangedError,
    FileTooLargeError,
    PinnedPlainDirectory,
    UnsafePathError,
    file_identity,
    lexical_absolute,
)
from awesome_agent.extensions.skills.manifest import (
    decode_skill_manifest,
    parse_skill_manifest,
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
        with PinnedPlainDirectory(
            source_root,
            source_root,
            mount_boundary=source_root,
        ) as pinned:
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
                        descriptor = parse_skill_manifest(
                            decode_skill_manifest(bounded.data),
                            source=source,
                            root_path=directory,
                            expected_name=directory.name,
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
        with PinnedPlainDirectory(
            anchor,
            root,
            mount_boundary=root,
        ) as pinned:
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
                        text = decode_skill_manifest(bounded.data)
                        descriptor = parse_skill_manifest(
                            text,
                            source=SkillSource.WORKSPACE,
                            root_path=directory,
                            expected_name=directory.name,
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


def _is_link_or_reparse(info: os.stat_result) -> bool:
    attributes = int(getattr(info, "st_file_attributes", 0))
    reparse_attribute = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    return stat.S_ISLNK(info.st_mode) or bool(
        reparse_attribute and attributes & reparse_attribute
    )


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
