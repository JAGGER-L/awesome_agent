from __future__ import annotations

import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Never, Self

from awesome_agent.core.changes.filesystem import BoundFileMutation, NodeSnapshot
from awesome_agent.core.changes.models import FileNodeType
from awesome_agent.core.filesystem import (
    DirectoryPin as _DirectoryPin,
)
from awesome_agent.core.filesystem import (
    FileIdentity,
    MutationTargetChanged,
    SafeDirectoryEntry,
    UnsafeWorkspacePath,
    is_link_or_reparse,
)
from awesome_agent.core.filesystem import (
    PinnedWorkspacePath as _PinnedWorkspacePath,
)
from awesome_agent.core.filesystem import (
    assert_child_identity as _assert_child_identity,
)
from awesome_agent.core.filesystem import (
    atomic_replace_child as _atomic_replace_child,
)
from awesome_agent.core.filesystem import (
    identity as _identity,
)
from awesome_agent.core.filesystem import (
    list_directory_entries as _list_directory_entries,
)
from awesome_agent.core.filesystem import (
    lstat_child as _lstat_child,
)
from awesome_agent.core.filesystem import (
    open_directory as _open_directory,
)
from awesome_agent.core.filesystem import (
    open_regular_file as _open_regular_file,
)
from awesome_agent.core.filesystem import (
    read_descriptor as _read_descriptor,
)
from awesome_agent.core.filesystem import (
    read_regular_child as _read_regular_child,
)
from awesome_agent.core.filesystem import (
    readlink_child as _readlink_child,
)
from awesome_agent.core.filesystem import (
    remove_child as _remove_child,
)
from awesome_agent.core.tools.contracts import ToolErrorCode
from awesome_agent.core.tools.errors import ExpectedToolFailure
from awesome_agent.core.tools.policy import SafeWorkspacePath

_FILE_ATTRIBUTE_DIRECTORY = getattr(stat, "FILE_ATTRIBUTE_DIRECTORY", 0x10)


@dataclass(frozen=True, slots=True)
class BoundRegularFile:
    snapshot: NodeSnapshot
    identity: FileIdentity

    @property
    def data(self) -> bytes:
        return self.snapshot.content or b""


@dataclass(frozen=True, slots=True)
class SecureDeleteNode:
    relative: Path
    node_type: FileNodeType
    content_bytes: int
    mutation: BoundFileMutation


class WorkspaceDirectoryTransaction:
    """Pins a workspace directory path and maps unsafe access to tool errors."""

    def __init__(self, safe: SafeWorkspacePath) -> None:
        self.safe = safe
        self._reader = _PinnedWorkspacePath(
            safe.workspace,
            safe.workspace_root_identity,
            safe.relative,
            safe.target_existed,
            safe.target_identity,
        )
        self._directory: _DirectoryPin | None = None

    def __enter__(self) -> Self:
        try:
            self._reader.__enter__()
            self._directory = self._reader.open_directory()
        except (
            ExpectedToolFailure,
            MutationTargetChanged,
            UnsafeWorkspacePath,
            OSError,
        ) as error:
            self._reader.close()
            self._raise_expected(error)
        return self

    def __exit__(self, *_args: object) -> None:
        self._reader.close()
        self._directory = None

    @property
    def directory(self) -> _DirectoryPin:
        if self._directory is None:
            raise RuntimeError("Workspace directory transaction is not open.")
        return self._directory

    def entries(self) -> tuple[SafeDirectoryEntry, ...]:
        try:
            return _list_directory_entries(self.directory)
        except (
            ExpectedToolFailure,
            MutationTargetChanged,
            UnsafeWorkspacePath,
            OSError,
        ) as error:
            self._raise_expected(error)

    def _raise_expected(self, error: BaseException) -> Never:
        if isinstance(error, ExpectedToolFailure):
            raise error
        if isinstance(error, MutationTargetChanged):
            raise ExpectedToolFailure(
                ToolErrorCode.CONFLICT,
                "Workspace path changed while the directory was being read.",
                metadata={"path": self.safe.requested},
            ) from error
        if isinstance(error, FileNotFoundError):
            raise ExpectedToolFailure(
                ToolErrorCode.NOT_FOUND,
                "Directory was not found.",
                metadata={"path": self.safe.requested},
            ) from error
        if isinstance(error, (UnsafeWorkspacePath, OSError)):
            raise ExpectedToolFailure(
                ToolErrorCode.PERMISSION_DENIED,
                "Directory could not be opened safely.",
                metadata={"path": self.safe.requested},
            ) from error
        raise error


class WorkspaceFileTransaction:
    """Pins a workspace path's directory chain for one bounded operation."""

    def __init__(self, safe: SafeWorkspacePath) -> None:
        self.safe = safe
        self._pins: list[_DirectoryPin] = []

    def __enter__(self) -> Self:
        try:
            root = _open_directory(
                self.safe.workspace,
                expected_identity=self.safe.workspace_root_identity,
            )
            self._pins.append(root)
            current = root
            for part in self.safe.relative.parts[:-1]:
                if part == ".":
                    continue
                current = _open_directory(
                    current.path / part, parent=current, name=part
                )
                self._pins.append(current)
        except (ExpectedToolFailure, MutationTargetChanged):
            self.close()
            raise
        except UnsafeWorkspacePath as error:
            self.close()
            raise ExpectedToolFailure(
                ToolErrorCode.PERMISSION_DENIED,
                "Workspace path could not be opened safely.",
                metadata={"path": self.safe.requested},
            ) from error
        except FileNotFoundError as error:
            self.close()
            raise ExpectedToolFailure(
                ToolErrorCode.NOT_FOUND,
                "Parent directory was not found.",
                metadata={"path": self.safe.requested},
            ) from error
        except OSError as error:
            self.close()
            raise ExpectedToolFailure(
                ToolErrorCode.PERMISSION_DENIED,
                "Workspace path could not be opened safely.",
                metadata={"path": self.safe.requested},
            ) from error
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    @property
    def _parent(self) -> _DirectoryPin:
        if not self._pins:
            raise RuntimeError("Workspace file transaction is not open.")
        return self._pins[-1]

    @property
    def _name(self) -> str:
        if not self.safe.relative.parts or self.safe.relative == Path("."):
            raise ExpectedToolFailure(
                ToolErrorCode.INVALID_ARGUMENTS,
                "Path is not a file.",
                metadata={"path": self.safe.requested},
            )
        return self.safe.relative.name

    def close(self) -> None:
        for pin in reversed(self._pins):
            pin.close()
        self._pins.clear()

    def read_regular(
        self,
        *,
        max_bytes: int | None,
        allow_missing: bool = False,
    ) -> BoundRegularFile | None:
        try:
            self._parent.verify_reachable()
            if not self.safe.target_existed:
                _assert_child_identity(self._parent, self._name, None)
                if allow_missing:
                    return None
                raise ExpectedToolFailure(
                    ToolErrorCode.NOT_FOUND,
                    "Path was not found.",
                    metadata={"path": self.safe.requested},
                )
            expected_identity = self.safe.target_identity
            if expected_identity is None:
                raise RuntimeError(
                    "An existing workspace target must have an identity."
                )
            opened = _read_regular_child(
                self._parent,
                self._name,
                max_bytes=max_bytes,
                expected_identity=expected_identity,
            )
        except FileNotFoundError as error:
            raise ExpectedToolFailure(
                ToolErrorCode.NOT_FOUND,
                "Path was not found.",
                metadata={"path": self.safe.requested},
            ) from error
        except MutationTargetChanged:
            raise
        except ExpectedToolFailure:
            raise
        except UnsafeWorkspacePath as error:
            raise ExpectedToolFailure(
                ToolErrorCode.PERMISSION_DENIED,
                "File could not be opened safely.",
                metadata={"path": self.safe.requested},
            ) from error
        except OSError as error:
            raise ExpectedToolFailure(
                ToolErrorCode.PERMISSION_DENIED,
                "File could not be opened safely.",
                metadata={"path": self.safe.requested},
            ) from error

        return BoundRegularFile(
            snapshot=NodeSnapshot(
                FileNodeType.FILE,
                opened.data,
                stat.S_IMODE(opened.status.st_mode),
            ),
            identity=_identity(opened.status),
        )

    def replace_mutation(
        self,
        *,
        before: BoundRegularFile | None,
        content: bytes,
        mode: int | None,
    ) -> BoundFileMutation:
        expected_identity = before.identity if before is not None else None

        def mutate() -> None:
            self._parent.verify_reachable()
            _assert_child_identity(self._parent, self._name, expected_identity)
            _atomic_replace_child(self._parent, self._name, content, mode)

        def capture_after() -> NodeSnapshot | None:
            current = self._capture_current_regular(max_bytes=None)
            return current.snapshot if current is not None else None

        return BoundFileMutation(
            relative_path=self.safe.relative.as_posix(),
            before=before.snapshot if before is not None else None,
            mutate=mutate,
            capture_after=capture_after,
        )

    def _capture_current_regular(
        self,
        *,
        max_bytes: int | None,
    ) -> BoundRegularFile | None:
        self._parent.verify_reachable()
        try:
            current = _lstat_child(self._parent, self._name)
        except FileNotFoundError:
            return None
        current_identity = _identity(current)
        try:
            opened = _read_regular_child(
                self._parent,
                self._name,
                max_bytes=max_bytes,
                expected_identity=current_identity,
            )
        except MutationTargetChanged:
            raise
        except UnsafeWorkspacePath as error:
            raise ExpectedToolFailure(
                ToolErrorCode.PERMISSION_DENIED,
                "File could not be opened safely.",
                metadata={"path": self.safe.requested},
            ) from error
        except OSError as error:
            raise ExpectedToolFailure(
                ToolErrorCode.PERMISSION_DENIED,
                "File could not be opened safely.",
                metadata={"path": self.safe.requested},
            ) from error
        return BoundRegularFile(
            snapshot=NodeSnapshot(
                FileNodeType.FILE,
                opened.data,
                stat.S_IMODE(opened.status.st_mode),
            ),
            identity=_identity(opened.status),
        )


class WorkspaceDeleteTransaction(WorkspaceFileTransaction):
    def __init__(self, safe: SafeWorkspacePath) -> None:
        super().__init__(safe)
        self._tree_pins: list[_DirectoryPin] = []
        self._inventory_nodes = 0
        self._inventory_bytes = 0
        self._max_inventory_nodes = 0
        self._max_inventory_bytes = 0

    def close(self) -> None:
        for pin in reversed(self._tree_pins):
            pin.close()
        self._tree_pins.clear()
        super().close()

    def inventory(
        self,
        *,
        validate_relative: Callable[[Path], None],
        max_nodes: int,
        max_bytes: int,
    ) -> list[SecureDeleteNode]:
        self._inventory_nodes = 0
        self._inventory_bytes = 0
        self._max_inventory_nodes = max_nodes
        self._max_inventory_bytes = max_bytes
        nodes: list[SecureDeleteNode] = []
        if not self.safe.target_existed or self.safe.target_identity is None:
            raise MutationTargetChanged(
                "The deletion target existence changed after path validation."
            )
        try:
            self._inventory_child(
                self._parent,
                self._name,
                self.safe.relative,
                validate_relative,
                nodes,
                root=True,
            )
        except UnsafeWorkspacePath as error:
            raise ExpectedToolFailure(
                ToolErrorCode.PERMISSION_DENIED,
                "Workspace path could not be inventoried safely.",
                metadata={"path": self.safe.requested},
            ) from error
        return nodes

    def _inventory_child(
        self,
        parent: _DirectoryPin,
        name: str,
        relative: Path,
        validate_relative: Callable[[Path], None],
        nodes: list[SecureDeleteNode],
        *,
        root: bool,
    ) -> None:
        validate_relative(relative)
        parent.verify_reachable()
        try:
            info = _lstat_child(parent, name)
        except FileNotFoundError as error:
            if root and self.safe.target_existed:
                raise MutationTargetChanged(
                    "The deletion target disappeared after path validation."
                ) from error
            raise ExpectedToolFailure(
                ToolErrorCode.NOT_FOUND,
                "Path was not found.",
                metadata={"path": relative.as_posix()},
            ) from error
        node_identity = _identity(info)
        if root and node_identity != self.safe.target_identity:
            raise MutationTargetChanged(
                "The deletion target generation changed after path validation."
            )
        if is_link_or_reparse(info) and (
            not root
            or bool(
                int(getattr(info, "st_file_attributes", 0)) & _FILE_ATTRIBUTE_DIRECTORY
            )
        ):
            raise ExpectedToolFailure(
                ToolErrorCode.PERMISSION_DENIED,
                "Links and reparse points cannot be deleted recursively.",
                metadata={"path": relative.as_posix()},
            )

        if is_link_or_reparse(info):
            link_target = _readlink_child(parent, name)
            self._reserve_inventory(len(os.fsencode(link_target)))
            snapshot = NodeSnapshot(
                FileNodeType.SYMLINK,
                os.fsencode(link_target),
                stat.S_IMODE(info.st_mode),
            )
            nodes.append(
                self._delete_node(
                    parent=parent,
                    name=name,
                    relative=relative,
                    identity=node_identity,
                    snapshot=snapshot,
                    own_pin=None,
                )
            )
            return
        if stat.S_ISREG(info.st_mode):
            self._reserve_inventory(int(info.st_size))
            opened = _read_regular_child(
                parent,
                name,
                max_bytes=int(info.st_size),
                expected_identity=node_identity,
            )
            snapshot = NodeSnapshot(
                FileNodeType.FILE,
                opened.data,
                stat.S_IMODE(opened.status.st_mode),
            )
            nodes.append(
                self._delete_node(
                    parent=parent,
                    name=name,
                    relative=relative,
                    identity=node_identity,
                    snapshot=snapshot,
                    own_pin=None,
                )
            )
            return
        if not stat.S_ISDIR(info.st_mode):
            raise ExpectedToolFailure(
                ToolErrorCode.INVALID_ARGUMENTS,
                "Unsupported filesystem node.",
                metadata={"path": relative.as_posix()},
            )

        self._reserve_inventory(0)
        directory = _open_directory(
            parent.path / name,
            parent=parent,
            name=name,
            expected_identity=node_identity,
        )
        self._tree_pins.append(directory)
        with os.scandir(
            directory.path if os.name == "nt" else directory.descriptor
        ) as entries:
            names = sorted(
                (entry.name for entry in entries),
                key=lambda item: (item.casefold(), item),
            )
        for child_name in names:
            self._inventory_child(
                directory,
                child_name,
                relative / child_name,
                validate_relative,
                nodes,
                root=False,
            )
        snapshot = NodeSnapshot(
            FileNodeType.DIRECTORY,
            None,
            stat.S_IMODE(info.st_mode),
        )
        nodes.append(
            self._delete_node(
                parent=parent,
                name=name,
                relative=relative,
                identity=node_identity,
                snapshot=snapshot,
                own_pin=directory,
            )
        )

    def _reserve_inventory(
        self,
        content_bytes: int,
    ) -> None:
        if self._inventory_nodes + 1 > self._max_inventory_nodes:
            raise ExpectedToolFailure(
                ToolErrorCode.EXECUTION_FAILED,
                "ChangeSet file limit exceeded.",
            )
        if self._inventory_bytes + content_bytes > self._max_inventory_bytes:
            raise ExpectedToolFailure(
                ToolErrorCode.EXECUTION_FAILED,
                "ChangeSet byte limit exceeded.",
            )
        self._inventory_nodes += 1
        self._inventory_bytes += content_bytes

    @staticmethod
    def _delete_node(
        *,
        parent: _DirectoryPin,
        name: str,
        relative: Path,
        identity: FileIdentity,
        snapshot: NodeSnapshot,
        own_pin: _DirectoryPin | None,
    ) -> SecureDeleteNode:
        def mutate() -> None:
            parent.verify_reachable()
            _assert_child_identity(
                parent,
                name,
                identity,
                allow_reparse=snapshot.node_type is FileNodeType.SYMLINK,
            )
            if own_pin is not None and os.name == "nt":
                own_pin.close()
            _remove_child(
                parent,
                name,
                directory=snapshot.node_type is FileNodeType.DIRECTORY,
            )

        def capture_after() -> NodeSnapshot | None:
            try:
                info = _lstat_child(parent, name)
            except FileNotFoundError:
                return None
            if is_link_or_reparse(info):
                return NodeSnapshot(FileNodeType.SYMLINK, b"", None)
            if stat.S_ISDIR(info.st_mode):
                return NodeSnapshot(
                    FileNodeType.DIRECTORY,
                    None,
                    stat.S_IMODE(info.st_mode),
                )
            descriptor, opened = _open_regular_file(parent, name)
            try:
                data = _read_descriptor(descriptor, max_bytes=None)
            finally:
                os.close(descriptor)
            return NodeSnapshot(
                FileNodeType.FILE,
                data,
                stat.S_IMODE(opened.st_mode),
            )

        mutation = BoundFileMutation(
            relative_path=relative.as_posix(),
            before=snapshot,
            mutate=mutate,
            capture_after=capture_after,
        )
        return SecureDeleteNode(
            relative=relative,
            node_type=snapshot.node_type,
            content_bytes=len(snapshot.content or b""),
            mutation=mutation,
        )
