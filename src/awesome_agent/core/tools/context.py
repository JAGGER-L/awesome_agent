from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from pydantic import BaseModel

from awesome_agent.core.events import EventEmitter
from awesome_agent.core.tools.contracts import (
    ToolActivityWriter,
    ToolExecutionOrigin,
    ToolOutput,
)
from awesome_agent.core.tools.permissions import (
    PermissionSession,
    ToolApprovalDecision,
    ToolApprovalRequest,
)
from awesome_agent.core.workspace import WorkspaceIdentity

type ToolApprovalResolver = Callable[
    [ToolApprovalRequest], Awaitable[ToolApprovalDecision]
]


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    workspace: WorkspaceIdentity
    thread_id: str
    operation_id: str
    turn_id: str | None
    origin: ToolExecutionOrigin
    emitter: EventEmitter
    activity_writer: ToolActivityWriter
    monotonic: Callable[[], float]
    change_set_id: str | None = None
    permission_session: PermissionSession = field(default_factory=PermissionSession)
    approval_resolver: ToolApprovalResolver | None = None
    turn_active: bool = True

    def __post_init__(self) -> None:
        if self.origin is ToolExecutionOrigin.AGENT and self.turn_id is None:
            raise ValueError("agent tool execution requires turn_id")
        if self.origin is ToolExecutionOrigin.DIRECT and self.turn_id is not None:
            raise ValueError("direct tool execution forbids turn_id")


type ToolHandler = Callable[
    [BaseModel, ToolExecutionContext],
    Awaitable[ToolOutput],
]
