from __future__ import annotations

import hashlib
import re
import unicodedata

from awesome_agent.memory.models import (
    CloudPolicyResult,
    MemoryCandidate,
    MemoryPolicyResult,
    MemoryPolicyStatus,
    MemoryScope,
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
_REPOSITORY_IDENTIFIER = (
    re.compile(r"(?i)\b(?:repository|repo|git remote)\s+(?:is\s+)?\S+"),
    re.compile(r"(?i)(?:git@|https?://)(?:www\.)?(?:github|gitlab|bitbucket)\.[^\s]+"),
)
_TRANSIENT_TASK_STATE = (
    re.compile(r"(?i)\b(?:current|this)\s+(?:task|issue|turn|run)\b"),
    re.compile(
        r"(?i)\b(?:completed|failed|blocked|pending)\b.*\b(?:today|now|currently)\b"
    ),
    re.compile(r"(?i)\b(?:test|build|command)\s+(?:failed|passed)\b"),
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


class CloudMemoryPolicy:
    def __init__(self) -> None:
        self._base = LocalMemoryPolicy(max_entry_chars=500)

    def evaluate(
        self,
        content: str,
        *,
        scope: MemoryScope | str,
        workspace_key: str | None,
    ) -> CloudPolicyResult:
        try:
            parsed_scope = MemoryScope(scope)
        except ValueError:
            return _cloud_rejected("unsupported_scope")
        if parsed_scope is MemoryScope.WORKSPACE and (
            workspace_key is None
            or re.fullmatch(r"ws_[a-f0-9]{32}", workspace_key) is None
        ):
            return _cloud_rejected("workspace_scope_missing")
        base = self._base.evaluate(content)
        if base.status is MemoryPolicyStatus.REJECTED or base.content is None:
            return _cloud_rejected(base.error_code or "memory_rejected")
        if any(pattern.search(base.content) for pattern in _REPOSITORY_IDENTIFIER):
            return _cloud_rejected("repository_identifier")
        if any(pattern.search(base.content) for pattern in _TRANSIENT_TASK_STATE):
            return _cloud_rejected("transient_task_state")
        scoped_workspace = (
            workspace_key if parsed_scope is MemoryScope.WORKSPACE else None
        )
        return CloudPolicyResult(
            status=MemoryPolicyStatus.ELIGIBLE,
            candidate=MemoryCandidate(
                scope=parsed_scope,
                content=base.content,
                fact_hash=cloud_fact_hash(
                    base.content,
                    scope=parsed_scope,
                    workspace_key=scoped_workspace,
                ),
            ),
        )


def cloud_fact_hash(
    content: str,
    *,
    scope: MemoryScope,
    workspace_key: str | None,
) -> str:
    if scope is MemoryScope.WORKSPACE:
        if (
            workspace_key is None
            or re.fullmatch(r"ws_[a-f0-9]{32}", workspace_key) is None
        ):
            raise ValueError("workspace scope requires an opaque workspace key")
        authority = workspace_key
    else:
        authority = ""
    normalized = " ".join(unicodedata.normalize("NFKC", content).casefold().split())
    canonical = f"{scope.value}\n{authority}\n{normalized}"
    return hashlib.sha256(canonical.encode()).hexdigest()


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


def _cloud_rejected(code: str) -> CloudPolicyResult:
    return CloudPolicyResult(
        status=MemoryPolicyStatus.REJECTED,
        error_code=code,
        message="Cloud memory content was rejected by policy.",
    )
