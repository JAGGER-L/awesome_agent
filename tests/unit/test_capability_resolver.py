from uuid import uuid4

import pytest

from awesome_agent.domain.enums import RiskLevel
from awesome_agent.extensions.models import (
    ExtensionCatalog,
    ExtensionSourceSnapshot,
    ExtensionToolInventoryItem,
)
from awesome_agent.modeling import ToolCall
from awesome_agent.runtime.capabilities import (
    ALL_TEAM_TOOLS,
    WRITE_TEAM_TOOLS,
    CapabilityPurpose,
    CapabilityResolver,
    ToolDecisionReason,
)
from awesome_agent.runtime.team_assignments import TeamAssignment, TeamAssignmentKind
from awesome_agent.runtime.team_planning import TeamPlanTeammate
from awesome_agent.runtime.tool_exposure import (
    ToolExposureSet,
    before_tool_call,
    resolve_tool_exposure,
)


def test_team_policy_hides_deferred_and_reports_capabilities() -> None:
    assignment = _assignment(
        allowed_tools=["repo.read", "repo.apply_patch", "shell.execute"],
        deferred_tools=["repo.apply_patch", "shell.execute"],
        promoted_tools=["repo.apply_patch"],
        can_write=True,
    )

    policy = CapabilityResolver().resolve_team_assignment(
        assignment,
        purpose=CapabilityPurpose.ROLE_EXECUTION,
    )

    assert policy.tool_names == ("repo.read", "repo.apply_patch")
    assert policy.capabilities_for("repo.read") == frozenset({"repository:read"})
    assert policy.capabilities_for("repo.apply_patch") == frozenset(
        {"repository:write"}
    )
    assert policy.denied_reason("shell.execute") is ToolDecisionReason.DEFERRED


def test_read_only_assignment_denies_write_tools_even_if_granted() -> None:
    assignment = _assignment(
        allowed_tools=["repo.read", "repo.apply_patch", "shell.execute"],
        can_write=False,
    )

    policy = CapabilityResolver().resolve_team_assignment(
        assignment,
        purpose=CapabilityPurpose.ROLE_EXECUTION,
    )

    assert policy.tool_names == ("repo.read",)
    assert policy.denied_reason("repo.apply_patch") is ToolDecisionReason.REQUIRES_WRITE
    assert policy.denied_reason("shell.execute") is ToolDecisionReason.REQUIRES_WRITE


def test_teammate_control_tools_require_assignment_authority() -> None:
    assignment = _assignment(
        allowed_tools=[
            "repo.read",
            "team.create_subagent",
            "team.mailbox_list",
            "team.mailbox_send",
        ],
        can_delegate=False,
        max_subagents=0,
    )

    policy = CapabilityResolver().resolve_team_assignment(
        assignment,
        purpose=CapabilityPurpose.ROLE_EXECUTION,
    )

    assert policy.tool_names == ("repo.read", "team.mailbox_list", "team.mailbox_send")
    assert (
        policy.denied_reason("team.create_subagent")
        is ToolDecisionReason.REQUIRES_DELEGATION
    )
    assert policy.capabilities_for("team.mailbox_send") == frozenset({"team:mailbox"})


def test_subagent_cannot_receive_mailbox_delegation_or_write_tools() -> None:
    assignment = _assignment(
        kind=TeamAssignmentKind.SUBAGENT,
        allowed_tools=[
            "repo.read",
            "repo.apply_patch",
            "team.create_subagent",
            "team.mailbox_send",
        ],
        can_write=True,
        can_delegate=True,
        max_subagents=1,
    )

    policy = CapabilityResolver().resolve_team_assignment(
        assignment,
        purpose=CapabilityPurpose.ROLE_EXECUTION,
    )

    assert policy.tool_names == ("repo.read",)
    assert (
        policy.denied_reason("team.mailbox_send")
        is ToolDecisionReason.ACTOR_KIND_DENIED
    )
    assert (
        policy.denied_reason("team.create_subagent")
        is ToolDecisionReason.ACTOR_KIND_DENIED
    )
    assert policy.denied_reason("repo.apply_patch") is ToolDecisionReason.REQUIRES_WRITE


def test_subagent_grant_keeps_only_read_only_repository_subset() -> None:
    parent = _assignment(
        allowed_tools=[
            "repo.read",
            "repo.diff",
            "repo.apply_patch",
            "team.mailbox_send",
            "team.create_subagent",
        ],
        can_write=True,
        can_delegate=True,
        max_subagents=2,
    )

    policy = CapabilityResolver().resolve_team_assignment(
        parent,
        purpose=CapabilityPurpose.SUBAGENT_GRANT,
        requested_tools=[
            "repo.read",
            "repo.diff",
            "repo.apply_patch",
            "team.mailbox_send",
            "team.create_subagent",
        ],
    )

    assert policy.tool_names == ("repo.read", "repo.diff")
    assert policy.denied_reason("repo.apply_patch") is ToolDecisionReason.SUBAGENT_SCOPE
    assert (
        policy.denied_reason("team.mailbox_send") is ToolDecisionReason.SUBAGENT_SCOPE
    )
    assert (
        policy.denied_reason("team.create_subagent")
        is ToolDecisionReason.SUBAGENT_SCOPE
    )


def test_verifier_review_intersects_assignment_with_verifier_tool_subset() -> None:
    assignment = _assignment(
        kind=TeamAssignmentKind.VERIFIER,
        allowed_tools=[
            "repo.status",
            "repo.diff",
            "repo.read",
            "repo.search",
            "repo.apply_patch",
            "team.mailbox_send",
        ],
        can_write=True,
    )

    policy = CapabilityResolver().resolve_team_assignment(
        assignment,
        purpose=CapabilityPurpose.VERIFIER_REVIEW,
    )

    assert policy.tool_names == ("repo.status", "repo.diff", "repo.read", "repo.search")
    assert policy.denied_reason("repo.apply_patch") is ToolDecisionReason.VERIFIER_SCOPE
    assert (
        policy.denied_reason("team.mailbox_send") is ToolDecisionReason.VERIFIER_SCOPE
    )


def test_unknown_tools_are_denied_with_reason() -> None:
    assignment = _assignment(allowed_tools=["repo.read", "unknown.tool"])

    policy = CapabilityResolver().resolve_team_assignment(
        assignment,
        purpose=CapabilityPurpose.ROLE_EXECUTION,
    )

    assert policy.tool_names == ("repo.read",)
    assert policy.denied_reason("unknown.tool") is ToolDecisionReason.UNKNOWN_TOOL


def test_extension_tool_hidden_without_assignment_grant() -> None:
    exposure = resolve_tool_exposure(
        policy=CapabilityResolver().resolve_team_assignment(
            _assignment(allowed_tools=["repo.search"]),
            purpose=CapabilityPurpose.ROLE_EXECUTION,
        ),
        catalog=_catalog_with_tool("extension.local-demo.demo.search"),
    )

    assert not exposure.allows("extension.local-demo.demo.search")
    assert (
        exposure.denied_reason("extension.local-demo.demo.search")
        is ToolDecisionReason.NOT_ASSIGNED
    )


def test_extension_tool_exposed_with_assignment_grant_and_catalog_inventory() -> None:
    catalog = _catalog_with_tool("extension.local-demo.demo.search")
    exposure = resolve_tool_exposure(
        policy=CapabilityResolver().resolve_team_assignment(
            _assignment(allowed_tools=["extension.local-demo.demo.search"]),
            purpose=CapabilityPurpose.ROLE_EXECUTION,
            catalog=catalog,
        ),
        catalog=catalog,
    )

    assert exposure.allows("extension.local-demo.demo.search")
    assert exposure.capabilities_for(
        "extension.local-demo.demo.search"
    ) == frozenset({"repository:read"})
    assert [tool.name for tool in exposure.tool_definitions] == [
        "extension.local-demo.demo.search"
    ]


@pytest.mark.asyncio
async def test_tool_call_denies_not_exposed_tool() -> None:
    exposure = ToolExposureSet(
        catalog_version="ext_123",
        decisions=(),
        tool_definitions=(),
    )

    result = await before_tool_call(
        ToolCall(
            call_id="call-1",
            name="extension.local-demo.demo.search",
            arguments_json="{}",
        ),
        exposure,
    )

    assert result.status == "denied"
    assert result.reason == "not_exposed"


def test_team_planning_uses_shared_capability_catalog() -> None:
    teammate = TeamPlanTeammate(
        role_profile="backend",
        goal="Read and coordinate",
        allowed_tools=["repo.read", "team.mailbox_send"],
        deferred_tools=[],
        allowed_skills=[],
        can_write=False,
        can_delegate=False,
        max_subagents=0,
        acceptance_criteria=["Return evidence."],
    )

    assert "team.mailbox_send" in ALL_TEAM_TOOLS
    assert "repo.apply_patch" in WRITE_TEAM_TOOLS
    assert teammate.allowed_tools == ["repo.read", "team.mailbox_send"]


def _catalog_with_tool(tool_name: str) -> ExtensionCatalog:
    return ExtensionCatalog(
        version="ext_123",
        sources=[
            ExtensionSourceSnapshot(id="local-demo", type="static", trust="project")
        ],
        tools=[
            ExtensionToolInventoryItem(
                name=tool_name,
                source_id="local-demo",
                description="Search demo content.",
                risk_level=RiskLevel.LOW,
                required_capabilities={"repository:read"},
                input_schema={"type": "object"},
            )
        ],
    )


def _assignment(
    *,
    kind: TeamAssignmentKind = TeamAssignmentKind.TEAMMATE,
    allowed_tools: list[str] | None = None,
    deferred_tools: list[str] | None = None,
    promoted_tools: list[str] | None = None,
    can_write: bool = False,
    can_delegate: bool = False,
    max_subagents: int = 0,
) -> TeamAssignment:
    root_run_id = uuid4()
    route = "team-verifier" if kind is TeamAssignmentKind.VERIFIER else "team-role"
    return TeamAssignment(
        root_run_id=root_run_id,
        parent_run_id=root_run_id,
        child_run_id=uuid4(),
        kind=kind,
        role_profile=kind.value,
        runtime_route=route,
        goal="test assignment",
        allowed_tools=allowed_tools or [],
        deferred_tools=deferred_tools or [],
        promoted_tools=promoted_tools or [],
        can_write=can_write,
        can_delegate=can_delegate,
        max_subagents=max_subagents,
    )
