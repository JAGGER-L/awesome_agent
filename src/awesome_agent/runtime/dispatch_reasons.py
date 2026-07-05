from __future__ import annotations

APPROVAL_RESUME_REASONS: frozenset[str] = frozenset(
    {
        "approval_decided",
        "approval_granted",
        "approval_expired",
    }
)


def is_approval_resume_reason(reason: str | None) -> bool:
    return reason in APPROVAL_RESUME_REASONS
