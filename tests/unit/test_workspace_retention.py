from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from awesome_agent.domain.enums import (
    DispatchStatus,
    RunStatus,
    WorkspaceRetentionStatus,
)
from awesome_agent.domain.models import Run
from awesome_agent.runtime.workspaces import (
    WorkspaceCandidateStatus,
    WorkspaceInspection,
    evaluate_workspace_candidate,
)


def _run(
    *,
    status: RunStatus = RunStatus.COMPLETED,
    dispatch_status: DispatchStatus = DispatchStatus.TERMINAL,
) -> Run:
    run_id = uuid4()
    return Run(
        id=run_id,
        goal="Clean workspace",
        status=status,
        dispatch_status=dispatch_status,
        repository_id=uuid4(),
        workspace_path=Path("E:/managed/repository/run"),
        integration_branch=f"awesome-agent/run/{run_id}",
    )


def _inspection(*, dirty: bool = False) -> WorkspaceInspection:
    return WorkspaceInspection(
        owner_matches=True,
        path_in_managed_root=True,
        worktree_exists=True,
        branch_matches=True,
        branch_in_use_elsewhere=False,
        dirty=dirty,
    )


def test_completed_clean_workspace_is_cleanup_eligible() -> None:
    candidate = evaluate_workspace_candidate(_run(), _inspection(), force=False)

    assert candidate.status is WorkspaceCandidateStatus.ELIGIBLE
    assert candidate.retention_status is WorkspaceRetentionStatus.CLEANUP_ELIGIBLE
    assert candidate.can_cleanup


def test_cancelled_clean_workspace_is_cleanup_eligible() -> None:
    candidate = evaluate_workspace_candidate(
        _run(status=RunStatus.CANCELLED),
        _inspection(),
        force=False,
    )

    assert candidate.status is WorkspaceCandidateStatus.ELIGIBLE
    assert candidate.can_cleanup


def test_failed_workspace_requires_force() -> None:
    blocked = evaluate_workspace_candidate(
        _run(status=RunStatus.FAILED),
        _inspection(),
        force=False,
    )
    forced = evaluate_workspace_candidate(
        _run(status=RunStatus.FAILED),
        _inspection(),
        force=True,
    )

    assert blocked.status is WorkspaceCandidateStatus.BLOCKED_FAILED_OR_RECOVERY
    assert not blocked.can_cleanup
    assert forced.status is WorkspaceCandidateStatus.ELIGIBLE
    assert forced.can_cleanup


def test_dirty_workspace_requires_force() -> None:
    blocked = evaluate_workspace_candidate(_run(), _inspection(dirty=True), force=False)
    forced = evaluate_workspace_candidate(_run(), _inspection(dirty=True), force=True)

    assert blocked.status is WorkspaceCandidateStatus.BLOCKED_DIRTY_WORKTREE
    assert not blocked.can_cleanup
    assert forced.status is WorkspaceCandidateStatus.ELIGIBLE
    assert forced.can_cleanup


def test_recovery_required_workspace_is_blocked_even_with_force() -> None:
    candidate = evaluate_workspace_candidate(
        _run(status=RunStatus.RECOVERY_REQUIRED),
        _inspection(),
        force=True,
    )

    assert candidate.status is WorkspaceCandidateStatus.BLOCKED_RECOVERY_REQUIRED
    assert not candidate.can_cleanup


def test_active_dispatch_workspace_is_blocked() -> None:
    candidate = evaluate_workspace_candidate(
        _run(dispatch_status=DispatchStatus.EXECUTING),
        _inspection(),
        force=True,
    )

    assert candidate.status is WorkspaceCandidateStatus.BLOCKED_ACTIVE_RUN
    assert not candidate.can_cleanup


def test_owner_mismatch_is_a_hard_block() -> None:
    candidate = evaluate_workspace_candidate(
        _run(),
        WorkspaceInspection(
            owner_matches=False,
            path_in_managed_root=True,
            worktree_exists=True,
            branch_matches=True,
            branch_in_use_elsewhere=False,
            dirty=False,
        ),
        force=True,
    )

    assert candidate.status is WorkspaceCandidateStatus.BLOCKED_MISSING_OWNER
    assert not candidate.can_cleanup


def test_path_escape_is_a_hard_block() -> None:
    candidate = evaluate_workspace_candidate(
        _run(),
        WorkspaceInspection(
            owner_matches=True,
            path_in_managed_root=False,
            worktree_exists=True,
            branch_matches=True,
            branch_in_use_elsewhere=False,
            dirty=False,
        ),
        force=True,
    )

    assert candidate.status is WorkspaceCandidateStatus.BLOCKED_PATH_ESCAPE
    assert not candidate.can_cleanup
