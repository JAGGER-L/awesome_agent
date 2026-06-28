from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import MemorySaver

from awesome_agent.agents.profiles import RoleModelResolver
from awesome_agent.domain.enums import AgentKind, EventType, RunIntent, RunMode
from awesome_agent.domain.models import Agent, Run
from awesome_agent.modeling import (
    AssistantMessage,
    ModelRequest,
    ModelStreamEvent,
    ModelTurn,
    ModelUsage,
    StopReason,
    StructuredModelProvider,
    ToolCall,
    TurnCompleted,
)
from awesome_agent.persistence.budget import InMemoryBudgetRepository
from awesome_agent.persistence.tool_invocations import InMemoryToolInvocationRepository
from awesome_agent.persistence.validation import InMemoryValidationRepository
from awesome_agent.runtime.budget import BudgetDecision, BudgetPolicy
from awesome_agent.runtime.dispatch import PermanentExecutionError
from awesome_agent.runtime.graphs import (
    SCOPED_TEAM_CODING_VERSION,
    TEAM_CODING_GRAPH,
)
from awesome_agent.runtime.repository import InMemoryRuntimeRepository
from awesome_agent.runtime.team_graph import AgentAssignment, TeamCodingGraph

TEAM_CODING_VERSION = SCOPED_TEAM_CODING_VERSION


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


def _models() -> RoleModelResolver:
    return RoleModelResolver(
        leader_model="deepseek-v4-pro",
        teammate_model="deepseek-v4-flash",
        verifier_model="deepseek-v4-flash",
        subagent_model="deepseek-v4-flash",
    )


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


def _budget_policy(
    *,
    soft_context_tokens: int = 10_000,
    hard_context_tokens: int = 20_000,
    recent_context_tokens: int = 5_000,
    max_total_tokens_per_run: int = 100_000,
    max_reasoning_tokens_per_run: int = 100_000,
    max_active_seconds_per_run: int = 3600,
) -> BudgetPolicy:
    return BudgetPolicy(
        soft_context_tokens=soft_context_tokens,
        hard_context_tokens=hard_context_tokens,
        recent_context_tokens=recent_context_tokens,
        max_total_tokens_per_run=max_total_tokens_per_run,
        max_reasoning_tokens_per_run=max_reasoning_tokens_per_run,
        max_active_seconds_per_run=max_active_seconds_per_run,
    )


def _turn(
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    reasoning_tokens: int | None = None,
) -> ModelTurn:
    return ModelTurn(
        assistant=AssistantMessage(content="ack"),
        stop_reason=StopReason.COMPLETED,
        model="deepseek-v4-flash",
        provider="fake",
        usage=ModelUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
        ),
    )


def _team_run(tmp_path: Path) -> tuple[Run, Agent]:
    run = Run(
        goal="Implement backend and verify it",
        mode=RunMode.TEAM,
        intent=RunIntent.MODIFYING,
        graph_name=TEAM_CODING_GRAPH,
        graph_version=TEAM_CODING_VERSION,
        graph_thread_id=f"run:{uuid4()}",
        workspace_path=tmp_path,
    )
    leader = Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="deepseek-v4-pro",
    )
    return run, leader


@pytest.mark.asyncio
async def test_team_graph_creates_durable_team_agents_with_subagent_lineage(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-m", "Initial")
    repository = InMemoryRuntimeRepository()
    tool_repository = InMemoryToolInvocationRepository()
    run, leader = _team_run(tmp_path)
    await repository.create_run(run, leader)
    events: list[tuple[EventType, dict[str, object], str]] = []

    async def emit(
        event_type: EventType,
        payload: dict[str, object],
        transition_id: str,
    ) -> None:
        events.append((event_type, payload, transition_id))

    graph = TeamCodingGraph(
        MemorySaver(),  # type: ignore[arg-type]
        model_resolver=_models(),
        tool_repository=tool_repository,
    )

    state, recovered = await graph.execute(
        run,
        leader,
        repository=repository,
        event_sink=emit,
    )

    assert not recovered
    assert state["phase"] == "verified"
    assert state["assignments"]["backend-engineer"]["allowed_tools"] == [
        "repo.status",
        "repo.list",
        "repo.search",
        "repo.read",
        "repo.instructions",
        "repo.diff",
        "repo.apply_patch",
        "artifact.read",
    ]
    assert state["evidence"]["repo-explorer"][0]["tool"] == "repo.status"
    assert state["evidence"]["backend-subagent"][0]["tool"] == "repo.read"
    assert state["evidence"]["backend-engineer"][0]["tool"] == "repo.apply_patch"
    assert state["evidence"]["backend-engineer"][1]["tool"] == "repo.diff"
    assert "team runtime update" in (tmp_path / "README.md").read_text(encoding="utf-8")
    agents = await repository.list_agents(run.id)
    assert [agent.kind for agent in agents] == [
        AgentKind.LEADER,
        AgentKind.TEAMMATE,
        AgentKind.TEAMMATE,
        AgentKind.VERIFIER,
        AgentKind.SUBAGENT,
    ]
    backend = next(agent for agent in agents if agent.profile == "backend-engineer")
    subagent = next(agent for agent in agents if agent.kind is AgentKind.SUBAGENT)
    assert subagent.parent_agent_id == backend.id
    assert subagent.profile == "repo-explorer"
    assert all(agent.model == "deepseek-v4-flash" for agent in agents[1:])
    agent_events = [event for event in events if event[0] is EventType.AGENT_CREATED]
    tool_events = [event for event in events if event[0] is EventType.TOOL_CALL_CREATED]
    assert len(agent_events) == 4
    assert len(tool_events) == 5
    created_profiles = [event[1]["profile"] for event in agent_events]
    assert created_profiles.count("backend-engineer") == 1
    assert created_profiles.count("repo-explorer") == 2
    assert created_profiles.count("verifier") == 1
    assert {event[1]["tool"] for event in tool_events} == {
        "repo.status",
        "repo.read",
        "repo.diff",
        "repo.apply_patch",
    }
    invocations = await tool_repository.list_for_run(run.id)
    assert [invocation.tool_name for invocation in invocations] == [
        "repo.status",
        "repo.read",
        "repo.apply_patch",
        "repo.diff",
        "repo.apply_patch",
    ]
    assert invocations[2].status == "completed"
    assert invocations[2].path_refs == ["README.md"]


@pytest.mark.asyncio
async def test_team_graph_calls_provider_for_bounded_role_steps(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-m", "Initial")
    repository = InMemoryRuntimeRepository()
    run, leader = _team_run(tmp_path)
    await repository.create_run(run, leader)
    provider = SequenceProvider([_turn(), _turn(), _turn(), _turn(), _turn()])
    events: list[tuple[EventType, dict[str, object], str]] = []

    async def emit(
        event_type: EventType,
        payload: dict[str, object],
        transition_id: str,
    ) -> None:
        events.append((event_type, payload, transition_id))

    graph = TeamCodingGraph(
        MemorySaver(),  # type: ignore[arg-type]
        model_resolver=_models(),
        provider_resolver=lambda _: provider,
        verification_outcomes=["passed"],
    )

    await graph.execute(run, leader, repository=repository, event_sink=emit)

    assert provider.requests
    model_events = [
        event for event in events if event[0] is EventType.MODEL_CALL_CREATED
    ]
    assert len(model_events) == len(provider.requests)
    assert {event[1]["status"] for event in model_events} == {"completed"}


@pytest.mark.asyncio
async def test_team_graph_tracks_global_budget_without_context_compaction(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-m", "Initial")
    repository = InMemoryRuntimeRepository()
    budget_repository = InMemoryBudgetRepository()
    run, leader = _team_run(tmp_path)
    await repository.create_run(run, leader)
    provider = SequenceProvider(
        [
            _turn(input_tokens=10, output_tokens=2, reasoning_tokens=1),
            _turn(input_tokens=10, output_tokens=2, reasoning_tokens=1),
            _turn(input_tokens=10, output_tokens=2, reasoning_tokens=1),
            _turn(input_tokens=10, output_tokens=2, reasoning_tokens=1),
            _turn(input_tokens=10, output_tokens=2, reasoning_tokens=1),
        ]
    )
    graph = TeamCodingGraph(
        MemorySaver(),  # type: ignore[arg-type]
        model_resolver=_models(),
        provider_resolver=lambda _: provider,
        verification_outcomes=["passed"],
        budget_repository=budget_repository,
        budget_policy=_budget_policy(),
    )

    state, _ = await graph.execute(run, leader, repository=repository)

    ledger = await budget_repository.get_ledger(run.id)
    assert ledger.model_call_count == len(provider.requests)
    assert ledger.total_input_tokens == 50
    assert ledger.total_output_tokens == 10
    assert ledger.total_reasoning_tokens == 5
    assert "rolling_summary" not in state
    assert "context_artifact_refs" not in state


@pytest.mark.asyncio
async def test_team_graph_fails_when_global_token_budget_is_exhausted(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-m", "Initial")
    repository = InMemoryRuntimeRepository()
    budget_repository = InMemoryBudgetRepository()
    run, leader = _team_run(tmp_path)
    await repository.create_run(run, leader)
    provider = SequenceProvider([_turn()])
    graph = TeamCodingGraph(
        MemorySaver(),  # type: ignore[arg-type]
        model_resolver=_models(),
        provider_resolver=lambda _: provider,
        verification_outcomes=["passed"],
        budget_repository=budget_repository,
        budget_policy=_budget_policy(max_total_tokens_per_run=3),
    )

    with pytest.raises(PermanentExecutionError, match="budget_exhausted"):
        await graph.execute(run, leader, repository=repository)

    assert provider.requests == []
    ledger = await budget_repository.get_ledger(run.id)
    assert ledger.threshold_status == BudgetDecision.EXHAUSTED.value


@pytest.mark.asyncio
async def test_team_graph_rejects_reworks_and_passes_verification(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-m", "Initial")
    repository = InMemoryRuntimeRepository()
    validation = InMemoryValidationRepository()
    run, leader = _team_run(tmp_path)
    await repository.create_run(run, leader)
    graph = TeamCodingGraph(
        MemorySaver(),  # type: ignore[arg-type]
        model_resolver=_models(),
        validation_repository=validation,
        verification_outcomes=["failed", "passed"],
    )

    state, _ = await graph.execute(run, leader, repository=repository)

    reports = await validation.list_for_run(run.id)
    todos = await repository.list_todos(run.id)
    assert state["phase"] == "verified"
    assert state["verification_rework_count"] == 1
    assert [item.report.status for item in reports] == ["failed", "passed"]
    assert len(todos) == 1
    assert todos[0].status.value == "done"
    assert "team runtime rework" in (tmp_path / "README.md").read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_team_graph_fails_after_verification_rejection_limit(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-m", "Initial")
    repository = InMemoryRuntimeRepository()
    run, leader = _team_run(tmp_path)
    await repository.create_run(run, leader)
    graph = TeamCodingGraph(
        MemorySaver(),  # type: ignore[arg-type]
        model_resolver=_models(),
        verification_outcomes=["failed", "failed"],
        max_verification_reworks=1,
    )

    with pytest.raises(PermanentExecutionError, match="verification_rejected"):
        await graph.execute(run, leader, repository=repository)


@pytest.mark.asyncio
async def test_team_graph_rejects_tools_outside_leader_assignment_scope(
    tmp_path: Path,
) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README.md").write_text("fixture\n", encoding="utf-8")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-m", "Initial")
    run = Run(
        goal="Implement backend and verify it",
        mode=RunMode.TEAM,
        intent=RunIntent.MODIFYING,
        graph_name=TEAM_CODING_GRAPH,
        graph_version=TEAM_CODING_VERSION,
        graph_thread_id=f"run:{uuid4()}",
        workspace_path=tmp_path,
    )
    agent = Agent(
        run_id=run.id,
        kind=AgentKind.TEAMMATE,
        profile="backend-engineer",
        model="deepseek-v4-flash",
    )
    graph = TeamCodingGraph(
        MemorySaver(),  # type: ignore[arg-type]
        model_resolver=_models(),
    )

    result = await graph.execute_scoped_repository_tool(
        run=run,
        agent=agent,
        assignment=AgentAssignment(
            profile="backend-engineer",
            allowed_tools=["repo.diff"],
            allowed_skills=["patch-authoring"],
            can_write=True,
            can_delegate=True,
            max_subagents=1,
            acceptance_criteria=["Inspect diff only."],
        ),
        call=ToolCall(
            call_id="shell",
            name="shell.execute",
            arguments_json='{"argv":["python","-V"]}',
        ),
    )

    assert result.is_error
    assert "not allowed" in result.content
