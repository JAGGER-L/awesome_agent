from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from awesome_agent.domain.enums import RiskLevel
from awesome_agent.extensions.models import ExtensionCatalog, ExtensionToolInventoryItem
from awesome_agent.modeling import ToolCall, ToolDefinition
from awesome_agent.runtime.capabilities import (
    EffectiveToolPolicy,
    ToolDecisionReason,
)


class ToolExposureDecision(BaseModel):
    tool_name: str
    exposed: bool
    reason: ToolDecisionReason
    source_id: str | None = None
    required_capabilities: set[str] = Field(default_factory=set)
    risk_level: RiskLevel | None = None


class ToolExposureSet(BaseModel):
    catalog_version: str
    decisions: tuple[ToolExposureDecision, ...] = ()
    tool_definitions: tuple[ToolDefinition, ...] = ()

    @property
    def tool_names(self) -> tuple[str, ...]:
        return tuple(
            decision.tool_name for decision in self.decisions if decision.exposed
        )

    def allows(self, tool_name: str) -> bool:
        return any(
            decision.tool_name == tool_name and decision.exposed
            for decision in self.decisions
        )

    def capabilities_for(self, tool_name: str) -> frozenset[str]:
        for decision in self.decisions:
            if decision.tool_name == tool_name and decision.exposed:
                return frozenset(decision.required_capabilities)
        return frozenset()

    def denied_reason(self, tool_name: str) -> ToolDecisionReason | None:
        for decision in self.decisions:
            if decision.tool_name == tool_name and not decision.exposed:
                return decision.reason
        return None

    def as_inspection_payload(self) -> dict[str, object]:
        return {
            "catalog_version": self.catalog_version,
            "effective_tools": list(self.tool_names),
            "denied_tools": [
                {
                    "tool": decision.tool_name,
                    "reason": decision.reason.value,
                    "source_id": decision.source_id,
                    "required_capabilities": sorted(decision.required_capabilities),
                }
                for decision in self.decisions
                if not decision.exposed
            ],
        }


class ToolCallExposureDecision(BaseModel):
    status: Literal["allowed", "denied"]
    reason: str


def resolve_tool_exposure(
    *,
    policy: EffectiveToolPolicy,
    catalog: ExtensionCatalog,
    tool_definitions: list[ToolDefinition] | tuple[ToolDefinition, ...] = (),
) -> ToolExposureSet:
    decisions = [
        ToolExposureDecision(
            tool_name=decision.tool_name,
            exposed=decision.allowed,
            reason=decision.reason,
            required_capabilities=set(decision.required_capabilities),
        )
        for decision in policy.decisions
    ]
    decided_tool_names = {decision.tool_name for decision in decisions}
    for tool in catalog.tools:
        if tool.name in decided_tool_names:
            continue
        decisions.append(_not_assigned_extension_decision(tool))
    exposed = {decision.tool_name for decision in decisions if decision.exposed}
    definitions = [
        definition for definition in tool_definitions if definition.name in exposed
    ]
    definitions.extend(
        _extension_tool_definition(tool)
        for tool in catalog.tools
        if tool.name in exposed
    )
    return ToolExposureSet(
        catalog_version=catalog.version,
        decisions=tuple(decisions),
        tool_definitions=tuple(definitions),
    )


def expose_builtin_tools(
    *,
    catalog_version: str,
    tool_definitions: list[ToolDefinition] | tuple[ToolDefinition, ...],
) -> ToolExposureSet:
    return ToolExposureSet(
        catalog_version=catalog_version,
        decisions=tuple(
            ToolExposureDecision(
                tool_name=definition.name,
                exposed=True,
                reason=ToolDecisionReason.GRANTED,
            )
            for definition in tool_definitions
        ),
        tool_definitions=tuple(tool_definitions),
    )


async def before_tool_call(
    call: ToolCall,
    exposure: ToolExposureSet,
) -> ToolCallExposureDecision:
    if not exposure.allows(call.name):
        return ToolCallExposureDecision(
            status="denied",
            reason=ToolDecisionReason.NOT_EXPOSED.value,
        )
    return ToolCallExposureDecision(status="allowed", reason="exposed")


def _not_assigned_extension_decision(
    tool: ExtensionToolInventoryItem,
) -> ToolExposureDecision:
    return ToolExposureDecision(
        tool_name=tool.name,
        exposed=False,
        reason=ToolDecisionReason.NOT_ASSIGNED,
        source_id=tool.source_id,
        required_capabilities=set(tool.required_capabilities),
        risk_level=tool.risk_level,
    )


def _extension_tool_definition(tool: ExtensionToolInventoryItem) -> ToolDefinition:
    return ToolDefinition(
        name=tool.name,
        description=tool.description,
        input_schema=tool.input_schema,
    )
