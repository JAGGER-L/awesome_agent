from __future__ import annotations

import os
from pathlib import Path

from awesome_agent.memory.models import MemoryCandidate, MemoryKind
from awesome_agent.memory.policy import MemoryPolicy


class BuiltinMemoryStore:
    def __init__(
        self,
        *,
        root: Path,
        policy: MemoryPolicy,
        max_file_chars: int = 12_000,
    ) -> None:
        self._root = root
        self._policy = policy
        self._max_file_chars = max_file_chars

    def snapshot(self) -> dict[MemoryKind, str]:
        return {
            kind: self._path(kind).read_text(encoding="utf-8")
            if self._path(kind).exists()
            else ""
            for kind in MemoryKind
        }

    def write(self, candidate: MemoryCandidate) -> bool:
        if not self._policy.accept(candidate):
            return False
        path = self._path(candidate.kind)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        entry = f"- {candidate.content.strip()}\n"
        if entry in existing:
            return False
        heading = self._heading(candidate.kind)
        content = existing or f"# {heading}\n\n"
        content = f"{content}{entry}"
        if len(content) > self._max_file_chars:
            return False
        self._atomic_write(path, content)
        return True

    def _path(self, kind: MemoryKind) -> Path:
        return self._root / ("USER.md" if kind is MemoryKind.USER else "MEMORY.md")

    @staticmethod
    def _heading(kind: MemoryKind) -> str:
        return "User Memory" if kind is MemoryKind.USER else "Operational Memory"

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
