from __future__ import annotations

import ctypes
import importlib
import os
import stat
import tempfile
from collections.abc import Callable, Iterator
from contextlib import suppress
from dataclasses import dataclass
from os import stat_result
from pathlib import Path
from typing import Any, Literal, Self

_FILE_ATTRIBUTE_DIRECTORY = getattr(stat, "FILE_ATTRIBUTE_DIRECTORY", 0x10)
_FILE_ATTRIBUTE_REPARSE_POINT = getattr(
    stat,
    "FILE_ATTRIBUTE_REPARSE_POINT",
    0x400,
)
_WINDOWS_FILE_LIST_DIRECTORY = 0x0001
_WINDOWS_FILE_READ_ATTRIBUTES = 0x0080
_WINDOWS_GENERIC_READ = 0x80000000
_WINDOWS_FILE_SHARE_READ = 0x00000001
_WINDOWS_FILE_SHARE_WRITE = 0x00000002
_WINDOWS_OPEN_EXISTING = 3
_WINDOWS_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000


class MutationTargetChanged(RuntimeError):
    """A bound filesystem object changed before a transaction completed."""


class UnsafeWorkspacePath(RuntimeError):
    """A path cannot be accessed without following an unsafe filesystem node."""


class WorkspaceFileTooLarge(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FileIdentity:
    device: int
    inode: int
    file_type: int
    reparse: bool


type SafeNodeKind = Literal["file", "directory"]


@dataclass(frozen=True, slots=True)
class SafeDirectoryEntry:
    name: str
    kind: SafeNodeKind
    identity: FileIdentity


@dataclass(frozen=True, slots=True)
class ReadRegularFile:
    data: bytes
    status: stat_result


@dataclass(slots=True)
class DirectoryPin:
    path: Path
    descriptor: int
    identity: FileIdentity
    parent: DirectoryPin | None
    name: str | None
    closed: bool = False

    def close(self) -> None:
        if self.closed:
            return
        os.close(self.descriptor)
        self.closed = True

    def verify_reachable(self) -> None:
        if self.closed:
            raise MutationTargetChanged("A bound workspace directory was closed.")
        if self.parent is None:
            info = os.lstat(self.path)
        else:
            self.parent.verify_reachable()
            assert self.name is not None
            info = lstat_child(self.parent, self.name)
        if is_link_or_reparse(info) or identity(info) != self.identity:
            raise MutationTargetChanged(
                "A workspace directory changed after path validation."
            )


def is_link_or_reparse(status: stat_result) -> bool:
    attributes = int(getattr(status, "st_file_attributes", 0))
    return stat.S_ISLNK(status.st_mode) or bool(
        attributes & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def is_directory_link_or_reparse(path: Path, status: stat_result) -> bool:
    del path
    if not is_link_or_reparse(status):
        return False
    attributes = int(getattr(status, "st_file_attributes", 0))
    return bool(attributes & _FILE_ATTRIBUTE_DIRECTORY) or stat.S_ISLNK(status.st_mode)


def identity(info: stat_result) -> FileIdentity:
    return FileIdentity(
        device=int(info.st_dev),
        inode=int(info.st_ino),
        file_type=stat.S_IFMT(info.st_mode),
        reparse=is_link_or_reparse(info),
    )


def open_directory(
    path: Path,
    *,
    parent: DirectoryPin | None = None,
    name: str | None = None,
    expected_identity: FileIdentity | None = None,
) -> DirectoryPin:
    try:
        before = os.lstat(path) if parent is None else lstat_child(parent, name or "")
    except FileNotFoundError:
        if expected_identity is not None:
            raise MutationTargetChanged(
                "A bound workspace directory disappeared before it was opened."
            ) from None
        raise
    if is_link_or_reparse(before):
        raise UnsafeWorkspacePath("Directory links and reparse points are not allowed.")
    if not stat.S_ISDIR(before.st_mode):
        raise UnsafeWorkspacePath("A workspace path component is not a directory.")
    if expected_identity is not None and identity(before) != expected_identity:
        raise MutationTargetChanged(
            "A bound workspace directory was replaced before it was opened."
        )

    if os.name == "nt":
        descriptor = windows_open_descriptor(path, directory=True)
    else:
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        directory_flag = getattr(os, "O_DIRECTORY", 0)
        if not no_follow or not directory_flag or os.open not in os.supports_dir_fd:
            raise UnsafeWorkspacePath(
                "This platform cannot safely bind workspace directories."
            )
        flags = os.O_RDONLY | no_follow | directory_flag | getattr(os, "O_CLOEXEC", 0)
        descriptor = (
            os.open(path, flags)
            if parent is None
            else os.open(name or "", flags, dir_fd=parent.descriptor)
        )
    try:
        opened = os.fstat(descriptor)
    except BaseException:
        os.close(descriptor)
        raise
    if (
        is_link_or_reparse(opened)
        or identity(opened) != identity(before)
        or (expected_identity is not None and identity(opened) != expected_identity)
    ):
        os.close(descriptor)
        raise MutationTargetChanged(
            "A workspace directory changed while it was being opened."
        )
    return DirectoryPin(
        path=path,
        descriptor=descriptor,
        identity=identity(opened),
        parent=parent,
        name=name,
    )


def lstat_child(parent: DirectoryPin, name: str) -> stat_result:
    if os.name == "nt":
        return os.lstat(parent.path / name)
    return os.stat(name, dir_fd=parent.descriptor, follow_symlinks=False)


def readlink_child(parent: DirectoryPin, name: str) -> str:
    if os.name == "nt":
        return os.readlink(parent.path / name)
    return os.readlink(name, dir_fd=parent.descriptor)


def open_regular_file(
    parent: DirectoryPin,
    name: str,
    *,
    expected_identity: FileIdentity | None = None,
) -> tuple[int, stat_result]:
    try:
        before = lstat_child(parent, name)
    except FileNotFoundError:
        if expected_identity is not None:
            raise MutationTargetChanged(
                "A bound workspace file disappeared before it was opened."
            ) from None
        raise
    if expected_identity is not None and identity(before) != expected_identity:
        raise MutationTargetChanged("A workspace file changed before it was opened.")
    if is_link_or_reparse(before):
        raise UnsafeWorkspacePath("File links and reparse points cannot be opened.")
    if not stat.S_ISREG(before.st_mode):
        raise UnsafeWorkspacePath("A workspace path is not a regular file.")
    if int(before.st_nlink) != 1:
        raise UnsafeWorkspacePath("Hard-linked files cannot be opened safely.")
    if os.name == "nt":
        descriptor = windows_open_descriptor(parent.path / name, directory=False)
    else:
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        if not no_follow or os.open not in os.supports_dir_fd:
            raise UnsafeWorkspacePath(
                "This platform cannot safely open workspace files."
            )
        descriptor = os.open(
            name,
            os.O_RDONLY | no_follow | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent.descriptor,
        )
    try:
        opened = os.fstat(descriptor)
    except BaseException:
        os.close(descriptor)
        raise
    if (
        is_link_or_reparse(opened)
        or not stat.S_ISREG(opened.st_mode)
        or int(opened.st_nlink) != 1
        or identity(opened) != identity(before)
        or (expected_identity is not None and identity(opened) != expected_identity)
    ):
        os.close(descriptor)
        raise MutationTargetChanged("A workspace file changed while it was opened.")
    return descriptor, opened


def windows_open_descriptor(path: Path, *, directory: bool) -> int:
    win_dll: Any = getattr(ctypes, "WinDLL", None)
    get_last_error: Any = getattr(ctypes, "get_last_error", None)
    if win_dll is None or get_last_error is None:
        raise OSError("Windows handle APIs are unavailable on this platform.")
    kernel32: Any = win_dll("kernel32", use_last_error=True)
    create_file: Any = kernel32.CreateFileW
    create_file.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    create_file.restype = ctypes.c_void_p
    access = (
        _WINDOWS_FILE_LIST_DIRECTORY | _WINDOWS_FILE_READ_ATTRIBUTES
        if directory
        else _WINDOWS_GENERIC_READ | _WINDOWS_FILE_READ_ATTRIBUTES
    )
    flags = _WINDOWS_FILE_FLAG_OPEN_REPARSE_POINT
    if directory:
        flags |= _WINDOWS_FILE_FLAG_BACKUP_SEMANTICS
    handle = create_file(
        str(path),
        access,
        _WINDOWS_FILE_SHARE_READ | _WINDOWS_FILE_SHARE_WRITE,
        None,
        _WINDOWS_OPEN_EXISTING,
        flags,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle == invalid_handle:
        error_number = int(get_last_error())
        raise OSError(error_number, os.strerror(error_number), str(path))
    msvcrt: Any = importlib.import_module("msvcrt")

    try:
        return int(
            msvcrt.open_osfhandle(
                int(handle),
                os.O_RDONLY | getattr(os, "O_BINARY", 0),
            )
        )
    except Exception:
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        raise


def read_descriptor(descriptor: int, *, max_bytes: int | None) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, 64 * 1024)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if max_bytes is not None and total > max_bytes:
            raise WorkspaceFileTooLarge
    return b"".join(chunks)


class PinnedWorkspacePath:
    """Pins a workspace root and every parent of one workspace-relative path."""

    def __init__(
        self,
        workspace: Path,
        root_identity: FileIdentity,
        relative: Path,
        target_existed: bool,
        target_identity: FileIdentity | None,
    ) -> None:
        if relative.is_absolute() or bool(relative.drive) or ".." in relative.parts:
            raise UnsafeWorkspacePath("Workspace paths must remain relative.")
        self.workspace = workspace
        self.root_identity = root_identity
        self.relative = relative
        if target_existed != (target_identity is not None):
            raise ValueError(
                "A pinned workspace path must bind target existence and identity."
            )
        self.target_existed = target_existed
        self.target_identity = target_identity
        self._parts = tuple(part for part in relative.parts if part != ".")
        self._pins: list[DirectoryPin] = []

    def __enter__(self) -> Self:
        try:
            root = open_directory(
                self.workspace,
                expected_identity=self.root_identity,
            )
            self._pins.append(root)
            current = root
            for part in self._parts[:-1]:
                current = open_directory(
                    current.path / part,
                    parent=current,
                    name=part,
                )
                self._pins.append(current)
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @property
    def parent(self) -> DirectoryPin:
        if not self._pins:
            raise RuntimeError("Pinned workspace path is not open.")
        return self._pins[-1]

    @property
    def name(self) -> str | None:
        return self._parts[-1] if self._parts else None

    def close(self) -> None:
        for pin in reversed(self._pins):
            pin.close()
        self._pins.clear()

    def open_directory(self) -> DirectoryPin:
        if self.name is None:
            if not self.target_existed or self.target_identity != self.parent.identity:
                raise MutationTargetChanged(
                    "The bound workspace directory generation changed."
                )
            return self.parent
        expected_identity = self._require_existing_target()
        current = open_directory(
            self.parent.path / self.name,
            parent=self.parent,
            name=self.name,
            expected_identity=expected_identity,
        )
        self._pins.append(current)
        return current

    def kind(self) -> SafeNodeKind:
        if self.name is None:
            if not self.target_existed or self.target_identity != self.parent.identity:
                raise MutationTargetChanged(
                    "The bound workspace target generation changed."
                )
            return "directory"
        self.parent.verify_reachable()
        expected_identity = self._require_existing_target()
        try:
            before = lstat_child(self.parent, self.name)
        except FileNotFoundError:
            raise MutationTargetChanged(
                "The bound workspace target disappeared."
            ) from None
        if identity(before) != expected_identity:
            raise MutationTargetChanged(
                "The bound workspace target generation changed."
            )
        if is_link_or_reparse(before):
            raise UnsafeWorkspacePath(
                "Links and reparse points cannot be opened safely."
            )
        if stat.S_ISDIR(before.st_mode):
            kind: SafeNodeKind = "directory"
        elif stat.S_ISREG(before.st_mode) and int(before.st_nlink) == 1:
            kind = "file"
        else:
            raise UnsafeWorkspacePath(
                "Workspace reads require a plain directory or regular file."
            )
        self.parent.verify_reachable()
        return kind

    def read_regular(self, *, max_bytes: int) -> ReadRegularFile:
        if self.name is None:
            raise UnsafeWorkspacePath("The workspace root is not a regular file.")
        return read_regular_child(
            self.parent,
            self.name,
            max_bytes=max_bytes,
            expected_identity=self._require_existing_target(),
        )

    def _require_existing_target(self) -> FileIdentity:
        if not self.target_existed or self.target_identity is None:
            if self.name is not None:
                assert_child_identity(self.parent, self.name, None)
            raise MutationTargetChanged(
                "A workspace target appeared after path validation."
            )
        return self.target_identity


def read_regular_child(
    parent: DirectoryPin,
    name: str,
    *,
    max_bytes: int | None,
    expected_identity: FileIdentity | None = None,
) -> ReadRegularFile:
    if max_bytes is not None and max_bytes < 0:
        raise ValueError("max_bytes must be non-negative")
    parent.verify_reachable()
    descriptor, opened = open_regular_file(
        parent,
        name,
        expected_identity=expected_identity,
    )
    try:
        if max_bytes is not None and int(opened.st_size) > max_bytes:
            raise WorkspaceFileTooLarge
        data = read_descriptor(descriptor, max_bytes=max_bytes)
        _assert_regular_read_status(opened, os.fstat(descriptor))
        try:
            position = os.lseek(descriptor, 0, os.SEEK_SET)
        except OSError as error:
            raise MutationTargetChanged(
                "A workspace file changed while it was being read."
            ) from error
        if position != 0:
            raise MutationTargetChanged(
                "A workspace file changed while it was being read."
            )
        verified = read_descriptor(descriptor, max_bytes=max_bytes)
        _assert_regular_read_status(opened, os.fstat(descriptor))
        if verified != data:
            raise MutationTargetChanged(
                "A workspace file changed while it was being read."
            )
    finally:
        os.close(descriptor)
    parent.verify_reachable()
    return ReadRegularFile(data=data, status=opened)


def _assert_regular_read_status(opened: stat_result, observed: stat_result) -> None:
    if (
        identity(observed) != identity(opened)
        or is_link_or_reparse(observed)
        or not stat.S_ISREG(observed.st_mode)
        or int(observed.st_nlink) != 1
        or int(observed.st_size) != int(opened.st_size)
        or int(observed.st_mtime_ns) != int(opened.st_mtime_ns)
    ):
        raise MutationTargetChanged("A workspace file changed while it was being read.")


def list_directory_entries(
    directory: DirectoryPin,
    *,
    check_cancelled: Callable[[], None] | None = None,
) -> tuple[SafeDirectoryEntry, ...]:
    """Return only children whose no-follow lstat and opened handle agree."""

    return tuple(
        iter_directory_entries(
            directory,
            check_cancelled=check_cancelled,
        )
    )


def iter_directory_entries(
    directory: DirectoryPin,
    *,
    check_cancelled: Callable[[], None] | None = None,
) -> Iterator[SafeDirectoryEntry]:
    """Yield children only after no-follow lstat and opened handles agree."""

    directory.verify_reachable()
    listing_path: int | Path = (
        directory.path if os.name == "nt" else directory.descriptor
    )
    names = sorted(
        os.listdir(listing_path),
        key=lambda value: (value.casefold(), value),
    )
    for name in names:
        if check_cancelled is not None:
            check_cancelled()
        directory.verify_reachable()
        try:
            before = lstat_child(directory, name)
            if is_link_or_reparse(before):
                continue
            expected = identity(before)
            if stat.S_ISDIR(before.st_mode):
                child = open_directory(
                    directory.path / name,
                    parent=directory,
                    name=name,
                    expected_identity=expected,
                )
                try:
                    child.verify_reachable()
                finally:
                    child.close()
                kind: SafeNodeKind = "directory"
            elif stat.S_ISREG(before.st_mode) and int(before.st_nlink) == 1:
                descriptor, _opened = open_regular_file(
                    directory,
                    name,
                    expected_identity=expected,
                )
                os.close(descriptor)
                kind = "file"
            else:
                continue
        except (FileNotFoundError, MutationTargetChanged, UnsafeWorkspacePath, OSError):
            directory.verify_reachable()
            continue
        directory.verify_reachable()
        yield SafeDirectoryEntry(name=name, kind=kind, identity=expected)
    directory.verify_reachable()


def assert_child_identity(
    parent: DirectoryPin,
    name: str,
    expected: FileIdentity | None,
    *,
    allow_reparse: bool = False,
) -> None:
    try:
        current = lstat_child(parent, name)
    except FileNotFoundError:
        if expected is None:
            return
        raise MutationTargetChanged(
            "A workspace file disappeared before mutation."
        ) from None
    if expected is None or identity(current) != expected:
        raise MutationTargetChanged("A workspace file changed before mutation.")
    if is_link_or_reparse(current) and not allow_reparse:
        raise MutationTargetChanged("A workspace file changed before mutation.")


def atomic_replace_child(
    parent: DirectoryPin,
    name: str,
    content: bytes,
    mode: int | None,
) -> None:
    if os.name == "nt":
        _atomic_replace_windows(parent.path / name, content, mode)
        return
    temporary_name = f".{name}.{os.getpid()}.{os.urandom(8).hex()}.tmp"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=parent.descriptor,
        )
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
        if mode is not None:
            fchmod: Any = getattr(os, "fchmod", None)
            if fchmod is None:
                raise OSError("Descriptor chmod is unavailable on this platform.")
            fchmod(descriptor, mode)
        os.close(descriptor)
        descriptor = None
        os.replace(
            temporary_name,
            name,
            src_dir_fd=parent.descriptor,
            dst_dir_fd=parent.descriptor,
        )
        temporary_name = ""
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_name:
            with suppress(FileNotFoundError):
                os.unlink(temporary_name, dir_fd=parent.descriptor)


def _atomic_replace_windows(path: Path, content: bytes, mode: int | None) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
            if mode is not None:
                os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def remove_child(parent: DirectoryPin, name: str, *, directory: bool) -> None:
    if os.name == "nt":
        path = parent.path / name
        path.rmdir() if directory else path.unlink()
        return
    if directory:
        os.rmdir(name, dir_fd=parent.descriptor)
    else:
        os.unlink(name, dir_fd=parent.descriptor)


def make_directory_child(parent: DirectoryPin, name: str, mode: int | None) -> None:
    creation_mode = mode if mode is not None else 0o777
    if os.name == "nt":
        (parent.path / name).mkdir(mode=creation_mode)
        if mode is not None:
            os.chmod(parent.path / name, mode)
        return
    os.mkdir(name, mode=creation_mode, dir_fd=parent.descriptor)


def make_symlink_child(parent: DirectoryPin, name: str, target: str) -> None:
    if os.name == "nt":
        (parent.path / name).symlink_to(target, target_is_directory=False)
        return
    os.symlink(target, name, dir_fd=parent.descriptor)
