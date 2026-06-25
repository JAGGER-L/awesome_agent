from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import MemorySaver

from awesome_agent.domain.enums import AgentKind, RunIntent
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
from awesome_agent.runtime.graphs import (
    READ_ONLY_CODING_GRAPH,
    READ_ONLY_CODING_VERSION,
)
from awesome_agent.runtime.readonly_graph import ReadOnlyCodingGraph


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
        graph_name=READ_ONLY_CODING_GRAPH,
        graph_version=READ_ONLY_CODING_VERSION,
        graph_thread_id=f"run:{uuid4()}",
        workspace_path=workspace,
    )
    return run, Agent(
        run_id=run.id,
        kind=AgentKind.LEADER,
        profile="leader",
        model="fake-model",
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
