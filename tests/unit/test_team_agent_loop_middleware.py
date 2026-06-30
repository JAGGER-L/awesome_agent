from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from uuid import uuid4

import pytest
from opentelemetry.sdk.trace import TracerProvider
from tests.fakes import FakeModelProvider

from awesome_agent.domain.enums import AgentKind, RunIntent, RunMode
from awesome_agent.domain.models import Agent, Run
from awesome_agent.modeling import (
    AssistantMessage,
    ModelTurn,
    ModelUsage,
    StopReason,
    SystemMessage,
    ToolResultMessage,
)
from awesome_agent.observability.facade import ObservabilityFacade
from awesome_agent.observability.repository import InMemoryObservabilityRepository
from awesome_agent.runtime.agent_loop import (
    MiddlewareContext,
    MiddlewareDecision,
    MiddlewareStack,
    MiddlewareStage,
)
from awesome_agent.runtime.agent_loop.observability_middleware import (
    ObservabilityMiddleware,
)
from awesome_agent.runtime.agent_loop.team import TeamAgentLoop
from awesome_agent.runtime.agent_loop.team_middleware import (
    TeamPlanningMiddleware,
    TeamVerificationMiddleware,
)
from awesome_agent.runtime.team_assignments import (
    TeamAssignment,
    TeamAssignmentKind,
    TeamChildResult,
)


class RecordingTeamMiddleware:
    name = "recording-team"

    def __init__(self) -> None:
        self.handled: list[tuple[MiddlewareStage, dict[str, object]]] = []
        self.wrapped: list[tuple[MiddlewareStage, dict[str, object]]] = []

    async def handle(
        self,
        stage: MiddlewareStage,
        context: MiddlewareContext,
        call_next: Callable[[MiddlewareContext], Awaitable[MiddlewareDecision]],
    ) -> MiddlewareDecision:
        self.handled.append((stage, dict(context.metadata)))
        return await call_next(context)

    async def wrap_stage(
        self,
        stage: MiddlewareStage,
        context: MiddlewareContext,
        call_next: Callable[[MiddlewareContext], Awaitable[dict[str, object]]],
    ) -> dict[str, object]:
        self.wrapped.append((stage, dict(context.metadata)))
        return await call_next(context)


@pytest.mark.asyncio
async def test_team_loop_builds_structural_context_for_agent_operation() -> None:
    recorder = RecordingTeamMiddleware()
    loop = TeamAgentLoop(middleware_stack=MiddlewareStack([recorder]))
    run, agent = _team_run_and_agent()
    assignment_id = uuid4()
    messages = [SystemMessage(content="private planning prompt")]

    async def operation(state: dict[str, object]) -> dict[str, object]:
        return {**state, "handled": True}

    agent_state: dict[str, object] = {"phase": "planning"}
    result = await loop.run_agent_operation(
        agent_state,
        run=run,
        agent=agent,
        messages=messages,
        assignment_id=assignment_id,
        team_role="leader",
        agent_kind=AgentKind.LEADER.value,
        handler=operation,
    )

    assert result == {"phase": "planning", "handled": True}
    assert recorder.handled[0][0] is MiddlewareStage.BEFORE_AGENT
    metadata = recorder.handled[0][1]
    assert metadata["team_root_run_id"] == str(run.root_run_id or run.id)
    assert metadata["assignment_id"] == str(assignment_id)
    assert metadata["team_role"] == "leader"
    assert metadata["agent_kind"] == "leader"
    assert metadata["runtime_route"] == "team-coding"
    assert "private planning prompt" not in str(metadata)


@pytest.mark.asyncio
async def test_team_loop_wraps_model_and_tool_operations() -> None:
    recorder = RecordingTeamMiddleware()
    loop = TeamAgentLoop(middleware_stack=MiddlewareStack([recorder]))
    run, agent = _team_run_and_agent()

    async def model_operation(state: dict[str, object]) -> dict[str, object]:
        return {**state, "model": "called"}

    async def tool_operation(state: dict[str, object]) -> dict[str, object]:
        return {**state, "tool": "called"}

    model_state: dict[str, object] = {}
    model_result: dict[str, object] = await loop.wrap_model_call(
        model_state,
        run=run,
        agent=agent,
        messages=[SystemMessage(content="private model prompt")],
        team_role="teammate",
        agent_kind=AgentKind.TEAMMATE.value,
        metadata={"prompt": "must-not-enter-metadata"},
        handler=model_operation,
    )
    tool_state: dict[str, object] = {}
    tool_result: dict[str, object] = await loop.wrap_tool_call(
        tool_state,
        run=run,
        agent=agent,
        messages=[SystemMessage(content="private tool prompt")],
        team_role="teammate",
        agent_kind=AgentKind.TEAMMATE.value,
        metadata={
            "tool": "repo.apply_patch",
            "patch": "secret patch body",
            "tool_result": "raw tool result",
            "verifier_json": '{"decision":"passed"}',
        },
        handler=tool_operation,
    )

    assert model_result == {"model": "called"}
    assert tool_result == {"tool": "called"}
    assert [stage for stage, _ in recorder.wrapped] == [
        MiddlewareStage.WRAP_MODEL_CALL,
        MiddlewareStage.WRAP_TOOL_CALL,
    ]
    assert recorder.wrapped[0][1]["team_role"] == "teammate"
    tool_metadata = recorder.wrapped[1][1]
    assert tool_metadata["tool"] == "repo.apply_patch"
    assert "patch" not in tool_metadata
    assert "tool_result" not in tool_metadata
    assert "verifier_json" not in tool_metadata
    assert "secret patch body" not in str(tool_metadata)
    assert "raw tool result" not in str(tool_metadata)


def test_team_loop_installs_observability_middleware() -> None:
    facade = ObservabilityFacade(
        repository=InMemoryObservabilityRepository(),
        tracer=TracerProvider().get_tracer("test"),
    )

    loop = TeamAgentLoop(observability=facade)

    assert any(
        isinstance(middleware, ObservabilityMiddleware)
        for middleware in loop.middleware_stack.middleware
    )


def test_team_verification_middleware_rejects_invalid_attempt_budget() -> None:
    with pytest.raises(ValueError, match="verifier_model_output_attempts"):
        TeamVerificationMiddleware(
            provider_resolver=None,
            team_loop=TeamAgentLoop(),
            verifier_model_output_attempts=0,
        )


@pytest.mark.asyncio
async def test_team_planning_middleware_creates_plan_repair() -> None:
    run, leader = _leader_run()
    target = _assignment(run)
    provider = FakeModelProvider([_repair_json(str(target.child_run_id))])
    recorder = RepairRecordingMiddleware()
    middleware = TeamPlanningMiddleware(
        provider_resolver=lambda _: provider,
        team_loop=TeamAgentLoop(middleware_stack=MiddlewareStack([recorder])),
    )
    result = _result(run, target)
    events: list[tuple[object, dict[str, object], str]] = []

    async def emit(
        event_type: object, payload: dict[str, object], transition_id: str
    ) -> None:
        events.append((event_type, payload, transition_id))

    repair, attempt = await middleware.create_team_plan_repair(
        run,
        leader,
        assignments=[target],
        child_results=[result],
        verifier_child_run_id=uuid4(),
        verifier_feedback="Missing README evidence.",
        attempt=1,
        event_sink=emit,
    )

    assert attempt == 1
    assert repair.actions[0].action == "replace_teammate"
    assert repair.actions[0].target_child_run_id == str(target.child_run_id)
    assert recorder.model_call_metadata == [
        {
            "runtime_route": "team-coding",
            "team_root_run_id": str(run.id),
            "team_role": "leader",
            "agent_kind": "leader",
            "team_operation": "plan_repair",
            "attempt": 1,
        }
    ]
    assert "Leader repairing a coding-agent team plan" in recorder.model_prompt_text
    assert "Missing README evidence" in recorder.model_prompt_text
    assert len(provider.requests) == 1


@pytest.mark.asyncio
async def test_team_planning_middleware_retries_invalid_plan_repair_once() -> None:
    run, leader = _leader_run()
    target = _assignment(run)
    provider = FakeModelProvider(
        [
            json.dumps({"rationale": "bad", "actions": []}),
            _repair_json(str(target.child_run_id)),
        ]
    )
    middleware = TeamPlanningMiddleware(
        provider_resolver=lambda _: provider,
        team_loop=TeamAgentLoop(),
    )
    events: list[tuple[object, dict[str, object], str]] = []

    async def emit(
        event_type: object, payload: dict[str, object], transition_id: str
    ) -> None:
        events.append((event_type, payload, transition_id))

    repair, attempt = await middleware.create_team_plan_repair(
        run,
        leader,
        assignments=[target],
        child_results=[_result(run, target)],
        verifier_child_run_id=uuid4(),
        verifier_feedback="Missing evidence.",
        attempt=1,
        event_sink=emit,
    )

    assert attempt == 2
    assert repair.actions[0].action == "replace_teammate"
    assert events[0][1]["attempt"] == 1
    assert events[0][1]["operation"] == "plan_repair"


@pytest.mark.asyncio
async def test_team_loop_observability_records_direct_model_and_tool_results() -> None:
    repository = InMemoryObservabilityRepository()
    facade = ObservabilityFacade(
        repository=repository,
        tracer=TracerProvider().get_tracer("test"),
    )
    loop = TeamAgentLoop(observability=facade)
    run, agent = _team_run_and_agent()
    assignment_id = uuid4()

    async def model_operation(_: object) -> ModelTurn:
        return ModelTurn(
            assistant=AssistantMessage(content="team model answer"),
            stop_reason=StopReason.COMPLETED,
            provider="deepseek",
            model="deepseek-v4-flash",
            usage=ModelUsage(input_tokens=3, output_tokens=5),
        )

    async def tool_operation(_: object) -> ToolResultMessage:
        return ToolResultMessage(call_id="call-1", content="bounded result")

    async def agent_operation(_: object) -> dict[str, object]:
        await loop.wrap_model_call(
            object(),
            run=run,
            agent=agent,
            messages=[SystemMessage(content="private role prompt")],
            assignment_id=assignment_id,
            team_role="teammate",
            agent_kind=AgentKind.TEAMMATE.value,
            metadata={"team_operation": "role_model", "turn": 2},
            handler=model_operation,
        )
        await loop.wrap_tool_call(
            object(),
            run=run,
            agent=agent,
            messages=[SystemMessage(content="private tool prompt")],
            assignment_id=assignment_id,
            team_role="teammate",
            agent_kind=AgentKind.TEAMMATE.value,
            metadata={
                "team_operation": "role_tool",
                "turn": 2,
                "tool": "repo.read",
                "call_id": "call-1",
            },
            handler=tool_operation,
        )
        return {"done": True}

    result = await loop.run_agent_operation(
        object(),
        run=run,
        agent=agent,
        messages=[],
        assignment_id=assignment_id,
        team_role="teammate",
        agent_kind=AgentKind.TEAMMATE.value,
        metadata={"team_operation": "role_execute"},
        handler=agent_operation,
    )

    spans = await repository.list_spans_for_run(run.id)
    model_calls = await repository.list_model_calls_for_run(run.id)

    assert result == {"done": True}
    assert {span.name for span in spans} >= {"agent.run", "model.call", "tool.call"}
    assert len(model_calls) == 1
    assert model_calls[0].agent_id == agent.id
    assert model_calls[0].turn == 2
    assert model_calls[0].provider == "deepseek"
    assert model_calls[0].input_tokens == 3
    assert model_calls[0].output_tokens == 5
    tool_span = next(span for span in spans if span.name == "tool.call")
    assert tool_span.attributes["tool"] == "repo.read"
    assert tool_span.attributes["call_id"] == "call-1"
    assert tool_span.status == "completed"


def _team_run_and_agent() -> tuple[Run, Agent]:
    run = Run(
        goal="team task",
        mode=RunMode.TEAM,
        root_run_id=uuid4(),
        runtime_route="team-coding",
    )
    agent = Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="fake",
    )
    return run, agent


def _leader_run() -> tuple[Run, Agent]:
    run = Run(
        id=uuid4(),
        goal="Repair team output.",
        mode=RunMode.TEAM,
        intent=RunIntent.MODIFYING,
        runtime_route="team-coding",
    )
    leader = Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="leader-model",
    )
    return run, leader


def _assignment(run: Run) -> TeamAssignment:
    return TeamAssignment(
        root_run_id=run.id,
        parent_run_id=run.id,
        child_run_id=uuid4(),
        kind=TeamAssignmentKind.TEAMMATE,
        role_profile="backend-engineer",
        runtime_route="team-role",
        goal="Inspect README.",
        allowed_tools=["repo.read"],
        acceptance_criteria=["Return README evidence."],
    )


def _result(run: Run, assignment: TeamAssignment) -> TeamChildResult:
    return TeamChildResult(
        assignment_id=assignment.id,
        child_run_id=assignment.child_run_id,
        parent_run_id=run.id,
        root_run_id=run.id,
        status="completed",
        summary="No README evidence.",
        patch_aggregated=True,
    )


def _repair_json(target_child_run_id: str) -> str:
    return json.dumps(
        {
            "rationale": "Replace the teammate with a focused README inspection role.",
            "actions": [
                {
                    "action": "replace_teammate",
                    "target_child_run_id": target_child_run_id,
                    "reason": "Verifier reported missing README evidence.",
                    "teammate": {
                        "role_profile": "repository-inspector",
                        "goal": "Read README.md and return bounded evidence.",
                        "allowed_tools": ["repo.read"],
                        "deferred_tools": [],
                        "allowed_skills": ["repository-inspection"],
                        "can_write": False,
                        "can_delegate": False,
                        "max_subagents": 0,
                        "acceptance_criteria": ["Quote README evidence in the result."],
                    },
                }
            ],
        }
    )


class RepairRecordingMiddleware:
    name = "repair-recorder"

    def __init__(self) -> None:
        self.model_call_metadata: list[dict[str, object]] = []
        self.model_prompt_text = ""

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
