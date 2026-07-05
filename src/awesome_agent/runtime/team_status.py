from __future__ import annotations

from typing import TypedDict
from uuid import UUID

from pydantic import BaseModel, Field

from awesome_agent.domain.enums import AgentKind, DispatchStatus, RunStatus
from awesome_agent.domain.models import Agent, Run
from awesome_agent.extensions.models import ExtensionCatalog
from awesome_agent.persistence.approval_contracts import (
    ApprovalRepository,
    DurableApproval,
)
from awesome_agent.persistence.team import TeamRepository
from awesome_agent.runtime.capabilities import CapabilityPurpose, CapabilityResolver
from awesome_agent.runtime.repository import RuntimeRepository
from awesome_agent.runtime.team_assignments import TeamAssignment, TeamChildResult


class TeamStatusNode(BaseModel):
    run_id: str
    assignment_id: str | None = None
    parent_run_id: str | None = None
    role: str
    profile: str
    status: str
    dispatch_status: str | None = None
    runtime_route: str | None = None
    can_write: bool = False
    can_delegate: bool = False
    allowed_tools: list[str] = Field(default_factory=list)
    effective_tools: list[str] = Field(default_factory=list)
    denied_tools: list[dict[str, object]] = Field(default_factory=list)
    pending_approval: dict[str, object] | None = None
    workspace_isolated: bool | None = None
    workspace_summary: str | None = None
    waiting_reason: str | None = None
    result_summary: str | None = None
    failure_kind: str | None = None
    patch_aggregated: bool = False
    changed_files: list[str] = Field(default_factory=list)
    children: list[TeamStatusNode] = Field(default_factory=list)


class TeamStatusTree(BaseModel):
    root_run_id: str
    status: str
    phase: str
    nodes_total: int
    active_nodes: int
    failed_nodes: int
    waiting_nodes: int
    root: TeamStatusNode


class _ToolPayload(TypedDict):
    effective_tools: list[str]
    denied_tools: list[dict[str, object]]


async def build_team_status_tree(
    *,
    runtime: RuntimeRepository,
    teams: TeamRepository,
    run_id: UUID,
    catalog: ExtensionCatalog | None = None,
    approval_repository: ApprovalRepository | None = None,
) -> TeamStatusTree:
    requested = await runtime.get_run(run_id)
    root_id = requested.root_run_id or requested.id
    root = requested if requested.id == root_id else await runtime.get_run(root_id)
    runs = [root, *(await runtime.list_descendant_runs(root.id))]
    assignments = await teams.list_assignments(root.id, include_inactive=True)
    assignment_by_child = {
        assignment.child_run_id: assignment for assignment in assignments
    }
    pending_approvals_by_run = await _pending_approvals_by_run(
        runs,
        approval_repository=approval_repository,
    )
    results_by_child: dict[UUID, TeamChildResult] = {}
    agents_by_run: dict[UUID, Agent] = {}
    for run in runs:
        for result in await teams.list_child_results(run.id):
            results_by_child[result.child_run_id] = result
        agents = await runtime.list_agents(run.id)
        if agents:
            agents_by_run[run.id] = agents[0]
    children_by_parent: dict[UUID, list[Run]] = {}
    for run in runs:
        if run.parent_run_id is not None:
            children_by_parent.setdefault(run.parent_run_id, []).append(run)
    runs_by_id = {run.id: run for run in runs}

    def build_node(run: Run) -> TeamStatusNode:
        assignment = assignment_by_child.get(run.id)
        result = results_by_child.get(run.id)
        agent = agents_by_run.get(run.id)
        pending_approval = _pending_approval_payload(
            pending_approvals_by_run.get(run.id, [])
        )
        tool_payload = _tool_payload(assignment, catalog=catalog)
        workspace_isolated, workspace_summary = _workspace_payload(
            run,
            parent=runs_by_id.get(run.parent_run_id) if run.parent_run_id else None,
        )
        children = [
            build_node(child)
            for child in sorted(
                children_by_parent.get(run.id, []),
                key=lambda item: (item.depth, item.created_at, item.id.hex),
            )
        ]
        return TeamStatusNode(
            run_id=str(run.id),
            assignment_id=str(assignment.id) if assignment is not None else None,
            parent_run_id=str(run.parent_run_id) if run.parent_run_id else None,
            role=_role(run, assignment, agent),
            profile=_profile(run, assignment, agent),
            status=run.status.value,
            dispatch_status=run.dispatch_status.value,
            runtime_route=run.runtime_route,
            can_write=assignment.can_write if assignment is not None else False,
            can_delegate=assignment.can_delegate if assignment is not None else False,
            allowed_tools=list(assignment.allowed_tools) if assignment else [],
            effective_tools=tool_payload["effective_tools"],
            denied_tools=tool_payload["denied_tools"],
            pending_approval=pending_approval,
            workspace_isolated=workspace_isolated,
            workspace_summary=workspace_summary,
            waiting_reason=_waiting_reason(run, pending_approval=pending_approval),
            result_summary=result.summary if result is not None else run.result_text,
            failure_kind=result.failure_kind if result is not None else None,
            patch_aggregated=result.patch_aggregated if result is not None else False,
            changed_files=list(result.changed_files) if result is not None else [],
            children=children,
        )

    root_node = build_node(root)
    flat_nodes = _flatten(root_node)
    return TeamStatusTree(
        root_run_id=str(root.id),
        status=root.status.value,
        phase=_phase(root),
        nodes_total=len(flat_nodes),
        active_nodes=sum(1 for node in flat_nodes if _is_active(node)),
        failed_nodes=sum(1 for node in flat_nodes if _is_failed(node)),
        waiting_nodes=sum(1 for node in flat_nodes if _is_waiting(node)),
        root=root_node,
    )


async def _pending_approvals_by_run(
    runs: list[Run],
    *,
    approval_repository: ApprovalRepository | None,
) -> dict[UUID, list[DurableApproval]]:
    if approval_repository is None:
        return {}
    pending: dict[UUID, list[DurableApproval]] = {}
    for run in runs:
        items = await approval_repository.list_for_run(run.id)
        active = [item for item in items if item.status.value == "pending"]
        if active:
            pending[run.id] = active
    return pending


def _pending_approval_payload(
    approvals: list[DurableApproval],
) -> dict[str, object] | None:
    if not approvals:
        return None
    approval = approvals[0]
    return {
        "approval_id": str(approval.id),
        "tool": approval.tool_name,
        "risk": approval.risk_level,
        "status": approval.status.value,
    }


def _tool_payload(
    assignment: TeamAssignment | None,
    *,
    catalog: ExtensionCatalog | None,
) -> _ToolPayload:
    if assignment is None:
        return {"effective_tools": [], "denied_tools": []}
    policy = CapabilityResolver().resolve_team_assignment(
        assignment,
        purpose=CapabilityPurpose.INSPECTION,
        catalog=catalog,
    )
    payload = policy.as_inspection_payload()
    raw_effective_tools = payload.get("effective_tools", [])
    raw_denied_tools = payload.get("denied_tools", [])
    effective_tools = [
        item
        for item in (
            raw_effective_tools if isinstance(raw_effective_tools, list) else []
        )
        if isinstance(item, str)
    ]
    denied_tools = [
        item
        for item in (raw_denied_tools if isinstance(raw_denied_tools, list) else [])
        if isinstance(item, dict)
    ]
    return {
        "effective_tools": effective_tools,
        "denied_tools": denied_tools,
    }


def _workspace_payload(
    run: Run,
    *,
    parent: Run | None,
) -> tuple[bool | None, str | None]:
    if run.workspace_path is None:
        return None, None
    state = run.workspace_state.value if run.workspace_state is not None else "unknown"
    if parent is None or parent.workspace_path is None:
        return False, f"root {state}"
    isolated = run.workspace_path.resolve() != parent.workspace_path.resolve()
    return isolated, f"{'isolated' if isolated else 'inherited'} {state}"


def _role(
    run: Run,
    assignment: TeamAssignment | None,
    agent: Agent | None,
) -> str:
    if assignment is not None:
        return assignment.kind.value
    if agent is not None:
        return agent.kind.value
    return run.child_role or AgentKind.LEADER.value


def _profile(
    run: Run,
    assignment: TeamAssignment | None,
    agent: Agent | None,
) -> str:
    if agent is not None:
        return agent.profile
    if assignment is not None:
        return assignment.role_profile
    return run.child_role or AgentKind.LEADER.value


def _waiting_reason(
    run: Run,
    *,
    pending_approval: dict[str, object] | None,
) -> str | None:
    if pending_approval is not None:
        return "waiting_approval"
    if run.dispatch_status is DispatchStatus.WAITING or run.status is RunStatus.WAITING:
        return run.last_release_reason or "waiting"
    return None


def _phase(run: Run) -> str:
    return run.last_release_reason or run.dispatch_status.value or run.status.value


def _flatten(node: TeamStatusNode) -> list[TeamStatusNode]:
    return [node, *(child for item in node.children for child in _flatten(item))]


def _is_active(node: TeamStatusNode) -> bool:
    return node.dispatch_status in {
        DispatchStatus.QUEUED.value,
        DispatchStatus.CLAIMED.value,
        DispatchStatus.EXECUTING.value,
        DispatchStatus.RETRY_SCHEDULED.value,
    }


def _is_failed(node: TeamStatusNode) -> bool:
    return node.status in {
        RunStatus.FAILED.value,
        RunStatus.RECOVERY_REQUIRED.value,
    } or node.failure_kind is not None


def _is_waiting(node: TeamStatusNode) -> bool:
    return node.status == RunStatus.WAITING.value or (
        node.dispatch_status == DispatchStatus.WAITING.value
    )
