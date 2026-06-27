from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import cast
from uuid import UUID

from awesome_agent.domain.enums import (
    DispatchStatus,
    EventType,
    RunStatus,
    WorkspaceRetentionStatus,
)
from awesome_agent.domain.models import Run
from awesome_agent.repositories.policy import normalize_path
from awesome_agent.repositories.registry import RepositoryRegistry
from awesome_agent.repositories.worktrees import (
    ManagedRunWorktreeManager,
    ManagedWorktreeError,
    WorktreeOwnership,
)
from awesome_agent.runtime.repository import RuntimeRepository


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


class WorkspaceRetentionService:
    def __init__(
        self,
        *,
        runtime_repository: RuntimeRepository,
        repository_registry: RepositoryRegistry,
        worktrees: ManagedRunWorktreeManager,
    ) -> None:
        self.runtime_repository = runtime_repository
        self.repository_registry = repository_registry
        self.worktrees = worktrees

    def validate_cleanup_options(self, *, force: bool, reason: str | None) -> None:
        if force and not (reason and reason.strip()):
            raise ValueError("force workspace cleanup requires a reason.")

    async def list_candidates(
        self,
        request: WorkspaceCleanupRequest | None = None,
    ) -> list[WorkspaceCandidate]:
        request = request or WorkspaceCleanupRequest()
        self.validate_cleanup_options(force=request.force, reason=request.reason)
        runs = await self.runtime_repository.list_runs()
        if request.run_id is not None:
            runs = [run for run in runs if run.id == request.run_id]
        if request.older_than is not None:
            cutoff = datetime.now(UTC) - request.older_than
            runs = [run for run in runs if run.updated_at <= cutoff]
        candidates: list[WorkspaceCandidate] = []
        for run in runs:
            if run.workspace_path is None:
                continue
            candidates.append(await self.evaluate_run(run, force=request.force))
        return candidates

    async def cleanup_preview(
        self,
        request: WorkspaceCleanupRequest,
    ) -> list[WorkspaceCandidate]:
        preview_request = WorkspaceCleanupRequest(
            run_id=request.run_id,
            older_than=request.older_than,
            apply=False,
            force=request.force,
            reason=request.reason,
        )
        return await self.list_candidates(preview_request)

    async def cleanup(
        self,
        request: WorkspaceCleanupRequest,
    ) -> list[WorkspaceCandidate]:
        if not request.apply:
            return await self.cleanup_preview(request)
        candidates = await self.list_candidates(request)
        results: list[WorkspaceCandidate] = []
        for candidate in candidates:
            if not candidate.can_cleanup:
                await self.runtime_repository.update_workspace_retention(
                    candidate.run_id,
                    status=candidate.retention_status,
                    reason=candidate.reason,
                )
                results.append(candidate)
                continue
            results.append(await self._cleanup_candidate(candidate, request))
        return results

    async def evaluate_run(self, run: Run, *, force: bool) -> WorkspaceCandidate:
        if not _has_managed_workspace_identity(run):
            return _candidate(
                run,
                WorkspaceCandidateStatus.BLOCKED_NOT_MANAGED,
                dirty=None,
            )
        repository_id = cast(UUID, run.repository_id)
        workspace_path = cast(Path, run.workspace_path)
        integration_branch = cast(str, run.integration_branch)
        try:
            repository = await self.repository_registry.get(repository_id)
            workspace = normalize_path(workspace_path)
            owner = self.worktrees.read_owner(repository_id, run.id)
            owner_matches = _owner_matches(run, owner)
            path_in_managed_root = workspace.is_relative_to(
                self.worktrees.workspace_root
            )
            if not path_in_managed_root or not owner_matches:
                inspection = WorkspaceInspection(
                    owner_matches=owner_matches,
                    path_in_managed_root=path_in_managed_root,
                    worktree_exists=workspace.exists(),
                    branch_matches=False,
                    branch_in_use_elsewhere=False,
                    dirty=None,
                )
                return evaluate_workspace_candidate(run, inspection, force=force)

            entry = await self.worktrees.worktree_entry(repository.root, workspace)
            entries = await self.worktrees.worktree_entries(repository.root)
            worktree_exists = entry is not None
            branch_matches = entry == (run.base_commit, integration_branch)
            if entry is not None and entry[1] == integration_branch:
                branch_matches = True
            branch_in_use_elsewhere = any(
                path != workspace and data[1] == integration_branch
                for path, data in entries.items()
            )
            dirty = (
                await self.worktrees.is_dirty(workspace) if worktree_exists else None
            )
            inspection = WorkspaceInspection(
                owner_matches=owner_matches,
                path_in_managed_root=path_in_managed_root,
                worktree_exists=worktree_exists,
                branch_matches=branch_matches,
                branch_in_use_elsewhere=branch_in_use_elsewhere,
                dirty=dirty,
            )
            return evaluate_workspace_candidate(run, inspection, force=force)
        except (KeyError, ManagedWorktreeError) as error:
            inspection = WorkspaceInspection(
                owner_matches=True,
                path_in_managed_root=True,
                worktree_exists=True,
                branch_matches=True,
                branch_in_use_elsewhere=False,
                dirty=None,
                git_error=str(error),
            )
            return evaluate_workspace_candidate(run, inspection, force=force)

    async def _cleanup_candidate(
        self,
        candidate: WorkspaceCandidate,
        request: WorkspaceCleanupRequest,
    ) -> WorkspaceCandidate:
        run = await self.runtime_repository.get_run(candidate.run_id)
        checked = await self.evaluate_run(run, force=request.force)
        if not checked.can_cleanup:
            await self.runtime_repository.update_workspace_retention(
                checked.run_id,
                status=checked.retention_status,
                reason=checked.reason,
            )
            return checked
        if not _has_managed_workspace_identity(run):
            return _candidate(
                run,
                WorkspaceCandidateStatus.BLOCKED_NOT_MANAGED,
                dirty=checked.dirty,
            )
        repository_id = cast(UUID, run.repository_id)
        workspace_path = cast(Path, run.workspace_path)
        integration_branch = cast(str, run.integration_branch)
        repository = await self.repository_registry.get(repository_id)
        await self.worktrees.remove_worktree(
            repository=repository.root,
            target=workspace_path,
            force=bool(checked.dirty),
        )
        entries = await self.worktrees.worktree_entries(repository.root)
        if any(data[1] == integration_branch for data in entries.values()):
            blocked = _candidate(
                run,
                WorkspaceCandidateStatus.BLOCKED_BRANCH_IN_USE,
                dirty=checked.dirty,
            )
            await self.runtime_repository.update_workspace_retention(
                run.id,
                status=blocked.retention_status,
                reason=blocked.reason,
            )
            return blocked
        await self.worktrees.delete_branch(
            repository=repository.root,
            branch=integration_branch,
        )
        self.worktrees.remove_owner(repository_id, run.id)
        cleaned_at = datetime.now(UTC)
        cleaned = _candidate(
            run,
            WorkspaceCandidateStatus.CLEANED,
            dirty=False,
        )
        await self.runtime_repository.update_workspace_retention(
            run.id,
            status=WorkspaceRetentionStatus.CLEANED,
            reason=request.reason or cleaned.reason,
            cleaned_at=cleaned_at,
        )
        await self.runtime_repository.append_event(
            run_id=run.id,
            event_type=EventType.WORKSPACE_CLEANED,
            payload={
                "run_id": str(run.id),
                "workspace_path": str(run.workspace_path),
                "branch": run.integration_branch,
                "status": WorkspaceCandidateStatus.CLEANED.value,
                "force": request.force,
                "reason": request.reason or cleaned.reason,
            },
        )
        return cleaned


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


def _has_managed_workspace_identity(run: Run) -> bool:
    return (
        run.repository_id is not None
        and run.base_commit is not None
        and run.workspace_path is not None
        and run.integration_branch is not None
    )


def _owner_matches(run: Run, owner: WorktreeOwnership | None) -> bool:
    if owner is None or not _has_managed_workspace_identity(run):
        return False
    return (
        owner.run_id == str(run.id)
        and owner.repository_id == str(run.repository_id)
        and owner.integration_branch == run.integration_branch
        and owner.base_commit == run.base_commit
    )


def _candidate(
    run: Run,
    status: WorkspaceCandidateStatus,
    *,
    dirty: bool | None,
) -> WorkspaceCandidate:
    return WorkspaceCandidate(
        run_id=run.id,
        repository_id=run.repository_id,
        workspace_path=run.workspace_path,
        branch=run.integration_branch,
        status=status,
        retention_status=_retention_status(status),
        reason=_reason(status),
        dirty=dirty,
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
