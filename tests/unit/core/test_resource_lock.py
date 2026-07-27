from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from awesome_agent.core import resource_lock
from awesome_agent.core.filesystem import open_directory
from awesome_agent.core.resource_lock import (
    ResourceLockTimeout,
    ResourceLockUnavailable,
    exclusive_resource_lock,
)


@pytest.mark.parametrize(
    ("resource_name", "lock_name"),
    [
        ("config.yaml", ".config.yaml.lock"),
        (".env", ".env.lock"),
        ("USER.md", ".USER.md.lock"),
    ],
)
def test_resource_lock_uses_one_hidden_sidecar_name(
    tmp_path: Path,
    resource_name: str,
    lock_name: str,
) -> None:
    assert resource_lock._lock_path(tmp_path / resource_name) == tmp_path / lock_name


def test_resource_lock_is_reentrant_and_released_after_failure(
    tmp_path: Path,
) -> None:
    resource = tmp_path / "config.yaml"

    with (
        pytest.raises(RuntimeError, match="transform failed"),
        exclusive_resource_lock(resource),
        exclusive_resource_lock(resource),
    ):
        raise RuntimeError("transform failed")

    with exclusive_resource_lock(resource, timeout_seconds=0.1):
        pass


def test_opposite_resource_order_times_out_instead_of_deadlocking(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first.yaml"
    second = tmp_path / "second.yaml"
    barrier = threading.Barrier(2)

    def acquire_pair(outer: Path, inner: Path) -> str:
        with exclusive_resource_lock(outer):
            barrier.wait(timeout=1.0)
            try:
                with exclusive_resource_lock(inner, timeout_seconds=0.1):
                    pass
            except ResourceLockTimeout:
                outcome = "timed_out"
            else:
                outcome = "acquired"
            barrier.wait(timeout=1.0)
            assert outcome == "timed_out"
        return "released"

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(acquire_pair, first, second),
            executor.submit(acquire_pair, second, first),
        )
        assert [future.result(timeout=2.0) for future in futures] == [
            "released",
            "released",
        ]


def test_unrelated_resources_do_not_share_a_process_lock(tmp_path: Path) -> None:
    first = tmp_path / "workspace-a" / "MEMORY.md"
    second = tmp_path / "workspace-b" / "MEMORY.md"
    entered = threading.Event()
    release = threading.Event()

    def hold_first() -> None:
        with exclusive_resource_lock(first):
            entered.set()
            assert release.wait(timeout=1.0)

    with ThreadPoolExecutor(max_workers=1) as executor:
        holding = executor.submit(hold_first)
        assert entered.wait(timeout=1.0)
        try:
            with exclusive_resource_lock(second, timeout_seconds=0.1):
                pass
        finally:
            release.set()
        holding.result(timeout=1.0)


def test_resource_lock_rejects_hardlink_without_touching_external_file(
    tmp_path: Path,
) -> None:
    external = tmp_path / "external-sentinel"
    external.write_bytes(b"")
    resource = tmp_path / "config.yaml"
    lock_path = resource_lock._lock_path(resource)
    os.link(external, lock_path)

    with pytest.raises(ResourceLockUnavailable), exclusive_resource_lock(resource):
        pass

    assert external.read_bytes() == b""
    assert lock_path.stat().st_nlink == 2


def test_pinned_resource_lock_cannot_be_redirected_by_directory_replacement(
    tmp_path: Path,
) -> None:
    directory_path = tmp_path / "logs"
    directory_path.mkdir()
    detached = tmp_path / "detached-logs"
    outside = tmp_path / "outside"
    outside.mkdir()
    directory = open_directory(directory_path)
    replaced = False
    try:
        try:
            directory_path.rename(detached)
        except OSError:
            assert os.name == "nt"
        else:
            directory_path.symlink_to(outside, target_is_directory=True)
            replaced = True
        with exclusive_resource_lock(
            directory_path / "application.jsonl",
            directory=directory,
        ):
            pass
    finally:
        directory.close()

    lock_parent = detached if replaced else directory_path
    assert (lock_parent / ".application.jsonl.lock").is_file()
    assert not (outside / ".application.jsonl.lock").exists()


@pytest.mark.parametrize("outer_pinned", [False, True])
def test_resource_lock_rejects_mixed_nested_lock_forms(
    tmp_path: Path,
    outer_pinned: bool,
) -> None:
    resource = tmp_path / "application.jsonl"
    directory = open_directory(tmp_path)
    try:
        outer_directory = directory if outer_pinned else None
        inner_directory = None if outer_pinned else directory
        with (
            exclusive_resource_lock(resource, directory=outer_directory),
            pytest.raises(ResourceLockUnavailable, match="cannot be nested"),
            exclusive_resource_lock(resource, directory=inner_directory),
        ):
            pass

        with exclusive_resource_lock(resource, directory=directory):
            pass
    finally:
        directory.close()
