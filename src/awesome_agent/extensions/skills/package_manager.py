from __future__ import annotations

import ctypes
import errno
import io
import json
import lzma
import os
import re
import stat
import struct
import unicodedata
import zipfile
import zlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Literal, Self, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from awesome_agent.core.filesystem import (
    DirectoryEntryLimitExceeded,
    DirectoryPin,
    MutationTargetChanged,
    UnsafeWorkspacePath,
    WorkspaceFileTooLarge,
    assert_child_identity,
    bounded_directory_names,
    lstat_child,
    make_directory_child,
    open_directory,
    read_regular_child,
    remove_child,
)
from awesome_agent.core.filesystem import (
    FileIdentity as CoreFileIdentity,
)
from awesome_agent.core.filesystem import (
    identity as core_file_identity,
)
from awesome_agent.core.resource_lock import (
    ResourceLockUnavailable,
    exclusive_resource_lock,
)
from awesome_agent.core.safe_files import (
    DirectoryEntryLimitError,
    FileChangedError,
    FileTooLargeError,
    PinnedPlainDirectory,
    UnsafePathError,
    lexical_absolute,
)
from awesome_agent.extensions.skills.manifest import (
    decode_skill_manifest,
    parse_skill_manifest,
)
from awesome_agent.extensions.skills.models import SkillDescriptor, SkillSource

_MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
_MAX_EXPANDED_BYTES = 32 * 1024 * 1024
_MAX_FILE_BYTES = 1024 * 1024
_MAX_ENTRIES = 512
_MAX_PATH_DEPTH = 64
_MAX_COMPONENT_BYTES = 255
_MAX_RELATIVE_PATH_BYTES = 4_096
_ZIP_EOCD_MIN_BYTES = 22
_ZIP_MAX_COMMENT_BYTES = 65_535
_ZIP_CENTRAL_HEADER_BYTES = 46
_ZIP_EOCD_SIGNATURE = b"PK\x05\x06"
_ZIP_CENTRAL_SIGNATURE = b"PK\x01\x02"
_MARKER_NAME = ".skills-transaction.json"
_MARKER_TEMP_NAME = ".skills-transaction.tmp"
_STAGE_PREFIX = ".skill-stage-"
_QUARANTINE_PREFIX = ".skill-quarantine-"
_STAGE_NAME_PATTERN = re.compile(r"^\.skill-stage-[a-f0-9]{32}$")
_QUARANTINE_NAME_PATTERN = re.compile(r"^\.skill-quarantine-[a-f0-9]{32}$")
_INTERNAL_NAME_PATTERN = r"^\.skill-(?:stage|quarantine)-[a-f0-9]{32}$"
_WINDOWS_RESERVED = frozenset(
    {
        "aux",
        "con",
        "conin$",
        "conout$",
        "nul",
        "prn",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
        *(f"com{suffix}" for suffix in "¹²³"),
        *(f"lpt{suffix}" for suffix in "¹²³"),
    }
)
_WINDOWS_FORBIDDEN = frozenset('<>:"\\|?*')
_ResultT = TypeVar("_ResultT")


class SkillPackageAction(StrEnum):
    INSTALLED = "installed"
    REPLACED = "replaced"
    REMOVED = "removed"


class SkillPackageError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class InstalledSkillPackage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    description: str = Field(min_length=1, max_length=500)
    allowed_tools: tuple[str, ...] = Field(default=(), max_length=128)


class SkillPackageMutation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    action: SkillPackageAction
    restart_required: Literal[True] = True


class _MarkerIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    device: int = Field(ge=0)
    inode: int = Field(ge=0)
    file_type: int = Field(ge=0)
    reparse: bool

    @classmethod
    def from_core(cls, value: CoreFileIdentity) -> _MarkerIdentity:
        return cls(
            device=value.device,
            inode=value.inode,
            file_type=value.file_type,
            reparse=value.reparse,
        )

    def to_core(self) -> CoreFileIdentity:
        return CoreFileIdentity(
            device=self.device,
            inode=self.inode,
            file_type=self.file_type,
            reparse=self.reparse,
        )


class _TransactionMarker(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    action: Literal["install", "replace", "remove"]
    phase: Literal["prepared", "quarantined", "published"]
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    stage_name: str | None = Field(default=None, pattern=_INTERNAL_NAME_PATTERN)
    quarantine_name: str | None = Field(
        default=None,
        pattern=_INTERNAL_NAME_PATTERN,
    )
    stage_identity: _MarkerIdentity | None = None
    original_identity: _MarkerIdentity | None = None

    @model_validator(mode="after")
    def validate_paths(self) -> Self:
        if self.action == "install" and (
            self.phase not in {"prepared", "published"}
            or self.stage_name is None
            or self.quarantine_name is not None
            or self.stage_identity is None
            or self.original_identity is not None
        ):
            raise ValueError("Install transaction paths are invalid.")
        if self.action == "replace" and (
            self.stage_name is None
            or self.quarantine_name is None
            or self.stage_identity is None
            or self.original_identity is None
        ):
            raise ValueError("Replace transaction paths are invalid.")
        if self.action == "remove" and (
            self.phase not in {"prepared", "quarantined", "published"}
            or self.stage_name is not None
            or self.quarantine_name is None
            or self.stage_identity is not None
            or self.original_identity is None
        ):
            raise ValueError("Remove transaction paths are invalid.")
        return self


@dataclass(frozen=True, slots=True)
class _PackageFile:
    path: PurePosixPath
    data: bytes


@dataclass(frozen=True, slots=True)
class _PackageSnapshot:
    descriptor: SkillDescriptor
    files: tuple[_PackageFile, ...]


@dataclass(frozen=True, slots=True)
class _TransactionPins:
    home: DirectoryPin
    skills: DirectoryPin

    def verify(self) -> None:
        self.home.verify_reachable()
        self.skills.verify_reachable()


@dataclass(slots=True)
class _TraversalBudget:
    remaining: int = _MAX_ENTRIES

    def consume(self) -> None:
        if self.remaining <= 0:
            raise _transaction_failed()
        self.remaining -= 1


class _PathRegistry:
    def __init__(self) -> None:
        self._nodes: dict[str, Literal["file", "directory"]] = {}
        self._explicit: set[str] = set()

    def register(
        self,
        path: PurePosixPath,
        *,
        kind: Literal["file", "directory"],
    ) -> None:
        components = _validated_components(path)
        if len(components) > _MAX_PATH_DEPTH:
            raise _package_too_large()
        keys = ["/".join(components[:index]) for index in range(1, len(components) + 1)]
        for parent in keys[:-1]:
            existing = self._nodes.get(parent)
            if existing == "file":
                raise _invalid_package("Skill package contains a path collision.")
            if existing is None:
                self._add(parent, "directory")
        key = keys[-1]
        existing = self._nodes.get(key)
        if existing is not None and (existing != kind or key in self._explicit):
            raise _invalid_package("Skill package contains a duplicate path.")
        if kind == "file" and any(item.startswith(f"{key}/") for item in self._nodes):
            raise _invalid_package("Skill package contains a path collision.")
        if existing is None:
            self._add(key, kind)
        self._explicit.add(key)

    @property
    def remaining(self) -> int:
        return _MAX_ENTRIES - len(self._nodes)

    def _add(self, key: str, kind: Literal["file", "directory"]) -> None:
        if len(self._nodes) >= _MAX_ENTRIES:
            raise _package_too_large()
        self._nodes[key] = kind


class SkillPackageManager:
    def __init__(self, home: Path, skills_root: Path) -> None:
        self._home = lexical_absolute(home)
        self._skills_root = lexical_absolute(skills_root)
        try:
            relative = self._skills_root.relative_to(self._home)
        except ValueError as error:
            raise ValueError(
                "Skill install root must be inside Awesome home."
            ) from error
        if not relative.parts:
            raise ValueError("Skill install root must be below Awesome home.")
        for component in relative.parts:
            _validate_component(component)
        self._lock_resource = self._home / "skills"
        self._marker_path = self._home / _MARKER_NAME

    def list(self) -> tuple[InstalledSkillPackage, ...]:
        def operation(pins: _TransactionPins) -> tuple[InstalledSkillPackage, ...]:
            packages: list[InstalledSkillPackage] = []
            names = tuple(
                name
                for name in _directory_names(pins.skills)
                if not name.startswith(".skill-")
            )
            for name in names:
                pins.verify()
                try:
                    _validate_skill_name(name)
                    target = self._skills_root / name
                    if not _is_plain_directory(target):
                        continue
                    descriptor = _read_installed_manifest(
                        target,
                        expected_name=name,
                    )
                except SkillPackageError:
                    continue
                packages.append(
                    InstalledSkillPackage(
                        name=descriptor.name,
                        description=descriptor.description,
                        allowed_tools=descriptor.allowed_tools,
                    )
                )
            pins.verify()
            return tuple(sorted(packages, key=lambda item: item.name))

        return self._locked(operation)

    def recover(self) -> None:
        """Converge one interrupted package transaction under the package lock."""

        self._locked(lambda _pins: None)

    def install(
        self,
        source: Path,
        *,
        replace: bool = False,
    ) -> SkillPackageMutation:
        snapshot = _read_source_package(source)

        def operation(pins: _TransactionPins) -> SkillPackageMutation:
            name = snapshot.descriptor.name
            stage_name = f"{_STAGE_PREFIX}{uuid4().hex}"
            stage = self._skills_root / stage_name
            stage_identity = _write_stage(stage, snapshot, pins=pins)

            target_exists = _child_exists(pins.skills, name)
            if target_exists and not replace:
                _remove_internal_entry(
                    stage,
                    self._skills_root,
                    pins=pins,
                    expected_identity=stage_identity,
                )
                raise SkillPackageError(
                    "package_exists",
                    "A Skill package with this name is already installed.",
                )
            if not target_exists:
                marker = _TransactionMarker(
                    action="install",
                    phase="prepared",
                    name=name,
                    stage_name=stage_name,
                    stage_identity=_MarkerIdentity.from_core(stage_identity),
                )
                try:
                    self._write_marker(marker, pins=pins)
                    _rename_child_noreplace(
                        pins.skills,
                        stage_name,
                        name,
                        expected_source=stage_identity,
                    )
                    _fsync_directory(self._skills_root)
                    self._write_marker(
                        marker.model_copy(update={"phase": "published"}),
                        pins=pins,
                    )
                    self._clear_marker(pins=pins)
                except BaseException as error:
                    if (
                        isinstance(error, SkillPackageError)
                        and error.code == "package_exists"
                    ):
                        _remove_internal_entry(
                            stage,
                            self._skills_root,
                            pins=pins,
                            expected_identity=stage_identity,
                        )
                        self._clear_marker_best_effort(pins=pins)
                        raise
                    if not _child_exists(pins.skills, name):
                        _remove_internal_entry(
                            stage,
                            self._skills_root,
                            pins=pins,
                            expected_identity=stage_identity,
                        )
                        self._clear_marker_best_effort(pins=pins)
                    if isinstance(error, SkillPackageError):
                        raise
                    raise _transaction_failed() from error
                return SkillPackageMutation(
                    name=name,
                    action=SkillPackageAction.INSTALLED,
                )

            quarantine_name = f"{_QUARANTINE_PREFIX}{uuid4().hex}"
            quarantine = self._skills_root / quarantine_name
            target_identity = _child_identity(pins.skills, name)
            marker = _TransactionMarker(
                action="replace",
                phase="prepared",
                name=name,
                stage_name=stage_name,
                quarantine_name=quarantine_name,
                stage_identity=_MarkerIdentity.from_core(stage_identity),
                original_identity=_MarkerIdentity.from_core(target_identity),
            )
            commit_outcome_uncertain = False
            try:
                self._write_marker(marker, pins=pins)
                _rename_child_noreplace(
                    pins.skills,
                    name,
                    quarantine_name,
                    expected_source=target_identity,
                    allow_source_reparse=True,
                )
                _fsync_directory(self._skills_root)
                quarantined_marker = marker.model_copy(update={"phase": "quarantined"})
                self._write_marker(quarantined_marker, pins=pins)
                marker = quarantined_marker
                _rename_child_noreplace(
                    pins.skills,
                    stage_name,
                    name,
                    expected_source=stage_identity,
                )
                _fsync_directory(self._skills_root)
                published_marker = marker.model_copy(update={"phase": "published"})
                commit_outcome_uncertain = True
                self._write_marker(published_marker, pins=pins)
                marker = published_marker
                _remove_internal_entry(
                    quarantine,
                    self._skills_root,
                    pins=pins,
                    expected_identity=target_identity,
                )
                self._clear_marker(pins=pins)
            except BaseException as error:
                if not commit_outcome_uncertain:
                    self._rollback_replace(
                        marker,
                        pins=pins,
                        original_identity=target_identity,
                        stage_identity=stage_identity,
                    )
                if isinstance(error, SkillPackageError):
                    raise
                raise _transaction_failed() from error
            return SkillPackageMutation(
                name=name,
                action=SkillPackageAction.REPLACED,
            )

        return self._locked(operation)

    def remove(self, name: str) -> SkillPackageMutation:
        try:
            _validate_skill_name(name)
        except SkillPackageError as error:
            raise SkillPackageError(
                "invalid_package",
                "Skill package name is invalid.",
            ) from error

        def operation(pins: _TransactionPins) -> SkillPackageMutation:
            if not _child_exists(pins.skills, name):
                raise SkillPackageError(
                    "package_not_found",
                    "The requested Skill package is not installed.",
                )
            quarantine_name = f"{_QUARANTINE_PREFIX}{uuid4().hex}"
            quarantine = self._skills_root / quarantine_name
            target_identity = _child_identity(pins.skills, name)
            marker = _TransactionMarker(
                action="remove",
                phase="prepared",
                name=name,
                quarantine_name=quarantine_name,
                original_identity=_MarkerIdentity.from_core(target_identity),
            )
            commit_outcome_uncertain = False
            try:
                self._write_marker(marker, pins=pins)
                _rename_child_noreplace(
                    pins.skills,
                    name,
                    quarantine_name,
                    expected_source=target_identity,
                    allow_source_reparse=True,
                )
                _fsync_directory(self._skills_root)
                quarantined_marker = marker.model_copy(update={"phase": "quarantined"})
                self._write_marker(quarantined_marker, pins=pins)
                marker = quarantined_marker
                published_marker = marker.model_copy(update={"phase": "published"})
                commit_outcome_uncertain = True
                self._write_marker(published_marker, pins=pins)
                marker = published_marker
                _remove_internal_entry(
                    quarantine,
                    self._skills_root,
                    pins=pins,
                    expected_identity=target_identity,
                )
                self._clear_marker(pins=pins)
            except BaseException as error:
                if (
                    not commit_outcome_uncertain
                    and not _child_exists(pins.skills, name)
                    and _child_exists(pins.skills, quarantine_name)
                ):
                    try:
                        if (
                            _child_identity(pins.skills, quarantine_name)
                            != target_identity
                        ):
                            raise MutationTargetChanged(
                                "The quarantined Skill package changed."
                            )
                        _rename_child_noreplace(
                            pins.skills,
                            quarantine_name,
                            name,
                            expected_source=_child_identity(
                                pins.skills,
                                quarantine_name,
                            ),
                            allow_source_reparse=True,
                        )
                        _fsync_directory(self._skills_root)
                        self._clear_marker_best_effort(pins=pins)
                    except (
                        MutationTargetChanged,
                        OSError,
                        SkillPackageError,
                        UnsafeWorkspacePath,
                    ):
                        pass
                if isinstance(error, SkillPackageError):
                    raise
                raise _transaction_failed() from error
            return SkillPackageMutation(
                name=name,
                action=SkillPackageAction.REMOVED,
            )

        return self._locked(operation)

    def _locked(self, operation: Callable[[_TransactionPins], _ResultT]) -> _ResultT:
        self._ensure_layout()
        try:
            with (
                _transaction_pins(
                    self._home,
                    self._skills_root,
                ) as pins,
                exclusive_resource_lock(
                    self._lock_resource,
                    directory=pins.home,
                ),
            ):
                pins.verify()
                self._recover_marker_temporary(pins=pins)
                marker = self._read_marker(pins=pins)
                self._recover_orphan_stages(marker=marker, pins=pins)
                self._recover_transaction(marker=marker, pins=pins)
                result = operation(pins)
                pins.verify()
                return result
        except (MutationTargetChanged, OSError, UnsafeWorkspacePath) as error:
            raise _transaction_failed() from error
        except ResourceLockUnavailable as error:
            raise SkillPackageError(
                "package_busy",
                "Another Skill package operation is in progress.",
            ) from error

    def _ensure_layout(self) -> None:
        try:
            _ensure_plain_directory_chain(self._home)
            _ensure_plain_directory_chain(self._skills_root)
            with PinnedPlainDirectory(
                _path_anchor(self._home),
                self._skills_root,
                mount_boundary=self._skills_root,
            ):
                pass
        except (FileChangedError, OSError, UnsafePathError, ValueError) as error:
            raise _transaction_failed() from error

    def _write_marker(
        self,
        marker: _TransactionMarker,
        *,
        pins: _TransactionPins,
    ) -> None:
        raw = (
            json.dumps(
                marker.model_dump(mode="json"),
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        temporary = self._home / _MARKER_TEMP_NAME
        temporary_name = temporary.name
        descriptor: int | None = None
        try:
            pins.verify()
            descriptor = (
                os.open(temporary, _exclusive_file_flags(), 0o600)
                if os.name == "nt"
                else os.open(
                    temporary_name,
                    _exclusive_file_flags(),
                    0o600,
                    dir_fd=pins.home.descriptor,
                )
            )
            _write_all(descriptor, raw)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            pins.verify()
            temporary_identity = core_file_identity(
                lstat_child(pins.home, temporary_name)
            )
            assert_child_identity(
                pins.home,
                temporary_name,
                temporary_identity,
            )
            if os.name == "nt":
                os.replace(temporary, self._marker_path)
            else:
                os.replace(
                    temporary_name,
                    _MARKER_NAME,
                    src_dir_fd=pins.home.descriptor,
                    dst_dir_fd=pins.home.descriptor,
                )
            pins.verify()
            _fsync_directory(self._home)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            with suppress(FileNotFoundError):
                if os.name == "nt":
                    temporary.unlink()
                else:
                    os.unlink(temporary_name, dir_fd=pins.home.descriptor)

    def _recover_marker_temporary(self, *, pins: _TransactionPins) -> None:
        try:
            info = lstat_child(pins.home, _MARKER_TEMP_NAME)
        except FileNotFoundError:
            return
        expected = core_file_identity(info)
        if (
            _is_link_or_reparse(info)
            or not stat.S_ISREG(info.st_mode)
            or int(info.st_nlink) != 1
        ):
            raise _transaction_failed()
        assert_child_identity(pins.home, _MARKER_TEMP_NAME, expected)
        remove_child(pins.home, _MARKER_TEMP_NAME, directory=False)
        pins.verify()
        _fsync_directory(self._home)

    def _read_marker(self, *, pins: _TransactionPins) -> _TransactionMarker | None:
        try:
            pins.verify()
            bounded = read_regular_child(
                pins.home,
                _MARKER_NAME,
                max_bytes=16_384,
            )
        except FileNotFoundError:
            return None
        except (
            MutationTargetChanged,
            OSError,
            UnsafeWorkspacePath,
            WorkspaceFileTooLarge,
        ) as error:
            raise _transaction_failed() from error
        try:
            return _TransactionMarker.model_validate_json(bounded.data)
        except ValueError as error:
            raise _transaction_failed() from error

    def _clear_marker(self, *, pins: _TransactionPins) -> None:
        marker = self._read_marker(pins=pins)
        if marker is None:
            return
        try:
            pins.verify()
            remove_child(pins.home, _MARKER_NAME, directory=False)
            pins.verify()
            _fsync_directory(self._home)
        except OSError as error:
            raise _transaction_failed() from error

    def _clear_marker_best_effort(self, *, pins: _TransactionPins) -> None:
        with suppress(SkillPackageError):
            self._clear_marker(pins=pins)

    def _recover_orphan_stages(
        self,
        *,
        marker: _TransactionMarker | None,
        pins: _TransactionPins,
    ) -> None:
        referenced = {
            name
            for name in (
                marker.stage_name if marker is not None else None,
                marker.quarantine_name if marker is not None else None,
            )
            if name is not None
        }
        try:
            names = bounded_directory_names(
                pins.skills,
                max_entries=_MAX_ENTRIES + len(referenced) + 1,
            )
        except DirectoryEntryLimitExceeded as error:
            raise _transaction_failed() from error
        except (MutationTargetChanged, OSError, UnsafeWorkspacePath) as error:
            raise _transaction_failed() from error
        ordinary_count = 0
        orphan_stages: list[str] = []
        for name in names:
            if name in referenced:
                continue
            if _STAGE_NAME_PATTERN.fullmatch(name):
                orphan_stages.append(name)
                continue
            if _QUARANTINE_NAME_PATTERN.fullmatch(name) or name.startswith(".skill-"):
                raise _transaction_failed()
            ordinary_count += 1
        if ordinary_count > _MAX_ENTRIES:
            raise _package_too_large()
        if len(orphan_stages) > 1:
            raise _transaction_failed()
        for name in orphan_stages:
            identity = _child_identity(pins.skills, name)
            _remove_internal_entry(
                self._skills_root / name,
                self._skills_root,
                pins=pins,
                expected_identity=identity,
            )

    def _recover_transaction(
        self,
        *,
        marker: _TransactionMarker | None,
        pins: _TransactionPins,
    ) -> None:
        if marker is None:
            return
        try:
            if marker.action == "install":
                self._recover_install(marker, pins=pins)
            elif marker.action == "replace":
                self._recover_replace(marker, pins=pins)
            else:
                self._recover_remove(marker, pins=pins)
            _fsync_directory(self._skills_root)
            self._clear_marker(pins=pins)
        except SkillPackageError:
            raise
        except OSError as error:
            raise _transaction_failed() from error

    def _recover_install(
        self,
        marker: _TransactionMarker,
        *,
        pins: _TransactionPins,
    ) -> None:
        if marker.stage_name is None or marker.stage_identity is None:
            raise _transaction_failed()
        expected = marker.stage_identity.to_core()
        stage_role = _child_role(
            pins.skills,
            marker.stage_name,
            {"candidate": expected},
        )
        target_role = _child_role(
            pins.skills,
            marker.name,
            {"candidate": expected},
            allow_other=True,
        )
        stage = self._skills_root / marker.stage_name
        if marker.phase == "published":
            if stage_role != "absent" or target_role != "candidate":
                raise _transaction_failed()
            return
        if stage_role == "candidate" and target_role == "absent":
            _rename_child_noreplace(
                pins.skills,
                marker.stage_name,
                marker.name,
                expected_source=expected,
            )
        elif stage_role == "candidate" and target_role == "other":
            _remove_internal_entry(
                stage,
                self._skills_root,
                pins=pins,
                expected_identity=expected,
            )
        elif stage_role != "absent":
            raise _transaction_failed()

    def _recover_replace(
        self,
        marker: _TransactionMarker,
        *,
        pins: _TransactionPins,
    ) -> None:
        if (
            marker.stage_name is None
            or marker.quarantine_name is None
            or marker.stage_identity is None
            or marker.original_identity is None
        ):
            raise _transaction_failed()
        candidate = marker.stage_identity.to_core()
        original = marker.original_identity.to_core()
        stage_role = _child_role(
            pins.skills,
            marker.stage_name,
            {"candidate": candidate},
        )
        target_role = _child_role(
            pins.skills,
            marker.name,
            {"candidate": candidate, "original": original},
        )
        quarantine_role = _child_role(
            pins.skills,
            marker.quarantine_name,
            {"original": original},
        )
        stage = self._skills_root / marker.stage_name
        quarantine = self._skills_root / marker.quarantine_name

        if marker.phase in {"prepared", "quarantined"}:
            if (
                target_role == "original"
                and stage_role == "candidate"
                and quarantine_role == "absent"
            ):
                _remove_internal_entry(
                    stage,
                    self._skills_root,
                    pins=pins,
                    expected_identity=candidate,
                )
                return
            if (
                target_role == "original"
                and stage_role == "absent"
                and quarantine_role == "absent"
            ):
                return
            if target_role == "candidate" and stage_role == "absent":
                _rename_child_noreplace(
                    pins.skills,
                    marker.name,
                    marker.stage_name,
                    expected_source=candidate,
                )
                stage_role = "candidate"
                target_role = "absent"
            if (
                target_role == "absent"
                and quarantine_role == "original"
                and stage_role in {"absent", "candidate"}
            ):
                _rename_child_noreplace(
                    pins.skills,
                    marker.quarantine_name,
                    marker.name,
                    expected_source=original,
                    allow_source_reparse=True,
                )
                if stage_role == "candidate":
                    _remove_internal_entry(
                        stage,
                        self._skills_root,
                        pins=pins,
                        expected_identity=candidate,
                    )
                return
            raise _transaction_failed()

        if marker.phase != "published" or (
            target_role != "candidate"
            or stage_role != "absent"
            or quarantine_role not in {"absent", "original"}
        ):
            raise _transaction_failed()
        if quarantine_role == "original":
            _remove_internal_entry(
                quarantine,
                self._skills_root,
                pins=pins,
                expected_identity=original,
            )

    def _recover_remove(
        self,
        marker: _TransactionMarker,
        *,
        pins: _TransactionPins,
    ) -> None:
        if marker.quarantine_name is None or marker.original_identity is None:
            raise _transaction_failed()
        original = marker.original_identity.to_core()
        target_role = _child_role(
            pins.skills,
            marker.name,
            {"original": original},
        )
        quarantine_role = _child_role(
            pins.skills,
            marker.quarantine_name,
            {"original": original},
        )
        quarantine = self._skills_root / marker.quarantine_name
        if marker.phase in {"prepared", "quarantined"}:
            if target_role == "original" and quarantine_role == "absent":
                return
            if target_role == "absent" and quarantine_role == "original":
                _rename_child_noreplace(
                    pins.skills,
                    marker.quarantine_name,
                    marker.name,
                    expected_source=original,
                    allow_source_reparse=True,
                )
                return
            raise _transaction_failed()
        if marker.phase != "published":
            raise _transaction_failed()
        if target_role == "absent" and quarantine_role == "original":
            _remove_internal_entry(
                quarantine,
                self._skills_root,
                pins=pins,
                expected_identity=original,
            )
        elif not (target_role == "absent" and quarantine_role == "absent"):
            raise _transaction_failed()

    def _rollback_replace(
        self,
        marker: _TransactionMarker,
        *,
        pins: _TransactionPins,
        original_identity: CoreFileIdentity,
        stage_identity: CoreFileIdentity,
    ) -> None:
        if marker.stage_name is None or marker.quarantine_name is None:
            return
        stage = self._skills_root / marker.stage_name
        try:
            if _child_exists(pins.skills, marker.quarantine_name):
                if (
                    _child_identity(pins.skills, marker.quarantine_name)
                    != original_identity
                ):
                    return
                if _child_exists(pins.skills, marker.name):
                    if (
                        not _child_exists(pins.skills, marker.stage_name)
                        and _child_identity(pins.skills, marker.name) == stage_identity
                    ):
                        _rename_child_noreplace(
                            pins.skills,
                            marker.name,
                            marker.stage_name,
                            expected_source=_child_identity(
                                pins.skills,
                                marker.name,
                            ),
                        )
                    else:
                        return
                _rename_child_noreplace(
                    pins.skills,
                    marker.quarantine_name,
                    marker.name,
                    expected_source=_child_identity(
                        pins.skills,
                        marker.quarantine_name,
                    ),
                    allow_source_reparse=True,
                )
            _remove_internal_entry(
                stage,
                self._skills_root,
                pins=pins,
                expected_identity=stage_identity,
            )
            _fsync_directory(self._skills_root)
            self._clear_marker_best_effort(pins=pins)
        except (OSError, SkillPackageError):
            return


@contextmanager
def _transaction_pins(
    home: Path,
    skills_root: Path,
) -> Iterator[_TransactionPins]:
    opened: list[DirectoryPin] = []
    home_pin: DirectoryPin | None = None
    try:
        anchor = _path_anchor(skills_root)
        current = open_directory(
            anchor,
            establish_mount_boundary=(
                _same_path(anchor, home) or _same_path(anchor, skills_root)
            ),
        )
        opened.append(current)
        if _same_path(anchor, home):
            home_pin = current
        for component in skills_root.relative_to(anchor).parts:
            child_path = current.path / component
            current = open_directory(
                child_path,
                parent=current,
                name=component,
                establish_mount_boundary=(
                    _same_path(child_path, home) or _same_path(child_path, skills_root)
                ),
            )
            opened.append(current)
            if _same_path(current.path, home):
                home_pin = current
        if home_pin is None or not _same_path(current.path, skills_root):
            raise UnsafeWorkspacePath("Skill transaction boundary is inconsistent.")
        pins = _TransactionPins(home=home_pin, skills=current)
        pins.verify()
        yield pins
        pins.verify()
    finally:
        for pin in reversed(opened):
            pin.close()


def _ensure_plain_directory_chain(path: Path) -> None:
    opened: list[DirectoryPin] = []
    try:
        anchor = _path_anchor(path)
        current = open_directory(anchor)
        opened.append(current)
        for component in path.relative_to(anchor).parts:
            current.verify_reachable()
            try:
                status = lstat_child(current, component)
            except FileNotFoundError:
                make_directory_child(current, component, 0o700)
                status = lstat_child(current, component)
            if _is_link_or_reparse(status) or not stat.S_ISDIR(status.st_mode):
                raise UnsafeWorkspacePath(
                    "Skill install paths must contain only plain directories."
                )
            child = open_directory(
                current.path / component,
                parent=current,
                name=component,
                expected_identity=core_file_identity(status),
            )
            opened.append(child)
            current = child
        current.verify_reachable()
    finally:
        for pin in reversed(opened):
            pin.close()


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.path.abspath(left)) == os.path.normcase(
        os.path.abspath(right)
    )


def _child_exists(parent: DirectoryPin, name: str) -> bool:
    parent.verify_reachable()
    try:
        lstat_child(parent, name)
    except FileNotFoundError:
        parent.verify_reachable()
        return False
    parent.verify_reachable()
    return True


def _child_identity(parent: DirectoryPin, name: str) -> CoreFileIdentity:
    parent.verify_reachable()
    result = core_file_identity(lstat_child(parent, name))
    assert_child_identity(
        parent,
        name,
        result,
        allow_reparse=True,
    )
    parent.verify_reachable()
    return result


def _child_role(
    parent: DirectoryPin,
    name: str,
    expected: dict[str, CoreFileIdentity],
    *,
    allow_other: bool = False,
) -> str:
    if not _child_exists(parent, name):
        return "absent"
    observed = _child_identity(parent, name)
    for role, identity in expected.items():
        if observed == identity:
            return role
    if allow_other:
        return "other"
    raise MutationTargetChanged("A Skill transaction entry changed identity.")


def _rename_child_noreplace(
    parent: DirectoryPin,
    source: str,
    destination: str,
    *,
    expected_source: CoreFileIdentity,
    allow_source_reparse: bool = False,
) -> None:
    _validate_component(source)
    _validate_component(destination)
    parent.verify_reachable()
    assert_child_identity(
        parent,
        source,
        expected_source,
        allow_reparse=allow_source_reparse,
    )
    assert_child_identity(parent, destination, None)
    try:
        _platform_rename_noreplace(parent, source, destination)
    except FileExistsError as error:
        raise SkillPackageError(
            "package_exists",
            "A Skill package with this name is already installed.",
        ) from error
    parent.verify_reachable()
    assert_child_identity(parent, source, None)
    assert_child_identity(
        parent,
        destination,
        expected_source,
        allow_reparse=allow_source_reparse,
    )


def _platform_rename_noreplace(
    parent: DirectoryPin,
    source: str,
    destination: str,
) -> None:
    if os.name == "nt":
        os.rename(parent.path / source, parent.path / destination)
        return
    runtime = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(runtime, "renameat2", None)
    if renameat2 is not None:
        result = renameat2(
            parent.descriptor,
            os.fsencode(source),
            parent.descriptor,
            os.fsencode(destination),
            1,
        )
    else:
        renameatx = getattr(runtime, "renameatx_np", None)
        if renameatx is None:
            raise OSError(errno.ENOTSUP, "No no-replace rename primitive.")
        result = renameatx(
            parent.descriptor,
            os.fsencode(source),
            parent.descriptor,
            os.fsencode(destination),
            0x00000004,
        )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number))


def _read_source_package(source: Path) -> _PackageSnapshot:
    path = lexical_absolute(source)
    try:
        info = os.lstat(path)
    except OSError as error:
        raise SkillPackageError(
            "invalid_source",
            "Skill package source could not be opened safely.",
        ) from error
    try:
        if _is_link_or_reparse(info):
            raise UnsafePathError("Source is a link or reparse point.")
        if stat.S_ISDIR(info.st_mode):
            return _read_directory_package(path, expected_name=None)
        if stat.S_ISREG(info.st_mode) and int(info.st_nlink) == 1:
            return _read_zip_package(path)
    except SkillPackageError:
        raise
    except (FileChangedError, FileTooLargeError, OSError, UnsafePathError) as error:
        raise SkillPackageError(
            "invalid_source",
            "Skill package source could not be opened safely.",
        ) from error
    raise SkillPackageError(
        "invalid_source",
        "Skill package source must be a plain directory or ZIP file.",
    )


def _read_directory_package(
    root: Path,
    *,
    expected_name: str | None,
) -> _PackageSnapshot:
    files: list[_PackageFile] = []
    registry = _PathRegistry()
    expanded = 0
    try:
        with PinnedPlainDirectory(
            _path_anchor(root),
            root,
            mount_boundary=root,
        ) as pinned:

            def walk(prefix: PurePosixPath) -> None:
                nonlocal expanded
                for name in pinned.bounded_names(max_entries=registry.remaining):
                    relative = prefix / name
                    info = pinned.child_status(name)
                    if _is_link_or_reparse(info):
                        raise _invalid_package(
                            "Skill packages cannot contain links or reparse points."
                        )
                    if stat.S_ISDIR(info.st_mode):
                        registry.register(relative, kind="directory")
                        with pinned.descend(Path(name)):
                            walk(relative)
                        continue
                    if not stat.S_ISREG(info.st_mode) or int(info.st_nlink) != 1:
                        raise _invalid_package(
                            "Skill packages must contain only plain files."
                        )
                    registry.register(relative, kind="file")
                    if int(info.st_size) > _MAX_FILE_BYTES:
                        raise _package_too_large()
                    bounded = pinned.read_file(Path(name), max_bytes=_MAX_FILE_BYTES)
                    expanded += len(bounded.data)
                    if expanded > _MAX_EXPANDED_BYTES:
                        raise _package_too_large()
                    files.append(_PackageFile(path=relative, data=bounded.data))

            walk(PurePosixPath())
    except SkillPackageError:
        raise
    except FileTooLargeError as error:
        raise _package_too_large() from error
    except DirectoryEntryLimitError as error:
        raise _package_too_large() from error
    except (FileChangedError, OSError, UnsafePathError, ValueError) as error:
        raise _invalid_package(
            "Skill package directory could not be read safely."
        ) from error
    return _snapshot_from_files(files, expected_name=expected_name)


def _read_installed_manifest(
    root: Path,
    *,
    expected_name: str,
) -> SkillDescriptor:
    try:
        with PinnedPlainDirectory(
            root.parent,
            root,
            mount_boundary=root.parent,
        ) as pinned:
            manifest = pinned.read_file(
                Path("SKILL.md"),
                max_bytes=_MAX_FILE_BYTES,
            ).data
        return parse_skill_manifest(
            decode_skill_manifest(manifest),
            source=SkillSource.USER,
            root_path=root,
            expected_name=expected_name,
        )
    except FileTooLargeError as error:
        raise _package_too_large() from error
    except (
        FileChangedError,
        OSError,
        UnicodeError,
        UnsafePathError,
        ValueError,
    ) as error:
        raise _invalid_package(
            "Skill package manifest could not be read safely."
        ) from error


def _read_zip_package(path: Path) -> _PackageSnapshot:
    try:
        with PinnedPlainDirectory(_path_anchor(path.parent), path.parent) as pinned:
            archive_bytes = pinned.read_file(
                Path(path.name),
                max_bytes=_MAX_ARCHIVE_BYTES,
            ).data
    except FileTooLargeError as error:
        raise _package_too_large() from error
    except (FileChangedError, OSError, UnsafePathError, ValueError) as error:
        raise SkillPackageError(
            "invalid_source",
            "Skill package source could not be opened safely.",
        ) from error

    files: list[_PackageFile] = []
    registry = _PathRegistry()
    expanded = 0
    try:
        expected_entries = _preflight_zip_archive(archive_bytes)
        with zipfile.ZipFile(io.BytesIO(archive_bytes), mode="r") as archive:
            entries = archive.infolist()
            if len(entries) != expected_entries:
                raise _invalid_package("Skill package ZIP directory is inconsistent.")
            if sum(item.file_size for item in entries) > _MAX_EXPANDED_BYTES:
                raise _package_too_large()
            for item in entries:
                if item.flag_bits & 0x1:
                    raise _invalid_package("Encrypted ZIP entries are not supported.")
                is_directory = item.is_dir()
                relative = _zip_relative_path(
                    item.orig_filename,
                    is_directory=is_directory,
                )
                _validate_zip_mode(item, is_directory=is_directory)
                registry.register(
                    relative,
                    kind="directory" if is_directory else "file",
                )
                if is_directory:
                    if item.file_size != 0:
                        raise _invalid_package("ZIP directory entry is invalid.")
                    continue
                if item.file_size > _MAX_FILE_BYTES:
                    raise _package_too_large()
                with archive.open(item, mode="r") as stream:
                    data = stream.read(_MAX_FILE_BYTES + 1)
                    if len(data) > _MAX_FILE_BYTES or stream.read(1):
                        raise _package_too_large()
                if len(data) != item.file_size:
                    raise _invalid_package("ZIP entry size is inconsistent.")
                expanded += len(data)
                if expanded > _MAX_EXPANDED_BYTES:
                    raise _package_too_large()
                files.append(_PackageFile(path=relative, data=data))
    except SkillPackageError:
        raise
    except (
        OSError,
        EOFError,
        OverflowError,
        RuntimeError,
        UnicodeError,
        lzma.LZMAError,
        struct.error,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        zlib.error,
    ) as error:
        raise _invalid_package("Skill package ZIP is invalid.") from error
    return _snapshot_from_files(files, expected_name=None)


def _preflight_zip_archive(raw: bytes) -> int:
    if len(raw) < _ZIP_EOCD_MIN_BYTES:
        raise _invalid_package("Skill package ZIP is invalid.")
    search_start = max(
        0,
        len(raw) - _ZIP_EOCD_MIN_BYTES - _ZIP_MAX_COMMENT_BYTES,
    )
    eocd_offset = raw.rfind(_ZIP_EOCD_SIGNATURE, search_start)
    while eocd_offset >= search_start:
        if eocd_offset + _ZIP_EOCD_MIN_BYTES <= len(raw):
            comment_length = struct.unpack_from("<H", raw, eocd_offset + 20)[0]
            if eocd_offset + _ZIP_EOCD_MIN_BYTES + comment_length == len(raw):
                break
        eocd_offset = raw.rfind(
            _ZIP_EOCD_SIGNATURE,
            search_start,
            eocd_offset,
        )
    if eocd_offset < search_start:
        raise _invalid_package("Skill package ZIP is invalid.")

    (
        disk_number,
        central_disk,
        entries_on_disk,
        total_entries,
        central_size,
        central_offset,
    ) = struct.unpack_from("<4H2I", raw, eocd_offset + 4)
    if disk_number != 0 or central_disk != 0 or entries_on_disk != total_entries:
        raise _invalid_package("Multi-disk ZIP packages are not supported.")
    if (
        total_entries == 0xFFFF
        or central_size == 0xFFFFFFFF
        or central_offset == 0xFFFFFFFF
    ):
        raise _invalid_package("ZIP64 Skill packages are not supported.")
    if total_entries > _MAX_ENTRIES:
        raise _package_too_large()
    if central_size > eocd_offset or central_offset + central_size != eocd_offset:
        raise _invalid_package("Skill package ZIP directory is invalid.")

    cursor = central_offset
    central_end = eocd_offset
    observed_entries = 0
    while cursor < central_end:
        if (
            cursor + _ZIP_CENTRAL_HEADER_BYTES > central_end
            or raw[cursor : cursor + 4] != _ZIP_CENTRAL_SIGNATURE
        ):
            raise _invalid_package("Skill package ZIP directory is invalid.")
        name_length, extra_length, comment_length, entry_disk = struct.unpack_from(
            "<4H",
            raw,
            cursor + 28,
        )
        if entry_disk != 0:
            raise _invalid_package("Multi-disk ZIP packages are not supported.")
        cursor += (
            _ZIP_CENTRAL_HEADER_BYTES + name_length + extra_length + comment_length
        )
        if cursor > central_end:
            raise _invalid_package("Skill package ZIP directory is invalid.")
        observed_entries += 1
        if observed_entries > _MAX_ENTRIES:
            raise _package_too_large()
    if cursor != central_end or observed_entries != total_entries:
        raise _invalid_package("Skill package ZIP directory is inconsistent.")
    return observed_entries


def _snapshot_from_files(
    files: list[_PackageFile],
    *,
    expected_name: str | None,
) -> _PackageSnapshot:
    by_path = {item.path.as_posix(): item.data for item in files}
    manifest = by_path.get("SKILL.md")
    if manifest is None:
        raise _invalid_package("Skill package must contain a root SKILL.md.")
    try:
        descriptor = parse_skill_manifest(
            decode_skill_manifest(manifest),
            source=SkillSource.USER,
            root_path=Path("."),
            expected_name=expected_name,
        )
    except (UnicodeError, ValueError) as error:
        raise _invalid_package("Skill package manifest is invalid.") from error
    return _PackageSnapshot(
        descriptor=descriptor,
        files=tuple(sorted(files, key=lambda item: item.path.as_posix())),
    )


def _write_stage(
    path: Path,
    snapshot: _PackageSnapshot,
    *,
    pins: _TransactionPins,
) -> CoreFileIdentity:
    opened: list[DirectoryPin] = []
    stage_identity: CoreFileIdentity | None = None
    failure: BaseException | None = None
    try:
        pins.verify()
        stage_name = path.name
        make_directory_child(pins.skills, stage_name, 0o700)
        stage_identity = _child_identity(pins.skills, stage_name)
        stage_pin = open_directory(
            path,
            parent=pins.skills,
            name=stage_name,
            expected_identity=stage_identity,
        )
        opened.append(stage_pin)
        directories: dict[PurePosixPath, DirectoryPin] = {PurePosixPath(): stage_pin}
        required_directories = {
            parent
            for item in snapshot.files
            for parent in item.path.parents
            if parent != PurePosixPath()
        }
        for relative in sorted(
            required_directories,
            key=lambda item: (len(item.parts), item.as_posix()),
        ):
            parent = directories[relative.parent]
            name = relative.name
            make_directory_child(parent, name, 0o700)
            child = open_directory(
                parent.path / name,
                parent=parent,
                name=name,
                expected_identity=_child_identity(parent, name),
            )
            opened.append(child)
            directories[relative] = child
        for item in snapshot.files:
            _write_new_file(
                directories[item.path.parent],
                item.path.name,
                item.data,
            )
        for directory in reversed(opened):
            _fsync_descriptor(directory.descriptor)
        pins.verify()
        _fsync_directory(path.parent)
    except BaseException as error:
        failure = error
    finally:
        for directory in reversed(opened):
            directory.close()
    if failure is not None:
        if stage_identity is not None:
            try:
                _remove_internal_entry(
                    path,
                    path.parent,
                    pins=pins,
                    expected_identity=stage_identity,
                )
            except BaseException as cleanup_error:
                raise _transaction_failed() from cleanup_error
        if isinstance(failure, SkillPackageError):
            raise failure
        if isinstance(
            failure,
            (
                FileExistsError,
                MutationTargetChanged,
                OSError,
                UnsafeWorkspacePath,
            ),
        ):
            raise _transaction_failed() from failure
        raise failure
    if stage_identity is None:
        raise _transaction_failed()
    return stage_identity


def _write_new_file(parent: DirectoryPin, name: str, data: bytes) -> None:
    parent.verify_reachable()
    assert_child_identity(parent, name, None)
    descriptor = (
        os.open(parent.path / name, _exclusive_file_flags(), 0o600)
        if os.name == "nt"
        else os.open(
            name,
            _exclusive_file_flags(),
            0o600,
            dir_fd=parent.descriptor,
        )
    )
    try:
        _write_all(descriptor, data)
        os.fsync(descriptor)
        opened = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    parent.verify_reachable()
    linked = lstat_child(parent, name)
    linked_identity = core_file_identity(linked)
    if (
        linked_identity != core_file_identity(opened)
        or _is_link_or_reparse(linked)
        or not stat.S_ISREG(linked.st_mode)
        or int(linked.st_nlink) != 1
    ):
        raise MutationTargetChanged("Staged Skill file changed while being written.")
    assert_child_identity(parent, name, linked_identity)


def _validated_components(path: PurePosixPath) -> tuple[str, ...]:
    raw = path.as_posix()
    if (
        not raw
        or raw in {".", ".."}
        or raw.startswith("/")
        or raw.startswith("//")
        or path.is_absolute()
    ):
        raise _invalid_package("Skill package path is invalid.")
    components = path.parts
    if not components or any(component in {"", ".", ".."} for component in components):
        raise _invalid_package("Skill package path is invalid.")
    for component in components:
        _validate_component(component)
    normalized = tuple(
        unicodedata.normalize("NFC", item).casefold() for item in components
    )
    if len("/".join(normalized).encode("utf-8")) > _MAX_RELATIVE_PATH_BYTES:
        raise _invalid_package("Skill package path is too long.")
    return normalized


def _zip_relative_path(raw_name: str, *, is_directory: bool) -> PurePosixPath:
    if (
        not raw_name
        or "\x00" in raw_name
        or "\\" in raw_name
        or raw_name.startswith("/")
        or "//" in raw_name
        or (is_directory and not raw_name.endswith("/"))
        or (not is_directory and raw_name.endswith("/"))
    ):
        raise _invalid_package("ZIP entry path is invalid.")
    normalized = raw_name[:-1] if is_directory else raw_name
    if not normalized or any(
        component in {"", ".", ".."} for component in normalized.split("/")
    ):
        raise _invalid_package("ZIP entry path is invalid.")
    return PurePosixPath(normalized)


def _validate_component(component: str) -> None:
    if (
        not component
        or component in {".", ".."}
        or component != unicodedata.normalize("NFC", component)
        or component[-1] in {".", " "}
        or any(character in _WINDOWS_FORBIDDEN for character in component)
        or any(ord(character) < 32 for character in component)
        or len(component.encode("utf-8")) > _MAX_COMPONENT_BYTES
    ):
        raise _invalid_package("Skill package path component is invalid.")
    stem = component.split(".", 1)[0].casefold()
    if stem in _WINDOWS_RESERVED:
        raise _invalid_package("Skill package uses a reserved file name.")


def _validate_skill_name(name: str) -> None:
    if (
        not name
        or len(name) > 64
        or not name[0].islower()
        or not name[0].isascii()
        or any(
            not (character.isascii() and (character.islower() or character.isdigit()))
            and character != "-"
            for character in name[1:]
        )
    ):
        raise _invalid_package("Skill package name is invalid.")


def _validate_zip_mode(item: zipfile.ZipInfo, *, is_directory: bool) -> None:
    mode = item.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    expected = stat.S_IFDIR if is_directory else stat.S_IFREG
    if file_type not in {0, expected}:
        raise _invalid_package("ZIP entries must be plain files or directories.")


def _directory_names(directory: DirectoryPin) -> tuple[str, ...]:
    try:
        return bounded_directory_names(
            directory,
            max_entries=_MAX_ENTRIES,
        )
    except DirectoryEntryLimitExceeded as error:
        raise _package_too_large() from error
    except SkillPackageError:
        raise
    except (MutationTargetChanged, OSError, UnsafeWorkspacePath) as error:
        raise _transaction_failed() from error


def _is_plain_directory(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and not _is_link_or_reparse(info)


def _is_link_or_reparse(info: os.stat_result) -> bool:
    attributes = int(getattr(info, "st_file_attributes", 0))
    reparse = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return stat.S_ISLNK(info.st_mode) or bool(attributes & reparse)


def _remove_internal_entry(
    path: Path,
    root: Path,
    *,
    pins: _TransactionPins,
    expected_identity: CoreFileIdentity | None = None,
) -> None:
    try:
        path.relative_to(root)
    except ValueError as error:
        raise _transaction_failed() from error
    if path.parent != root or not (
        path.name.startswith(_STAGE_PREFIX)
        or path.name.startswith(_QUARANTINE_PREFIX)
        or not path.name.startswith(".")
    ):
        raise _transaction_failed()
    pins.verify()
    name = path.name
    if not _child_exists(pins.skills, name):
        return
    info = lstat_child(pins.skills, name)
    expected = core_file_identity(info)
    if expected_identity is not None and expected != expected_identity:
        raise MutationTargetChanged("A Skill package entry changed before cleanup.")
    if stat.S_ISDIR(info.st_mode) and not _is_link_or_reparse(info):
        child = open_directory(
            path,
            parent=pins.skills,
            name=name,
            expected_identity=expected,
        )
        try:
            _remove_directory_contents(
                child,
                budget=_TraversalBudget(remaining=_MAX_ENTRIES),
                depth=0,
            )
        finally:
            child.close()
        assert_child_identity(pins.skills, name, expected)
        remove_child(pins.skills, name, directory=True)
    else:
        assert_child_identity(
            pins.skills,
            name,
            expected,
            allow_reparse=True,
        )
        remove_child(
            pins.skills,
            name,
            directory=_entry_is_directory_node(info),
        )
    pins.verify()
    _fsync_directory(root)


def _remove_directory_contents(
    parent: DirectoryPin,
    *,
    budget: _TraversalBudget,
    depth: int,
) -> None:
    parent.verify_reachable()
    try:
        names = bounded_directory_names(
            parent,
            max_entries=budget.remaining,
        )
    except DirectoryEntryLimitExceeded as error:
        raise _transaction_failed() from error
    for name in names:
        budget.consume()
        child_depth = depth + 1
        if child_depth > _MAX_PATH_DEPTH:
            raise _transaction_failed()
        parent.verify_reachable()
        info = lstat_child(parent, name)
        expected = core_file_identity(info)
        if stat.S_ISDIR(info.st_mode) and not _is_link_or_reparse(info):
            child = open_directory(
                parent.path / name,
                parent=parent,
                name=name,
                expected_identity=expected,
            )
            try:
                _remove_directory_contents(
                    child,
                    budget=budget,
                    depth=child_depth,
                )
            finally:
                child.close()
            assert_child_identity(parent, name, expected)
            remove_child(parent, name, directory=True)
        else:
            assert_child_identity(
                parent,
                name,
                expected,
                allow_reparse=True,
            )
            remove_child(
                parent,
                name,
                directory=_entry_is_directory_node(info),
            )
    parent.verify_reachable()


def _entry_is_directory_node(info: os.stat_result) -> bool:
    attributes = int(getattr(info, "st_file_attributes", 0))
    directory_attribute = int(getattr(stat, "FILE_ATTRIBUTE_DIRECTORY", 0x10))
    return stat.S_ISDIR(info.st_mode) or bool(attributes & directory_attribute)


def _exclusive_file_flags() -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    for name in ("O_BINARY", "O_CLOEXEC", "O_NOINHERIT", "O_NOFOLLOW"):
        flags |= int(getattr(os, name, 0))
    return flags


def _write_all(descriptor: int, data: bytes) -> None:
    written = 0
    while written < len(data):
        count = os.write(descriptor, data[written:])
        if count <= 0:
            raise OSError("Could not write Skill package data.")
        written += count


def _fsync_descriptor(descriptor: int) -> None:
    try:
        os.fsync(descriptor)
    except OSError:
        if os.name != "nt":
            raise


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | int(getattr(os, "O_DIRECTORY", 0))
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        os.fsync(descriptor)
    except OSError:
        if os.name != "nt":
            raise
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _path_anchor(path: Path) -> Path:
    anchor = path.anchor
    if not anchor:
        raise ValueError("Path must be absolute.")
    return Path(anchor)


def _invalid_package(message: str) -> SkillPackageError:
    return SkillPackageError("invalid_package", message)


def _package_too_large() -> SkillPackageError:
    return SkillPackageError(
        "package_too_large",
        "Skill package exceeds a size or entry limit.",
    )


def _transaction_failed() -> SkillPackageError:
    return SkillPackageError(
        "transaction_failed",
        "Skill package transaction could not be completed safely.",
    )
