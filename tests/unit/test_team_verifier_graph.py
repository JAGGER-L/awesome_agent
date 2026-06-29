import json
from collections.abc import Awaitable, Callable
from uuid import uuid4

import pytest
from tests.fakes import FakeModelProvider

from awesome_agent.agents.profiles import RoleModelResolver
from awesome_agent.domain.enums import AgentKind, DispatchStatus, RunIntent, RunMode
from awesome_agent.domain.models import Agent, Run
from awesome_agent.persistence.team import InMemoryTeamRepository
from awesome_agent.runtime.agent_loop import (
    MiddlewareContext,
    MiddlewareDecision,
    MiddlewareStack,
    MiddlewareStage,
    TeamAgentLoop,
)
from awesome_agent.runtime.dispatch import PermanentExecutionError
from awesome_agent.runtime.graphs import TEAM_CODING_ROUTE, TEAM_VERIFIER_ROUTE
from awesome_agent.runtime.repository import InMemoryRuntimeRepository
from awesome_agent.runtime.team_assignments import (
    TeamAssignment,
    TeamAssignmentKind,
    TeamAssignmentStatus,
    TeamChildResult,
)
from awesome_agent.runtime.team_leader_graph import TeamLeaderGraph
from awesome_agent.runtime.team_mailbox import MailboxRoute
from awesome_agent.runtime.team_verifier_graph import (
    TeamVerifierGraph,
    verifier_external_retry_budget,
    verifier_model_rejection_budget,
)


@pytest.mark.asyncio
async def test_verifier_passes_aggregated_child_results() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    provider = FakeModelProvider([_decision("passed", "Verifier passed evidence.")])
    graph = TeamVerifierGraph(
        team_repository=teams,
        provider_resolver=lambda _: provider,
    )
    parent = Run(goal="parent", mode=RunMode.TEAM)
    verifier, agent, assignment = _verifier_run(parent)
    await runtime.create_run(
        parent,
        Agent(
            run_id=parent.id,
            kind=AgentKind.LEADER,
            profile="leader",
            model="fake",
        ),
    )
    await runtime.create_run(verifier, agent)
    await teams.create_assignment(assignment)
    teammate = await _teammate_assignment(teams, parent)
    await teams.record_child_result(
        TeamChildResult(
            assignment_id=teammate.id,
            child_run_id=teammate.child_run_id,
            parent_run_id=parent.id,
            root_run_id=parent.id,
            status="completed",
            summary="done",
            patch_aggregated=True,
        )
    )

    state, recovered = await graph.execute(verifier, agent, repository=runtime)

    result = next(
        item
        for item in await teams.list_child_results(parent.id)
        if item.child_run_id == verifier.id
    )
    mailbox = await teams.list_mailbox_messages(parent.id)
    assert not recovered
    assert state["phase"] == "passed"
    assert result.child_run_id == verifier.id
    assert result.status == "completed"
    assert result.summary == "Verifier passed evidence."
    assert mailbox[-1].route is MailboxRoute.VERIFIER_TO_LEADER
    assert len(provider.requests) == 1


@pytest.mark.asyncio
async def test_verifier_model_decision_uses_team_agent_loop_boundary() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    provider = FakeModelProvider([_decision("passed", "Verifier passed evidence.")])
    recorder = RecordingTeamMiddleware()
    graph = TeamVerifierGraph(
        team_repository=teams,
        provider_resolver=lambda _: provider,
        team_loop=TeamAgentLoop(middleware_stack=MiddlewareStack([recorder])),
    )
    parent = Run(goal="parent", mode=RunMode.TEAM)
    verifier, agent, assignment = _verifier_run(parent)
    await runtime.create_run(
        parent,
        Agent(
            run_id=parent.id,
            kind=AgentKind.LEADER,
            profile="leader",
            model="fake",
        ),
    )
    await runtime.create_run(verifier, agent)
    await teams.create_assignment(assignment)
    teammate = await _teammate_assignment(teams, parent)
    await teams.record_child_result(
        TeamChildResult(
            assignment_id=teammate.id,
            child_run_id=teammate.child_run_id,
            parent_run_id=parent.id,
            root_run_id=parent.id,
            status="completed",
            summary="done",
            patch_aggregated=True,
        )
    )

    state, recovered = await graph.execute(verifier, agent, repository=runtime)

    assert not recovered
    assert state["phase"] == "passed"
    assert recorder.model_call_metadata == [
        {
            "runtime_route": "team-verifier",
            "team_root_run_id": str(parent.id),
            "assignment_id": str(assignment.id),
            "team_role": "verifier",
            "agent_kind": "verifier",
            "team_operation": "verification",
            "attempt": 1,
        }
    ]
    assert "independent Verifier" in recorder.model_prompt_text
    assert "Verifier passed evidence" not in recorder.model_metadata_text


@pytest.mark.asyncio
async def test_verifier_rework_decision_is_persisted_and_fails_child() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    parent = Run(goal="parent", mode=RunMode.TEAM)
    target = Run(goal="teammate", mode=RunMode.TEAM)
    provider = FakeModelProvider(
        [
            _decision(
                "rework_required",
                "Needs one more check.",
                rework_requests=[
                    {
                        "target_child_run_id": str(target.id),
                        "reason": "Missing README evidence.",
                        "acceptance_criteria": ["Read README.md."],
                    }
                ],
            )
        ]
    )
    graph = TeamVerifierGraph(
        team_repository=teams,
        provider_resolver=lambda _: provider,
    )
    verifier, agent, assignment = _verifier_run(parent)
    await runtime.create_run(
        parent,
        Agent(
            run_id=parent.id,
            kind=AgentKind.LEADER,
            profile="leader",
            model="fake",
        ),
    )
    await runtime.create_run(verifier, agent)
    await teams.create_assignment(assignment)
    await teams.create_assignment(
        TeamAssignment(
            root_run_id=parent.id,
            parent_run_id=parent.id,
            child_run_id=target.id,
            kind=TeamAssignmentKind.TEAMMATE,
            role_profile="backend-engineer",
            runtime_route="team-role",
            goal="inspect",
        )
    )
    await teams.record_child_result(
        TeamChildResult(
            assignment_id=assignment.id,
            child_run_id=target.id,
            parent_run_id=parent.id,
            root_run_id=parent.id,
            status="completed",
            summary="done",
            patch_aggregated=True,
        )
    )

    with pytest.raises(PermanentExecutionError, match="team_verification_rework"):
        await graph.execute(verifier, agent, repository=runtime)

    result = next(
        item
        for item in await teams.list_child_results(parent.id)
        if item.child_run_id == verifier.id
    )
    mailbox = await teams.list_mailbox_messages(parent.id)
    assert result.status == "failed"
    assert result.failure_kind == "rework_required"
    assert mailbox[-1].requires_response


@pytest.mark.asyncio
async def test_verifier_rejects_unaggregated_patch_even_if_model_passes() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    provider = FakeModelProvider([_decision("passed", "Looks good.")])
    graph = TeamVerifierGraph(
        team_repository=teams,
        provider_resolver=lambda _: provider,
    )
    parent = Run(goal="parent", mode=RunMode.TEAM)
    verifier, agent, assignment = _verifier_run(parent)
    await runtime.create_run(
        parent,
        Agent(
            run_id=parent.id,
            kind=AgentKind.LEADER,
            profile="leader",
            model="fake",
        ),
    )
    await runtime.create_run(verifier, agent)
    await teams.create_assignment(assignment)
    teammate = await _teammate_assignment(teams, parent)
    await teams.record_child_result(
        TeamChildResult(
            assignment_id=teammate.id,
            child_run_id=teammate.child_run_id,
            parent_run_id=parent.id,
            root_run_id=parent.id,
            status="completed",
            summary="patch not aggregated",
            patch_artifact_id=Run(goal="artifact").id,
            patch_aggregated=False,
        )
    )

    with pytest.raises(PermanentExecutionError, match="unaggregated_patch"):
        await graph.execute(verifier, agent, repository=runtime)


@pytest.mark.asyncio
async def test_verifier_ignores_superseded_patch_conflict_result() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    provider = FakeModelProvider([_decision("passed", "Replacement looks good.")])
    graph = TeamVerifierGraph(
        team_repository=teams,
        provider_resolver=lambda _: provider,
    )
    parent = Run(goal="parent", mode=RunMode.TEAM)
    verifier, agent, assignment = _verifier_run(parent)
    await runtime.create_run(
        parent,
        Agent(
            run_id=parent.id,
            kind=AgentKind.LEADER,
            profile="leader",
            model="fake",
        ),
    )
    await runtime.create_run(verifier, agent)
    await teams.create_assignment(assignment)
    original = await _teammate_assignment(teams, parent)
    replacement = TeamAssignment(
        root_run_id=parent.id,
        parent_run_id=parent.id,
        child_run_id=uuid4(),
        kind=TeamAssignmentKind.TEAMMATE,
        status=TeamAssignmentStatus.COMPLETED,
        role_profile="backend",
        runtime_route="team-role",
        goal="replacement",
        handoff_context={
            "rework_reason": "patch_conflict",
            "previous_assignment_id": str(original.id),
            "previous_child_run_id": str(original.child_run_id),
            "rework_attempt": 1,
        },
    )
    await teams.create_assignment(replacement)
    await teams.record_child_result(
        TeamChildResult(
            assignment_id=original.id,
            child_run_id=original.child_run_id,
            parent_run_id=parent.id,
            root_run_id=parent.id,
            status="recovery_required",
            summary="Old patch conflict.",
            patch_artifact_id=uuid4(),
            patch_aggregated=False,
            failure_kind="patch_conflict",
        )
    )
    await teams.record_child_result(
        TeamChildResult(
            assignment_id=replacement.id,
            child_run_id=replacement.child_run_id,
            parent_run_id=parent.id,
            root_run_id=parent.id,
            status="completed",
            summary="Replacement patch aggregated.",
            patch_artifact_id=uuid4(),
            patch_aggregated=True,
        )
    )

    state, recovered = await graph.execute(verifier, agent, repository=runtime)

    payload = json.loads(provider.requests[0].messages[-1].content)
    assert not recovered
    assert state["phase"] == "passed"
    assert [item["child_run_id"] for item in payload["child_results"]] == [
        str(replacement.child_run_id)
    ]


@pytest.mark.asyncio
async def test_verifier_invalid_output_retries_once_then_fails() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    provider = FakeModelProvider(["not-json", "still-not-json"])
    graph = TeamVerifierGraph(
        team_repository=teams,
        provider_resolver=lambda _: provider,
    )
    parent = Run(goal="parent", mode=RunMode.TEAM)
    verifier, agent, assignment = _verifier_run(parent)
    await runtime.create_run(
        parent,
        Agent(
            run_id=parent.id,
            kind=AgentKind.LEADER,
            profile="leader",
            model="fake",
        ),
    )
    await runtime.create_run(verifier, agent)
    await teams.create_assignment(assignment)

    with pytest.raises(PermanentExecutionError, match="team_verifier_invalid_output"):
        await graph.execute(verifier, agent, repository=runtime)

    result = next(
        item
        for item in await teams.list_child_results(parent.id)
        if item.child_run_id == verifier.id
    )
    assert len(provider.requests) == 2
    assert result.status == "failed"
    assert result.failure_kind == "model_output_failure"


@pytest.mark.asyncio
async def test_leader_creates_verifier_with_verifier_model() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    graph = TeamLeaderGraph(
        team_repository=teams,
        provider_resolver=lambda _: FakeModelProvider([_team_plan_json()]),
        model_resolver=RoleModelResolver(
            leader_model="leader-model",
            teammate_model="teammate-model",
            verifier_model="verifier-model",
            subagent_model="subagent-model",
        ),
    )
    root = Run(
        goal="root",
        mode=RunMode.TEAM,
        runtime_route=TEAM_CODING_ROUTE,
    )
    leader = Agent(
        run_id=root.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="leader-model",
    )
    await runtime.create_run(root, leader)
    teammate = Run(
        goal="done",
        mode=RunMode.TEAM,
        parent_run_id=root.id,
        root_run_id=root.id,
        depth=1,
        child_role="teammate",
        dispatch_status=DispatchStatus.TERMINAL,
    )
    await runtime.create_run(
        teammate,
        Agent(
            run_id=teammate.id,
            kind=AgentKind.TEAMMATE,
            profile="teammate",
            model="teammate-model",
        ),
    )
    teammate_assignment = TeamAssignment(
        root_run_id=root.id,
        parent_run_id=root.id,
        child_run_id=teammate.id,
        kind=TeamAssignmentKind.TEAMMATE,
        status=TeamAssignmentStatus.COMPLETED,
        role_profile="backend-engineer",
        runtime_route="team-role",
        goal="done",
    )
    await teams.create_assignment(teammate_assignment)
    await teams.record_child_result(
        TeamChildResult(
            assignment_id=teammate_assignment.id,
            child_run_id=teammate.id,
            parent_run_id=root.id,
            root_run_id=root.id,
            status="completed",
            summary="done",
            patch_aggregated=True,
        )
    )

    with pytest.raises(Exception, match="waiting_verifier"):
        await graph.execute(root, leader, repository=runtime)

    verifier = next(
        run
        for run in await runtime.list_child_runs(root.id)
        if run.child_role == "verifier"
    )
    verifier_agent = (await runtime.list_agents(verifier.id))[0]
    assert verifier_agent.model == "verifier-model"


def test_verifier_retry_budgets_are_explicit() -> None:
    assert verifier_model_rejection_budget() == 10
    assert verifier_external_retry_budget() == 1


def _verifier_run(parent: Run) -> tuple[Run, Agent, TeamAssignment]:
    run = Run(
        goal="verify",
        mode=RunMode.TEAM,
        intent=RunIntent.MODIFYING,
        parent_run_id=parent.id,
        root_run_id=parent.id,
        depth=1,
        child_role="verifier",
        runtime_route=TEAM_VERIFIER_ROUTE,
    )
    agent = Agent(
        run_id=run.id,
        kind=AgentKind.VERIFIER,
        profile="verifier",
        model="fake",
    )
    assignment = TeamAssignment(
        root_run_id=parent.id,
        parent_run_id=parent.id,
        child_run_id=run.id,
        kind=TeamAssignmentKind.VERIFIER,
        role_profile="verifier",
        runtime_route=TEAM_VERIFIER_ROUTE,
        goal="verify",
    )
    return run, agent, assignment


async def _teammate_assignment(
    teams: InMemoryTeamRepository,
    parent: Run,
) -> TeamAssignment:
    teammate = Run(goal="teammate", mode=RunMode.TEAM)
    assignment = TeamAssignment(
        root_run_id=parent.id,
        parent_run_id=parent.id,
        child_run_id=teammate.id,
        kind=TeamAssignmentKind.TEAMMATE,
        role_profile="backend-engineer",
        runtime_route="team-role",
        goal="inspect",
    )
    await teams.create_assignment(assignment)
    return assignment


def _decision(
    decision: str,
    summary: str,
    *,
    rework_requests: list[dict[str, object]] | None = None,
    failure_kind: str | None = None,
) -> str:
    import json

    return json.dumps(
        {
            "decision": decision,
            "summary": summary,
            "rework_requests": rework_requests or [],
            "failure_kind": failure_kind,
            "risks": [],
        }
    )


def _team_plan_json() -> str:
    import json

    return json.dumps(
        {
            "rationale": "One teammate.",
            "teammates": [
                {
                    "role_profile": "backend-engineer",
                    "goal": "Inspect.",
                    "allowed_tools": ["repo.read"],
                    "deferred_tools": [],
                    "allowed_skills": [],
                    "can_write": False,
                    "can_delegate": False,
                    "max_subagents": 0,
                    "acceptance_criteria": ["Done."],
                }
            ],
        }
    )


class RecordingTeamMiddleware:
    name = "recording-team-verifier"

    def __init__(self) -> None:
        self.model_call_metadata: list[dict[str, object]] = []
        self.model_prompt_text = ""

    @property
    def model_metadata_text(self) -> str:
        return str(self.model_call_metadata)

    async def handle(
        self,
        stage: MiddlewareStage,
        context: MiddlewareContext,
        call_next: Callable[[MiddlewareContext], Awaitable[MiddlewareDecision]],
    ) -> MiddlewareDecision:
        return await call_next(context)

    async def wrap_stage(
        self,
        stage: MiddlewareStage,
        context: MiddlewareContext,
        call_next: Callable[[MiddlewareContext], Awaitable[object]],
    ) -> object:
        if stage is MiddlewareStage.WRAP_MODEL_CALL:
            self.model_prompt_text = "\n".join(
                message.content for message in context.messages
            )
            self.model_call_metadata.append(dict(context.metadata))
        return await call_next(context)
