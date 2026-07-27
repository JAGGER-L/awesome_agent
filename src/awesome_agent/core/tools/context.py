from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from pydantic import BaseModel

from awesome_agent.core.citations import CitationAllocator
from awesome_agent.core.events import EventEmitter
from awesome_agent.core.tools.contracts import (
    ToolActivityWriter,
    ToolErrorCode,
    ToolExecutionOrigin,
    ToolOutput,
)
from awesome_agent.core.tools.errors import ExpectedToolFailure
from awesome_agent.core.tools.permissions import (
    PermissionSession,
    ToolApprovalDecision,
    ToolApprovalRequest,
)
from awesome_agent.core.workspace import WorkspaceIdentity

type ToolApprovalResolver = Callable[
    [ToolApprovalRequest], Awaitable[ToolApprovalDecision]
]


class CapabilityQuotaLedger:
    """Synchronous per-context capability quotas with readable usage deltas."""

    __slots__ = ("_limits", "_used_counts")

    def __init__(
        self,
        limits: Mapping[str, int] | None = None,
        *,
        used_counts: Mapping[str, int] | None = None,
    ) -> None:
        self._limits = self._validated_counts(limits or {}, label="limit")
        self._used_counts = self._validated_counts(
            used_counts or {},
            label="used count",
        )
        unknown = self._used_counts.keys() - self._limits.keys()
        if unknown:
            raise ValueError("quota usage requires a configured capability limit")
        if any(
            used > self._limits[capability]
            for capability, used in self._used_counts.items()
        ):
            raise ValueError("quota usage must not exceed its capability limit")

    @property
    def limits(self) -> Mapping[str, int]:
        return MappingProxyType(dict(self._limits))

    @property
    def used_counts(self) -> Mapping[str, int]:
        return MappingProxyType(dict(self._used_counts))

    def used(self, capability: str) -> int:
        return self._used_counts.get(capability, 0)

    def remaining(self, capability: str) -> int:
        return max(0, self._limits.get(capability, 0) - self.used(capability))

    def require_remaining(self, capability: str, amount: int = 1) -> None:
        self._validate_amount(amount)
        if self.remaining(capability) < amount:
            raise ExpectedToolFailure(
                ToolErrorCode.WEB_REQUEST_BUDGET_EXHAUSTED,
                "The web request budget is exhausted.",
                metadata={"capability": capability},
            )

    def consume(self, capability: str, amount: int = 1) -> int:
        self.require_remaining(capability, amount)
        used = self.used(capability) + amount
        self._used_counts[capability] = used
        return used

    @staticmethod
    def _validated_counts(
        values: Mapping[str, int],
        *,
        label: str,
    ) -> dict[str, int]:
        validated: dict[str, int] = {}
        for capability, value in values.items():
            if not capability:
                raise ValueError("quota capability must not be empty")
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"quota {label} must be a non-negative integer")
            validated[capability] = value
        return validated

    @staticmethod
    def _validate_amount(amount: int) -> None:
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise ValueError("quota amount must be a positive integer")


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
    capability_quotas: CapabilityQuotaLedger = field(
        default_factory=CapabilityQuotaLedger
    )
    citation_allocator: CitationAllocator = field(default_factory=CitationAllocator)
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
