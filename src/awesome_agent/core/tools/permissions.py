from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ToolCapability(StrEnum):
    WORKSPACE_READ = "workspace.read"
    WORKSPACE_WRITE = "workspace.write"
    WORKSPACE_DELETE = "workspace.delete"
    SHELL_EXECUTE = "shell.execute"


class PermissionMode(StrEnum):
    REQUEST_APPROVAL = "request_approval"
    ACCEPT_EDITS = "accept_edits"
    FULL_ACCESS = "full_access"


class PolicyAction(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class ToolApprovalDecision(StrEnum):
    ALLOW_ONCE = "allow_once"
    ALLOW_THREAD_WRITES = "allow_thread_writes"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class ToolApprovalRequest:
    capability: str
    operation: str
    target: str
    prompt: str


@dataclass(slots=True)
class PermissionSession:
    mode: PermissionMode = PermissionMode.REQUEST_APPROVAL
    granted_capabilities: set[str] = field(default_factory=set)
    generation: int = 0

    def grant_thread_writes(self) -> None:
        self.granted_capabilities.add(ToolCapability.WORKSPACE_WRITE.value)

    def reset(self) -> None:
        self.set_mode(PermissionMode.REQUEST_APPROVAL)

    def set_mode(self, mode: PermissionMode) -> None:
        self.mode = mode
        self.granted_capabilities.clear()
        self.generation += 1


@dataclass(frozen=True, slots=True)
class PolicyRequest:
    capability: ToolCapability | str
    mode: PermissionMode
    granted_capabilities: frozenset[ToolCapability | str] = frozenset()


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    action: PolicyAction
    reason: str


class PermissionPolicy:
    """Pure capability policy evaluated after registration-owned admission."""

    def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        capability = str(request.capability)
        if capability in {"memory.read", "memory.write"}:
            return PolicyDecision(
                PolicyAction.ALLOW,
                "Built-in memory operations are governed by memory policy.",
            )
        known = {item.value for item in ToolCapability}
        granted = {str(item) for item in request.granted_capabilities}
        if capability not in known:
            return PolicyDecision(
                PolicyAction.ASK,
                "This extension capability requires explicit approval.",
            )
        if capability == ToolCapability.WORKSPACE_READ:
            return PolicyDecision(
                PolicyAction.ALLOW,
                "Workspace reads are allowed in a trusted workspace.",
            )
        if capability in granted:
            return PolicyDecision(
                PolicyAction.ALLOW,
                "The active permission session allows this capability.",
            )
        if (
            request.mode is PermissionMode.ACCEPT_EDITS
            and capability == ToolCapability.WORKSPACE_WRITE
        ):
            return PolicyDecision(
                PolicyAction.ALLOW,
                "Accept edits allows workspace file creation and modification.",
            )
        if request.mode is PermissionMode.FULL_ACCESS:
            return PolicyDecision(
                PolicyAction.ALLOW,
                "Full access allows this built-in local capability.",
            )
        return PolicyDecision(
            PolicyAction.ASK,
            "This operation requires approval in the active permission mode.",
        )
