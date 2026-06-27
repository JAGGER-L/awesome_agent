from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from awesome_agent.domain.enums import (
    DispatchStatus,
    RunStatus,
    WorkspaceRetentionStatus,
)
from awesome_agent.domain.models import Run


class WorkspaceCandidateStatus(StrEnum):
    ELIGIBLE = "eligible"
    CLEANED = "cleaned"
    MISSING = "missing"
    BLOCKED_ACTIVE_RUN = "blocked_active_run"
    BLOCKED_UNOWNED_PATH = "blocked_unowned_path"
    BLOCKED_DIRTY_WORKTREE = "blocked_dirty_worktree"
    BLOCKED_FAILED_OR_RECOVERY = "blocked_failed_or_recovery"
    BLOCKED_RECOVERY_REQUIRED = "blocked_recovery_required"
    BLOCKED_BRANCH_MISMATCH = "blocked_branch_mismatch"
    BLOCKED_MISSING_OWNER = "blocked_missing_owner"
    BLOCKED_PATH_ESCAPE = "blocked_path_escape"
    BLOCKED_NOT_MANAGED = "blocked_not_managed"
    BLOCKED_BRANCH_IN_USE = "blocked_branch_in_use"
    BLOCKED_GIT_ERROR = "blocked_git_error"


@dataclass(frozen=True, slots=True)
class WorkspaceCleanupRequest:
    run_id: UUID | None = None
    older_than: timedelta | None = None
    apply: bool = False
    force: bool = False
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceInspection:
    owner_matches: bool
    path_in_managed_root: bool
    worktree_exists: bool
    branch_matches: bool
    branch_in_use_elsewhere: bool
    dirty: bool | None
    git_error: str | None = None


@dataclass(frozen=True, slots=True)
class WorkspaceCandidate:
    run_id: UUID
    repository_id: UUID | None
    workspace_path: Path | None
    branch: str | None
    status: WorkspaceCandidateStatus
    retention_status: WorkspaceRetentionStatus
    reason: str
    dirty: bool | None
    can_cleanup: bool


def evaluate_workspace_candidate(
    run: Run,
    inspection: WorkspaceInspection,
    *,
    force: bool,
) -> WorkspaceCandidate:
    status = _candidate_status(run, inspection, force=force)
    retention_status = _retention_status(status)
    return WorkspaceCandidate(
        run_id=run.id,
        repository_id=run.repository_id,
        workspace_path=run.workspace_path,
        branch=run.integration_branch,
        status=status,
        retention_status=retention_status,
        reason=_reason(status),
        dirty=inspection.dirty,
        can_cleanup=status is WorkspaceCandidateStatus.ELIGIBLE,
    )


def _candidate_status(
    run: Run,
    inspection: WorkspaceInspection,
    *,
    force: bool,
) -> WorkspaceCandidateStatus:
    if run.dispatch_status is not DispatchStatus.TERMINAL:
        return WorkspaceCandidateStatus.BLOCKED_ACTIVE_RUN
    if run.status is RunStatus.RECOVERY_REQUIRED:
        return WorkspaceCandidateStatus.BLOCKED_RECOVERY_REQUIRED
    if not inspection.path_in_managed_root:
        return WorkspaceCandidateStatus.BLOCKED_PATH_ESCAPE
    if not inspection.owner_matches:
        return WorkspaceCandidateStatus.BLOCKED_MISSING_OWNER
    if inspection.git_error is not None:
        return WorkspaceCandidateStatus.BLOCKED_GIT_ERROR
    if not inspection.worktree_exists:
        return WorkspaceCandidateStatus.MISSING
    if not inspection.branch_matches:
        return WorkspaceCandidateStatus.BLOCKED_BRANCH_MISMATCH
    if inspection.branch_in_use_elsewhere:
        return WorkspaceCandidateStatus.BLOCKED_BRANCH_IN_USE
    if run.status is RunStatus.FAILED and not force:
        return WorkspaceCandidateStatus.BLOCKED_FAILED_OR_RECOVERY
    if run.status not in {
        RunStatus.COMPLETED,
        RunStatus.CANCELLED,
        RunStatus.FAILED,
    }:
        return WorkspaceCandidateStatus.BLOCKED_ACTIVE_RUN
    if inspection.dirty and not force:
        return WorkspaceCandidateStatus.BLOCKED_DIRTY_WORKTREE
    return WorkspaceCandidateStatus.ELIGIBLE


def _retention_status(
    status: WorkspaceCandidateStatus,
) -> WorkspaceRetentionStatus:
    if status is WorkspaceCandidateStatus.ELIGIBLE:
        return WorkspaceRetentionStatus.CLEANUP_ELIGIBLE
    if status is WorkspaceCandidateStatus.CLEANED:
        return WorkspaceRetentionStatus.CLEANED
    if status is WorkspaceCandidateStatus.MISSING:
        return WorkspaceRetentionStatus.MISSING
    return WorkspaceRetentionStatus.CLEANUP_BLOCKED


def _reason(status: WorkspaceCandidateStatus) -> str:
    reasons = {
        WorkspaceCandidateStatus.ELIGIBLE: "workspace is eligible for cleanup",
        WorkspaceCandidateStatus.CLEANED: "workspace was cleaned",
        WorkspaceCandidateStatus.MISSING: "managed workspace is missing",
        WorkspaceCandidateStatus.BLOCKED_ACTIVE_RUN: "run is not terminal",
        WorkspaceCandidateStatus.BLOCKED_UNOWNED_PATH: "workspace path is unowned",
        WorkspaceCandidateStatus.BLOCKED_DIRTY_WORKTREE: (
            "workspace has unexported changes; use force with a reason"
        ),
        WorkspaceCandidateStatus.BLOCKED_FAILED_OR_RECOVERY: (
            "failed workspace requires force with a reason"
        ),
        WorkspaceCandidateStatus.BLOCKED_RECOVERY_REQUIRED: (
            "recovery_required workspace is retained as recovery evidence"
        ),
        WorkspaceCandidateStatus.BLOCKED_BRANCH_MISMATCH: (
            "integration branch does not match expected run branch"
        ),
        WorkspaceCandidateStatus.BLOCKED_MISSING_OWNER: (
            "workspace ownership marker is missing or mismatched"
        ),
        WorkspaceCandidateStatus.BLOCKED_PATH_ESCAPE: (
            "workspace path is outside the managed workspace root"
        ),
        WorkspaceCandidateStatus.BLOCKED_NOT_MANAGED: (
            "run does not have a managed workspace"
        ),
        WorkspaceCandidateStatus.BLOCKED_BRANCH_IN_USE: (
            "integration branch is in use by another worktree"
        ),
        WorkspaceCandidateStatus.BLOCKED_GIT_ERROR: "git inspection failed",
    }
    return reasons[status]
