from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path


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


def lexical_absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def plain_directory_identity(path: Path) -> FileIdentity:
    info = os.lstat(path)
    _reject_link_or_reparse(path, info)
    if not stat.S_ISDIR(info.st_mode):
        raise NotADirectoryError(path)
    return _identity(info)


def plain_file_fingerprint(path: Path) -> FileFingerprint:
    info = os.lstat(path)
    _reject_link_or_reparse(path, info)
    if not stat.S_ISREG(info.st_mode):
        raise UnsafePathError(f"Path is not a regular file: {path}")
    if int(info.st_nlink) != 1:
        raise UnsafePathError(f"Hard-linked files are not allowed: {path}")
    return _fingerprint(info)


def validate_plain_components(
    anchor: Path,
    target: Path,
    *,
    target_kind: str,
) -> tuple[FileIdentity, ...]:
    normalized_anchor = lexical_absolute(anchor)
    normalized_target = lexical_absolute(target)
    try:
        relative = normalized_target.relative_to(normalized_anchor)
    except ValueError as error:
        raise UnsafePathError("Path escapes its trusted anchor.") from error

    paths = [normalized_anchor]
    current = normalized_anchor
    for part in relative.parts:
        current /= part
        paths.append(current)

    identities: list[FileIdentity] = []
    for index, component in enumerate(paths):
        info = os.lstat(component)
        _reject_link_or_reparse(component, info)
        final = index == len(paths) - 1
        expected = target_kind if final else "directory"
        if expected == "directory" and not stat.S_ISDIR(info.st_mode):
            raise NotADirectoryError(component)
        if expected == "file" and not stat.S_ISREG(info.st_mode):
            raise UnsafePathError(f"Path is not a regular file: {component}")
        identities.append(_identity(info))
    return tuple(identities)


def read_bounded_file(
    path: Path,
    *,
    max_bytes: int,
    expected: FileFingerprint | None = None,
) -> BoundedFile:
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    before = plain_file_fingerprint(path)
    if expected is not None and before != expected:
        raise FileChangedError("File changed after its trusted snapshot.")
    if before.size > max_bytes:
        raise FileTooLargeError(f"File exceeds the {max_bytes}-byte limit.")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = _fingerprint(os.fstat(descriptor))
        if opened != before:
            raise FileChangedError("File changed while it was being opened.")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            data = stream.read(max_bytes + 1)
    finally:
        os.close(descriptor)

    if len(data) > max_bytes:
        raise FileTooLargeError(f"File exceeds the {max_bytes}-byte limit.")
    after = plain_file_fingerprint(path)
    if after != before:
        raise FileChangedError("File changed while it was being read.")
    return BoundedFile(data=data, fingerprint=before)


def ensure_identity(path: Path, expected: FileIdentity) -> None:
    try:
        current = plain_directory_identity(path)
    except (FileNotFoundError, NotADirectoryError, OSError, UnsafePathError) as error:
        raise FileChangedError("Directory changed after discovery.") from error
    if current != expected:
        raise FileChangedError("Directory changed after discovery.")


def _reject_link_or_reparse(path: Path, info: os.stat_result) -> None:
    attributes = int(getattr(info, "st_file_attributes", 0))
    reparse_attribute = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    if stat.S_ISLNK(info.st_mode) or (
        reparse_attribute and attributes & reparse_attribute
    ):
        raise UnsafePathError(f"Links and reparse points are not allowed: {path}")


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
