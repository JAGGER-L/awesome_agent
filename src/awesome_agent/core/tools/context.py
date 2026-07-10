from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import BaseModel

from awesome_agent.core.events import EventEmitter
from awesome_agent.core.tools.contracts import ToolOutput
from awesome_agent.core.workspace import WorkspaceIdentity


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    workspace: WorkspaceIdentity
    operation_id: str
    turn_id: str | None
    emitter: EventEmitter
    change_set_id: str | None = None
    allowed_interaction_scopes: frozenset[str] = frozenset()


type ToolHandler = Callable[
    [BaseModel, ToolExecutionContext],
    Awaitable[ToolOutput],
]
