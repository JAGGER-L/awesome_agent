from __future__ import annotations

from awesome_agent.runtime.dispatch_reasons import (
    APPROVAL_RESUME_REASONS,
    is_approval_resume_reason,
)


def test_approval_resume_reasons_are_explicit() -> None:
    assert (
        frozenset(
            {
                "approval_decided",
                "approval_granted",
                "approval_expired",
            }
        )
        == APPROVAL_RESUME_REASONS
    )


def test_is_approval_resume_reason_matches_only_resume_reasons() -> None:
    assert is_approval_resume_reason("approval_decided") is True
    assert is_approval_resume_reason("approval_granted") is True
    assert is_approval_resume_reason("approval_expired") is True
    assert is_approval_resume_reason("approval_wait") is False
    assert is_approval_resume_reason("lease expired") is False
    assert is_approval_resume_reason(None) is False
