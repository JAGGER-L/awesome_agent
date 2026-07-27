from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType

import pytest

import awesome_agent.core.filesystem as filesystem
from awesome_agent.core.filesystem import (
    DirectoryEntryLimitExceeded,
    MountIdentity,
    UnsafeWorkspacePath,
    bounded_directory_names,
    open_directory,
)
from awesome_agent.core.safe_files import PinnedPlainDirectory, UnsafePathError


class _Entry:
    def __init__(self, name: str, accesses: list[str]) -> None:
        self._name = name
        self._accesses = accesses

    @property
    def name(self) -> str:
        self._accesses.append(self._name)
        return self._name


class _Scandir:
    def __init__(self, entries: tuple[_Entry, ...]) -> None:
        self._entries = entries

    def __enter__(self) -> Iterator[_Entry]:
        return iter(self._entries)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback


def test_bounded_directory_names_rejects_before_materializing_overflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    pin = open_directory(root)
    accesses: list[str] = []
    entries = tuple(_Entry(name, accesses) for name in ("one", "two", "three"))
    monkeypatch.setattr(os, "scandir", lambda _path: _Scandir(entries))
    try:
        with pytest.raises(DirectoryEntryLimitExceeded):
            bounded_directory_names(pin, max_entries=2)
    finally:
        pin.close()

    assert accesses == ["one", "two"]


def test_open_directory_rejects_injected_descendant_mount_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    child = root / "child"
    child.mkdir(parents=True)
    root_mount = MountIdentity("device", 1)
    foreign_mount = MountIdentity("device", 2)

    def path_mount(
        _path: Path,
        *,
        parent: filesystem.DirectoryPin | None,
        name: str | None,
        status: os.stat_result,
    ) -> MountIdentity:
        del parent, status
        return foreign_mount if name == "child" else root_mount

    monkeypatch.setattr(filesystem, "_path_mount_identity", path_mount)
    monkeypatch.setattr(
        filesystem,
        "_descriptor_mount_identity",
        lambda _descriptor, _status: root_mount,
    )

    pin = open_directory(root, establish_mount_boundary=True)
    try:
        with pytest.raises(UnsafeWorkspacePath):
            open_directory(child, parent=pin, name="child")
    finally:
        pin.close()


def test_mount_boundary_support_failure_is_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()

    def unsupported(*_args: object, **_kwargs: object) -> MountIdentity:
        raise UnsafeWorkspacePath("mount identity unavailable")

    monkeypatch.setattr(filesystem, "_path_mount_identity", unsupported)

    with pytest.raises(UnsafeWorkspacePath, match="mount identity unavailable"):
        open_directory(root, establish_mount_boundary=True)


@pytest.mark.parametrize("operation", ["verify", "bounded_names", "child_status"])
def test_pinned_directory_translates_late_mount_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "child").write_text("child", encoding="utf-8")
    initial_mount = MountIdentity("device", 1)
    changed_mount = MountIdentity("device", 2)
    path_calls = 0

    def path_mount(
        path: Path,
        *,
        parent: filesystem.DirectoryPin | None,
        name: str | None,
        status: os.stat_result,
    ) -> MountIdentity:
        nonlocal path_calls
        del parent, name, status
        if path == root:
            path_calls += 1
            return initial_mount if path_calls == 1 else changed_mount
        return initial_mount

    monkeypatch.setattr(filesystem, "_path_mount_identity", path_mount)
    monkeypatch.setattr(
        filesystem,
        "_descriptor_mount_identity",
        lambda _descriptor, _status: initial_mount,
    )

    with (
        PinnedPlainDirectory(root, root, mount_boundary=root) as pinned,
        pytest.raises(UnsafePathError),
    ):
        if operation == "verify":
            pinned.verify()
        elif operation == "bounded_names":
            pinned.bounded_names(max_entries=2)
        else:
            pinned.child_status("child")
