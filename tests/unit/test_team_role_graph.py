import json
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from uuid import uuid4

import pytest

from awesome_agent.artifacts.repository import InMemoryArtifactMetadataRepository
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.domain.enums import (
    AgentKind,
    DispatchStatus,
    EventType,
    RunIntent,
    RunMode,
)
from awesome_agent.domain.models import Agent, Run
from awesome_agent.modeling import (
    AssistantMessage,
    ModelRequest,
    ModelStreamEvent,
    ModelTurn,
    StopReason,
    StructuredModelProvider,
    ToolCall,
    TurnCompleted,
)
from awesome_agent.persistence.budget import InMemoryBudgetRepository
from awesome_agent.persistence.team import InMemoryTeamRepository
from awesome_agent.persistence.validation import (
    DurableValidationGateResult,
    DurableValidationReport,
    InMemoryValidationRepository,
    ValidationReportWithGates,
)
from awesome_agent.runtime.agent_loop import (
    MiddlewareContext,
    MiddlewareDecision,
    MiddlewareStack,
    MiddlewareStage,
    TeamAgentLoop,
)
from awesome_agent.runtime.dispatch import ChildRunWait, PermanentExecutionError
from awesome_agent.runtime.graphs import TEAM_ROLE_ROUTE
from awesome_agent.runtime.repository import InMemoryRuntimeRepository
from awesome_agent.runtime.team_assignments import (
    TeamAssignment,
    TeamAssignmentKind,
    TeamAssignmentStatus,
    TeamChildResult,
)
from awesome_agent.runtime.team_mailbox import (
    MailboxMessage,
    MailboxMessageStatus,
    MailboxMessageType,
    MailboxRoute,
)
from awesome_agent.runtime.team_role_graph import TeamRoleGraph
from awesome_agent.runtime.validation.models import ValidationGate, ValidationPlan


class SequenceProvider(StructuredModelProvider):
    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = deque(turns)
        self.requests: list[ModelRequest] = []

    async def stream(
        self,
        request: ModelRequest,
    ) -> AsyncIterator[ModelStreamEvent]:
        self.requests.append(request)
        yield TurnCompleted(turn=self.turns.popleft())


@pytest.mark.asyncio
async def test_teammate_loads_assignment_permissions() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    graph = TeamRoleGraph(team_repository=teams)
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.TEAMMATE,
            allowed_tools=["repo.read", "repo.apply_patch", "shell.execute"],
            deferred_tools=["repo.apply_patch", "shell.execute"],
            promoted_tools=["repo.apply_patch"],
            allowed_skills=["repository-inspection"],
            can_write=True,
        )
    )

    state, recovered = await graph.execute(run, agent, repository=runtime)

    assert not recovered
    assert state["allowed_tools"] == ["repo.read", "repo.apply_patch"]
    assert state["allowed_skills"] == ["repository-inspection"]


@pytest.mark.asyncio
async def test_teammate_can_create_limited_subagents() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    graph = TeamRoleGraph(team_repository=teams)
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.TEAMMATE,
            allowed_tools=["repo.read", "shell.execute"],
            deferred_tools=["shell.execute"],
            can_delegate=True,
            max_subagents=3,
            handoff_context={"subagent_goals": ["Read README", "Inspect tests"]},
        )
    )

    with pytest.raises(ChildRunWait, match="waiting_subagents"):
        await graph.execute(run, agent, repository=runtime)

    subagents = await runtime.list_child_runs(run.id)
    assignments = await teams.list_assignments(run.root_run_id or run.id)
    subagent_assignments = [
        item for item in assignments if item.parent_run_id == run.id
    ]

    assert len(subagents) == 2
    assert all(child.depth == 2 for child in subagents)
    assert [item.kind for item in subagent_assignments] == [
        TeamAssignmentKind.SUBAGENT,
        TeamAssignmentKind.SUBAGENT,
    ]
    assert all(item.allowed_tools == ["repo.read"] for item in subagent_assignments)


@pytest.mark.asyncio
async def test_subagent_cannot_delegate() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    graph = TeamRoleGraph(team_repository=teams)
    run, agent = _role_run(kind=TeamAssignmentKind.SUBAGENT)
    await runtime.create_run(run, agent)
    await teams.create_assignment(_assignment(run, kind=TeamAssignmentKind.SUBAGENT))

    state, _ = await graph.execute(run, agent, repository=runtime)

    assert state["phase"] == "completed"
    assert await runtime.list_child_runs(run.id) == []


@pytest.mark.asyncio
async def test_teammate_resumes_after_subagents_terminal() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    graph = TeamRoleGraph(team_repository=teams)
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.TEAMMATE,
            can_delegate=True,
            max_subagents=1,
            handoff_context={"subagent_goals": ["Read README"]},
        )
    )

    with pytest.raises(ChildRunWait):
        await graph.execute(run, agent, repository=runtime)
    subagent_assignment = next(
        item
        for item in await teams.list_assignments(run.root_run_id or run.id)
        if item.kind is TeamAssignmentKind.SUBAGENT
    )
    await teams.record_child_terminal(
        subagent_assignment.child_run_id,
        status=TeamAssignmentStatus.COMPLETED,
    )

    state, recovered = await graph.execute(run, agent, repository=runtime)

    assert not recovered
    assert state["phase"] == "completed"


@pytest.mark.asyncio
async def test_teammate_records_patch_artifact_result(tmp_path: Path) -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    artifacts = InMemoryArtifactMetadataRepository()
    graph = TeamRoleGraph(
        team_repository=teams,
        artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
        artifact_repository=artifacts,
    )
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.TEAMMATE,
            can_delegate=False,
            handoff_context={
                "patch": "--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-old\n+new\n",
                "changed_files": ["README.md"],
            },
        ).model_copy(update={"can_write": True})
    )

    await graph.execute(run, agent, repository=runtime)

    result = (await teams.list_child_results(run.parent_run_id or run.id))[0]
    assert result.patch_artifact_id is not None
    assert result.changed_files == ["README.md"]
    assert (await artifacts.get(result.patch_artifact_id)).artifact_type == "patch"


@pytest.mark.asyncio
async def test_teammate_compacts_large_child_result_summary(tmp_path: Path) -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    artifacts = InMemoryArtifactMetadataRepository()
    budgets = InMemoryBudgetRepository()
    graph = TeamRoleGraph(
        team_repository=teams,
        artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
        artifact_repository=artifacts,
        budget_repository=budgets,
    )
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.TEAMMATE,
            handoff_context={"result_summary": "large evidence " * 2000},
        )
    )

    await graph.execute(run, agent, repository=runtime)

    result = (await teams.list_child_results(run.parent_run_id or run.id))[0]
    assert "offloaded to artifact" in result.summary
    assert result.evidence_artifact_refs
    metadata = await artifacts.get(result.evidence_artifact_refs[0])
    assert metadata.artifact_type == "team-context"
    assert await budgets.list_compactions(run.id)


@pytest.mark.asyncio
async def test_write_teammate_validates_patch_before_publishing_result(
    tmp_path: Path,
) -> None:
    workspace = _git_workspace(tmp_path)
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    artifacts = InMemoryArtifactMetadataRepository()
    validation = InMemoryValidationRepository()
    provider = SequenceProvider(
        [
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="apply",
                        name="repo.apply_patch",
                        arguments_json=json.dumps({"patch": _readme_update_patch()}),
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="diff",
                        name="repo.diff",
                        arguments_json="{}",
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            _turn(content="Changed README.md after validation-ready diff."),
        ]
    )
    validation_calls: list[ValidationPlan] = []

    async def validation_runner(
        plan: ValidationPlan,
        current_run: Run,
        current_agent: Agent,
    ) -> ValidationReportWithGates:
        validation_calls.append(plan)
        return _validation_report(
            run=current_run,
            agent=current_agent,
            status="passed",
            summary="Validation passed with 1 gate(s).",
        )

    graph = TeamRoleGraph(
        team_repository=teams,
        provider_resolver=lambda _: provider,
        artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
        artifact_repository=artifacts,
        validation_repository=validation,
        validation_plan_resolver=lambda _: _validation_plan(),
        validation_runner=validation_runner,
    )
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    run = run.model_copy(update={"workspace_path": workspace})
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.TEAMMATE,
            allowed_tools=["repo.diff", "repo.apply_patch"],
            can_write=True,
        )
    )
    events: list[tuple[object, dict[str, object], str]] = []

    async def emit(
        event_type: object,
        payload: dict[str, object],
        transition_id: str,
    ) -> None:
        events.append((event_type, payload, transition_id))

    state, recovered = await graph.execute(
        run,
        agent,
        repository=runtime,
        event_sink=emit,
    )

    result = (await teams.list_child_results(run.parent_run_id or run.id))[0]
    reports = await validation.list_for_run(run.id)
    assert not recovered
    assert state["phase"] == "completed"
    assert len(validation_calls) == 1
    assert [item.report.status for item in reports] == ["passed"]
    assert result.status == "completed"
    assert result.patch_artifact_id is not None
    assert any(
        event_type is EventType.VERIFICATION_CREATED
        and payload.get("status") == "passed"
        and payload.get("summary") == "Validation passed with 1 gate(s)."
        for event_type, payload, _ in events
    )


@pytest.mark.asyncio
async def test_write_teammate_validation_failure_records_failed_child_result(
    tmp_path: Path,
) -> None:
    workspace = _git_workspace(tmp_path)
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    artifacts = InMemoryArtifactMetadataRepository()
    validation = InMemoryValidationRepository()
    provider = SequenceProvider(
        [
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="apply",
                        name="repo.apply_patch",
                        arguments_json=json.dumps({"patch": _readme_update_patch()}),
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="diff",
                        name="repo.diff",
                        arguments_json="{}",
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            _turn(content="Changed README.md but validation will fail."),
        ]
    )

    async def validation_runner(
        plan: ValidationPlan,
        current_run: Run,
        current_agent: Agent,
    ) -> ValidationReportWithGates:
        return _validation_report(
            run=current_run,
            agent=current_agent,
            status="failed",
            summary="Validation failed: unit",
            failure_kind="command_failed",
        )

    graph = TeamRoleGraph(
        team_repository=teams,
        provider_resolver=lambda _: provider,
        artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
        artifact_repository=artifacts,
        validation_repository=validation,
        validation_plan_resolver=lambda _: _validation_plan(),
        validation_runner=validation_runner,
    )
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    run = run.model_copy(update={"workspace_path": workspace})
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.TEAMMATE,
            allowed_tools=["repo.diff", "repo.apply_patch"],
            can_write=True,
        )
    )

    with pytest.raises(PermanentExecutionError, match="team_role_validation_failed"):
        await graph.execute(run, agent, repository=runtime)

    result = (await teams.list_child_results(run.parent_run_id or run.id))[0]
    reports = await validation.list_for_run(run.id)
    assert [item.report.status for item in reports] == ["failed"]
    assert result.status == "failed"
    assert result.failure_kind == "validation_failed"
    assert result.patch_artifact_id is None
    assert result.changed_files == ["README.md"]
    assert "Validation failed: unit" in result.summary
    assert not await artifacts.list_for_run(run.id)


@pytest.mark.asyncio
async def test_write_teammate_reworks_same_child_after_validation_failure(
    tmp_path: Path,
) -> None:
    workspace = _git_workspace(tmp_path)
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    artifacts = InMemoryArtifactMetadataRepository()
    validation = InMemoryValidationRepository()
    provider = SequenceProvider(
        [
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="apply-bad",
                        name="repo.apply_patch",
                        arguments_json=json.dumps(
                            {"patch": _readme_bad_update_patch()}
                        ),
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="diff-bad",
                        name="repo.diff",
                        arguments_json="{}",
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            _turn(content="Changed README.md but validation may fail."),
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="fix-bad",
                        name="repo.apply_patch",
                        arguments_json=json.dumps(
                            {"patch": _readme_fix_update_patch()}
                        ),
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="diff-fixed",
                        name="repo.diff",
                        arguments_json="{}",
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            _turn(content="Fixed README.md after validation feedback."),
        ]
    )
    validation_calls = 0

    async def validation_runner(
        plan: ValidationPlan,
        current_run: Run,
        current_agent: Agent,
    ) -> ValidationReportWithGates:
        nonlocal validation_calls
        validation_calls += 1
        if validation_calls == 1:
            return _validation_report(
                run=current_run,
                agent=current_agent,
                status="failed",
                summary="Validation failed: unit",
                failure_kind="command_failed",
                attempt=1,
            )
        return _validation_report(
            run=current_run,
            agent=current_agent,
            status="passed",
            summary="Validation passed with 1 gate(s).",
            attempt=2,
        )

    graph = TeamRoleGraph(
        team_repository=teams,
        provider_resolver=lambda _: provider,
        artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
        artifact_repository=artifacts,
        validation_repository=validation,
        validation_plan_resolver=lambda _: _validation_plan_with_rework(1),
        validation_runner=validation_runner,
    )
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    run = run.model_copy(update={"workspace_path": workspace})
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.TEAMMATE,
            allowed_tools=["repo.diff", "repo.apply_patch"],
            can_write=True,
        )
    )

    state, recovered = await graph.execute(run, agent, repository=runtime)

    results = await teams.list_child_results(run.parent_run_id or run.id)
    reports = await validation.list_for_run(run.id)
    descendants = await runtime.list_child_runs(run.id)
    assert not recovered
    assert state["phase"] == "completed"
    assert [item.report.status for item in reports] == ["failed", "passed"]
    assert [result.status for result in results] == ["completed"]
    assert results[0].patch_artifact_id is not None
    assert not descendants
    assert "fixed update" in (workspace / "README.md").read_text(encoding="utf-8")
    patch = (await artifacts.get(results[0].patch_artifact_id)).path.read_text(
        encoding="utf-8"
    )
    assert "+fixed update" in patch
    assert any(
        "Validation failed" in message.content
        and "Validation failed: unit" in message.content
        for message in provider.requests[3].messages
    )


@pytest.mark.asyncio
async def test_write_teammate_validation_rework_exhaustion_records_failed_child_result(
    tmp_path: Path,
) -> None:
    workspace = _git_workspace(tmp_path)
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    artifacts = InMemoryArtifactMetadataRepository()
    validation = InMemoryValidationRepository()
    provider = SequenceProvider(
        [
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="apply-bad",
                        name="repo.apply_patch",
                        arguments_json=json.dumps(
                            {"patch": _readme_bad_update_patch()}
                        ),
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="diff-bad",
                        name="repo.diff",
                        arguments_json="{}",
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            _turn(content="Changed README.md but validation will fail."),
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="fix-bad",
                        name="repo.apply_patch",
                        arguments_json=json.dumps(
                            {"patch": _readme_fix_update_patch()}
                        ),
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="diff-fixed",
                        name="repo.diff",
                        arguments_json="{}",
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            _turn(content="Fixed README.md but validation still fails."),
        ]
    )
    validation_calls = 0

    async def validation_runner(
        plan: ValidationPlan,
        current_run: Run,
        current_agent: Agent,
    ) -> ValidationReportWithGates:
        nonlocal validation_calls
        validation_calls += 1
        return _validation_report(
            run=current_run,
            agent=current_agent,
            status="failed",
            summary=f"Validation failed: unit attempt {validation_calls}",
            failure_kind="command_failed",
            attempt=validation_calls,
        )

    graph = TeamRoleGraph(
        team_repository=teams,
        provider_resolver=lambda _: provider,
        artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
        artifact_repository=artifacts,
        validation_repository=validation,
        validation_plan_resolver=lambda _: _validation_plan_with_rework(1),
        validation_runner=validation_runner,
    )
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    run = run.model_copy(update={"workspace_path": workspace})
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.TEAMMATE,
            allowed_tools=["repo.diff", "repo.apply_patch"],
            can_write=True,
        )
    )

    with pytest.raises(PermanentExecutionError, match="team_role_validation_failed"):
        await graph.execute(run, agent, repository=runtime)

    results = await teams.list_child_results(run.parent_run_id or run.id)
    reports = await validation.list_for_run(run.id)
    assert [item.report.status for item in reports] == ["failed", "failed"]
    assert len(results) == 1
    assert results[0].status == "failed"
    assert results[0].failure_kind == "validation_failed"
    assert results[0].patch_artifact_id is None
    assert results[0].changed_files == ["README.md"]
    assert not await artifacts.list_for_run(run.id)


@pytest.mark.asyncio
async def test_write_teammate_non_reworkable_validation_failure_skips_rework(
    tmp_path: Path,
) -> None:
    workspace = _git_workspace(tmp_path)
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    artifacts = InMemoryArtifactMetadataRepository()
    validation = InMemoryValidationRepository()
    provider = SequenceProvider(
        [
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="apply",
                        name="repo.apply_patch",
                        arguments_json=json.dumps({"patch": _readme_update_patch()}),
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="diff",
                        name="repo.diff",
                        arguments_json="{}",
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            _turn(content="Changed README.md but validation is not reworkable."),
        ]
    )

    async def validation_runner(
        plan: ValidationPlan,
        current_run: Run,
        current_agent: Agent,
    ) -> ValidationReportWithGates:
        return _validation_report(
            run=current_run,
            agent=current_agent,
            status="failed",
            summary="Validation requires approval.",
            failure_kind="approval_required",
            gate_status="approval_required",
            attempt=1,
        )

    graph = TeamRoleGraph(
        team_repository=teams,
        provider_resolver=lambda _: provider,
        artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
        artifact_repository=artifacts,
        validation_repository=validation,
        validation_plan_resolver=lambda _: _validation_plan_with_rework(2),
        validation_runner=validation_runner,
    )
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    run = run.model_copy(update={"workspace_path": workspace})
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.TEAMMATE,
            allowed_tools=["repo.diff", "repo.apply_patch"],
            can_write=True,
        )
    )

    with pytest.raises(PermanentExecutionError, match="team_role_validation_failed"):
        await graph.execute(run, agent, repository=runtime)

    results = await teams.list_child_results(run.parent_run_id or run.id)
    reports = await validation.list_for_run(run.id)
    assert len(provider.requests) == 3
    assert [item.report.status for item in reports] == ["failed"]
    assert [result.status for result in results] == ["failed"]
    assert results[0].patch_artifact_id is None
    assert not await artifacts.list_for_run(run.id)


@pytest.mark.asyncio
async def test_teammate_validation_enters_team_agent_loop_without_patch_metadata(
    tmp_path: Path,
) -> None:
    workspace = _git_workspace(tmp_path)
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    validation = InMemoryValidationRepository()
    recorder = RecordingAgentOperationMiddleware()
    graph = TeamRoleGraph(
        team_repository=teams,
        artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
        artifact_repository=InMemoryArtifactMetadataRepository(),
        validation_repository=validation,
        validation_plan_resolver=lambda _: _validation_plan(),
        validation_runner=lambda plan, current_run, current_agent: _async_report(
            _validation_report(
                run=current_run,
                agent=current_agent,
                status="passed",
                summary="Validation passed with 1 gate(s).",
            )
        ),
        team_loop=TeamAgentLoop(middleware_stack=MiddlewareStack([recorder])),
    )
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    run = run.model_copy(update={"workspace_path": workspace})
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.TEAMMATE,
            can_write=True,
            handoff_context={
                "patch": _readme_update_patch(),
                "changed_files": ["README.md"],
            },
        )
    )

    await graph.execute(run, agent, repository=runtime)

    validation_call = next(
        call
        for call in recorder.calls
        if call.get("team_operation") == "role_validation"
    )
    assert validation_call["assignment_id"]
    assert validation_call["team_role"] == "teammate"
    assert validation_call["agent_kind"] == "teammate"
    assert "team role update" not in str(validation_call)
    assert "Validation passed" not in str(validation_call)


@pytest.mark.asyncio
async def test_read_only_teammate_skips_local_validation(tmp_path: Path) -> None:
    workspace = _git_workspace(tmp_path)
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    validation_calls = 0

    async def validation_runner(
        plan: ValidationPlan,
        current_run: Run,
        current_agent: Agent,
    ) -> ValidationReportWithGates:
        nonlocal validation_calls
        validation_calls += 1
        return _validation_report(
            run=current_run,
            agent=current_agent,
            status="passed",
            summary="unexpected",
        )

    graph = TeamRoleGraph(
        team_repository=teams,
        provider_resolver=lambda _: SequenceProvider(
            [
                _turn(
                    tool_calls=[
                        ToolCall(
                            call_id="read",
                            name="repo.read",
                            arguments_json='{"path":"README.md"}',
                        )
                    ],
                    stop_reason=StopReason.TOOL_CALLS,
                ),
                _turn(content="Read only."),
            ]
        ),
        validation_plan_resolver=lambda _: _validation_plan(),
        validation_runner=validation_runner,
    )
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    run = run.model_copy(update={"workspace_path": workspace})
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.TEAMMATE,
            allowed_tools=["repo.read"],
            can_write=False,
        )
    )

    await graph.execute(run, agent, repository=runtime)

    assert validation_calls == 0


@pytest.mark.asyncio
async def test_write_teammate_without_patch_skips_local_validation(
    tmp_path: Path,
) -> None:
    workspace = _git_workspace(tmp_path)
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    validation_calls = 0

    async def validation_runner(
        plan: ValidationPlan,
        current_run: Run,
        current_agent: Agent,
    ) -> ValidationReportWithGates:
        nonlocal validation_calls
        validation_calls += 1
        return _validation_report(
            run=current_run,
            agent=current_agent,
            status="passed",
            summary="unexpected",
        )

    graph = TeamRoleGraph(
        team_repository=teams,
        provider_resolver=lambda _: SequenceProvider([_turn(content="No change.")]),
        validation_plan_resolver=lambda _: _validation_plan(),
        validation_runner=validation_runner,
    )
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    run = run.model_copy(update={"workspace_path": workspace})
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.TEAMMATE,
            allowed_tools=["repo.diff"],
            can_write=True,
        )
    )

    await graph.execute(run, agent, repository=runtime)

    assert validation_calls == 0


@pytest.mark.asyncio
async def test_teammate_runs_model_tool_loop_and_writes_patch_artifact(
    tmp_path: Path,
) -> None:
    workspace = _git_workspace(tmp_path)
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    artifacts = InMemoryArtifactMetadataRepository()
    provider = SequenceProvider(
        [
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="apply",
                        name="repo.apply_patch",
                        arguments_json=(
                            '{"patch":"diff --git a/README.md b/README.md\\n'
                            "--- a/README.md\\n+++ b/README.md\\n"
                            '@@ -1 +1,2 @@\\n fixture\\n+team role update\\n"}'
                        ),
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="diff",
                        name="repo.diff",
                        arguments_json="{}",
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            _turn(content="Changed README.md after inspecting the diff."),
        ]
    )
    graph = TeamRoleGraph(
        team_repository=teams,
        provider_resolver=lambda _: provider,
        artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
        artifact_repository=artifacts,
        validation_plan_resolver=lambda _: _validation_plan(),
        validation_runner=lambda plan, current_run, current_agent: _async_report(
            _validation_report(
                run=current_run,
                agent=current_agent,
                status="passed",
                summary="Validation passed with 1 gate(s).",
            )
        ),
    )
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    run = run.model_copy(update={"workspace_path": workspace})
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.TEAMMATE,
            allowed_tools=["repo.read", "repo.diff", "repo.apply_patch"],
            can_write=True,
        )
    )
    events: list[tuple[object, dict[str, object], str]] = []

    async def emit(
        event_type: object,
        payload: dict[str, object],
        transition_id: str,
    ) -> None:
        events.append((event_type, payload, transition_id))

    state, recovered = await graph.execute(
        run, agent, repository=runtime, event_sink=emit
    )

    result = (await teams.list_child_results(run.parent_run_id or run.id))[0]
    assert not recovered
    assert state["phase"] == "completed"
    assert "team role update" in (workspace / "README.md").read_text(encoding="utf-8")
    assert result.patch_artifact_id is not None
    assert result.changed_files == ["README.md"]
    patch = (await artifacts.get(result.patch_artifact_id)).path.read_text(
        encoding="utf-8"
    )
    assert "+team role update" in patch
    assert [tool.name for request in provider.requests for tool in request.tools] == [
        "repo.read",
        "repo.diff",
        "repo.apply_patch",
        "repo.read",
        "repo.diff",
        "repo.apply_patch",
        "repo.read",
        "repo.diff",
        "repo.apply_patch",
    ]
    assert any(
        payload.get("root_run_id") == str(run.root_run_id)
        and payload.get("agent_id") == str(agent.id)
        for _, payload, _ in events
    )


@pytest.mark.asyncio
async def test_teammate_model_and_tool_calls_use_team_agent_loop(
    tmp_path: Path,
) -> None:
    workspace = _git_workspace(tmp_path)
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    recorder = RecordingTeamMiddleware()
    provider = SequenceProvider(
        [
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="read",
                        name="repo.read",
                        arguments_json='{"path":"README.md"}',
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            _turn(content="Read README.md."),
        ]
    )
    graph = TeamRoleGraph(
        team_repository=teams,
        provider_resolver=lambda _: provider,
        team_loop=TeamAgentLoop(middleware_stack=MiddlewareStack([recorder])),
    )
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    run = run.model_copy(update={"workspace_path": workspace})
    await runtime.create_run(run, agent)
    assignment = _assignment(
        run,
        kind=TeamAssignmentKind.TEAMMATE,
        allowed_tools=["repo.read"],
        can_write=False,
    )
    await teams.create_assignment(assignment)

    state, recovered = await graph.execute(run, agent, repository=runtime)

    assert not recovered
    assert state["phase"] == "completed"
    assert [item["stage"] for item in recorder.calls] == [
        "wrap_model_call",
        "wrap_tool_call",
        "wrap_model_call",
    ]
    assert recorder.calls[0]["assignment_id"] == str(assignment.id)
    assert recorder.calls[0]["team_role"] == "teammate"
    assert recorder.calls[0]["agent_kind"] == "teammate"
    assert recorder.calls[1]["tool"] == "repo.read"
    assert "README.md" not in str(recorder.calls[1])


@pytest.mark.asyncio
async def test_write_teammate_must_diff_after_write(tmp_path: Path) -> None:
    workspace = _git_workspace(tmp_path)
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    provider = SequenceProvider(
        [
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="apply",
                        name="repo.apply_patch",
                        arguments_json=(
                            '{"patch":"diff --git a/README.md b/README.md\\n'
                            "--- a/README.md\\n+++ b/README.md\\n"
                            '@@ -1 +1,2 @@\\n fixture\\n+team role update\\n"}'
                        ),
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            _turn(content="Done without diff."),
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="diff",
                        name="repo.diff",
                        arguments_json="{}",
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            _turn(content="Done after diff reminder."),
        ]
    )
    graph = TeamRoleGraph(
        team_repository=teams,
        provider_resolver=lambda _: provider,
        validation_plan_resolver=lambda _: _validation_plan(),
        validation_runner=lambda plan, current_run, current_agent: _async_report(
            _validation_report(
                run=current_run,
                agent=current_agent,
                status="passed",
                summary="Validation passed with 1 gate(s).",
            )
        ),
    )
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    run = run.model_copy(update={"workspace_path": workspace})
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.TEAMMATE,
            allowed_tools=["repo.apply_patch", "repo.diff"],
            can_write=True,
        )
    )

    state, _ = await graph.execute(run, agent, repository=runtime)

    assert state["phase"] == "completed"
    assert any(
        "call repo.diff after the last write" in message.content
        for request in provider.requests
        for message in request.messages
    )
    assert len(provider.requests) == 4


@pytest.mark.asyncio
async def test_read_only_teammate_rejects_write_tool_without_execution(
    tmp_path: Path,
) -> None:
    workspace = _git_workspace(tmp_path)
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    provider = SequenceProvider(
        [
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="apply",
                        name="repo.apply_patch",
                        arguments_json='{"patch":"bad"}',
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="read",
                        name="repo.read",
                        arguments_json='{"path":"README.md"}',
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            _turn(content="No write executed; README was inspected."),
        ]
    )
    graph = TeamRoleGraph(team_repository=teams, provider_resolver=lambda _: provider)
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    run = run.model_copy(update={"workspace_path": workspace})
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.TEAMMATE,
            allowed_tools=["repo.read"],
            can_write=False,
        )
    )

    state, _ = await graph.execute(run, agent, repository=runtime)

    result = (await teams.list_child_results(run.parent_run_id or run.id))[0]
    assert state["phase"] == "completed"
    assert result.patch_artifact_id is None
    assert (workspace / "README.md").read_text(encoding="utf-8") == "fixture\n"
    assert len(provider.requests) == 3


@pytest.mark.asyncio
async def test_teammate_can_create_dynamic_subagent(tmp_path: Path) -> None:
    workspace = _git_workspace(tmp_path)
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    provider = SequenceProvider(
        [
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="subagent-1",
                        name="team.create_subagent",
                        arguments_json=(
                            '{"goal":"Read README for focused evidence",'
                            '"allowed_tools":["repo.read"],'
                            '"allowed_skills":["repository-inspection"],'
                            '"acceptance_criteria":["Return README evidence."]}'
                        ),
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            )
        ]
    )
    graph = TeamRoleGraph(team_repository=teams, provider_resolver=lambda _: provider)
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    run = run.model_copy(update={"workspace_path": workspace})
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.TEAMMATE,
            allowed_tools=["repo.read", "team.create_subagent"],
            allowed_skills=["repository-inspection"],
            can_delegate=True,
            max_subagents=3,
        )
    )
    events: list[tuple[object, dict[str, object], str]] = []

    async def emit(
        event_type: object,
        payload: dict[str, object],
        transition_id: str,
    ) -> None:
        events.append((event_type, payload, transition_id))

    with pytest.raises(ChildRunWait, match="waiting_subagents"):
        await graph.execute(run, agent, repository=runtime, event_sink=emit)

    children = await runtime.list_child_runs(run.id)
    assignments = await teams.list_assignments(run.root_run_id or run.id)
    subagent_assignment = next(
        item for item in assignments if item.kind is TeamAssignmentKind.SUBAGENT
    )
    assert len(children) == 1
    assert children[0].depth == 2
    assert children[0].dispatch_status is DispatchStatus.QUEUED
    assert subagent_assignment.child_run_id == children[0].id
    assert subagent_assignment.allowed_tools == ["repo.read"]
    assert subagent_assignment.can_write is False
    assert subagent_assignment.can_delegate is False
    assert (
        subagent_assignment.handoff_context["created_by_tool_call_id"] == "subagent-1"
    )
    team_events = [
        event[0]
        for event in events
        if event[0]
        in {
            EventType.TEAM_SUBAGENT_REQUESTED,
            EventType.TEAM_CHILD_RUN_CREATED,
            EventType.TEAM_ASSIGNMENT_CREATED,
        }
    ]
    assert team_events == [
        EventType.TEAM_SUBAGENT_REQUESTED,
        EventType.TEAM_CHILD_RUN_CREATED,
        EventType.TEAM_ASSIGNMENT_CREATED,
    ]


@pytest.mark.asyncio
async def test_subagent_cannot_create_dynamic_subagent(tmp_path: Path) -> None:
    workspace = _git_workspace(tmp_path)
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    provider = SequenceProvider(
        [
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="nested",
                        name="team.create_subagent",
                        arguments_json=(
                            '{"goal":"nested",'
                            '"allowed_tools":["repo.read"],'
                            '"allowed_skills":[],'
                            '"acceptance_criteria":["no nesting"]}'
                        ),
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            )
        ]
    )
    graph = TeamRoleGraph(team_repository=teams, provider_resolver=lambda _: provider)
    run, agent = _role_run(kind=TeamAssignmentKind.SUBAGENT)
    run = run.model_copy(update={"workspace_path": workspace})
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.SUBAGENT,
            allowed_tools=["repo.read", "team.create_subagent"],
        )
    )

    with pytest.raises(PermanentExecutionError, match="only teammates can create"):
        await graph.execute(run, agent, repository=runtime)


@pytest.mark.asyncio
async def test_teammate_dynamic_subagent_active_limit(tmp_path: Path) -> None:
    workspace = _git_workspace(tmp_path)
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    provider = SequenceProvider(
        [
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="too-many",
                        name="team.create_subagent",
                        arguments_json=(
                            '{"goal":"extra",'
                            '"allowed_tools":["repo.read"],'
                            '"allowed_skills":[],'
                            '"acceptance_criteria":["limit"]}'
                        ),
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            )
        ]
    )
    graph = TeamRoleGraph(team_repository=teams, provider_resolver=lambda _: provider)
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    run = run.model_copy(update={"workspace_path": workspace})
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.TEAMMATE,
            allowed_tools=["repo.read", "team.create_subagent"],
            can_delegate=True,
            max_subagents=3,
        )
    )
    for index in range(3):
        child = Run(
            goal=f"subagent {index}",
            mode=RunMode.TEAM,
            parent_run_id=run.id,
            root_run_id=run.root_run_id,
            depth=2,
            child_role="subagent",
            runtime_route=TEAM_ROLE_ROUTE,
        )
        await runtime.create_run(
            child,
            Agent(
                run_id=child.id,
                parent_agent_id=agent.id,
                kind=AgentKind.SUBAGENT,
                profile="subagent",
                model="fake",
            ),
        )
        await teams.create_assignment(
            TeamAssignment(
                root_run_id=run.root_run_id or run.id,
                parent_run_id=run.id,
                child_run_id=child.id,
                kind=TeamAssignmentKind.SUBAGENT,
                role_profile="subagent",
                runtime_route=TEAM_ROLE_ROUTE,
                goal=child.goal,
                allowed_tools=["repo.read"],
            )
        )

    with pytest.raises(ChildRunWait, match="waiting_subagents"):
        await graph.execute(run, agent, repository=runtime)
    assert provider.requests == []


@pytest.mark.asyncio
async def test_completed_subagent_result_is_injected_into_teammate_context(
    tmp_path: Path,
) -> None:
    workspace = _git_workspace(tmp_path)
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    provider = SequenceProvider([_turn(content="Used subagent evidence.")])
    graph = TeamRoleGraph(team_repository=teams, provider_resolver=lambda _: provider)
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    run = run.model_copy(update={"workspace_path": workspace})
    await runtime.create_run(run, agent)
    teammate_assignment = _assignment(
        run,
        kind=TeamAssignmentKind.TEAMMATE,
        allowed_tools=["repo.read", "team.create_subagent"],
        can_delegate=True,
        max_subagents=3,
    )
    await teams.create_assignment(teammate_assignment)
    child = Run(
        goal="read evidence",
        mode=RunMode.TEAM,
        parent_run_id=run.id,
        root_run_id=run.root_run_id,
        depth=2,
        child_role="subagent",
        runtime_route=TEAM_ROLE_ROUTE,
    )
    await runtime.create_run(
        child,
        Agent(
            run_id=child.id,
            parent_agent_id=agent.id,
            kind=AgentKind.SUBAGENT,
            profile="subagent",
            model="fake",
        ),
    )
    sub_assignment = TeamAssignment(
        root_run_id=run.root_run_id or run.id,
        parent_run_id=run.id,
        child_run_id=child.id,
        kind=TeamAssignmentKind.SUBAGENT,
        status=TeamAssignmentStatus.COMPLETED,
        role_profile="subagent",
        runtime_route=TEAM_ROLE_ROUTE,
        goal=child.goal,
        allowed_tools=["repo.read"],
    )
    await teams.create_assignment(sub_assignment)
    await teams.record_child_result(
        TeamChildResult(
            assignment_id=sub_assignment.id,
            child_run_id=child.id,
            parent_run_id=run.id,
            root_run_id=run.root_run_id or run.id,
            status="completed",
            summary="Subagent found README evidence.",
        )
    )

    await graph.execute(run, agent, repository=runtime)

    request_text = "\n".join(
        message.content for message in provider.requests[0].messages
    )
    assert "Subagent found README evidence." in request_text


@pytest.mark.asyncio
async def test_teammate_receives_mailbox_tools_and_directory(
    tmp_path: Path,
) -> None:
    workspace = _git_workspace(tmp_path)
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    provider = SequenceProvider(
        [
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="read",
                        name="repo.read",
                        arguments_json='{"path":"README.md"}',
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            _turn(content="Read README and saw mailbox tools."),
        ]
    )
    graph = TeamRoleGraph(team_repository=teams, provider_resolver=lambda _: provider)
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    run = run.model_copy(update={"workspace_path": workspace})
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.TEAMMATE,
            role_profile="backend-engineer",
            allowed_tools=["repo.read", "team.mailbox_list", "team.mailbox_send"],
            can_write=False,
        )
    )
    sibling = Run(
        goal="QA teammate",
        mode=RunMode.TEAM,
        parent_run_id=run.parent_run_id,
        root_run_id=run.root_run_id,
        depth=1,
        child_role="teammate",
        runtime_route=TEAM_ROLE_ROUTE,
        workspace_path=workspace,
    )
    await runtime.create_run(
        sibling,
        Agent(
            run_id=sibling.id,
            kind=AgentKind.TEAMMATE,
            profile="qa-engineer",
            model="fake",
        ),
    )
    await teams.create_assignment(
        TeamAssignment(
            root_run_id=run.root_run_id or run.id,
            parent_run_id=run.parent_run_id or run.id,
            child_run_id=sibling.id,
            kind=TeamAssignmentKind.TEAMMATE,
            role_profile="qa-engineer",
            runtime_route=TEAM_ROLE_ROUTE,
            goal=sibling.goal,
            allowed_tools=["repo.read", "team.mailbox_list", "team.mailbox_send"],
        )
    )

    await graph.execute(run, agent, repository=runtime)

    first_request = provider.requests[0]
    assert [tool.name for tool in first_request.tools] == [
        "repo.read",
        "team.mailbox_list",
        "team.mailbox_send",
    ]
    prompt_text = "\n".join(message.content for message in first_request.messages)
    assert f"- leader root: run_id={run.root_run_id}" in prompt_text
    assert f"- teammate qa-engineer: run_id={sibling.id}" in prompt_text
    directory_lines = [
        line
        for line in prompt_text.splitlines()
        if line.startswith("- leader root:") or line.startswith("- teammate ")
    ]
    assert all("subagent" not in line.lower() for line in directory_lines)
    assert all("verifier" not in line.lower() for line in directory_lines)


@pytest.mark.asyncio
async def test_teammate_can_send_mailbox_message_to_sibling(
    tmp_path: Path,
) -> None:
    workspace = _git_workspace(tmp_path)
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    run = run.model_copy(update={"workspace_path": workspace})
    sibling = await _create_sibling_teammate(
        runtime,
        teams,
        run,
        role_profile="qa-engineer",
        workspace=workspace,
    )
    provider = SequenceProvider(
        [
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="ask-qa",
                        name="team.mailbox_send",
                        arguments_json=json.dumps(
                            {
                                "recipient_run_id": str(sibling.id),
                                "message_type": "question",
                                "subject": "Response field",
                                "body_summary": (
                                    "Please confirm the response field name."
                                ),
                                "requires_response": True,
                            }
                        ),
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="read",
                        name="repo.read",
                        arguments_json='{"path":"README.md"}',
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            _turn(content="Asked QA and read README."),
        ]
    )
    graph = TeamRoleGraph(team_repository=teams, provider_resolver=lambda _: provider)
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.TEAMMATE,
            role_profile="backend-engineer",
            allowed_tools=["repo.read", "team.mailbox_send"],
            can_write=False,
        )
    )
    events: list[tuple[object, dict[str, object], str]] = []

    async def emit(
        event_type: object,
        payload: dict[str, object],
        transition_id: str,
    ) -> None:
        events.append((event_type, payload, transition_id))

    state, _ = await graph.execute(run, agent, repository=runtime, event_sink=emit)

    messages = await teams.list_mailbox_messages(run.root_run_id or run.id)
    assert state["phase"] == "completed"
    assert len(messages) == 1
    assert messages[0].route is MailboxRoute.TEAMMATE_TO_TEAMMATE
    assert messages[0].message_type is MailboxMessageType.QUESTION
    assert messages[0].sender_run_id == run.id
    assert messages[0].recipient_run_id == sibling.id
    assert messages[0].requires_response is True
    assert EventType.TEAM_MAILBOX_MESSAGE_CREATED in {
        event_type for event_type, _, _ in events
    }


@pytest.mark.asyncio
async def test_mailbox_tool_does_not_satisfy_read_only_evidence(
    tmp_path: Path,
) -> None:
    workspace = _git_workspace(tmp_path)
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    run = run.model_copy(update={"workspace_path": workspace})
    sibling = await _create_sibling_teammate(
        runtime,
        teams,
        run,
        role_profile="qa-engineer",
        workspace=workspace,
    )
    provider = SequenceProvider(
        [
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="status",
                        name="team.mailbox_send",
                        arguments_json=json.dumps(
                            {
                                "recipient_run_id": str(sibling.id),
                                "message_type": "status",
                                "subject": "Status",
                                "body_summary": "Coordination note only.",
                            }
                        ),
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            _turn(content="Done after mailbox only."),
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="read",
                        name="repo.read",
                        arguments_json='{"path":"README.md"}',
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            _turn(content="Now read README."),
        ]
    )
    graph = TeamRoleGraph(team_repository=teams, provider_resolver=lambda _: provider)
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.TEAMMATE,
            role_profile="backend-engineer",
            allowed_tools=["repo.read", "team.mailbox_send"],
            can_write=False,
        )
    )

    state, _ = await graph.execute(run, agent, repository=runtime)

    assert state["phase"] == "completed"
    assert len(provider.requests) == 4
    feedback_text = "\n".join(
        message.content for message in provider.requests[2].messages
    )
    assert "Do not finish yet" in feedback_text


@pytest.mark.asyncio
async def test_subagent_cannot_use_mailbox_tool_even_if_granted(
    tmp_path: Path,
) -> None:
    workspace = _git_workspace(tmp_path)
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    provider = SequenceProvider(
        [
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="mail",
                        name="team.mailbox_send",
                        arguments_json=json.dumps(
                            {
                                "recipient_run_id": str(uuid4()),
                                "message_type": "status",
                                "subject": "Invalid",
                                "body_summary": "Subagents must not use mailbox.",
                            }
                        ),
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="read",
                        name="repo.read",
                        arguments_json='{"path":"README.md"}',
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            _turn(content="Read README after mailbox denial."),
        ]
    )
    graph = TeamRoleGraph(team_repository=teams, provider_resolver=lambda _: provider)
    run, agent = _role_run(kind=TeamAssignmentKind.SUBAGENT)
    run = run.model_copy(update={"workspace_path": workspace})
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.SUBAGENT,
            allowed_tools=["repo.read", "team.mailbox_send"],
        )
    )

    state, _ = await graph.execute(run, agent, repository=runtime)

    assert state["phase"] == "completed"
    assert await teams.list_mailbox_messages(run.root_run_id or run.id) == []
    tool_text = "\n".join(message.content for message in provider.requests[1].messages)
    assert "Tool team.mailbox_send is not allowed for this assignment." in tool_text


@pytest.mark.asyncio
async def test_teammate_mailbox_response_marks_original_responded(
    tmp_path: Path,
) -> None:
    workspace = _git_workspace(tmp_path)
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    run = run.model_copy(update={"workspace_path": workspace})
    sibling = await _create_sibling_teammate(
        runtime,
        teams,
        run,
        role_profile="backend-engineer",
        workspace=workspace,
    )
    original = await teams.create_mailbox_message(
        MailboxMessage(
            team_root_run_id=run.root_run_id or run.id,
            sender_run_id=sibling.id,
            recipient_run_id=run.id,
            route=MailboxRoute.TEAMMATE_TO_TEAMMATE,
            message_type=MailboxMessageType.QUESTION,
            subject="Response field",
            body_summary="What response field should backend use?",
            requires_response=True,
        )
    )
    provider = SequenceProvider(
        [
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="list-mail",
                        name="team.mailbox_list",
                        arguments_json='{"limit":5}',
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="respond",
                        name="team.mailbox_send",
                        arguments_json=json.dumps(
                            {
                                "recipient_run_id": str(sibling.id),
                                "message_type": "status",
                                "subject": "Response field",
                                "body_summary": "Use response_text.",
                                "response_to_message_id": str(original.id),
                            }
                        ),
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="read",
                        name="repo.read",
                        arguments_json='{"path":"README.md"}',
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            ),
            _turn(content="Answered mailbox and read README."),
        ]
    )
    graph = TeamRoleGraph(team_repository=teams, provider_resolver=lambda _: provider)
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.TEAMMATE,
            role_profile="qa-engineer",
            allowed_tools=["repo.read", "team.mailbox_list", "team.mailbox_send"],
            can_write=False,
        )
    )
    events: list[tuple[object, dict[str, object], str]] = []

    async def emit(
        event_type: object,
        payload: dict[str, object],
        transition_id: str,
    ) -> None:
        events.append((event_type, payload, transition_id))

    await graph.execute(run, agent, repository=runtime, event_sink=emit)

    messages = await teams.list_mailbox_messages(run.root_run_id or run.id)
    restored_original = next(
        message for message in messages if message.id == original.id
    )
    response = next(
        message for message in messages if message.response_to_message_id == original.id
    )
    assert restored_original.status is MailboxMessageStatus.RESPONDED
    assert restored_original.read_at is not None
    assert response.sender_run_id == run.id
    assert response.recipient_run_id == sibling.id
    assert EventType.TEAM_MAILBOX_MESSAGE_READ in {
        event_type for event_type, _, _ in events
    }
    assert EventType.TEAM_MAILBOX_MESSAGE_RESPONDED in {
        event_type for event_type, _, _ in events
    }


@pytest.mark.asyncio
async def test_legacy_subagent_goals_receive_resolved_read_only_tools() -> None:
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    graph = TeamRoleGraph(team_repository=teams)
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.TEAMMATE,
            allowed_tools=[
                "repo.read",
                "repo.apply_patch",
                "team.mailbox_send",
                "team.create_subagent",
            ],
            can_write=True,
            can_delegate=True,
            max_subagents=2,
            handoff_context={"subagent_goals": ["Read README"]},
        )
    )

    with pytest.raises(ChildRunWait, match="waiting_subagents"):
        await graph.execute(run, agent, repository=runtime)

    assignments = await teams.list_assignments(run.root_run_id or run.id)
    subagent_assignment = next(
        item for item in assignments if item.kind is TeamAssignmentKind.SUBAGENT
    )
    assert subagent_assignment.allowed_tools == ["repo.read"]
    assert not subagent_assignment.can_write
    assert not subagent_assignment.can_delegate


@pytest.mark.asyncio
async def test_dynamic_subagent_grant_rejects_non_read_only_tools_via_policy(
    tmp_path: Path,
) -> None:
    workspace = _git_workspace(tmp_path)
    runtime = InMemoryRuntimeRepository()
    teams = InMemoryTeamRepository()
    provider = SequenceProvider(
        [
            _turn(
                tool_calls=[
                    ToolCall(
                        call_id="delegate",
                        name="team.create_subagent",
                        arguments_json=json.dumps(
                            {
                                "goal": "Inspect repository.",
                                "allowed_tools": ["repo.read", "team.mailbox_send"],
                                "allowed_skills": [],
                                "acceptance_criteria": ["Return README evidence."],
                            }
                        ),
                    )
                ],
                stop_reason=StopReason.TOOL_CALLS,
            )
        ]
    )
    graph = TeamRoleGraph(team_repository=teams, provider_resolver=lambda _: provider)
    run, agent = _role_run(kind=TeamAssignmentKind.TEAMMATE)
    run = run.model_copy(update={"workspace_path": workspace})
    await runtime.create_run(run, agent)
    await teams.create_assignment(
        _assignment(
            run,
            kind=TeamAssignmentKind.TEAMMATE,
            allowed_tools=["repo.read", "team.mailbox_send", "team.create_subagent"],
            can_delegate=True,
            max_subagents=1,
        )
    )

    with pytest.raises(PermanentExecutionError, match="subagent tools must be read-only"):
        await graph.execute(run, agent, repository=runtime)

    assert await runtime.list_child_runs(run.id) == []


def _role_run(kind: TeamAssignmentKind) -> tuple[Run, Agent]:
    root_id = Run(goal="root", mode=RunMode.TEAM).id
    parent_id = root_id if kind is TeamAssignmentKind.TEAMMATE else Run(goal="p").id
    run = Run(
        goal=kind.value,
        mode=RunMode.TEAM,
        intent=RunIntent.MODIFYING,
        parent_run_id=parent_id,
        root_run_id=root_id,
        depth=(1 if kind is TeamAssignmentKind.TEAMMATE else 2),
        child_role=kind.value,
        runtime_route=TEAM_ROLE_ROUTE,
    )
    agent = Agent(
        run_id=run.id,
        kind=(
            AgentKind.TEAMMATE
            if kind is TeamAssignmentKind.TEAMMATE
            else AgentKind.SUBAGENT
        ),
        profile=kind.value,
        model="fake",
    )
    return run, agent


def _assignment(
    run: Run,
    *,
    kind: TeamAssignmentKind,
    role_profile: str | None = None,
    allowed_tools: list[str] | None = None,
    deferred_tools: list[str] | None = None,
    promoted_tools: list[str] | None = None,
    allowed_skills: list[str] | None = None,
    can_write: bool = False,
    can_delegate: bool = False,
    max_subagents: int = 0,
    handoff_context: dict[str, object] | None = None,
) -> TeamAssignment:
    return TeamAssignment(
        root_run_id=run.root_run_id or run.id,
        parent_run_id=run.parent_run_id or run.id,
        child_run_id=run.id,
        kind=kind,
        role_profile=role_profile or kind.value,
        runtime_route=TEAM_ROLE_ROUTE,
        goal=run.goal,
        allowed_tools=allowed_tools or [],
        deferred_tools=deferred_tools or [],
        promoted_tools=promoted_tools or [],
        allowed_skills=allowed_skills or [],
        can_write=can_write,
        can_delegate=can_delegate,
        max_subagents=max_subagents,
        handoff_context=handoff_context or {},
    )


async def _create_sibling_teammate(
    runtime: InMemoryRuntimeRepository,
    teams: InMemoryTeamRepository,
    run: Run,
    *,
    role_profile: str,
    workspace: Path,
) -> Run:
    sibling = Run(
        goal=f"{role_profile} teammate",
        mode=RunMode.TEAM,
        parent_run_id=run.parent_run_id,
        root_run_id=run.root_run_id,
        depth=1,
        child_role="teammate",
        runtime_route=TEAM_ROLE_ROUTE,
        workspace_path=workspace,
    )
    await runtime.create_run(
        sibling,
        Agent(
            run_id=sibling.id,
            kind=AgentKind.TEAMMATE,
            profile=role_profile,
            model="fake",
        ),
    )
    await teams.create_assignment(
        TeamAssignment(
            root_run_id=run.root_run_id or run.id,
            parent_run_id=run.parent_run_id or run.id,
            child_run_id=sibling.id,
            kind=TeamAssignmentKind.TEAMMATE,
            role_profile=role_profile,
            runtime_route=TEAM_ROLE_ROUTE,
            goal=sibling.goal,
            allowed_tools=["repo.read", "team.mailbox_list", "team.mailbox_send"],
        )
    )
    return sibling


def _turn(
    *,
    content: str = "",
    tool_calls: list[ToolCall] | None = None,
    stop_reason: StopReason = StopReason.COMPLETED,
) -> ModelTurn:
    return ModelTurn(
        assistant=AssistantMessage(content=content, tool_calls=tool_calls or []),
        stop_reason=stop_reason,
        model="fake-model",
        provider="fake",
    )


def _validation_plan() -> ValidationPlan:
    return _validation_plan_with_rework(0)


def _validation_plan_with_rework(max_rework_attempts: int) -> ValidationPlan:
    return ValidationPlan(
        gates=[
            ValidationGate(
                id="unit",
                name="Unit validation",
                command=["python", "-m", "pytest", "-q"],
                required=True,
                timeout_seconds=30,
            )
        ],
        source="configured",
        max_rework_attempts=max_rework_attempts,
    )


def _validation_report(
    *,
    run: Run,
    agent: Agent,
    status: str,
    summary: str,
    failure_kind: str | None = None,
    attempt: int = 0,
    gate_status: str | None = None,
) -> ValidationReportWithGates:
    report = DurableValidationReport(
        run_id=run.id,
        agent_id=agent.id,
        attempt=attempt,
        status=status,
        summary=summary,
    )
    gate_status = gate_status or status
    gate = DurableValidationGateResult(
        report_id=report.id,
        run_id=run.id,
        gate_id="unit",
        name="Unit validation",
        command=["python", "-m", "pytest", "-q"],
        required=True,
        status=gate_status,
        exit_code=0 if status == "passed" else 1,
        failure_kind=failure_kind,
        stdout_summary=summary if status == "passed" else "",
        stderr_summary=summary if status != "passed" else "",
    )
    return ValidationReportWithGates(report=report, gates=[gate])


async def _async_report(
    report: ValidationReportWithGates,
) -> ValidationReportWithGates:
    return report


def _readme_update_patch() -> str:
    return (
        "diff --git a/README.md b/README.md\n"
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1 +1,2 @@\n"
        " fixture\n"
        "+team role update\n"
    )


def _readme_bad_update_patch() -> str:
    return (
        "diff --git a/README.md b/README.md\n"
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1 +1,2 @@\n"
        " fixture\n"
        "+bad update\n"
    )


def _readme_fix_update_patch() -> str:
    return (
        "diff --git a/README.md b/README.md\n"
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1,2 +1,2 @@\n"
        " fixture\n"
        "-bad update\n"
        "+fixed update\n"
    )


def _git_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "repository"
    workspace.mkdir()
    _git(workspace, "init")
    _git(workspace, "config", "user.email", "test@example.com")
    _git(workspace, "config", "user.name", "Test")
    (workspace / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(workspace, "add", "README.md")
    _git(workspace, "commit", "-m", "Initial")
    return workspace


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
    name = "recording-team-role"

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

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
        if stage in {
            MiddlewareStage.WRAP_MODEL_CALL,
            MiddlewareStage.WRAP_TOOL_CALL,
        }:
            self.calls.append(
                {
                    "stage": stage.value,
                    **context.metadata,
                }
            )
        return await call_next(context)


class RecordingAgentOperationMiddleware:
    name = "recording-agent-operation"

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

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
        if stage is MiddlewareStage.BEFORE_AGENT:
            self.calls.append({"stage": stage.value, **context.metadata})
        return await call_next(context)
