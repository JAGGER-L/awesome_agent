from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from awesome_agent.core.filesystem import DirectoryPin as _CoreDirectoryPin
from awesome_agent.core.filesystem import FileIdentity as _CoreFileIdentity
from awesome_agent.core.filesystem import MutationTargetChanged as _CoreTargetChanged
from awesome_agent.core.filesystem import ReadRegularFile as _CoreReadRegularFile
from awesome_agent.core.filesystem import UnsafeWorkspacePath as _CoreUnsafePath
from awesome_agent.core.filesystem import WorkspaceFileTooLarge as _CoreFileTooLarge
from awesome_agent.core.filesystem import lstat_child as _lstat_child
from awesome_agent.core.filesystem import open_directory as _core_open_directory
from awesome_agent.core.filesystem import read_regular_child as _core_read_regular_child


class UnsafePathError(ValueError):
    pass


class FileChangedError(ValueError):
    pass


class FileTooLargeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FileIdentity:
    device: int
    inode: int
    file_type: int


@dataclass(frozen=True, slots=True)
class FileFingerprint:
    identity: FileIdentity
    size: int
    modified_ns: int


@dataclass(frozen=True, slots=True)
class BoundedFile:
    data: bytes
    fingerprint: FileFingerprint


class PinnedPlainDirectory:
    """Pin one plain directory chain and perform no-follow descendant reads."""

    def __init__(
        self,
        anchor: Path,
        target: Path,
        *,
        expected_identities: tuple[FileIdentity, ...] | None = None,
    ) -> None:
        self.anchor = lexical_absolute(anchor)
        self.target = lexical_absolute(target)
        try:
            relative = self.target.relative_to(self.anchor)
        except ValueError as error:
            raise UnsafePathError("Path escapes its trusted anchor.") from error
        self._parts = tuple(part for part in relative.parts if part != ".")
        self._expected_identities = expected_identities
        expected_count = len(self._parts) + 1
        if (
            expected_identities is not None
            and len(expected_identities) != expected_count
        ):
            raise ValueError(
                "Pinned directory identities must cover anchor through target."
            )
        self._pins: list[_CoreDirectoryPin] = []

    def __enter__(self) -> Self:
        try:
            root_expected = self._expected_at(0)
            root = _open_pinned_directory(
                self.anchor,
                expected_identity=_to_core_identity(root_expected),
            )
            self._pins.append(root)
            current = root
            for index, part in enumerate(self._parts, start=1):
                current = _open_pinned_directory(
                    current.path / part,
                    parent=current,
                    name=part,
                    expected_identity=_to_core_identity(self._expected_at(index)),
                )
                self._pins.append(current)
        except _CoreTargetChanged as error:
            self.close()
            raise FileChangedError("Directory changed after discovery.") from error
        except _CoreUnsafePath as error:
            self.close()
            raise UnsafePathError(
                "Directory chain contains a link or reparse point."
            ) from error
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @property
    def identities(self) -> tuple[FileIdentity, ...]:
        return tuple(_from_core_identity(pin.identity) for pin in self._pins)

    def close(self) -> None:
        for pin in reversed(self._pins):
            pin.close()
        self._pins.clear()

    def verify(self) -> None:
        try:
            self._directory.verify_reachable()
        except (FileNotFoundError, OSError, _CoreTargetChanged) as error:
            raise FileChangedError("Directory changed while it was pinned.") from error

    def names(self) -> tuple[str, ...]:
        self.verify()
        listing_path: int | Path = (
            self._directory.path if os.name == "nt" else self._directory.descriptor
        )
        names = tuple(
            sorted(
                os.listdir(listing_path),
                key=lambda value: (value.casefold(), value),
            )
        )
        self.verify()
        return names

    def child_status(self, name: str) -> os.stat_result:
        _validate_plain_name(name)
        self.verify()
        try:
            result = _lstat_child(self._directory, name)
        except _CoreTargetChanged as error:
            raise FileChangedError("Directory changed while it was pinned.") from error
        self.verify()
        return result

    @contextmanager
    def descend(
        self,
        relative: Path,
        *,
        expected_identities: tuple[FileIdentity, ...] | None = None,
    ) -> Iterator[PinnedPlainDirectory]:
        parts = _plain_relative_parts(relative)
        if expected_identities is not None and len(expected_identities) != len(parts):
            raise ValueError("Descendant identities must cover every directory.")
        start = len(self._pins)
        try:
            current = self._directory
            for index, part in enumerate(parts):
                expected = (
                    expected_identities[index]
                    if expected_identities is not None
                    else None
                )
                current = _open_pinned_directory(
                    current.path / part,
                    parent=current,
                    name=part,
                    expected_identity=_to_core_identity(expected),
                )
                self._pins.append(current)
            yield self
            self.verify()
        except _CoreTargetChanged as error:
            raise FileChangedError("Directory changed while it was pinned.") from error
        except _CoreUnsafePath as error:
            raise UnsafePathError(
                "Directory chain contains a link or reparse point."
            ) from error
        finally:
            for pin in reversed(self._pins[start:]):
                pin.close()
            del self._pins[start:]

    def read_file(
        self,
        relative: Path,
        *,
        max_bytes: int,
        expected: FileFingerprint | None = None,
    ) -> BoundedFile:
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        parts = _plain_relative_parts(relative)
        if not parts:
            raise UnsafePathError("A file name is required.")
        with self.descend(Path(*parts[:-1])):
            try:
                opened = _read_pinned_regular_child(
                    self._directory,
                    parts[-1],
                    max_bytes=max_bytes,
                    expected_identity=(
                        _to_core_identity(expected.identity)
                        if expected is not None
                        else None
                    ),
                )
            except _CoreFileTooLarge as error:
                raise FileTooLargeError(
                    f"File exceeds the {max_bytes}-byte limit."
                ) from error
            except _CoreTargetChanged as error:
                raise FileChangedError(
                    "File changed while it was being read."
                ) from error
            except _CoreUnsafePath as error:
                raise UnsafePathError(
                    "File is a link, reparse point, hard link, or non-regular node."
                ) from error
            fingerprint = _fingerprint(opened.status)
            if expected is not None and fingerprint != expected:
                raise FileChangedError("File changed after its trusted snapshot.")
            self.verify()
            return BoundedFile(data=opened.data, fingerprint=fingerprint)

    @property
    def _directory(self) -> _CoreDirectoryPin:
        if not self._pins:
            raise RuntimeError("Pinned directory chain is not open.")
        return self._pins[-1]

    def _expected_at(self, index: int) -> FileIdentity | None:
        if self._expected_identities is None:
            return None
        return self._expected_identities[index]


def lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def file_identity(info: os.stat_result) -> FileIdentity:
    return _identity(info)


def _identity(info: os.stat_result) -> FileIdentity:
    return FileIdentity(
        device=int(info.st_dev),
        inode=int(info.st_ino),
        file_type=stat.S_IFMT(info.st_mode),
    )


def _fingerprint(info: os.stat_result) -> FileFingerprint:
    return FileFingerprint(
        identity=_identity(info),
        size=int(info.st_size),
        modified_ns=int(info.st_mtime_ns),
    )


def _to_core_identity(value: FileIdentity | None) -> _CoreFileIdentity | None:
    if value is None:
        return None
    return _CoreFileIdentity(
        device=value.device,
        inode=value.inode,
        file_type=value.file_type,
        reparse=False,
    )


def _open_pinned_directory(
    path: Path,
    *,
    parent: _CoreDirectoryPin | None = None,
    name: str | None = None,
    expected_identity: _CoreFileIdentity | None = None,
) -> _CoreDirectoryPin:
    return _core_open_directory(
        path,
        parent=parent,
        name=name,
        expected_identity=expected_identity,
    )


def _read_pinned_regular_child(
    parent: _CoreDirectoryPin,
    name: str,
    *,
    max_bytes: int | None,
    expected_identity: _CoreFileIdentity | None = None,
) -> _CoreReadRegularFile:
    return _core_read_regular_child(
        parent,
        name,
        max_bytes=max_bytes,
        expected_identity=expected_identity,
    )


def _from_core_identity(value: _CoreFileIdentity) -> FileIdentity:
    return FileIdentity(
        device=value.device,
        inode=value.inode,
        file_type=value.file_type,
    )


def _plain_relative_parts(relative: Path) -> tuple[str, ...]:
    if relative.is_absolute() or bool(relative.drive) or ".." in relative.parts:
        raise UnsafePathError("Path escapes its trusted directory.")
    parts = tuple(part for part in relative.parts if part != ".")
    for part in parts:
        _validate_plain_name(part)
    return parts


def _validate_plain_name(name: str) -> None:
    parsed = Path(name)
    if (
        not name
        or parsed.is_absolute()
        or bool(parsed.drive)
        or parsed.name != name
        or name in {".", ".."}
    ):
        raise UnsafePathError("Path contains an unsafe directory component.")
