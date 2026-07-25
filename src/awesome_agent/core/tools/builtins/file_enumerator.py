from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event

from awesome_agent.core.filesystem import (
    DirectoryPin,
    FileIdentity,
    MutationTargetChanged,
    UnsafeWorkspacePath,
    WorkspaceFileTooLarge,
    iter_directory_entries,
    open_directory,
    open_regular_file,
    read_regular_child,
)
from awesome_agent.core.tools.context import ToolExecutionContext
from awesome_agent.core.tools.contracts import ToolErrorCode
from awesome_agent.core.tools.errors import ExpectedToolFailure
from awesome_agent.core.tools.filesystem import WorkspaceDirectoryTransaction
from awesome_agent.core.tools.policy import (
    SafeWorkspacePath,
    is_sensitive_workspace_path,
    resolve_workspace_path,
)

DEFAULT_PRUNED_DIRECTORIES = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "venv",
    }
)


class ScanCancelled(RuntimeError):
    pass


class ScanCancellation:
    def __init__(self) -> None:
        self._cancelled = Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def raise_if_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise ScanCancelled


@dataclass(frozen=True, slots=True)
class EnumeratedFile:
    relative: str
    resolved: Path
    _parent: DirectoryPin | None = field(default=None, repr=False, compare=False)
    _name: str | None = field(default=None, repr=False, compare=False)
    _identity: FileIdentity | None = field(default=None, repr=False, compare=False)

    def read_bytes(self, *, max_bytes: int) -> bytes:
        if self._parent is None or self._name is None or self._identity is None:
            raise RuntimeError("Enumerated file is not bound to a workspace directory.")
        try:
            return read_regular_child(
                self._parent,
                self._name,
                max_bytes=max_bytes,
                expected_identity=self._identity,
            ).data
        except WorkspaceFileTooLarge:
            raise
        except MutationTargetChanged as error:
            raise ExpectedToolFailure(
                ToolErrorCode.CONFLICT,
                "Workspace path changed while search content was being read.",
                metadata={"path": self.relative},
            ) from error
        except (FileNotFoundError, UnsafeWorkspacePath, OSError) as error:
            raise ExpectedToolFailure(
                ToolErrorCode.PERMISSION_DENIED,
                "Workspace file could not be opened safely.",
                metadata={"path": self.relative},
            ) from error


def enumerate_workspace_files(
    root: SafeWorkspacePath,
    context: ToolExecutionContext,
    *,
    cancellation: ScanCancellation,
    prune_defaults: bool,
) -> Iterator[EnumeratedFile]:
    try:
        with WorkspaceDirectoryTransaction(root) as transaction:
            yield from _enumerate_directory(
                transaction.directory,
                root.relative,
                context,
                cancellation=cancellation,
                prune_defaults=prune_defaults,
            )
    except ExpectedToolFailure:
        raise
    except MutationTargetChanged as error:
        raise ExpectedToolFailure(
            ToolErrorCode.CONFLICT,
            "Workspace path changed while files were being enumerated.",
            metadata={"path": root.requested},
        ) from error
    except (FileNotFoundError, UnsafeWorkspacePath, OSError) as error:
        raise ExpectedToolFailure(
            ToolErrorCode.PERMISSION_DENIED,
            "Workspace directory could not be enumerated safely.",
            metadata={"path": root.requested},
        ) from error


def _enumerate_directory(
    directory: DirectoryPin,
    relative_directory: Path,
    context: ToolExecutionContext,
    *,
    cancellation: ScanCancellation,
    prune_defaults: bool,
) -> Iterator[EnumeratedFile]:
    cancellation.raise_if_cancelled()
    directories = []
    for entry in iter_directory_entries(
        directory,
        check_cancelled=cancellation.raise_if_cancelled,
    ):
        cancellation.raise_if_cancelled()
        if entry.kind == "directory":
            directories.append(entry)
            continue
        relative_path = relative_directory / entry.name
        if is_sensitive_workspace_path(relative_path):
            continue
        relative = relative_path.as_posix()
        try:
            safe = resolve_workspace_path(
                context.workspace,
                relative,
                must_exist=True,
                expected_kind="file",
            )
        except ExpectedToolFailure:
            continue
        descriptor, _opened = open_regular_file(
            directory,
            entry.name,
            expected_identity=entry.identity,
        )
        try:
            directory.verify_reachable()
        finally:
            os.close(descriptor)
        yield EnumeratedFile(
            relative=relative,
            resolved=safe.resolved,
            _parent=directory,
            _name=entry.name,
            _identity=entry.identity,
        )
        directory.verify_reachable()

    for entry in directories:
        cancellation.raise_if_cancelled()
        if prune_defaults and entry.name.casefold() in DEFAULT_PRUNED_DIRECTORIES:
            continue
        relative_path = relative_directory / entry.name
        if is_sensitive_workspace_path(relative_path):
            continue
        relative = relative_path.as_posix()
        try:
            resolve_workspace_path(
                context.workspace,
                relative,
                must_exist=True,
                expected_kind="directory",
            )
        except ExpectedToolFailure:
            continue
        child = open_directory(
            directory.path / entry.name,
            parent=directory,
            name=entry.name,
            expected_identity=entry.identity,
        )
        try:
            yield from _enumerate_directory(
                child,
                relative_path,
                context,
                cancellation=cancellation,
                prune_defaults=prune_defaults,
            )
        finally:
            child.close()
        directory.verify_reachable()
