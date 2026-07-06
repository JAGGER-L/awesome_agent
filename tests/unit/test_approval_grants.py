from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from awesome_agent.domain.enums import ApprovalStatus
from awesome_agent.persistence.approval_contracts import DurableApproval
from awesome_agent.runtime.approval_grants import (
    approval_matches_grant_scope,
    approval_grant_scope_from_arguments,
    find_matching_approval_grant,
)
from awesome_agent.tools.repository import canonical_arguments_hash_from_arguments


def _approval(
    *,
    tool_name: str,
    arguments: dict[str, object],
    workspace: Path,
    status: ApprovalStatus = ApprovalStatus.APPROVED,
    expires_at: datetime | None = None,
    capabilities: list[str] | None = None,
    risk_level: str = "medium",
) -> DurableApproval:
    now = datetime.now(UTC)
    return DurableApproval(
        run_id=uuid4(),
        tool_invocation_id=uuid4(),
        tool_call_id="source-call",
        tool_name=tool_name,
        tool_version="1",
        canonical_arguments=arguments,
        arguments_hash=canonical_arguments_hash_from_arguments(arguments),
        workspace_path=str(workspace.resolve()),
        workspace_fingerprint="fingerprint",
        capabilities=capabilities or ["repository:write"],
        risk_level=risk_level,
        expires_at=expires_at or now + timedelta(minutes=30),
        status=status,
        decided_at=now,
        updated_at=now,
    )


def test_shell_grant_matches_exact_argv(tmp_path: Path) -> None:
    approval = _approval(
        tool_name="shell.execute",
        arguments={"argv": ["python", "square.py"]},
        workspace=tmp_path,
        capabilities=["shell:execute"],
        risk_level="high",
    )
    requested = approval_grant_scope_from_arguments(
        tool_name="shell.execute",
        tool_version="1",
        arguments={"argv": ["python", "square.py"]},
        workspace=tmp_path,
        capabilities=("shell:execute",),
        risk_level="high",
    )

    match = find_matching_approval_grant([approval], requested_scope=requested)

    assert match is not None
    assert match.approval is approval
    assert match.scope.resource_kind == "shell.argv"


def test_shell_grant_does_not_match_different_argv(tmp_path: Path) -> None:
    approval = _approval(
        tool_name="shell.execute",
        arguments={"argv": ["python", "square.py"]},
        workspace=tmp_path,
        capabilities=["shell:execute"],
        risk_level="high",
    )
    requested = approval_grant_scope_from_arguments(
        tool_name="shell.execute",
        tool_version="1",
        arguments={"argv": ["python", "cube.py"]},
        workspace=tmp_path,
        capabilities=("shell:execute",),
        risk_level="high",
    )

    assert find_matching_approval_grant([approval], requested_scope=requested) is None


def test_patch_grant_matches_same_path_set_with_different_content(
    tmp_path: Path,
) -> None:
    approval = _approval(
        tool_name="repo.apply_patch",
        arguments={
            "patch": (
                "--- a/calc.py\n"
                "+++ b/calc.py\n"
                "@@\n"
                "-return 1\n"
                "+return 2\n"
            )
        },
        workspace=tmp_path,
    )
    requested = approval_grant_scope_from_arguments(
        tool_name="repo.apply_patch",
        tool_version="1",
        arguments={
            "patch": (
                "--- a/calc.py\n"
                "+++ b/calc.py\n"
                "@@\n"
                "-return 2\n"
                "+return 3\n"
            )
        },
        workspace=tmp_path,
        capabilities=("repository:write",),
        risk_level="medium",
    )

    match = find_matching_approval_grant([approval], requested_scope=requested)

    assert match is not None
    assert match.scope.resource_kind == "repo.patch_paths"


def test_patch_grant_does_not_match_different_path(tmp_path: Path) -> None:
    approval = _approval(
        tool_name="repo.apply_patch",
        arguments={"patch": "--- a/calc.py\n+++ b/calc.py\n@@\n-a\n+b\n"},
        workspace=tmp_path,
    )
    requested = approval_grant_scope_from_arguments(
        tool_name="repo.apply_patch",
        tool_version="1",
        arguments={"patch": "--- a/other.py\n+++ b/other.py\n@@\n-a\n+b\n"},
        workspace=tmp_path,
        capabilities=("repository:write",),
        risk_level="medium",
    )

    assert find_matching_approval_grant([approval], requested_scope=requested) is None


def test_grant_does_not_match_expired_or_pending_approvals(tmp_path: Path) -> None:
    arguments = {"argv": ["python", "square.py"]}
    requested = approval_grant_scope_from_arguments(
        tool_name="shell.execute",
        tool_version="1",
        arguments=arguments,
        workspace=tmp_path,
        capabilities=("shell:execute",),
        risk_level="high",
    )
    expired = _approval(
        tool_name="shell.execute",
        arguments=arguments,
        workspace=tmp_path,
        status=ApprovalStatus.APPROVED,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
        capabilities=["shell:execute"],
        risk_level="high",
    )
    pending = _approval(
        tool_name="shell.execute",
        arguments=arguments,
        workspace=tmp_path,
        status=ApprovalStatus.PENDING,
        capabilities=["shell:execute"],
        risk_level="high",
    )

    assert (
        find_matching_approval_grant([expired, pending], requested_scope=requested)
        is None
    )


def test_denied_grant_is_reusable_until_expiry(tmp_path: Path) -> None:
    approval = _approval(
        tool_name="shell.execute",
        arguments={"argv": ["python", "square.py"]},
        workspace=tmp_path,
        status=ApprovalStatus.DENIED,
        capabilities=["shell:execute"],
        risk_level="high",
    )
    requested = approval_grant_scope_from_arguments(
        tool_name="shell.execute",
        tool_version="1",
        arguments={"argv": ["python", "square.py"]},
        workspace=tmp_path,
        capabilities=("shell:execute",),
        risk_level="high",
    )

    match = find_matching_approval_grant([approval], requested_scope=requested)

    assert match is not None
    assert match.approval.status is ApprovalStatus.DENIED


def test_writefile_grant_matches_same_path_with_different_content(
    tmp_path: Path,
) -> None:
    approval = _approval(
        tool_name="WriteFile",
        arguments={"path": ".env", "content": "TOKEN=one\n"},
        workspace=tmp_path,
    )
    scope = approval_grant_scope_from_arguments(
        tool_name="WriteFile",
        tool_version="1",
        arguments={"path": ".env", "content": "TOKEN=two\n", "overwrite": True},
        workspace=tmp_path,
        capabilities=("repository:write",),
        risk_level="medium",
    )

    assert scope is not None
    assert approval_matches_grant_scope(approval, scope, now=datetime.now(UTC))


def test_editfile_grant_does_not_match_different_path(tmp_path: Path) -> None:
    approval = _approval(
        tool_name="EditFile",
        arguments={"path": ".env", "old_text": "one", "new_text": "two"},
        workspace=tmp_path,
    )
    scope = approval_grant_scope_from_arguments(
        tool_name="EditFile",
        tool_version="1",
        arguments={"path": ".npmrc", "old_text": "one", "new_text": "two"},
        workspace=tmp_path,
        capabilities=("repository:write",),
        risk_level="medium",
    )

    assert scope is not None
    assert not approval_matches_grant_scope(approval, scope, now=datetime.now(UTC))


def test_bash_grant_matches_exact_command_only(tmp_path: Path) -> None:
    approval = _approval(
        tool_name="Bash",
        arguments={"command": "pytest -q"},
        workspace=tmp_path,
        capabilities=["shell:execute"],
    )
    same = approval_grant_scope_from_arguments(
        tool_name="Bash",
        tool_version="1",
        arguments={"command": "pytest -q"},
        workspace=tmp_path,
        capabilities=("shell:execute",),
        risk_level="medium",
    )
    different = approval_grant_scope_from_arguments(
        tool_name="Bash",
        tool_version="1",
        arguments={"command": "pytest tests/unit -q"},
        workspace=tmp_path,
        capabilities=("shell:execute",),
        risk_level="medium",
    )

    assert same is not None
    assert different is not None
    assert approval_matches_grant_scope(approval, same, now=datetime.now(UTC))
    assert not approval_matches_grant_scope(approval, different, now=datetime.now(UTC))
