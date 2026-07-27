from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ToolCapability(StrEnum):
    WORKSPACE_READ = "workspace.read"
    WORKSPACE_WRITE = "workspace.write"
    WORKSPACE_DELETE = "workspace.delete"
    SHELL_EXECUTE = "shell.execute"
    NETWORK_READ = "network.read"


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
    ALLOW_THREAD_NETWORK = "allow_thread_network"
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
    _thread_granted_capabilities: set[tuple[str, str]] = field(
        default_factory=set,
        init=False,
        repr=False,
    )

    def grant_thread_writes(self) -> None:
        self.granted_capabilities.add(ToolCapability.WORKSPACE_WRITE.value)

    @property
    def thread_granted_capabilities(self) -> frozenset[tuple[str, str]]:
        return frozenset(self._thread_granted_capabilities)

    def grant_thread_network(self, thread_id: str) -> None:
        if not thread_id:
            raise ValueError("thread_id must not be empty")
        self._thread_granted_capabilities.add(
            (thread_id, ToolCapability.NETWORK_READ.value)
        )

    def revoke_thread_network(self, thread_id: str | None = None) -> None:
        capability = ToolCapability.NETWORK_READ.value
        self._thread_granted_capabilities = {
            grant
            for grant in self._thread_granted_capabilities
            if grant[1] != capability
            or (thread_id is not None and grant[0] != thread_id)
        }

    def reset(self) -> None:
        self.set_mode(PermissionMode.REQUEST_APPROVAL)

    def set_mode(self, mode: PermissionMode) -> None:
        self.mode = mode
        self.granted_capabilities.clear()
        self._thread_granted_capabilities.clear()
        self.generation += 1


@dataclass(frozen=True, slots=True)
class PolicyRequest:
    capability: ToolCapability | str
    mode: PermissionMode
    granted_capabilities: frozenset[ToolCapability | str] = frozenset()
    thread_id: str | None = None
    granted_thread_capabilities: frozenset[tuple[str, str]] = frozenset()


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
        if capability == ToolCapability.NETWORK_READ:
            thread_grant = (request.thread_id, capability)
            if request.thread_id is not None and thread_grant in (
                request.granted_thread_capabilities
            ):
                return PolicyDecision(
                    PolicyAction.ALLOW,
                    "The active Thread explicitly allows network reads.",
                )
            return PolicyDecision(
                PolicyAction.ASK,
                "Network reads require explicit approval for the active Thread.",
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
