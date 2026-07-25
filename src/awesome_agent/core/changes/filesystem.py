from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from awesome_agent.core.changes.errors import ChangeLifecycleError
from awesome_agent.core.changes.models import FileNodeType
from awesome_agent.core.filesystem import (
    DirectoryPin,
    FileIdentity,
    MutationTargetChanged,
    UnsafeWorkspacePath,
    WorkspaceFileTooLarge,
    assert_child_identity,
    atomic_replace_child,
    identity,
    is_link_or_reparse,
    lstat_child,
    make_directory_child,
    make_symlink_child,
    open_directory,
    open_regular_file,
    read_descriptor,
    readlink_child,
    remove_child,
)
from awesome_agent.core.workspace import WorkspaceIdentity
from awesome_agent.core.workspace.path_syntax import (
    WorkspacePathSyntaxError,
    validate_workspace_relative_path_syntax,
)

MAX_CHANGESET_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class NodeSnapshot:
    node_type: FileNodeType
    content: bytes | None
    mode: int | None


@dataclass(frozen=True, slots=True)
class BoundFileMutation:
    relative_path: str
    before: NodeSnapshot | None
    mutate: Callable[[], None]
    capture_after: Callable[[], NodeSnapshot | None]


@dataclass(frozen=True, slots=True)
class BoundWorkspaceNode:
    relative: Path
    snapshot: NodeSnapshot | None
    identity: FileIdentity | None
    missing_ancestor: tuple[str, ...] | None


def normalize_workspace_relative(relative_path: str) -> Path:
    try:
        validate_workspace_relative_path_syntax(relative_path)
    except WorkspacePathSyntaxError as error:
        raise ChangeLifecycleError(
            "Mutation path escapes or aliases the workspace."
        ) from error
    candidate = Path(relative_path)
    if not candidate.parts or candidate in {Path("."), Path()}:
        raise ChangeLifecycleError("Mutation path escapes or aliases the workspace.")
    return candidate


def snapshot_digest(snapshot: NodeSnapshot | None) -> str | None:
    if snapshot is None:
        return None
    return hashlib.sha256(snapshot.content or b"").hexdigest()


def snapshot_matches_record(
    snapshot: NodeSnapshot | None,
    *,
    digest: str | None,
    mode: int | None,
    node_type: FileNodeType,
) -> bool:
    if digest is None:
        return snapshot is None
    return (
        snapshot is not None
        and snapshot.node_type is node_type
        and snapshot_digest(snapshot) == digest
        and (mode is None or snapshot.mode == mode)
    )


def snapshots_match(
    snapshot: NodeSnapshot | None,
    expected: NodeSnapshot | None,
) -> bool:
    if expected is None:
        return snapshot is None
    return (
        snapshot is not None
        and snapshot.node_type is expected.node_type
        and snapshot_digest(snapshot) == snapshot_digest(expected)
        and snapshot.mode == expected.mode
    )


class WorkspaceTreeTransaction:
    """Pins one workspace tree for an inspect-and-restore transaction."""

    def __init__(self, workspace: WorkspaceIdentity) -> None:
        self._workspace = workspace.canonical_path
        self._workspace_root_identity = workspace.root_identity
        self._pins: dict[tuple[str, ...], DirectoryPin] = {}
        self._created: set[tuple[str, ...]] = set()

    def __enter__(self) -> Self:
        self._pins[()] = open_directory(
            self._workspace,
            expected_identity=self._workspace_root_identity,
        )
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        for parts in sorted(self._pins, key=len, reverse=True):
            self._pins[parts].close()
        self._pins.clear()
        self._created.clear()

    def bind(self, relative_path: str | Path) -> BoundWorkspaceNode:
        relative = normalize_workspace_relative(str(relative_path))
        parent, missing_ancestor = self._open_parent(relative.parent)
        if parent is None:
            return BoundWorkspaceNode(relative, None, None, missing_ancestor)
        snapshot, node_identity = self._capture_child(parent, relative.name)
        return BoundWorkspaceNode(relative, snapshot, node_identity, None)

    def capture(self, target: BoundWorkspaceNode) -> NodeSnapshot | None:
        parent, _ = self._open_parent(target.relative.parent)
        if parent is None:
            if target.identity is not None:
                raise MutationTargetChanged(
                    "A workspace parent directory disappeared after binding."
                )
            return None
        self._verify_missing_ancestor(target)
        assert_child_identity(
            parent,
            target.relative.name,
            target.identity,
            allow_reparse=(
                target.snapshot is not None
                and target.snapshot.node_type is FileNodeType.SYMLINK
            ),
        )
        snapshot, _ = self._capture_child(parent, target.relative.name)
        return snapshot

    def restore(
        self,
        target: BoundWorkspaceNode,
        desired: NodeSnapshot | None,
    ) -> NodeSnapshot | None:
        parent, _ = self._open_parent(target.relative.parent)
        if parent is None:
            if desired is None and target.snapshot is None:
                return None
            raise MutationTargetChanged(
                "A workspace parent directory changed before restoration."
            )
        self._verify_missing_ancestor(target)
        parent.verify_reachable()
        assert_child_identity(
            parent,
            target.relative.name,
            target.identity,
            allow_reparse=(
                target.snapshot is not None
                and target.snapshot.node_type is FileNodeType.SYMLINK
            ),
        )
        current = target.snapshot
        if current is not None and (
            desired is None
            or current.node_type is not desired.node_type
            or desired.node_type is FileNodeType.SYMLINK
        ):
            if current.node_type is FileNodeType.DIRECTORY:
                self._release_subtree(target.relative.parts)
            remove_child(
                parent,
                target.relative.name,
                directory=current.node_type is FileNodeType.DIRECTORY,
            )
            current = None

        if desired is None:
            pass
        elif desired.node_type is FileNodeType.FILE:
            atomic_replace_child(
                parent,
                target.relative.name,
                desired.content or b"",
                desired.mode,
            )
        elif desired.node_type is FileNodeType.DIRECTORY:
            if current is None:
                make_directory_child(parent, target.relative.name, desired.mode)
                self._created.add(target.relative.parts)
            directory = open_directory(
                parent.path / target.relative.name,
                parent=parent,
                name=target.relative.name,
            )
            existing = self._pins.pop(target.relative.parts, None)
            if existing is not None:
                existing.close()
            self._pins[target.relative.parts] = directory
            if desired.mode is not None:
                if os.name == "nt":
                    os.chmod(directory.path, desired.mode)
                else:
                    fchmod = getattr(os, "fchmod", None)
                    if fchmod is None:
                        raise UnsafeWorkspacePath(
                            "Descriptor chmod is unavailable on this platform."
                        )
                    fchmod(directory.descriptor, desired.mode)
        else:
            make_symlink_child(
                parent,
                target.relative.name,
                os.fsdecode(desired.content or b""),
            )

        parent.verify_reachable()
        snapshot, _ = self._capture_child(parent, target.relative.name)
        return snapshot

    def _open_parent(
        self,
        relative: Path,
    ) -> tuple[DirectoryPin | None, tuple[str, ...] | None]:
        current = self._root
        parts: tuple[str, ...] = ()
        for part in relative.parts:
            if part == ".":
                continue
            next_parts = (*parts, part)
            existing = self._pins.get(next_parts)
            if existing is not None:
                existing.verify_reachable()
                current = existing
                parts = next_parts
                continue
            current.verify_reachable()
            try:
                info = lstat_child(current, part)
            except FileNotFoundError:
                return None, next_parts
            if is_link_or_reparse(info) or not stat.S_ISDIR(info.st_mode):
                raise UnsafeWorkspacePath(
                    "Workspace parent links and non-directories are not allowed."
                )
            opened = open_directory(current.path / part, parent=current, name=part)
            self._pins[next_parts] = opened
            current = opened
            parts = next_parts
        return current, None

    def _verify_missing_ancestor(self, target: BoundWorkspaceNode) -> None:
        missing = target.missing_ancestor
        if missing is not None and missing not in self._created:
            raise MutationTargetChanged(
                "A missing workspace parent was replaced before restoration."
            )

    @property
    def _root(self) -> DirectoryPin:
        try:
            return self._pins[()]
        except KeyError:
            raise RuntimeError("Workspace tree transaction is not open.") from None

    @staticmethod
    def _capture_child(
        parent: DirectoryPin,
        name: str,
    ) -> tuple[NodeSnapshot | None, FileIdentity | None]:
        parent.verify_reachable()
        try:
            info = lstat_child(parent, name)
        except FileNotFoundError:
            return None, None
        node_identity = identity(info)
        mode = stat.S_IMODE(info.st_mode)
        if is_link_or_reparse(info):
            target = readlink_child(parent, name)
            return (
                NodeSnapshot(FileNodeType.SYMLINK, os.fsencode(target), mode),
                node_identity,
            )
        if stat.S_ISDIR(info.st_mode):
            return NodeSnapshot(FileNodeType.DIRECTORY, None, mode), node_identity
        if not stat.S_ISREG(info.st_mode):
            raise UnsafeWorkspacePath("Unsupported filesystem node.")
        descriptor, opened = open_regular_file(parent, name)
        try:
            if int(opened.st_size) > MAX_CHANGESET_BYTES:
                raise UnsafeWorkspacePath(
                    "A recovery file exceeds the ChangeSet byte limit."
                )
            try:
                content = read_descriptor(
                    descriptor,
                    max_bytes=MAX_CHANGESET_BYTES,
                )
            except WorkspaceFileTooLarge as error:
                raise UnsafeWorkspacePath(
                    "A recovery file exceeds the ChangeSet byte limit."
                ) from error
        finally:
            os.close(descriptor)
        return (
            NodeSnapshot(
                FileNodeType.FILE,
                content,
                stat.S_IMODE(opened.st_mode),
            ),
            identity(opened),
        )

    def _release_subtree(self, prefix: tuple[str, ...]) -> None:
        matches = [parts for parts in self._pins if parts[: len(prefix)] == prefix]
        for parts in sorted(matches, key=len, reverse=True):
            self._pins.pop(parts).close()
        self._created = {
            parts for parts in self._created if parts[: len(prefix)] != prefix
        }
