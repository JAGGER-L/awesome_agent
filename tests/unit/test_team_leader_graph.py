import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import uuid4

import pytest
from tests.fakes import FakeModelProvider

from awesome_agent.agents.profiles import RoleModelResolver
from awesome_agent.artifacts.repository import InMemoryArtifactMetadataRepository
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.domain.enums import (
    AgentKind,
    DispatchStatus,
    EventType,
    ExecutionKind,
    RunIntent,
    RunMode,
    RunStatus,
)
from awesome_agent.domain.models import Agent, Run
from awesome_agent.persistence.budget import (
    InMemoryBudgetRepository,
    RunBudgetLedgerRecord,
)
from awesome_agent.persistence.team import InMemoryTeamRepository
from awesome_agent.runtime.agent_loop import (
    MiddlewareContext,
    MiddlewareDecision,
    MiddlewareStack,
    MiddlewareStage,
    TeamAgentLoop,
)
from awesome_agent.runtime.budget import BudgetPolicy
from awesome_agent.runtime.dispatch import ChildRunWait, PermanentExecutionError
from awesome_agent.runtime.graphs import (
    TEAM_CODING_ROUTE,
    TEAM_VERIFIER_ROUTE,
)
from awesome_agent.runtime.repository import InMemoryRuntimeRepository
from awesome_agent.runtime.team_assignments import (
    TeamAssignment,
    TeamAssignmentKind,
    TeamAssignmentStatus,
    TeamChildResult,
)
from awesome_agent.runtime.team_leader_graph import TeamLeaderGraph
from awesome_agent.runtime.team_recovery_policy import TeamRecoveryPolicy
from awesome_agent.runtime.team_rework import encode_rework_decision
from awesome_agent.runtime.team_verification import (
    TeamReworkRequest,
    TeamVerificationDecision,
)


@pytest.mark.asyncio
async def test_leader_creates_teammate_child_run_and_assignment() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    graph, provider = _graph(teams)
    run, leader = _leader_run()
    await runtime.create_run(run, leader)
    events: list[tuple[object, dict[str, object], str]] = []

    async def emit(
        event_type: object,
        payload: dict[str, object],
        transition_id: str,
    ) -> None:
        events.append((event_type, payload, transition_id))

    with pytest.raises(ChildRunWait, match="waiting_children"):
        await graph.execute(run, leader, repository=runtime, event_sink=emit)

    children = await runtime.list_child_runs(run.id)
    assignments = await teams.list_assignments(run.id)

    assert len(children) == 1
    assert children[0].parent_run_id == run.id
    assert children[0].root_run_id == run.id
    assert children[0].depth == 1
    assert children[0].dispatch_status is DispatchStatus.QUEUED
    assert children[0].extension_catalog_version == run.extension_catalog_version
    assert len(assignments) == 1
    assert assignments[0].kind is TeamAssignmentKind.TEAMMATE
    assert assignments[0].child_run_id == children[0].id
    assert assignments[0].role_profile == "backend-engineer"
    assert assignments[0].allowed_tools == ["repo.read", "repo.apply_patch"]
    assert assignments[0].deferred_tools == ["repo.apply_patch"]
    assert assignments[0].allowed_skills == ["python"]
    assert assignments[0].can_write
    assert assignments[0].can_delegate
    assert assignments[0].max_subagents == 3
    assert "subagent_goals" not in assignments[0].handoff_context
    child_agents = await runtime.list_agents(children[0].id)
    assert child_agents[0].profile == "backend-engineer"
    assert child_agents[0].model == "backend-model"
    assert len(provider.requests) == 1
    assert len(events) == 3
    assert events[0][0] is EventType.TEAM_PLAN_CREATED
    assert events[0][1]["teammate_count"] == 1
    assert events[1][1]["root_run_id"] == str(run.id)
    assert events[1][1]["parent_run_id"] == str(run.id)
    assert events[1][1]["child_run_id"] == str(children[0].id)
    assert events[1][1]["assignment_id"] == str(assignments[0].id)
    assert events[1][1]["agent_id"]


@pytest.mark.asyncio
async def test_leader_planning_uses_team_agent_loop_boundary() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    recorder = RecordingTeamMiddleware()
    team_loop = TeamAgentLoop(middleware_stack=MiddlewareStack([recorder]))
    graph, _ = _graph(teams, team_loop=team_loop)
    run, leader = _leader_run()
    await runtime.create_run(run, leader)

    with pytest.raises(ChildRunWait, match="waiting_children"):
        await graph.execute(run, leader, repository=runtime)

    children = await runtime.list_child_runs(run.id)
    assert len(children) == 1
    assert len(recorder.model_call_metadata) == 1
    assert {
        "runtime_route": "team-coding",
        "team_root_run_id": str(run.root_run_id or run.id),
        "team_role": "leader",
        "agent_kind": "leader",
        "team_operation": "planning",
        "attempt": 1,
    }.items() <= recorder.model_call_metadata[0].items()
    assert "Leader planning a coding-agent team" in recorder.model_prompt_text
    assert "subagent_goals" not in recorder.model_metadata_text


@pytest.mark.asyncio
async def test_leader_retries_invalid_team_plan_once() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    graph, provider = _graph(
        teams,
        responses=[
            _team_plan_json(extra={"subagent_goals": ["inspect evidence"]}),
            _team_plan_json(),
        ],
    )
    run, leader = _leader_run()
    await runtime.create_run(run, leader)
    events: list[tuple[object, dict[str, object], str]] = []

    async def emit(
        event_type: object,
        payload: dict[str, object],
        transition_id: str,
    ) -> None:
        events.append((event_type, payload, transition_id))

    with pytest.raises(ChildRunWait, match="waiting_children"):
        await graph.execute(run, leader, repository=runtime, event_sink=emit)

    assert len(provider.requests) == 2
    assert events[0][0] is EventType.TEAM_PLAN_REJECTED
    assert events[0][1]["attempt"] == 1
    assert events[1][0] is EventType.TEAM_PLAN_CREATED
    assert events[1][1]["attempt"] == 2


@pytest.mark.asyncio
async def test_leader_plan_can_grant_subagent_creation_tool() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    graph, provider = _graph(
        teams,
        responses=[
            _team_plan_json(
                extra={
                    "allowed_tools": ["repo.read", "team.create_subagent"],
                    "deferred_tools": [],
                    "can_write": False,
                    "can_delegate": True,
                    "max_subagents": 3,
                }
            )
        ],
    )
    run, leader = _leader_run()
    await runtime.create_run(run, leader)

    with pytest.raises(ChildRunWait, match="waiting_children"):
        await graph.execute(run, leader, repository=runtime)

    assignments = await teams.list_assignments(run.id)
    assert assignments[0].allowed_tools == ["repo.read", "team.create_subagent"]
    assert assignments[0].can_delegate is True
    assert len(provider.requests) == 1


@pytest.mark.asyncio
async def test_leader_plan_can_grant_mailbox_tools() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    graph, provider = _graph(
        teams,
        responses=[
            _team_plan_json(
                extra={
                    "allowed_tools": [
                        "repo.read",
                        "team.mailbox_list",
                        "team.mailbox_send",
                    ],
                    "deferred_tools": [],
                    "can_write": False,
                    "can_delegate": False,
                    "max_subagents": 0,
                }
            )
        ],
    )
    run, leader = _leader_run()
    await runtime.create_run(run, leader)

    with pytest.raises(ChildRunWait, match="waiting_children"):
        await graph.execute(run, leader, repository=runtime)

    assignments = await teams.list_assignments(run.id)
    assert assignments[0].allowed_tools == [
        "repo.read",
        "team.mailbox_list",
        "team.mailbox_send",
    ]
    assert len(provider.requests) == 1


@pytest.mark.asyncio
async def test_leader_fails_after_second_invalid_team_plan() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    invalid = _team_plan_json(extra={"delegation_guidance": "create a subagent"})
    graph, provider = _graph(teams, responses=[invalid, invalid])
    run, leader = _leader_run()
    await runtime.create_run(run, leader)

    with pytest.raises(PermanentExecutionError, match="team_plan_invalid"):
        await graph.execute(run, leader, repository=runtime)

    assert len(provider.requests) == 2
    assert await runtime.list_child_runs(run.id) == []
    assert await teams.list_assignments(run.id) == []


@pytest.mark.asyncio
async def test_leader_rejects_writing_teammate_for_read_only_root() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    graph, _ = _graph(teams, responses=[_team_plan_json(), _team_plan_json()])
    run, leader = _leader_run()
    run = run.model_copy(update={"intent": RunIntent.READ_ONLY})
    await runtime.create_run(run, leader)

    with pytest.raises(PermanentExecutionError, match="read-only"):
        await graph.execute(run, leader, repository=runtime)

    assert await runtime.list_child_runs(run.id) == []


@pytest.mark.asyncio
async def test_leader_stops_before_child_creation_when_team_budget_exhausted() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    budgets = InMemoryBudgetRepository()
    graph = TeamLeaderGraph(
        team_repository=teams,
        budget_repository=budgets,
        budget_policy=_tight_budget_policy(),
    )
    run, leader = _leader_run()
    await runtime.create_run(run, leader)
    await budgets.upsert_ledger(
        RunBudgetLedgerRecord(
            run_id=run.id,
            total_input_tokens=100,
            total_output_tokens=50,
        )
    )
    events: list[tuple[object, dict[str, object], str]] = []

    async def emit(
        event_type: object,
        payload: dict[str, object],
        transition_id: str,
    ) -> None:
        events.append((event_type, payload, transition_id))

    with pytest.raises(PermanentExecutionError, match="team_budget_exhausted"):
        await graph.execute(run, leader, repository=runtime, event_sink=emit)

    assert await runtime.list_child_runs(run.id) == []
    assert events[0][1]["root_run_id"] == str(run.id)
    assert events[0][1]["scope"] == "team_root"


@pytest.mark.asyncio
async def test_leader_wait_is_idempotent_while_child_is_active() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    graph, provider = _graph(teams)
    run, leader = _leader_run()
    await runtime.create_run(run, leader)

    with pytest.raises(ChildRunWait):
        await graph.execute(run, leader, repository=runtime)
    with pytest.raises(ChildRunWait):
        await graph.execute(run, leader, repository=runtime)

    assert len(await runtime.list_child_runs(run.id)) == 1
    assert len(await teams.list_assignments(run.id)) == 1
    assert len(provider.requests) == 1


@pytest.mark.asyncio
async def test_leader_creates_verifier_after_teammate_terminal() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    graph, _ = _graph(teams)
    run, leader = _leader_run()
    await runtime.create_run(run, leader)

    with pytest.raises(ChildRunWait):
        await graph.execute(run, leader, repository=runtime)
    assignment = (await teams.list_assignments(run.id))[0]
    await teams.record_child_terminal(
        assignment.child_run_id,
        status=TeamAssignmentStatus.COMPLETED,
    )

    with pytest.raises(ChildRunWait, match="waiting_verifier"):
        await graph.execute(run, leader, repository=runtime)

    assignments = await teams.list_assignments(run.id)
    verifier = next(
        item for item in assignments if item.kind is TeamAssignmentKind.VERIFIER
    )
    verifier_run = await runtime.get_run(verifier.child_run_id)
    assert verifier.runtime_route == TEAM_VERIFIER_ROUTE
    assert not hasattr(verifier, "graph_version")
    assert verifier_run.child_role == "verifier"


@pytest.mark.asyncio
async def test_leader_aggregates_child_patch_artifact(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("old\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-m", "Initial")
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    artifacts = InMemoryArtifactMetadataRepository()
    store = LocalArtifactStore(tmp_path / ".artifacts")
    graph = TeamLeaderGraph(
        team_repository=teams,
        artifact_repository=artifacts,
        model_resolver=_models(),
    )
    run, leader = _leader_run()
    run = run.model_copy(update={"workspace_path": tmp_path})
    await runtime.create_run(run, leader)
    child = Run(
        goal="child",
        mode=RunMode.TEAM,
        intent=RunIntent.MODIFYING,
        parent_run_id=run.id,
        root_run_id=run.id,
        depth=1,
        child_role="teammate",
        runtime_route="team-role",
    )
    await runtime.create_run(
        child,
        Agent(
            run_id=child.id,
            parent_agent_id=leader.id,
            kind=AgentKind.TEAMMATE,
            profile="teammate",
            model="fake",
        ),
    )
    assignment = TeamAssignment(
        root_run_id=run.id,
        parent_run_id=run.id,
        child_run_id=child.id,
        kind=TeamAssignmentKind.TEAMMATE,
        role_profile="teammate",
        runtime_route="team-role",
        goal="child",
    )
    await teams.create_assignment(assignment)
    metadata = store.write(
        run_id=child.id,
        agent_id=None,
        artifact_type="patch",
        filename="change.patch",
        content=(b"--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-old\n+new\n"),
        mime_type="text/x-diff",
        summary="README patch",
    )
    await artifacts.record(metadata)
    await teams.record_child_result(
        TeamChildResult(
            assignment_id=assignment.id,
            child_run_id=child.id,
            parent_run_id=run.id,
            root_run_id=run.id,
            status="completed",
            summary="Changed README.",
            patch_artifact_id=metadata.id,
            changed_files=["README.md"],
        )
    )
    await teams.record_child_terminal(
        child.id,
        status=TeamAssignmentStatus.COMPLETED,
    )
    events: list[tuple[object, dict[str, object], str]] = []

    async def emit(
        event_type: object,
        payload: dict[str, object],
        transition_id: str,
    ) -> None:
        events.append((event_type, payload, transition_id))

    with pytest.raises(ChildRunWait, match="waiting_verifier"):
        await graph.execute(run, leader, repository=runtime, event_sink=emit)

    result = (await teams.list_child_results(run.id))[0]
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "new\n"
    assert result.patch_aggregated
    assert any(event[0] is EventType.TEAM_PATCH_AGGREGATED for event in events)


@pytest.mark.asyncio
async def test_leader_creates_patch_conflict_rework_child(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("applied by sibling\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-m", "Initial")
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    artifacts = InMemoryArtifactMetadataRepository()
    store = LocalArtifactStore(tmp_path / ".artifacts")
    graph = TeamLeaderGraph(
        team_repository=teams,
        artifact_repository=artifacts,
        model_resolver=_models(),
    )
    run, leader = _leader_run()
    run = run.model_copy(update={"workspace_path": tmp_path})
    await runtime.create_run(run, leader)
    child = Run(
        goal="Patch README with conflicting content.",
        mode=RunMode.TEAM,
        intent=RunIntent.MODIFYING,
        parent_run_id=run.id,
        root_run_id=run.id,
        depth=1,
        child_role="teammate",
        runtime_route="team-role",
    )
    await runtime.create_run(
        child,
        Agent(
            run_id=child.id,
            parent_agent_id=leader.id,
            kind=AgentKind.TEAMMATE,
            profile="backend-engineer",
            model="backend-model",
        ),
    )
    assignment = TeamAssignment(
        root_run_id=run.id,
        parent_run_id=run.id,
        child_run_id=child.id,
        kind=TeamAssignmentKind.TEAMMATE,
        status=TeamAssignmentStatus.COMPLETED,
        role_profile="backend-engineer",
        runtime_route="team-role",
        goal="Patch README with conflicting content.",
        allowed_tools=["repo.apply_patch", "repo.diff"],
        can_write=True,
        acceptance_criteria=["Patch README.md."],
    )
    await teams.create_assignment(assignment)
    metadata = store.write(
        run_id=child.id,
        agent_id=None,
        artifact_type="patch",
        filename="conflict.patch",
        content=(
            b"diff --git a/README.md b/README.md\n"
            b"--- a/README.md\n"
            b"+++ b/README.md\n"
            b"@@ -1 +1 @@\n"
            b"-fixture\n"
            b"+conflicting child patch\n"
        ),
        mime_type="text/x-diff",
        summary="Conflicting README patch",
    )
    await artifacts.record(metadata)
    await teams.record_child_result(
        TeamChildResult(
            assignment_id=assignment.id,
            child_run_id=child.id,
            parent_run_id=run.id,
            root_run_id=run.id,
            status="completed",
            summary="Conflicting patch.",
            patch_artifact_id=metadata.id,
            changed_files=["README.md"],
        )
    )
    events: list[tuple[object, dict[str, object], str]] = []

    async def emit(
        event_type: object,
        payload: dict[str, object],
        transition_id: str,
    ) -> None:
        events.append((event_type, payload, transition_id))

    with pytest.raises(ChildRunWait, match="waiting_children"):
        await graph.execute(run, leader, repository=runtime, event_sink=emit)

    assignments = await teams.list_assignments(run.id, include_inactive=True)
    replacement = next(
        item
        for item in assignments
        if item.handoff_context.get("rework_reason") == "patch_conflict"
    )
    result = (await teams.list_child_results(run.id))[0]
    replacement_run = await runtime.get_run(replacement.child_run_id)
    assert replacement.allowed_tools == assignment.allowed_tools
    assert replacement.can_write is True
    assert replacement.acceptance_criteria == assignment.acceptance_criteria
    assert replacement.handoff_context["previous_assignment_id"] == str(assignment.id)
    assert replacement.handoff_context["previous_child_run_id"] == str(child.id)
    assert replacement.handoff_context["conflicting_patch_artifact_id"] == str(
        metadata.id
    )
    assert replacement.handoff_context["patch_conflict_kind"] == "patch_does_not_apply"
    assert replacement.handoff_context["rework_attempt"] == 1
    assert "Patch conflict detected" in replacement.goal
    assert replacement_run.dispatch_status is DispatchStatus.QUEUED
    assert result.status == "recovery_required"
    assert result.failure_kind == "patch_conflict"
    assert result.patch_aggregated is False
    assert EventType.TEAM_REWORK_REQUESTED in {event[0] for event in events}
    assert any(event[1].get("rework_reason") == "patch_conflict" for event in events)


@pytest.mark.asyncio
async def test_leader_skips_superseded_conflict_result_and_aggregates_replacement(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("team patch A\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-m", "Initial")
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    artifacts = InMemoryArtifactMetadataRepository()
    store = LocalArtifactStore(tmp_path / ".artifacts")
    graph = TeamLeaderGraph(
        team_repository=teams,
        artifact_repository=artifacts,
        model_resolver=_models(),
    )
    run, leader = _leader_run()
    run = run.model_copy(update={"workspace_path": tmp_path})
    await runtime.create_run(run, leader)
    original_child = Run(
        goal="original",
        mode=RunMode.TEAM,
        parent_run_id=run.id,
        root_run_id=run.id,
        depth=1,
        child_role="teammate",
        runtime_route="team-role",
    )
    replacement_child = Run(
        goal="replacement",
        mode=RunMode.TEAM,
        parent_run_id=run.id,
        root_run_id=run.id,
        depth=1,
        child_role="teammate",
        runtime_route="team-role",
    )
    for child in [original_child, replacement_child]:
        await runtime.create_run(
            child,
            Agent(
                run_id=child.id,
                parent_agent_id=leader.id,
                kind=AgentKind.TEAMMATE,
                profile="backend-engineer",
                model="backend-model",
            ),
        )
    original = TeamAssignment(
        root_run_id=run.id,
        parent_run_id=run.id,
        child_run_id=original_child.id,
        kind=TeamAssignmentKind.TEAMMATE,
        status=TeamAssignmentStatus.COMPLETED,
        role_profile="backend-engineer",
        runtime_route="team-role",
        goal="replace fixture with B",
    )
    replacement = TeamAssignment(
        root_run_id=run.id,
        parent_run_id=run.id,
        child_run_id=replacement_child.id,
        kind=TeamAssignmentKind.TEAMMATE,
        status=TeamAssignmentStatus.COMPLETED,
        role_profile="backend-engineer",
        runtime_route="team-role",
        goal="add B after A",
        handoff_context={
            "rework_reason": "patch_conflict",
            "previous_assignment_id": str(original.id),
            "previous_child_run_id": str(original_child.id),
            "rework_attempt": 1,
        },
    )
    await teams.create_assignment(original)
    await teams.create_assignment(replacement)
    original_patch = store.write(
        run_id=original_child.id,
        agent_id=None,
        artifact_type="patch",
        filename="old-conflict.patch",
        content=(
            b"diff --git a/README.md b/README.md\n"
            b"--- a/README.md\n"
            b"+++ b/README.md\n"
            b"@@ -1 +1 @@\n"
            b"-fixture\n"
            b"+team patch B\n"
        ),
        mime_type="text/x-diff",
        summary="Old conflicting patch",
    )
    replacement_patch = store.write(
        run_id=replacement_child.id,
        agent_id=None,
        artifact_type="patch",
        filename="replacement.patch",
        content=(
            b"diff --git a/README.md b/README.md\n"
            b"--- a/README.md\n"
            b"+++ b/README.md\n"
            b"@@ -1 +1,2 @@\n"
            b" team patch A\n"
            b"+team patch B\n"
        ),
        mime_type="text/x-diff",
        summary="Replacement patch",
    )
    await artifacts.record(original_patch)
    await artifacts.record(replacement_patch)
    await teams.record_child_result(
        TeamChildResult(
            assignment_id=original.id,
            child_run_id=original_child.id,
            parent_run_id=run.id,
            root_run_id=run.id,
            status="recovery_required",
            summary="Superseded conflict.",
            patch_artifact_id=original_patch.id,
            changed_files=["README.md"],
            failure_kind="patch_conflict",
        )
    )
    await teams.record_child_result(
        TeamChildResult(
            assignment_id=replacement.id,
            child_run_id=replacement_child.id,
            parent_run_id=run.id,
            root_run_id=run.id,
            status="completed",
            summary="Replacement ready.",
            patch_artifact_id=replacement_patch.id,
            changed_files=["README.md"],
        )
    )

    with pytest.raises(ChildRunWait, match="waiting_verifier"):
        await graph.execute(run, leader, repository=runtime)

    results = await teams.list_child_results(run.id)
    original_result = next(
        item for item in results if item.child_run_id == original_child.id
    )
    replacement_result = next(
        item for item in results if item.child_run_id == replacement_child.id
    )
    assert original_result.patch_aggregated is False
    assert replacement_result.patch_aggregated is True
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == (
        "team patch A\nteam patch B\n"
    )


@pytest.mark.asyncio
async def test_leader_does_not_duplicate_existing_patch_conflict_replacement(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("team patch A\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-m", "Initial")
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    artifacts = InMemoryArtifactMetadataRepository()
    store = LocalArtifactStore(tmp_path / ".artifacts")
    graph = TeamLeaderGraph(
        team_repository=teams,
        artifact_repository=artifacts,
        model_resolver=_models(),
    )
    run, leader = _leader_run()
    run = run.model_copy(update={"workspace_path": tmp_path})
    await runtime.create_run(run, leader)
    original_child = Run(
        goal="original",
        mode=RunMode.TEAM,
        parent_run_id=run.id,
        root_run_id=run.id,
        depth=1,
        child_role="teammate",
        runtime_route="team-role",
    )
    existing_replacement_child = Run(
        goal="existing replacement",
        mode=RunMode.TEAM,
        parent_run_id=run.id,
        root_run_id=run.id,
        depth=1,
        child_role="teammate",
        runtime_route="team-role",
    )
    for child in [original_child, existing_replacement_child]:
        await runtime.create_run(
            child,
            Agent(
                run_id=child.id,
                parent_agent_id=leader.id,
                kind=AgentKind.TEAMMATE,
                profile="backend-engineer",
                model="backend-model",
            ),
        )
    original = TeamAssignment(
        root_run_id=run.id,
        parent_run_id=run.id,
        child_run_id=original_child.id,
        kind=TeamAssignmentKind.TEAMMATE,
        status=TeamAssignmentStatus.COMPLETED,
        role_profile="backend-engineer",
        runtime_route="team-role",
        goal="original",
    )
    existing_replacement = TeamAssignment(
        root_run_id=run.id,
        parent_run_id=run.id,
        child_run_id=existing_replacement_child.id,
        kind=TeamAssignmentKind.TEAMMATE,
        status=TeamAssignmentStatus.COMPLETED,
        role_profile="backend-engineer",
        runtime_route="team-role",
        goal="existing replacement",
        handoff_context={
            "rework_reason": "patch_conflict",
            "previous_assignment_id": str(original.id),
            "previous_child_run_id": str(original_child.id),
            "rework_attempt": 1,
        },
    )
    await teams.create_assignment(original)
    await teams.create_assignment(existing_replacement)
    metadata = store.write(
        run_id=original_child.id,
        agent_id=None,
        artifact_type="patch",
        filename="conflict.patch",
        content=(
            b"diff --git a/README.md b/README.md\n"
            b"--- a/README.md\n"
            b"+++ b/README.md\n"
            b"@@ -1 +1 @@\n"
            b"-fixture\n"
            b"+conflict\n"
        ),
        mime_type="text/x-diff",
        summary="Conflict",
    )
    await artifacts.record(metadata)
    await teams.record_child_result(
        TeamChildResult(
            assignment_id=original.id,
            child_run_id=original_child.id,
            parent_run_id=run.id,
            root_run_id=run.id,
            status="recovery_required",
            summary="Conflict already has replacement.",
            patch_artifact_id=metadata.id,
            failure_kind="patch_conflict",
        )
    )
    await teams.record_child_result(
        TeamChildResult(
            assignment_id=existing_replacement.id,
            child_run_id=existing_replacement_child.id,
            parent_run_id=run.id,
            root_run_id=run.id,
            status="completed",
            summary="Replacement already aggregated.",
            patch_aggregated=True,
        )
    )

    with pytest.raises(ChildRunWait, match="waiting_verifier"):
        await graph.execute(run, leader, repository=runtime)

    assignments = await teams.list_assignments(run.id, include_inactive=True)
    replacements = [
        item
        for item in assignments
        if item.handoff_context.get("rework_reason") == "patch_conflict"
    ]
    assert replacements == [existing_replacement]
    assert sum(item.kind is TeamAssignmentKind.VERIFIER for item in assignments) == 1


@pytest.mark.asyncio
async def test_leader_exhausts_patch_conflict_rework_budget(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("current\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-m", "Initial")
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    artifacts = InMemoryArtifactMetadataRepository()
    store = LocalArtifactStore(tmp_path / ".artifacts")
    graph = TeamLeaderGraph(
        team_repository=teams,
        artifact_repository=artifacts,
        model_resolver=_models(),
    )
    run, leader = _leader_run()
    run = run.model_copy(update={"workspace_path": tmp_path})
    await runtime.create_run(run, leader)
    root_original = TeamAssignment(
        root_run_id=run.id,
        parent_run_id=run.id,
        child_run_id=uuid4(),
        kind=TeamAssignmentKind.TEAMMATE,
        status=TeamAssignmentStatus.COMPLETED,
        role_profile="backend-engineer",
        runtime_route="team-role",
        goal="root",
    )
    second_conflict_child = Run(
        goal="second conflict",
        mode=RunMode.TEAM,
        parent_run_id=run.id,
        root_run_id=run.id,
        depth=1,
        child_role="teammate",
        runtime_route="team-role",
    )
    await runtime.create_run(
        second_conflict_child,
        Agent(
            run_id=second_conflict_child.id,
            parent_agent_id=leader.id,
            kind=AgentKind.TEAMMATE,
            profile="backend-engineer",
            model="backend-model",
        ),
    )
    first_replacement = TeamAssignment(
        root_run_id=run.id,
        parent_run_id=run.id,
        child_run_id=uuid4(),
        kind=TeamAssignmentKind.TEAMMATE,
        status=TeamAssignmentStatus.COMPLETED,
        role_profile="backend-engineer",
        runtime_route="team-role",
        goal="first replacement",
        handoff_context={
            "rework_reason": "patch_conflict",
            "previous_assignment_id": str(root_original.id),
            "previous_child_run_id": str(root_original.child_run_id),
            "rework_attempt": 1,
        },
    )
    second_conflict = TeamAssignment(
        root_run_id=run.id,
        parent_run_id=run.id,
        child_run_id=second_conflict_child.id,
        kind=TeamAssignmentKind.TEAMMATE,
        status=TeamAssignmentStatus.COMPLETED,
        role_profile="backend-engineer",
        runtime_route="team-role",
        goal="second conflict",
        handoff_context={
            "rework_reason": "patch_conflict",
            "previous_assignment_id": str(root_original.id),
            "previous_child_run_id": str(first_replacement.child_run_id),
            "rework_attempt": 2,
        },
    )
    await teams.create_assignment(root_original)
    await teams.create_assignment(first_replacement)
    await teams.create_assignment(second_conflict)
    metadata = store.write(
        run_id=second_conflict_child.id,
        agent_id=None,
        artifact_type="patch",
        filename="second-conflict.patch",
        content=(
            b"diff --git a/README.md b/README.md\n"
            b"--- a/README.md\n"
            b"+++ b/README.md\n"
            b"@@ -1 +1 @@\n"
            b"-fixture\n"
            b"+still conflict\n"
        ),
        mime_type="text/x-diff",
        summary="Second conflict",
    )
    await artifacts.record(metadata)
    await teams.record_child_result(
        TeamChildResult(
            assignment_id=second_conflict.id,
            child_run_id=second_conflict_child.id,
            parent_run_id=run.id,
            root_run_id=run.id,
            status="completed",
            summary="Second conflict.",
            patch_artifact_id=metadata.id,
        )
    )

    events: list[tuple[object, dict[str, object], str]] = []

    async def emit(
        event_type: object,
        payload: dict[str, object],
        transition_id: str,
    ) -> None:
        events.append((event_type, payload, transition_id))

    with pytest.raises(
        PermanentExecutionError,
        match="team_patch_conflict_rework_exhausted",
    ):
        await graph.execute(run, leader, repository=runtime, event_sink=emit)

    exhausted = next(
        event for event in events if event[0] is EventType.TEAM_REWORK_EXHAUSTED
    )
    assert exhausted[1]["budget"] == 2
    assert exhausted[1]["budget_source"] == "team_recovery_policy"


@pytest.mark.asyncio
async def test_leader_creates_rework_replacement_from_verifier_request() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    run, leader = _leader_run()
    await runtime.create_run(run, leader)
    teammate = Run(
        goal="original",
        mode=RunMode.TEAM,
        parent_run_id=run.id,
        root_run_id=run.id,
        depth=1,
        child_role="teammate",
        runtime_route="team-role",
    )
    await runtime.create_run(
        teammate,
        Agent(
            run_id=teammate.id,
            parent_agent_id=leader.id,
            kind=AgentKind.TEAMMATE,
            profile="backend-engineer",
            model="backend-model",
        ),
    )
    original = TeamAssignment(
        root_run_id=run.id,
        parent_run_id=run.id,
        child_run_id=teammate.id,
        kind=TeamAssignmentKind.TEAMMATE,
        status=TeamAssignmentStatus.COMPLETED,
        role_profile="backend-engineer",
        runtime_route="team-role",
        goal="Inspect README.",
        allowed_tools=["repo.read", "team.create_subagent"],
        allowed_skills=["repository-inspection"],
        can_delegate=True,
        max_subagents=3,
    )
    await teams.create_assignment(original)
    await teams.record_child_result(
        TeamChildResult(
            assignment_id=original.id,
            child_run_id=teammate.id,
            parent_run_id=run.id,
            root_run_id=run.id,
            status="completed",
            summary="Missing README details.",
            patch_aggregated=True,
        )
    )
    graph, _ = _graph(
        teams,
        responses=[_team_plan_repair_json(target_child_run_id=str(teammate.id))],
    )
    verifier, verifier_assignment = await _failed_verifier_with_rework(
        runtime,
        teams,
        run,
        leader,
        target_child_run_id=str(teammate.id),
    )
    events: list[tuple[object, dict[str, object], str]] = []

    async def emit(
        event_type: object,
        payload: dict[str, object],
        transition_id: str,
    ) -> None:
        events.append((event_type, payload, transition_id))

    with pytest.raises(ChildRunWait, match="waiting_children"):
        await graph.execute(run, leader, repository=runtime, event_sink=emit)

    assignments = await teams.list_assignments(run.id, include_inactive=True)
    replacement = next(
        item
        for item in assignments
        if item.handoff_context.get("previous_assignment_id") == str(original.id)
    )
    retired_verifier = next(
        item for item in assignments if item.id == verifier_assignment.id
    )
    assert replacement.allowed_tools == original.allowed_tools
    assert replacement.allowed_skills == original.allowed_skills
    assert replacement.can_delegate is True
    assert replacement.max_subagents == 3
    assert replacement.handoff_context["previous_child_run_id"] == str(teammate.id)
    assert replacement.handoff_context["plan_repair_action"] == "replace_teammate"
    assert replacement.handoff_context["plan_repair_attempt"] == 1
    assert replacement.handoff_context["plan_repair_verifier_child_run_id"] == str(
        verifier.id
    )
    assert retired_verifier.status is TeamAssignmentStatus.RETIRED
    assert EventType.TEAM_PLAN_REPAIR_CREATED in {event[0] for event in events}
    assert EventType.TEAM_PLAN_REPAIR_APPLIED in {event[0] for event in events}
    assert EventType.TEAM_REWORK_REQUESTED not in {event[0] for event in events}


@pytest.mark.asyncio
async def test_leader_applies_plan_repair_additive_teammate() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    graph, _ = _graph(teams, responses=[_team_plan_repair_add_json()])
    run, leader = _leader_run()
    await runtime.create_run(run, leader)
    teammate = Run(
        goal="original",
        mode=RunMode.TEAM,
        parent_run_id=run.id,
        root_run_id=run.id,
        depth=1,
        child_role="teammate",
        runtime_route="team-role",
    )
    await runtime.create_run(
        teammate,
        Agent(
            run_id=teammate.id,
            parent_agent_id=leader.id,
            kind=AgentKind.TEAMMATE,
            profile="backend-engineer",
            model="backend-model",
        ),
    )
    original = TeamAssignment(
        root_run_id=run.id,
        parent_run_id=run.id,
        child_run_id=teammate.id,
        kind=TeamAssignmentKind.TEAMMATE,
        status=TeamAssignmentStatus.COMPLETED,
        role_profile="backend-engineer",
        runtime_route="team-role",
        goal="Inspect README.",
        allowed_tools=["repo.read"],
        can_write=False,
    )
    await teams.create_assignment(original)
    await teams.record_child_result(
        TeamChildResult(
            assignment_id=original.id,
            child_run_id=teammate.id,
            parent_run_id=run.id,
            root_run_id=run.id,
            status="completed",
            summary="Need a separate reviewer.",
        )
    )
    verifier, verifier_assignment = await _failed_verifier_with_rework(
        runtime,
        teams,
        run,
        leader,
        target_child_run_id=str(teammate.id),
    )
    events: list[tuple[object, dict[str, object], str]] = []

    async def emit(
        event_type: object,
        payload: dict[str, object],
        transition_id: str,
    ) -> None:
        events.append((event_type, payload, transition_id))

    with pytest.raises(ChildRunWait, match="waiting_children"):
        await graph.execute(run, leader, repository=runtime, event_sink=emit)

    assignments = await teams.list_assignments(run.id, include_inactive=True)
    additive = next(
        item
        for item in assignments
        if item.handoff_context.get("plan_repair_action") == "add_teammate"
    )
    retired_verifier = next(
        item for item in assignments if item.id == verifier_assignment.id
    )

    assert additive.handoff_context["plan_repair_verifier_child_run_id"] == str(
        verifier.id
    )
    assert "previous_assignment_id" not in additive.handoff_context
    assert additive.allowed_tools == ["repo.read", "repo.apply_patch"]
    assert additive.can_write is True
    assert retired_verifier.status is TeamAssignmentStatus.RETIRED
    assert EventType.TEAM_PLAN_REPAIR_APPLIED in {event[0] for event in events}


@pytest.mark.asyncio
async def test_leader_exhausts_plan_repair_budget() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    graph, provider = _graph(teams, responses=[_team_plan_repair_add_json()])
    run, leader = _leader_run()
    await runtime.create_run(run, leader)
    teammate = Run(
        goal="original",
        mode=RunMode.TEAM,
        parent_run_id=run.id,
        root_run_id=run.id,
        depth=1,
        child_role="teammate",
        runtime_route="team-role",
    )
    await runtime.create_run(
        teammate,
        Agent(
            run_id=teammate.id,
            parent_agent_id=leader.id,
            kind=AgentKind.TEAMMATE,
            profile="backend-engineer",
            model="backend-model",
        ),
    )
    original = TeamAssignment(
        root_run_id=run.id,
        parent_run_id=run.id,
        child_run_id=teammate.id,
        kind=TeamAssignmentKind.TEAMMATE,
        status=TeamAssignmentStatus.COMPLETED,
        role_profile="backend-engineer",
        runtime_route="team-role",
        goal="Inspect README.",
    )
    await teams.create_assignment(original)
    verifier, _ = await _failed_verifier_with_rework(
        runtime,
        teams,
        run,
        leader,
        target_child_run_id=str(teammate.id),
    )
    for index in range(2):
        await teams.create_assignment(
            TeamAssignment(
                root_run_id=run.id,
                parent_run_id=run.id,
                child_run_id=uuid4(),
                kind=TeamAssignmentKind.TEAMMATE,
                status=TeamAssignmentStatus.COMPLETED,
                role_profile="backend-engineer",
                runtime_route="team-role",
                goal=f"Prior repair {index}",
                handoff_context={
                    "plan_repair_reason": "verifier_rework",
                    "plan_repair_verifier_child_run_id": str(verifier.id),
                },
            )
        )
    events: list[tuple[object, dict[str, object], str]] = []

    async def emit(
        event_type: object,
        payload: dict[str, object],
        transition_id: str,
    ) -> None:
        events.append((event_type, payload, transition_id))

    with pytest.raises(PermanentExecutionError, match="team_plan_repair_exhausted"):
        await graph.execute(run, leader, repository=runtime, event_sink=emit)

    assert provider.requests == []
    exhausted = next(
        event for event in events if event[0] is EventType.TEAM_PLAN_REPAIR_EXHAUSTED
    )
    assert exhausted[1]["budget"] == 2
    assert exhausted[1]["budget_source"] == "team_recovery_policy"


@pytest.mark.asyncio
async def test_leader_completes_after_verifier_assignment_terminal() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    graph, _ = _graph(teams)
    run, leader = _leader_run()
    await runtime.create_run(run, leader)

    with pytest.raises(ChildRunWait):
        await graph.execute(run, leader, repository=runtime)
    teammate = (await teams.list_assignments(run.id))[0]
    await teams.record_child_terminal(
        teammate.child_run_id,
        status=TeamAssignmentStatus.COMPLETED,
    )
    with pytest.raises(ChildRunWait):
        await graph.execute(run, leader, repository=runtime)
    verifier = next(
        item
        for item in await teams.list_assignments(run.id)
        if item.kind is TeamAssignmentKind.VERIFIER
    )
    await teams.record_child_terminal(
        verifier.child_run_id,
        status=TeamAssignmentStatus.COMPLETED,
    )

    state, recovered = await graph.execute(run, leader, repository=runtime)

    assert not recovered
    assert state["phase"] == "completed"
    assert state["final_answer"] == "Distributed team child Runs completed."


@pytest.mark.asyncio
async def test_worker_releases_parent_for_child_wait() -> None:
    from tests.unit.test_worker import (
        FakeDispatcher,
        FakeGraph,
        _config,
        _lease,
    )

    from awesome_agent.runtime.worker import DurableWorker

    lease = _lease()
    run = Run(
        id=lease.run_id,
        goal="team",
        mode=RunMode.TEAM,
        intent=RunIntent.MODIFYING,
        runtime_route=TEAM_CODING_ROUTE,
        graph_thread_id=f"run:{lease.run_id}",
    )
    leader = Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="fake",
    )
    dispatcher = FakeDispatcher(lease)
    runtime = InMemoryRuntimeRepository()
    await runtime.create_run(run, leader)
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=runtime,
        probe_graph=FakeGraph(),  # type: ignore[arg-type]
        team_leader_graph=_graph(InMemoryTeamRepository())[0],
        config=_config(),
    )

    assert await worker.run_once()
    assert dispatcher.calls[-1][0] == "child_wait"
    assert dispatcher.calls[-1][1] == {"reason": "waiting_children"}


@pytest.mark.asyncio
async def test_worker_child_completion_requeues_waiting_parent() -> None:
    from tests.unit.test_worker import FakeDispatcher, FakeGraph, _config, _lease

    from awesome_agent.runtime.worker import DurableWorker

    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    parent, leader = _leader_run()
    waiting_parent = parent.model_copy(update={"status": RunStatus.WAITING})
    child = Run(
        goal="child",
        mode=RunMode.TEAM,
        intent=RunIntent.MODIFYING,
        parent_run_id=parent.id,
        root_run_id=parent.id,
        depth=1,
        child_role="teammate",
        execution_kind=ExecutionKind.RUNTIME_PROBE,
        runtime_route="runtime-probe",
    )
    child_agent = Agent(
        run_id=child.id,
        parent_agent_id=leader.id,
        kind=AgentKind.TEAMMATE,
        profile="teammate",
        model="fake",
    )
    await runtime.create_run(waiting_parent, leader)
    await runtime.create_run(child, child_agent)
    from awesome_agent.runtime.team_assignments import TeamAssignment

    await teams.create_assignment(
        TeamAssignment(
            root_run_id=parent.id,
            parent_run_id=parent.id,
            child_run_id=child.id,
            kind=TeamAssignmentKind.TEAMMATE,
            role_profile="teammate",
            runtime_route="team-role",
            goal="child",
        )
    )
    lease = _lease().model_copy(update={"run_id": child.id})
    dispatcher = FakeDispatcher(lease)
    worker = DurableWorker(
        dispatcher=dispatcher,
        repository=runtime,
        probe_graph=FakeGraph(),  # type: ignore[arg-type]
        config=_config(),
        team_repository=teams,
    )

    assert await worker.run_once()
    requeued = await runtime.get_run(parent.id)
    assignment = await teams.get_assignment_for_child_run(child.id)

    assert assignment.status is TeamAssignmentStatus.COMPLETED
    assert requeued.dispatch_status is DispatchStatus.QUEUED


def _leader_run() -> tuple[Run, Agent]:
    run = Run(
        id=uuid4(),
        goal="Implement backend and verify it",
        mode=RunMode.TEAM,
        intent=RunIntent.MODIFYING,
        runtime_route=TEAM_CODING_ROUTE,
        extension_catalog_version="ext_team",
        graph_thread_id=f"run:{uuid4()}",
    )
    leader = Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="deepseek-v4-pro",
    )
    return run, leader


def _tight_budget_policy() -> BudgetPolicy:
    return BudgetPolicy(
        soft_context_tokens=1000,
        hard_context_tokens=2000,
        recent_context_tokens=800,
        max_total_tokens_per_run=120,
        max_reasoning_tokens_per_run=1000,
        max_active_seconds_per_run=3600,
    )


def _graph(
    teams: InMemoryTeamRepository,
    *,
    responses: list[str] | None = None,
    team_loop: TeamAgentLoop | None = None,
    team_recovery_policy: TeamRecoveryPolicy | None = None,
) -> tuple[TeamLeaderGraph, FakeModelProvider]:
    provider = FakeModelProvider(responses or [_team_plan_json()])
    return (
        TeamLeaderGraph(
            team_repository=teams,
            provider_resolver=lambda _: provider,
            model_resolver=_models(),
            team_loop=team_loop,
            team_recovery_policy=team_recovery_policy,
        ),
        provider,
    )


def _models() -> RoleModelResolver:
    return RoleModelResolver(
        leader_model="leader-model",
        teammate_model="teammate-model",
        verifier_model="verifier-model",
        subagent_model="subagent-model",
        role_overrides={"backend-engineer": "backend-model"},
    )


def _team_plan_json(*, extra: dict[str, object] | None = None) -> str:
    teammate: dict[str, object] = {
        "role_profile": "backend-engineer",
        "goal": "Implement the backend change and report the changed files.",
        "allowed_tools": ["repo.read", "repo.apply_patch"],
        "deferred_tools": ["repo.apply_patch"],
        "allowed_skills": ["python"],
        "can_write": True,
        "can_delegate": True,
        "max_subagents": 3,
        "acceptance_criteria": ["Return a patch or explain why no patch is needed."],
    }
    if extra:
        teammate.update(extra)
    return json.dumps(
        {
            "rationale": "The task benefits from one backend teammate.",
            "teammates": [teammate],
        }
    )


def _team_plan_repair_json(*, target_child_run_id: str) -> str:
    return json.dumps(
        {
            "rationale": "The verifier found missing README evidence.",
            "actions": [
                {
                    "action": "replace_teammate",
                    "reason": "The original teammate missed required evidence.",
                    "target_child_run_id": target_child_run_id,
                    "teammate": {
                        "role_profile": "backend-engineer",
                        "goal": "Re-read README.md and provide the missing evidence.",
                        "allowed_tools": ["repo.read", "team.create_subagent"],
                        "allowed_skills": ["repository-inspection"],
                        "can_write": False,
                        "can_delegate": True,
                        "max_subagents": 3,
                        "acceptance_criteria": [
                            "Read README.md and summarize the relevant evidence."
                        ],
                    },
                }
            ],
        }
    )


def _team_plan_repair_add_json() -> str:
    return json.dumps(
        {
            "rationale": "The verifier needs an additional teammate to close gaps.",
            "actions": [
                {
                    "action": "add_teammate",
                    "reason": "Add a focused implementer for the missing work.",
                    "teammate": {
                        "role_profile": "backend-engineer",
                        "goal": "Implement the missing change and report files.",
                        "allowed_tools": ["repo.read", "repo.apply_patch"],
                        "deferred_tools": ["repo.apply_patch"],
                        "allowed_skills": ["python"],
                        "can_write": True,
                        "can_delegate": False,
                        "max_subagents": 0,
                        "acceptance_criteria": [
                            "Return a patch or explain why no patch is needed."
                        ],
                    },
                }
            ],
        }
    )


async def _failed_verifier_with_rework(
    runtime: InMemoryRuntimeRepository,
    teams: InMemoryTeamRepository,
    run: Run,
    leader: Agent,
    *,
    target_child_run_id: str,
) -> tuple[Run, TeamAssignment]:
    verifier = Run(
        goal="verify",
        mode=RunMode.TEAM,
        parent_run_id=run.id,
        root_run_id=run.id,
        depth=1,
        child_role="verifier",
        runtime_route=TEAM_VERIFIER_ROUTE,
    )
    await runtime.create_run(
        verifier,
        Agent(
            run_id=verifier.id,
            parent_agent_id=leader.id,
            kind=AgentKind.VERIFIER,
            profile="verifier",
            model="verifier-model",
        ),
    )
    verifier_assignment = TeamAssignment(
        root_run_id=run.id,
        parent_run_id=run.id,
        child_run_id=verifier.id,
        kind=TeamAssignmentKind.VERIFIER,
        status=TeamAssignmentStatus.FAILED,
        role_profile="verifier",
        runtime_route=TEAM_VERIFIER_ROUTE,
        goal="verify",
    )
    await teams.create_assignment(verifier_assignment)
    decision = TeamVerificationDecision(
        decision="rework_required",
        summary="Need README evidence.",
        rework_requests=[
            TeamReworkRequest(
                target_child_run_id=target_child_run_id,
                reason="Missing README evidence.",
                acceptance_criteria=["Read README.md and summarize it."],
            )
        ],
    )
    await teams.record_child_result(
        TeamChildResult(
            assignment_id=verifier_assignment.id,
            child_run_id=verifier.id,
            parent_run_id=run.id,
            root_run_id=run.id,
            status="failed",
            summary=encode_rework_decision(decision),
            failure_kind="rework_required",
        )
    )
    return verifier, verifier_assignment


def _git(path: Path, *arguments: str) -> None:
    import subprocess

    result = subprocess.run(
        ["git", *arguments],
        cwd=path,
        capture_output=True,
        check=True,
        text=True,
    )
    assert result.returncode == 0


class RecordingTeamMiddleware:
    name = "recording-team-leader"

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
