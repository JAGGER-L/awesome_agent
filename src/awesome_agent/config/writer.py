from __future__ import annotations

import os
import threading
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

import yaml

from awesome_agent.config.loader import read_user_config_document
from awesome_agent.config.models import UserConfigDocument

_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[Path, threading.RLock] = {}


class UserConfigWriter:
    def __init__(self, path: Path) -> None:
        self._path = path.expanduser()
        self._lock = _lock_for(self._path)

    def update(
        self,
        transform: Callable[[UserConfigDocument], UserConfigDocument],
    ) -> UserConfigDocument:
        with self._lock:
            current = read_user_config_document(self._path)
            candidate = transform(current)
            if not isinstance(candidate, UserConfigDocument):
                raise TypeError("Config transform must return UserConfigDocument.")
            updated = UserConfigDocument.model_validate(
                candidate.model_dump(mode="python")
            )
            self._write(updated)
            return updated

    def _write(self, document: UserConfigDocument) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.parent / f".{self._path.name}.{uuid4().hex}.tmp"
        serialized = yaml.safe_dump(
            document.model_dump(mode="json"),
            allow_unicode=True,
            sort_keys=False,
        )
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path)
        finally:
            temporary.unlink(missing_ok=True)


def _lock_for(path: Path) -> threading.RLock:
    key = path.absolute()
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock
