import re

from awesome_agent.memory.models import MemoryCandidate, MemorySource

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?i)(api[_-]?key|authorization|password)\s*[:=]\s*\S+"),
]


class MemoryPolicy:
    def __init__(self, *, max_candidate_chars: int = 2000) -> None:
        self._max_candidate_chars = max_candidate_chars

    def accept(self, candidate: MemoryCandidate) -> bool:
        if candidate.source is MemorySource.MEMORY_RETRIEVAL:
            return False
        if len(candidate.content) > self._max_candidate_chars:
            return False
        if "```" in candidate.content:
            return False
        return not any(
            pattern.search(candidate.content) for pattern in _SECRET_PATTERNS
        )
