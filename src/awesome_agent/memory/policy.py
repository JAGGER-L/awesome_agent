from __future__ import annotations

import re
import unicodedata

from awesome_agent.memory.models import (
    MemoryPolicyResult,
    MemoryPolicyStatus,
)
from awesome_agent.safety import redact_text

_CREDENTIAL_PATH = re.compile(
    r"(?i)(?:~[/\\])?(?:\.aws[/\\]credentials|\.env(?:\.[\w-]+)?|"
    r"id_(?:rsa|dsa|ecdsa|ed25519)|credentials\.json|\.npmrc|\.pypirc)"
)
_ABSOLUTE_PRIVATE_PATH = re.compile(
    r"(?i)(?:\b[A-Z]:\\(?:Users|Documents and Settings)\\|"
    r"(?<![\w.])/(?:Users|home|root|private)/)"
)
_RAW_CODE_OR_OUTPUT = (
    re.compile(r"```"),
    re.compile(r"(?m)^diff --git "),
    re.compile(r"(?m)^@@ -\d"),
    re.compile(r"(?m)^Traceback \(most recent call last\):"),
    re.compile(r"(?m)^(?:stdout|stderr|tool output)\s*:", re.IGNORECASE),
)
_EXECUTABLE_INSTRUCTION = (
    re.compile(r"(?im)^\s*(?:\$|>)\s*(?:rm|curl|wget|sudo|bash|sh|powershell)\b"),
    re.compile(r"(?i)\bignore (?:all )?previous instructions\b"),
    re.compile(r"(?i)\bcurl\b[^\n|]*\|\s*(?:ba)?sh\b"),
    re.compile(r"(?i)\brm\s+-rf\b"),
    re.compile(r"(?m)^#!\s*/"),
)
_UNREDACTED_SECRET = (
    re.compile(r"(?i)\b(?:ghp|github_pat|glpat|xox[baprs])[-_A-Za-z0-9]{12,}\b"),
    re.compile(r"(?i)\b(?:api[_ -]?key|token|password|secret|credential)\s+is\s+\S+"),
)


class LocalMemoryPolicy:
    def __init__(self, *, max_entry_chars: int = 2_000) -> None:
        if max_entry_chars < 1:
            raise ValueError("max_entry_chars must be positive")
        self._max_entry_chars = max_entry_chars

    def evaluate(self, content: str) -> MemoryPolicyResult:
        normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            return _rejected("empty")
        if len(normalized) > self._max_entry_chars:
            return _rejected("too_large")
        if _has_control_character(normalized):
            return _rejected("control_character")
        redacted = redact_text(normalized)
        if redacted.redacted or any(
            pattern.search(normalized) for pattern in _UNREDACTED_SECRET
        ):
            return _rejected("secret_like_content")
        if _CREDENTIAL_PATH.search(normalized):
            return _rejected("credential_path")
        if _ABSOLUTE_PRIVATE_PATH.search(normalized):
            return _rejected("absolute_private_path")
        if any(pattern.search(normalized) for pattern in _RAW_CODE_OR_OUTPUT):
            return _rejected("raw_code_or_output")
        if any(pattern.search(normalized) for pattern in _EXECUTABLE_INSTRUCTION):
            return _rejected("executable_instruction")
        return MemoryPolicyResult(
            status=MemoryPolicyStatus.ELIGIBLE,
            content=normalized,
        )


def _has_control_character(content: str) -> bool:
    return any(
        character not in {"\n", "\t"} and unicodedata.category(character) == "Cc"
        for character in content
    )


def _rejected(code: str) -> MemoryPolicyResult:
    return MemoryPolicyResult(
        status=MemoryPolicyStatus.REJECTED,
        error_code=code,
        message="Memory content was rejected by policy.",
    )
