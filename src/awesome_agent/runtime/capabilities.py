from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from awesome_agent.extensions.models import ExtensionCatalog
from awesome_agent.runtime.team_assignments import TeamAssignment, TeamAssignmentKind

READ_ONLY_TEAM_TOOLS = frozenset(
    {
        "repo.status",
        "repo.list",
        "repo.search",
        "repo.read",
        "repo.instructions",
        "repo.diff",
    }
)
WRITE_TEAM_TOOLS = frozenset({"repo.apply_patch", "shell.execute"})
TEAM_CONTROL_TOOLS = frozenset({"team.create_subagent"})
TEAM_MAILBOX_TOOLS = frozenset({"team.mailbox_list", "team.mailbox_send"})
ALL_TEAM_TOOLS = (
    READ_ONLY_TEAM_TOOLS | WRITE_TEAM_TOOLS | TEAM_CONTROL_TOOLS | TEAM_MAILBOX_TOOLS
)
VERIFIER_REVIEW_TOOLS = frozenset(
    {"repo.status", "repo.diff", "repo.read", "repo.search"}
)

_TOOL_CAPABILITIES: Mapping[str, frozenset[str]] = {
    "repo.status": frozenset({"repository:read"}),
    "repo.list": frozenset({"repository:read"}),
    "repo.search": frozenset({"repository:read"}),
    "repo.read": frozenset({"repository:read"}),
    "repo.instructions": frozenset({"repository:read"}),
    "repo.diff": frozenset({"repository:read"}),
    "repo.apply_patch": frozenset({"repository:write"}),
    "shell.execute": frozenset({"shell:execute"}),
    "team.create_subagent": frozenset({"team:delegate"}),
    "team.mailbox_list": frozenset({"team:mailbox"}),
    "team.mailbox_send": frozenset({"team:mailbox"}),
}


class CapabilityPurpose(StrEnum):
    ROLE_EXECUTION = "role_execution"
    SUBAGENT_GRANT = "subagent_grant"
    VERIFIER_REVIEW = "verifier_review"
    INSPECTION = "inspection"


class ToolDecisionReason(StrEnum):
    GRANTED = "granted"
    DEFERRED = "deferred"
    NOT_ASSIGNED = "not_assigned"
    UNKNOWN_TOOL = "unknown_tool"
    REQUIRES_WRITE = "requires_write"
    REQUIRES_DELEGATION = "requires_delegation"
    ACTOR_KIND_DENIED = "actor_kind_denied"
    SUBAGENT_SCOPE = "subagent_scope"
    VERIFIER_SCOPE = "verifier_scope"
    NOT_EXPOSED = "not_exposed"
    SOURCE_UNHEALTHY = "source_unhealthy"
    CATALOG_MISSING = "catalog_missing"


@dataclass(frozen=True, slots=True)
class ToolPolicyDecision:
    tool_name: str
    allowed: bool
    reason: ToolDecisionReason
    required_capabilities: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class EffectiveToolPolicy:
    decisions: tuple[ToolPolicyDecision, ...]

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(
            decision.tool_name for decision in self.decisions if decision.allowed
        )

    @property
    def effective_capabilities(self) -> frozenset[str]:
        capabilities: set[str] = set()
        for decision in self.decisions:
            if decision.allowed:
                capabilities.update(decision.required_capabilities)
        return frozenset(capabilities)

    @property
    def denied(self) -> tuple[ToolPolicyDecision, ...]:
        return tuple(decision for decision in self.decisions if not decision.allowed)

    def allows(self, tool_name: str) -> bool:
        return any(
            decision.tool_name == tool_name and decision.allowed
            for decision in self.decisions
        )

    def capabilities_for(self, tool_name: str) -> frozenset[str]:
        for decision in self.decisions:
            if decision.tool_name == tool_name and decision.allowed:
                return decision.required_capabilities
        return frozenset()

    def denied_reason(self, tool_name: str) -> ToolDecisionReason | None:
        for decision in self.decisions:
            if decision.tool_name == tool_name and not decision.allowed:
                return decision.reason
        return None

    def as_inspection_payload(self) -> dict[str, object]:
        return {
            "effective_tools": list(self.tool_names),
            "effective_capabilities": sorted(self.effective_capabilities),
            "denied_tools": [
                {
                    "tool": decision.tool_name,
                    "reason": decision.reason.value,
                    "required_capabilities": sorted(decision.required_capabilities),
                }
                for decision in self.denied
            ],
        }


@dataclass(frozen=True, slots=True)
class TeamCapabilityRequest:
    assignment: TeamAssignment
    purpose: CapabilityPurpose = CapabilityPurpose.ROLE_EXECUTION
    requested_tools: Sequence[str] | None = None
    catalog: ExtensionCatalog | None = None


@dataclass(frozen=True, slots=True)
class CapabilityResolver:
    known_team_tools: frozenset[str] = field(default=ALL_TEAM_TOOLS)

    def resolve_team_assignment(
        self,
        assignment: TeamAssignment,
        *,
        purpose: CapabilityPurpose = CapabilityPurpose.ROLE_EXECUTION,
        requested_tools: Sequence[str] | None = None,
        catalog: ExtensionCatalog | None = None,
    ) -> EffectiveToolPolicy:
        request = TeamCapabilityRequest(
            assignment=assignment,
            purpose=purpose,
            requested_tools=requested_tools,
            catalog=catalog,
        )
        return EffectiveToolPolicy(
            tuple(
                self._decision_for(request, tool_name)
                for tool_name in _unique_tools(_tools_to_evaluate(request))
            )
        )

    def _decision_for(
        self,
        request: TeamCapabilityRequest,
        tool_name: str,
    ) -> ToolPolicyDecision:
        assignment = request.assignment
        if tool_name not in self.known_team_tools and not _catalog_has_tool(
            request.catalog,
            tool_name,
        ):
            return _denied(
                tool_name,
                ToolDecisionReason.UNKNOWN_TOOL,
                catalog=request.catalog,
            )

        effective_grants = set(_effective_grants(assignment))
        if tool_name not in effective_grants:
            hidden = set(assignment.deferred_tools) - set(assignment.promoted_tools)
            reason = (
                ToolDecisionReason.DEFERRED
                if tool_name in hidden
                else ToolDecisionReason.NOT_ASSIGNED
            )
            return _denied(tool_name, reason, catalog=request.catalog)

        if (
            request.purpose is CapabilityPurpose.SUBAGENT_GRANT
            and tool_name not in READ_ONLY_TEAM_TOOLS
        ):
            return _denied(
                tool_name,
                ToolDecisionReason.SUBAGENT_SCOPE,
                catalog=request.catalog,
            )
        if (
            request.purpose is CapabilityPurpose.VERIFIER_REVIEW
            and tool_name not in VERIFIER_REVIEW_TOOLS
        ):
            return _denied(
                tool_name,
                ToolDecisionReason.VERIFIER_SCOPE,
                catalog=request.catalog,
            )
        if assignment.kind is TeamAssignmentKind.SUBAGENT and (
            tool_name not in READ_ONLY_TEAM_TOOLS
        ):
            reason = (
                ToolDecisionReason.REQUIRES_WRITE
                if tool_name in WRITE_TEAM_TOOLS
                else ToolDecisionReason.ACTOR_KIND_DENIED
            )
            return _denied(tool_name, reason, catalog=request.catalog)
        if (
            assignment.kind is TeamAssignmentKind.VERIFIER
            and tool_name not in VERIFIER_REVIEW_TOOLS
        ):
            return _denied(
                tool_name,
                ToolDecisionReason.VERIFIER_SCOPE,
                catalog=request.catalog,
            )
        if tool_name in WRITE_TEAM_TOOLS and not assignment.can_write:
            return _denied(
                tool_name,
                ToolDecisionReason.REQUIRES_WRITE,
                catalog=request.catalog,
            )
        if tool_name == "team.create_subagent" and (
            assignment.kind is not TeamAssignmentKind.TEAMMATE
            or not assignment.can_delegate
            or assignment.max_subagents < 1
        ):
            reason = (
                ToolDecisionReason.ACTOR_KIND_DENIED
                if assignment.kind is not TeamAssignmentKind.TEAMMATE
                else ToolDecisionReason.REQUIRES_DELEGATION
            )
            return _denied(tool_name, reason, catalog=request.catalog)
        if (
            tool_name in TEAM_MAILBOX_TOOLS
            and assignment.kind is not TeamAssignmentKind.TEAMMATE
        ):
            return _denied(
                tool_name,
                ToolDecisionReason.ACTOR_KIND_DENIED,
                catalog=request.catalog,
            )
        return ToolPolicyDecision(
            tool_name=tool_name,
            allowed=True,
            reason=ToolDecisionReason.GRANTED,
            required_capabilities=_tool_capabilities(
                tool_name,
                catalog=request.catalog,
            ),
        )


def _tools_to_evaluate(request: TeamCapabilityRequest) -> list[str]:
    if request.requested_tools is not None:
        return [
            *request.assignment.allowed_tools,
            *request.assignment.deferred_tools,
            *request.requested_tools,
        ]
    return [*request.assignment.allowed_tools, *request.assignment.deferred_tools]


def _effective_grants(assignment: TeamAssignment) -> list[str]:
    deferred = set(assignment.deferred_tools)
    promoted = set(assignment.promoted_tools)
    hidden = deferred - promoted
    return [tool for tool in assignment.allowed_tools if tool not in hidden]


def _unique_tools(tool_names: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for tool_name in tool_names:
        if tool_name not in seen:
            seen.add(tool_name)
            ordered.append(tool_name)
    return tuple(ordered)


def _catalog_has_tool(catalog: ExtensionCatalog | None, tool_name: str) -> bool:
    return catalog is not None and any(tool.name == tool_name for tool in catalog.tools)


def _tool_capabilities(
    tool_name: str,
    *,
    catalog: ExtensionCatalog | None,
) -> frozenset[str]:
    if tool_name in _TOOL_CAPABILITIES:
        return _TOOL_CAPABILITIES[tool_name]
    if catalog is not None:
        for tool in catalog.tools:
            if tool.name == tool_name:
                return frozenset(tool.required_capabilities)
    return frozenset()


def _denied(
    tool_name: str,
    reason: ToolDecisionReason,
    *,
    catalog: ExtensionCatalog | None = None,
) -> ToolPolicyDecision:
    return ToolPolicyDecision(
        tool_name=tool_name,
        allowed=False,
        reason=reason,
        required_capabilities=_tool_capabilities(tool_name, catalog=catalog),
    )
