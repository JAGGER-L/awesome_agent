from __future__ import annotations

import re

from awesome_agent.memory.models import MemoryAddRequest, MemoryPolicyDecision

_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(
        r"(?i)(api[_-]?key|authorization|password|passwd|token|secret|"
        r"credential|dsn)\s*[:=]\s*\S+"
    ),
    re.compile(r"(?i)(postgres|mysql|redis|mongodb)://[^@\s]+:[^@\s]+@"),
]

_ONE_OFF_PATTERNS = [
    re.compile(r"(?i)\b(this|current|temporary|one[- ]off|just now)\b"),
    re.compile(r"(?i)\b(test failed|command output|stack trace|traceback)\b"),
]


class MemoryPolicy:
    def __init__(self, *, max_candidate_chars: int = 2000) -> None:
        self._max_candidate_chars = max_candidate_chars

    def evaluate(self, request: MemoryAddRequest) -> MemoryPolicyDecision:
        content = " ".join(request.content.strip().split())
        if not content:
            return MemoryPolicyDecision(action="reject", reason="empty")
        if len(content) > self._max_candidate_chars:
            return MemoryPolicyDecision(action="reject", reason="too_long")
        if "```" in request.content:
            return MemoryPolicyDecision(action="reject", reason="raw_source_code")
        if any(pattern.search(content) for pattern in _SECRET_PATTERNS):
            return MemoryPolicyDecision(action="reject", reason="secret_like_content")
        if any(pattern.search(content) for pattern in _ONE_OFF_PATTERNS):
            return MemoryPolicyDecision(action="reject", reason="temporary_or_one_off")
        if request.source == "model_initiated" and re.search(
            r"(?i)\b(may|might|maybe|probably|seems)\b",
            content,
        ):
            return MemoryPolicyDecision(action="reject", reason="uncertain_inference")
        return MemoryPolicyDecision(action="allow", sanitized_content=content)
