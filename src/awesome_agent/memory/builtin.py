from __future__ import annotations

import os
import re
from pathlib import Path

from awesome_agent.memory.models import (
    MemoryAddRequest,
    MemoryContextSnapshot,
    MemoryContextTarget,
    MemoryEntry,
    MemoryOperationResult,
    MemoryTarget,
    new_memory_id,
)
from awesome_agent.memory.policy import MemoryPolicy

_ENTRY_RE = re.compile(r"^- \[(?P<id>mem_[A-Za-z0-9_-]+)\] (?P<content>.*)$")


class BuiltinMemoryStore:
    def __init__(
        self,
        *,
        root: Path,
        policy: MemoryPolicy,
        max_file_chars: int = 12_000,
        inject_file_chars: int = 8_000,
        inject_total_chars: int = 16_000,
    ) -> None:
        self._root = root
        self._policy = policy
        self._max_file_chars = max_file_chars
        self._inject_file_chars = inject_file_chars
        self._inject_total_chars = inject_total_chars
        self._cache: dict[MemoryTarget, tuple[float | None, str]] = {}

    @property
    def root(self) -> Path:
        return self._root

    def path_for(self, target: MemoryTarget) -> Path:
        return self._path(target)

    def ensure_files(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        for target in MemoryTarget:
            path = self._path(target)
            if not path.exists():
                self._atomic_write(path, f"# {self._heading(target)}\n\n")

    def list_entries(self, target: MemoryTarget | None = None) -> list[MemoryEntry]:
        targets = [target] if target is not None else list(MemoryTarget)
        entries: list[MemoryEntry] = []
        for item in targets:
            entries.extend(self._entries_from_text(item, self._read(item)))
        return entries

    def add(self, request: MemoryAddRequest) -> MemoryOperationResult:
        decision = self._policy.evaluate(request)
        if decision.action != "allow" or decision.sanitized_content is None:
            return MemoryOperationResult(
                operation="add",
                target=request.target,
                status="rejected_by_policy",
                source=request.source,
                policy_decision=decision.action,
                reason=decision.reason,
            )
        existing = self._read(request.target)
        existing_entries = self._entries_from_text(request.target, existing)
        if any(
            entry.content == decision.sanitized_content for entry in existing_entries
        ):
            return MemoryOperationResult(
                operation="add",
                target=request.target,
                status="duplicate",
                source=request.source,
                policy_decision=decision.action,
                reason="duplicate",
            )
        entry = MemoryEntry(
            id=new_memory_id(),
            target=request.target,
            content=decision.sanitized_content,
        )
        if existing_entries:
            next_text = f"{existing.rstrip()}\n- [{entry.id}] {entry.content}\n"
        elif existing.strip():
            next_text = f"{existing.rstrip()}\n\n- [{entry.id}] {entry.content}\n"
        else:
            next_text = (
                f"# {self._heading(request.target)}\n\n- [{entry.id}] {entry.content}\n"
            )
        if len(next_text) > self._max_file_chars:
            return MemoryOperationResult(
                operation="add",
                target=request.target,
                status="rejected_by_policy",
                source=request.source,
                policy_decision=decision.action,
                reason="memory_file_too_large",
            )
        self._write(request.target, next_text)
        return MemoryOperationResult(
            operation="add",
            target=request.target,
            status="added",
            entry=entry,
            memory_id=entry.id,
            source=request.source,
            policy_decision=decision.action,
        )

    def delete(self, target: MemoryTarget, memory_id: str) -> MemoryOperationResult:
        text = self._read(target)
        lines = text.splitlines()
        kept: list[str] = []
        removed = False
        for line in lines:
            match = _ENTRY_RE.match(line)
            if match and match.group("id") == memory_id:
                removed = True
                continue
            kept.append(line)
        if not removed:
            return MemoryOperationResult(
                operation="delete",
                target=target,
                status="not_found",
                memory_id=memory_id,
            )
        self._write(target, "\n".join(kept).rstrip() + "\n")
        return MemoryOperationResult(
            operation="delete",
            target=target,
            status="deleted",
            memory_id=memory_id,
        )

    def context_snapshot(self) -> MemoryContextSnapshot:
        remaining = self._inject_total_chars
        targets: dict[str, MemoryContextTarget] = {}
        for target in MemoryTarget:
            path = self._path(target)
            text = self._read(target)
            if self._is_default_text(target, text):
                continue
            limit = max(0, min(self._inject_file_chars, remaining))
            content = text[:limit]
            truncated = len(text) > limit
            remaining -= len(content)
            if content.strip():
                targets[target.value] = MemoryContextTarget(
                    target=target,
                    path=str(path),
                    content=content,
                    chars=len(text),
                    truncated=truncated,
                )
        return MemoryContextSnapshot(enabled=bool(targets), targets=targets)

    def status(self) -> tuple[dict[str, int], dict[str, bool]]:
        counts: dict[str, int] = {}
        truncated: dict[str, bool] = {}
        for target in MemoryTarget:
            text = self._read(target)
            counts[target.value] = len(self._entries_from_text(target, text))
            truncated[target.value] = len(text) > self._inject_file_chars
        return counts, truncated

    def _read(self, target: MemoryTarget) -> str:
        self.ensure_files()
        path = self._path(target)
        mtime = path.stat().st_mtime if path.exists() else None
        cached = self._cache.get(target)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        self._cache[target] = (mtime, text)
        return text

    def _write(self, target: MemoryTarget, content: str) -> None:
        path = self._path(target)
        self._atomic_write(path, content)
        self._cache.pop(target, None)

    def _path(self, target: MemoryTarget) -> Path:
        return self._root / ("USER.md" if target is MemoryTarget.USER else "MEMORY.md")

    @staticmethod
    def _heading(target: MemoryTarget) -> str:
        return "User Memory" if target is MemoryTarget.USER else "Awesome Agent Memory"

    @classmethod
    def _is_default_text(cls, target: MemoryTarget, text: str) -> bool:
        return text.strip() == f"# {cls._heading(target)}"

    @staticmethod
    def _entries_from_text(target: MemoryTarget, text: str) -> list[MemoryEntry]:
        entries: list[MemoryEntry] = []
        for line in text.splitlines():
            match = _ENTRY_RE.match(line)
            if match:
                entries.append(
                    MemoryEntry(
                        id=match.group("id"),
                        target=target,
                        content=match.group("content"),
                    )
                )
        return entries

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
