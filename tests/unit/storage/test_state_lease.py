from __future__ import annotations

from pathlib import Path

import pytest

from awesome_agent.storage.state_lease import (
    StateLease,
    StateLeaseMode,
    StateLeaseUnavailable,
)


def test_shared_leases_coexist_and_block_exclusive_ownership(
    tmp_path: Path,
) -> None:
    first = StateLease.acquire(tmp_path, StateLeaseMode.SHARED)
    second = StateLease.acquire(tmp_path, StateLeaseMode.SHARED)
    try:
        assert first.mode is StateLeaseMode.SHARED
        assert second.mode is StateLeaseMode.SHARED
        with pytest.raises(StateLeaseUnavailable):
            StateLease.acquire(tmp_path, StateLeaseMode.EXCLUSIVE)
    finally:
        second.close()
        first.close()


def test_exclusive_lease_can_downgrade_to_shared_ownership(tmp_path: Path) -> None:
    lease = StateLease.acquire(tmp_path, StateLeaseMode.EXCLUSIVE)
    try:
        with pytest.raises(StateLeaseUnavailable):
            StateLease.acquire(tmp_path, StateLeaseMode.SHARED)

        lease.downgrade()
        second = StateLease.acquire(tmp_path, StateLeaseMode.SHARED)
        second.close()

        assert lease.mode is StateLeaseMode.SHARED
        assert lease.active is True
    finally:
        lease.close()


def test_close_is_idempotent_and_releases_ownership(tmp_path: Path) -> None:
    lease = StateLease.acquire(tmp_path, StateLeaseMode.EXCLUSIVE)

    lease.close()
    lease.close()

    assert lease.active is False
    replacement = StateLease.acquire(tmp_path, StateLeaseMode.EXCLUSIVE)
    replacement.close()


def test_lease_rejects_symlinked_lock_file_without_touching_target(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    target = tmp_path / "target"
    target.write_bytes(b"")
    try:
        (home / ".state.lock").symlink_to(target)
    except OSError as error:
        pytest.skip(f"file symlinks are unavailable: {error}")

    with pytest.raises(StateLeaseUnavailable):
        StateLease.acquire(home, StateLeaseMode.EXCLUSIVE)

    assert target.read_bytes() == b""


def test_lease_normalizes_lock_file_open_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_open(*_: object, **__: object) -> int:
        raise PermissionError("injected lock failure")

    monkeypatch.setattr("awesome_agent.storage.state_lease.os.open", fail_open)

    with pytest.raises(StateLeaseUnavailable) as raised:
        StateLease.acquire(tmp_path, StateLeaseMode.EXCLUSIVE)

    assert raised.value.home == tmp_path.resolve()
    assert raised.value.mode is StateLeaseMode.EXCLUSIVE
