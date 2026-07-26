from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import yaml

from awesome_agent.config.loader import read_user_config_document
from awesome_agent.config.models import UserConfigDocument
from awesome_agent.config.resource_lock import exclusive_resource_lock


class UserConfigWriter:
    def __init__(self, path: Path) -> None:
        self._path = path.expanduser()

    def update(
        self,
        transform: Callable[[UserConfigDocument], UserConfigDocument],
    ) -> UserConfigDocument:
        with self.transaction():
            current = read_user_config_document(self._path)
            candidate = transform(current)
            if not isinstance(candidate, UserConfigDocument):
                raise TypeError("Config transform must return UserConfigDocument.")
            updated = UserConfigDocument.model_validate(
                candidate.model_dump(mode="python")
            )
            self._write(updated)
            return updated

    def read(self) -> UserConfigDocument:
        with self.transaction():
            return read_user_config_document(self._path)

    def replace(self, document: UserConfigDocument) -> UserConfigDocument:
        with self.transaction():
            restored = UserConfigDocument.model_validate(
                document.model_dump(mode="python")
            )
            self._write(restored)
            return restored

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Hold the user-config lock across one related user-state transaction."""

        with exclusive_resource_lock(self._path):
            yield

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
