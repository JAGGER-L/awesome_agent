from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from awesome_agent.domain.enums import ApprovalStatus
from awesome_agent.persistence.approval_contracts import DurableApproval
from awesome_agent.tools.guardrails import parse_patch_paths
from awesome_agent.tools.workspace import WorkspaceToolError, parse_bash_command

ApprovalGrantResourceKind = Literal[
    "shell.argv",
    "repo.patch_paths",
    "repository.file_paths",
]


@dataclass(frozen=True, slots=True)
class ApprovalGrantScope:
    tool_name: str
    tool_version: str
    workspace_path: str
    capabilities: tuple[str, ...]
    risk_level: str
    resource_kind: ApprovalGrantResourceKind
    resources: tuple[str, ...]

    def payload(self) -> dict[str, object]:
        return {
            "tool": self.tool_name,
            "tool_version": self.tool_version,
            "resource_kind": self.resource_kind,
            "resource_count": len(self.resources),
            "resource_fingerprint": _fingerprint(self.resources),
            "workspace_path": self.workspace_path,
            "capabilities": list(self.capabilities),
            "risk_level": self.risk_level,
        }


@dataclass(frozen=True, slots=True)
class ApprovalGrantMatch:
    approval: DurableApproval
    scope: ApprovalGrantScope


def approval_grant_scope_from_arguments(
    *,
    tool_name: str,
    tool_version: str,
    arguments: dict[str, object],
    workspace: Path,
    capabilities: tuple[str, ...] | list[str] | set[str] | frozenset[str],
    risk_level: str,
) -> ApprovalGrantScope | None:
    resource_kind: ApprovalGrantResourceKind
    resources: tuple[str, ...]
    if tool_name == "shell.execute":
        argv = arguments.get("argv")
        if not isinstance(argv, list) or not all(
            isinstance(item, str) for item in argv
        ):
            return None
        resource_kind = "shell.argv"
        resources = tuple(argv)
    elif tool_name == "Bash":
        command = arguments.get("command")
        if not isinstance(command, str):
            return None
        try:
            resources = tuple(parse_bash_command(command))
        except WorkspaceToolError:
            return None
        resource_kind = "shell.argv"
    elif tool_name == "repo.apply_patch":
        patch = arguments.get("patch")
        if not isinstance(patch, str):
            return None
        paths = parse_patch_paths(patch)
        if not paths:
            return None
        resource_kind = "repo.patch_paths"
        resources = tuple(sorted(path.as_posix() for path in paths))
    elif tool_name in {"WriteFile", "EditFile"}:
        path = arguments.get("path")
        if not isinstance(path, str):
            return None
        relative = Path(path)
        if relative.is_absolute() or ".." in relative.parts or ".git" in relative.parts:
            return None
        resource_kind = "repository.file_paths"
        resources = (relative.as_posix(),)
    else:
        return None

    return ApprovalGrantScope(
        tool_name=tool_name,
        tool_version=tool_version,
        workspace_path=str(workspace.resolve()),
        capabilities=tuple(sorted(capabilities)),
        risk_level=risk_level,
        resource_kind=resource_kind,
        resources=resources,
    )


def find_matching_approval_grant(
    approvals: list[DurableApproval],
    *,
    requested_scope: ApprovalGrantScope,
    now: datetime | None = None,
) -> ApprovalGrantMatch | None:
    effective_now = now or datetime.now(UTC)
    matches = [
        ApprovalGrantMatch(approval=approval, scope=scope)
        for approval in approvals
        if _grant_status_is_reusable(approval, effective_now)
        for scope in [_scope_from_approval(approval)]
        if scope is not None and _scope_matches(scope, requested_scope)
    ]
    if not matches:
        return None
    return sorted(
        matches,
        key=lambda match: (match.approval.decided_at or match.approval.updated_at),
    )[-1]


def approval_matches_grant_scope(
    approval: DurableApproval,
    requested_scope: ApprovalGrantScope,
    *,
    now: datetime | None = None,
) -> bool:
    return (
        find_matching_approval_grant(
            [approval],
            requested_scope=requested_scope,
            now=now,
        )
        is not None
    )


def _scope_from_approval(approval: DurableApproval) -> ApprovalGrantScope | None:
    return approval_grant_scope_from_arguments(
        tool_name=approval.tool_name,
        tool_version=approval.tool_version,
        arguments=approval.canonical_arguments,
        workspace=Path(approval.workspace_path),
        capabilities=approval.capabilities,
        risk_level=approval.risk_level,
    )


def _scope_matches(
    granted: ApprovalGrantScope,
    requested: ApprovalGrantScope,
) -> bool:
    return granted == requested


def _grant_status_is_reusable(approval: DurableApproval, now: datetime) -> bool:
    return (
        approval.status in {ApprovalStatus.APPROVED, ApprovalStatus.DENIED}
        and approval.expires_at > now
    )


def _fingerprint(resources: tuple[str, ...]) -> str:
    return hashlib.sha256(
        json.dumps(
            list(resources),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
