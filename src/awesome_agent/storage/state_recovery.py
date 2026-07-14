from __future__ import annotations

import os
import shutil
from pathlib import Path
from uuid import uuid4

from awesome_agent.paths import AwesomePaths
from awesome_agent.storage.database import initialize_application_database
from awesome_agent.storage.state_lease import StateLease, StateLeaseMode


class StateResetError(RuntimeError):
    def __init__(self, code: str, path: Path) -> None:
        super().__init__(code)
        self.code = code
        self.path = path


def reset_local_state(paths: AwesomePaths, lease: StateLease) -> None:
    home, state_dir = _validated_boundary(paths)
    if (
        not lease.active
        or lease.mode is not StateLeaseMode.EXCLUSIVE
        or lease.home != home
    ):
        raise StateResetError("exclusive_lease_required", state_dir)

    if not state_dir.exists():
        _initialize_fresh_state(state_dir, paths.application_db)
        return

    staging = home / f".state-reset-{uuid4().hex}"
    try:
        os.rename(state_dir, staging)
    except OSError as error:
        raise StateResetError("state_replacement_failed", state_dir) from error

    try:
        _initialize_fresh_state(state_dir, paths.application_db)
    except Exception as error:
        _restore_original_state(state_dir, staging)
        raise StateResetError(
            "fresh_state_initialization_failed",
            state_dir,
        ) from error

    try:
        shutil.rmtree(staging)
    except OSError as error:
        _restore_original_state(state_dir, staging)
        raise StateResetError("state_cleanup_failed", state_dir) from error


def _validated_boundary(paths: AwesomePaths) -> tuple[Path, Path]:
    lexical_state_dir = Path(os.path.abspath(paths.state_dir.expanduser()))
    if lexical_state_dir.is_symlink():
        raise StateResetError("invalid_state_boundary", lexical_state_dir)
    home = paths.home.expanduser().resolve()
    state_dir = lexical_state_dir.resolve()
    expected = AwesomePaths.from_home(home, install_dir=paths.install_dir)
    owned_paths = {
        "state_dir": (state_dir, expected.state_dir),
        "application_db": (paths.application_db, expected.application_db),
        "checkpoint_db": (paths.checkpoint_db, expected.checkpoint_db),
        "change_journal_dir": (
            paths.change_journal_dir,
            expected.change_journal_dir,
        ),
    }
    if any(
        actual.expanduser().resolve() != required.expanduser().resolve()
        for actual, required in owned_paths.values()
    ):
        raise StateResetError("invalid_state_boundary", state_dir)
    if state_dir.parent != home or state_dir.name != "state":
        raise StateResetError("invalid_state_boundary", state_dir)
    return home, state_dir


def _initialize_fresh_state(state_dir: Path, application_db: Path) -> None:
    try:
        state_dir.mkdir(parents=False, exist_ok=False)
        initialize_application_database(application_db)
    except Exception:
        if state_dir.exists():
            shutil.rmtree(state_dir)
        raise


def _restore_original_state(state_dir: Path, staging: Path) -> None:
    try:
        if state_dir.exists():
            shutil.rmtree(state_dir)
        if staging.exists():
            os.rename(staging, state_dir)
    except OSError as error:
        raise StateResetError("state_rollback_failed", state_dir) from error
