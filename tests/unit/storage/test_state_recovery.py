from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from awesome_agent.paths import AwesomePaths
from awesome_agent.storage.compatibility import (
    StateCompatibility,
    inspect_application_state,
)
from awesome_agent.storage.state_lease import StateLease, StateLeaseMode
from awesome_agent.storage.state_recovery import StateResetError, reset_local_state


def _inventory(root: Path) -> tuple[tuple[str, bytes], ...]:
    if not root.exists():
        return ()
    return tuple(
        (item.relative_to(root).as_posix(), item.read_bytes())
        for item in sorted(root.rglob("*"))
        if item.is_file() and item.name != ".state.lock"
    )


def _build_home(tmp_path: Path) -> AwesomePaths:
    paths = AwesomePaths.from_home(tmp_path / "home")
    paths.state_dir.mkdir(parents=True)
    with closing(sqlite3.connect(paths.application_db)) as connection:
        connection.execute("CREATE TABLE legacy_state (value TEXT NOT NULL)")
        connection.execute("INSERT INTO legacy_state VALUES ('old')")
        connection.execute("PRAGMA user_version = 6")
        connection.commit()
    paths.application_db.with_name("application.db-wal").write_bytes(b"app wal")
    paths.application_db.with_name("application.db-shm").write_bytes(b"app shm")
    paths.checkpoint_db.write_bytes(b"checkpoint")
    paths.checkpoint_db.with_name("checkpoints.db-wal").write_bytes(b"checkpoint wal")
    paths.change_journal_dir.mkdir()
    (paths.change_journal_dir / "blob").write_bytes(b"change")

    paths.config_file.write_bytes(b"model: deepseek\n")
    paths.env_file.write_bytes(b"DEEPSEEK_API_KEY=secret\n")
    paths.skills_dir.mkdir()
    (paths.skills_dir / "custom.md").write_bytes(b"skill")
    paths.memory_dir.mkdir()
    (paths.memory_dir / "MEMORY.md").write_bytes(b"memory")
    paths.workspaces_dir.mkdir()
    (paths.workspaces_dir / "workspace-memory.md").write_bytes(b"workspace")
    paths.ui_file.write_bytes(b'{"theme":"aurora"}')
    return paths


def test_reset_replaces_only_owned_state_and_creates_schema_seven(
    tmp_path: Path,
) -> None:
    paths = _build_home(tmp_path)
    lease = StateLease.acquire(paths.home, StateLeaseMode.EXCLUSIVE)
    preserved = {
        path: path.read_bytes()
        for path in (
            paths.config_file,
            paths.env_file,
            paths.skills_dir / "custom.md",
            paths.memory_dir / "MEMORY.md",
            paths.workspaces_dir / "workspace-memory.md",
            paths.ui_file,
        )
    }
    try:
        reset_local_state(lease)
    finally:
        lease.close()

    preflight = inspect_application_state(paths.application_db)
    assert preflight.compatibility is StateCompatibility.CURRENT
    assert preflight.found_schema == 7
    assert not paths.checkpoint_db.exists()
    assert not paths.change_journal_dir.exists()
    assert not list(paths.home.glob(".state-reset-*"))
    for path, content in preserved.items():
        assert path.read_bytes() == content


def test_reset_changes_only_the_exclusive_lease_home(tmp_path: Path) -> None:
    paths = _build_home(tmp_path / "selected")
    other = _build_home(tmp_path / "other")
    other_before = _inventory(other.home)
    lease = StateLease.acquire(paths.home, StateLeaseMode.EXCLUSIVE)
    try:
        reset_local_state(lease)
    finally:
        lease.close()

    preflight = inspect_application_state(paths.application_db)
    assert preflight.compatibility is StateCompatibility.CURRENT
    assert preflight.found_schema == 7
    assert _inventory(other.home) == other_before


def test_reset_requires_matching_exclusive_lease_without_mutation(
    tmp_path: Path,
) -> None:
    paths = _build_home(tmp_path)
    shared = StateLease.acquire(paths.home, StateLeaseMode.SHARED)
    before = _inventory(paths.home)
    try:
        with pytest.raises(StateResetError) as raised:
            reset_local_state(shared)
    finally:
        shared.close()

    assert raised.value.code == "exclusive_lease_required"
    assert _inventory(paths.home) == before


def test_reset_fails_before_mutation_when_database_handle_is_open(
    tmp_path: Path,
) -> None:
    paths = _build_home(tmp_path)
    lease = StateLease.acquire(paths.home, StateLeaseMode.EXCLUSIVE)
    before = _inventory(paths.home)
    connection = sqlite3.connect(paths.application_db)
    try:
        with pytest.raises(StateResetError) as raised:
            reset_local_state(lease)
    finally:
        connection.close()
        lease.close()

    assert raised.value.code == "state_replacement_failed"
    assert _inventory(paths.home) == before


def test_reset_restores_original_state_when_fresh_initialization_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _build_home(tmp_path)
    lease = StateLease.acquire(paths.home, StateLeaseMode.EXCLUSIVE)
    before = _inventory(paths.home)

    def fail(_: Path) -> None:
        raise RuntimeError("injected initialization failure")

    monkeypatch.setattr(
        "awesome_agent.storage.state_recovery.initialize_application_database",
        fail,
    )
    try:
        with pytest.raises(StateResetError) as raised:
            reset_local_state(lease)
    finally:
        lease.close()

    assert raised.value.code == "fresh_state_initialization_failed"
    assert _inventory(paths.home) == before
    assert not list(paths.home.glob(".state-reset-*"))


def test_reset_rejects_symlinked_state_boundary(tmp_path: Path) -> None:
    paths = AwesomePaths.from_home(tmp_path / "home")
    target = tmp_path / "outside"
    target.mkdir()
    paths.home.mkdir()
    try:
        paths.state_dir.symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlinks are unavailable: {error}")
    lease = StateLease.acquire(paths.home, StateLeaseMode.EXCLUSIVE)
    try:
        with pytest.raises(StateResetError) as raised:
            reset_local_state(lease)
    finally:
        lease.close()

    assert raised.value.code == "invalid_state_boundary"
    assert not (target / "application.db").exists()
