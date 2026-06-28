from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import MemorySaver

from awesome_agent.artifacts.repository import InMemoryArtifactMetadataRepository
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.domain.enums import AgentKind, RunIntent
from awesome_agent.domain.models import Agent, Run
from awesome_agent.modeling import (
    AssistantMessage,
    ModelRequest,
    ModelStreamEvent,
    ModelTurn,
    ModelUsage,
    StopReason,
    StructuredModelProvider,
    SystemMessage,
    ToolCall,
    ToolChoiceMode,
    ToolResultMessage,
    TurnCompleted,
    UserMessage,
)
from awesome_agent.persistence.budget import InMemoryBudgetRepository
from awesome_agent.runtime.budget import BudgetDecision, BudgetPolicy
from awesome_agent.runtime.context import (
    ContextManager,
    DeterministicSummaryProvider,
)
from awesome_agent.runtime.graphs import (
    READ_ONLY_CODING_ROUTE,
)
from awesome_agent.runtime.readonly_graph import (
    AgentLoopFailed,
    ReadOnlyAgentState,
    ReadOnlyCodingGraph,
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


def _run(workspace: Path) -> tuple[Run, Agent]:
    run = Run(
        goal="Explain the fixture",
        intent=RunIntent.READ_ONLY,
        runtime_route=READ_ONLY_CODING_ROUTE,
        graph_thread_id=f"run:{uuid4()}",
        workspace_path=workspace,
    )
    return run, Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="fake-model",
    )


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


def _node_state(
    run: Run,
    agent: Agent,
    messages: list[dict[str, Any]],
) -> ReadOnlyAgentState:
    return cast(
        ReadOnlyAgentState,
        {
            "run_id": str(run.id),
            "agent_id": str(agent.id),
            "runtime_route": READ_ONLY_CODING_ROUTE,
            "messages": messages,
            "continuation": None,
            "model_turn_count": 0,
            "tool_call_count": 0,
            "successful_inspections": 1,
            "progress_fingerprints": [],
            "stagnant_turns": 0,
            "phase": "tools_completed",
            "force_final": False,
            "rolling_summary": "",
            "budget_ledger": {},
            "context_artifact_refs": [],
        },
    )


@pytest.mark.asyncio
async def test_graph_loops_from_tools_back_to_model_turn(
    tmp_path: Path,
) -> None:
    (tmp_path / "README.md").write_text("fixture evidence\n", encoding="utf-8")
    provider = SequenceProvider(
        [
            ModelTurn(
                assistant=AssistantMessage(
                    tool_calls=[
                        ToolCall(
                            call_id="read-1",
                            name="repo.read",
                            arguments_json='{"path":"README.md"}',
                        )
                    ]
                ),
                stop_reason=StopReason.TOOL_CALLS,
                model="fake-model",
                provider="fake",
            ),
            ModelTurn(
                assistant=AssistantMessage(
                    content="README.md line 1 contains fixture evidence."
                ),
                stop_reason=StopReason.COMPLETED,
                model="fake-model",
                provider="fake",
            ),
        ]
    )
    graph = ReadOnlyCodingGraph(
        MemorySaver(),  # type: ignore[arg-type]
        provider_resolver=lambda _: provider,
    )
    run, agent = _run(tmp_path)

    state, recovered = await graph.execute(run, agent)

    assert not recovered
    assert state["model_turn_count"] == 2
    assert state["tool_call_count"] == 1
    assert state["final_answer"].startswith("README.md")
    assert len(provider.requests) == 2
    assert provider.requests[1].messages[-1].role == "tool"


@pytest.mark.asyncio
async def test_graph_rejects_unsupported_early_completion(
    tmp_path: Path,
) -> None:
    (tmp_path / "README.md").write_text("evidence\n", encoding="utf-8")
    provider = SequenceProvider(
        [
            ModelTurn(
                assistant=AssistantMessage(content="Guess"),
                stop_reason=StopReason.COMPLETED,
                model="fake",
                provider="fake",
            ),
            ModelTurn(
                assistant=AssistantMessage(
                    tool_calls=[
                        ToolCall(
                            call_id="read",
                            name="repo.read",
                            arguments_json='{"path":"README.md"}',
                        )
                    ]
                ),
                stop_reason=StopReason.TOOL_CALLS,
                model="fake",
                provider="fake",
            ),
            ModelTurn(
                assistant=AssistantMessage(content="README.md:1 is evidence."),
                stop_reason=StopReason.COMPLETED,
                model="fake",
                provider="fake",
            ),
        ]
    )
    graph = ReadOnlyCodingGraph(
        MemorySaver(),  # type: ignore[arg-type]
        provider_resolver=lambda _: provider,
    )
    run, agent = _run(tmp_path)

    state, _ = await graph.execute(run, agent)

    assert state["model_turn_count"] == 3
    assert state["successful_inspections"] == 1
    assert any(
        getattr(message, "content", "").startswith("Do not finish yet")
        for message in provider.requests[1].messages
    )


@pytest.mark.asyncio
async def test_model_turn_compacts_context_before_provider_call(
    tmp_path: Path,
) -> None:
    provider = SequenceProvider(
        [
            ModelTurn(
                assistant=AssistantMessage(content="bounded answer"),
                stop_reason=StopReason.COMPLETED,
                model="fake",
                provider="fake",
            )
        ]
    )
    artifact_repository = InMemoryArtifactMetadataRepository()
    graph = ReadOnlyCodingGraph(
        MemorySaver(),  # type: ignore[arg-type]
        provider_resolver=lambda _: provider,
        context_manager=ContextManager(
            summary_provider=DeterministicSummaryProvider(),
            artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
            artifact_repository=artifact_repository,
        ),
        budget_repository=InMemoryBudgetRepository(),
        budget_policy=_budget_policy(
            soft_context_tokens=100,
            hard_context_tokens=2_000,
            recent_context_tokens=80,
        ),
    )
    run, agent = _run(tmp_path)
    graph._run = run
    graph._agent = agent
    tool_call = ToolCall(
        call_id="read-1",
        name="repo.read",
        arguments_json='{"path":"README.md"}',
    )
    state = _node_state(
        run,
        agent,
        [
            SystemMessage(content="system").model_dump(mode="json"),
            UserMessage(content="goal").model_dump(mode="json"),
            UserMessage(content="old observation " * 1000).model_dump(mode="json"),
            AssistantMessage(tool_calls=[tool_call]).model_dump(mode="json"),
            ToolResultMessage(
                call_id=tool_call.call_id,
                content="recent result",
            ).model_dump(mode="json"),
        ],
    )

    updated = await graph._model_turn(state)

    request = provider.requests[0]
    assert all(
        "old observation " * 20 not in getattr(message, "content", "")
        for message in request.messages
    )
    assert request.messages[1].content.startswith("Prior context summary:")
    assert updated["rolling_summary"]
    assert updated["context_artifact_refs"]
    assert await artifact_repository.list_for_run(run.id)


@pytest.mark.asyncio
async def test_model_turn_records_budget_usage(tmp_path: Path) -> None:
    provider = SequenceProvider(
        [
            ModelTurn(
                assistant=AssistantMessage(content="answer"),
                stop_reason=StopReason.COMPLETED,
                model="fake",
                provider="fake",
                usage=ModelUsage(
                    input_tokens=10,
                    output_tokens=20,
                    reasoning_tokens=5,
                ),
            )
        ]
    )
    budget_repository = InMemoryBudgetRepository()
    graph = ReadOnlyCodingGraph(
        MemorySaver(),  # type: ignore[arg-type]
        provider_resolver=lambda _: provider,
        budget_repository=budget_repository,
        budget_policy=_budget_policy(),
    )
    run, agent = _run(tmp_path)
    graph._run = run
    graph._agent = agent

    await graph._model_turn(
        _node_state(
            run,
            agent,
            [
                SystemMessage(content="system").model_dump(mode="json"),
                UserMessage(content="goal").model_dump(mode="json"),
            ],
        )
    )

    ledger = await budget_repository.get_ledger(run.id)
    assert ledger.total_input_tokens == 10
    assert ledger.total_output_tokens == 20
    assert ledger.total_reasoning_tokens == 5
    assert ledger.model_call_count == 1


@pytest.mark.asyncio
async def test_hard_context_limit_forces_final_answer_without_tools(
    tmp_path: Path,
) -> None:
    provider = SequenceProvider(
        [
            ModelTurn(
                assistant=AssistantMessage(content="bounded final"),
                stop_reason=StopReason.COMPLETED,
                model="fake",
                provider="fake",
            )
        ]
    )
    graph = ReadOnlyCodingGraph(
        MemorySaver(),  # type: ignore[arg-type]
        provider_resolver=lambda _: provider,
        context_manager=ContextManager(
            summary_provider=DeterministicSummaryProvider(),
            artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
            artifact_repository=InMemoryArtifactMetadataRepository(),
        ),
        budget_repository=InMemoryBudgetRepository(),
        budget_policy=_budget_policy(
            soft_context_tokens=10,
            hard_context_tokens=20,
            recent_context_tokens=5,
        ),
    )
    run, agent = _run(tmp_path)
    graph._run = run
    graph._agent = agent

    updated = await graph._model_turn(
        _node_state(
            run,
            agent,
            [
                SystemMessage(content="system " * 200).model_dump(mode="json"),
                UserMessage(content="goal").model_dump(mode="json"),
            ],
        )
    )

    request = provider.requests[0]
    assert request.tool_choice.mode is ToolChoiceMode.NONE
    assert request.tools == []
    assert any(
        "hard context limit" in getattr(message, "content", "")
        for message in request.messages
    )
    assert updated["force_final"]


@pytest.mark.asyncio
async def test_total_token_budget_exhaustion_fails_before_model_call(
    tmp_path: Path,
) -> None:
    provider = SequenceProvider(
        [
            ModelTurn(
                assistant=AssistantMessage(content="should not run"),
                stop_reason=StopReason.COMPLETED,
                model="fake",
                provider="fake",
            )
        ]
    )
    budget_repository = InMemoryBudgetRepository()
    graph = ReadOnlyCodingGraph(
        MemorySaver(),  # type: ignore[arg-type]
        provider_resolver=lambda _: provider,
        budget_repository=budget_repository,
        budget_policy=_budget_policy(max_total_tokens_per_run=3),
    )
    run, agent = _run(tmp_path)
    graph._run = run
    graph._agent = agent

    with pytest.raises(AgentLoopFailed, match="budget_exhausted"):
        await graph._model_turn(
            _node_state(
                run,
                agent,
                [
                    SystemMessage(content="system prompt").model_dump(mode="json"),
                    UserMessage(content="goal").model_dump(mode="json"),
                ],
            )
        )

    assert provider.requests == []
    ledger = await budget_repository.get_ledger(run.id)
    assert ledger.threshold_status == BudgetDecision.EXHAUSTED.value
