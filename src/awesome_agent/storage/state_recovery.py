from __future__ import annotations

import os
import shutil
from pathlib import Path
from uuid import uuid4

from awesome_agent.storage.database import initialize_application_database
from awesome_agent.storage.state_lease import StateLease, StateLeaseMode


class StateResetError(RuntimeError):
    def __init__(self, code: str, path: Path) -> None:
        super().__init__(code)
        self.code = code
        self.path = path


def reset_local_state(lease: StateLease) -> None:
    home, state_dir = _validated_boundary(lease)
    application_db = state_dir / "application.db"

    if not state_dir.exists():
        _initialize_fresh_state(state_dir, application_db)
        return

    staging = home / f".state-reset-{uuid4().hex}"
    try:
        os.rename(state_dir, staging)
    except OSError as error:
        raise StateResetError("state_replacement_failed", state_dir) from error

    try:
        _initialize_fresh_state(state_dir, application_db)
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


def _validated_boundary(lease: StateLease) -> tuple[Path, Path]:
    home = lease.home.expanduser().resolve()
    lexical_state_dir = Path(os.path.abspath(home / "state"))
    if not lease.active or lease.mode is not StateLeaseMode.EXCLUSIVE:
        raise StateResetError("exclusive_lease_required", lexical_state_dir)
    if lexical_state_dir.is_symlink():
        raise StateResetError("invalid_state_boundary", lexical_state_dir)
    state_dir = lexical_state_dir.resolve()
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
