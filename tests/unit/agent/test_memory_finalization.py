from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import StateSnapshot

from awesome_agent.agent import (
    AgentRuntimeContext,
    AgentState,
    CloudPostAnswerMemory,
    MemoryFinalizationResult,
    PostAnswerMemory,
    PreparedAgentContext,
    TurnBudget,
    compile_agent_graph,
    new_agent_state,
    validate_agent_state,
)
from awesome_agent.context import estimate_messages
from awesome_agent.core.tools import ToolExecutionContext, ToolResult
from awesome_agent.memory import (
    CloudWriteOutcome,
    DistillationResult,
    DistillationStatus,
    Mem0Identity,
    MemoryCandidate,
    MemoryScope,
)
from awesome_agent.modeling import (
    AssistantMessage,
    GatewayEvent,
    ModelRequest,
    ModelTurn,
    ModelUsage,
    SelectedModel,
    StopReason,
    TurnCompleted,
    UserMessage,
)


class Gateway:
    async def stream(
        self,
        selected: SelectedModel,
        request: ModelRequest,
    ) -> AsyncIterator[GatewayEvent]:
        del request
        yield TurnCompleted(
            turn=ModelTurn(
                provider=selected.provider,
                model=selected.model,
                assistant=AssistantMessage(content="final answer"),
                stop_reason=StopReason.COMPLETED,
            )
        )


class Executor:
    async def execute(
        self,
        request: object,
        *,
        context: ToolExecutionContext,
    ) -> ToolResult:
        del request, context
        raise AssertionError("no tool should execute")


class Projector:
    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.memory_statuses: list[str] = []

    async def project_gateway(self, event: GatewayEvent) -> None:
        del event

    async def project_tool(self, result: ToolResult) -> None:
        del result

    async def project_context(self, **kwargs: object) -> None:
        del kwargs

    async def project_warning(self, *, code: str, message: str) -> None:
        del message
        self.warnings.append(code)

    async def project_memory_status(self, *, enabled: bool, status: str) -> None:
        assert enabled
        self.memory_statuses.append(status)


class Finalizer:
    def __init__(self, *, cancel: bool = False) -> None:
        self.cancel = cancel
        self.calls: list[dict[str, object]] = []

    async def finalize(
        self,
        *,
        user_text: str,
        final_answer: str,
        selected_model: SelectedModel,
        remaining_model_calls: int,
        remaining_provider_retries: int,
        workspace_key: str,
    ) -> MemoryFinalizationResult:
        self.calls.append(
            {
                "user_text": user_text,
                "final_answer": final_answer,
                "selected_model": selected_model,
                "remaining_model_calls": remaining_model_calls,
                "remaining_provider_retries": remaining_provider_retries,
                "workspace_key": workspace_key,
            }
        )
        assert final_answer == "final answer"
        if self.cancel:
            raise asyncio.CancelledError
        return MemoryFinalizationResult(
            enabled=True,
            status="completed",
            model_calls=1,
            usage=ModelUsage(input_tokens=9, output_tokens=3, provider_retries=1),
        )


async def _run(
    finalizer: PostAnswerMemory,
) -> tuple[AgentState, StateSnapshot, Projector]:
    saver = InMemorySaver()
    graph = compile_agent_graph(saver)
    projector = Projector()

    async def context_builder(state: object) -> PreparedAgentContext:
        del state
        return PreparedAgentContext(
            messages=(UserMessage(content="current user text"),),
            manifest=(),
        )

    runtime = AgentRuntimeContext(
        gateway=cast(Any, Gateway()),
        executor=cast(Any, Executor()),
        tool_catalog=tuple,
        tool_context_factory=cast(Any, lambda state: state),
        event_projector=projector,
        context_builder=context_builder,
        budget=TurnBudget(),
        monotonic=lambda: 1.0,
        context_token_estimator=estimate_messages,
        current_user_text="current user text",
        post_answer_memory=finalizer,
    )
    config: RunnableConfig = {"configurable": {"thread_id": "turn_1"}}
    result = validate_agent_state(
        await graph.ainvoke(
            new_agent_state(
                thread_id="thread_1",
                turn_id="turn_1",
                workspace_key="ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                provider="deepseek",
                model="deepseek/deepseek-v4-flash",
                thinking_enabled=False,
            ),
            config=config,
            context=runtime,
        )
    )
    snapshot = await graph.aget_state(config)
    return result, snapshot, projector


@pytest.mark.asyncio
async def test_memory_finalization_is_checkpointed_and_charged() -> None:
    finalizer = Finalizer()

    result, snapshot, projector = await _run(finalizer)

    assert len(finalizer.calls) == 1
    assert result["termination_reason"] == "completed"
    assert result["model_calls"] == 3
    assert result["provider_retries"] == 1
    assert result["usage"]["input_tokens"] == 9
    assert snapshot.values["model_calls"] == 3
    assert projector.memory_statuses == ["completed"]


@pytest.mark.asyncio
async def test_optional_memory_cancellation_preserves_completed_answer() -> None:
    result, snapshot, projector = await _run(Finalizer(cancel=True))

    assert result["final_answer"] == "final answer"
    assert result["termination_reason"] == "completed"
    assert snapshot.values["final_answer"] == "final answer"
    assert projector.warnings == ["memory_finalization_cancelled"]


class Distiller:
    def __init__(self, candidate: MemoryCandidate) -> None:
        self.candidate = candidate

    async def distill(self, **kwargs: object) -> DistillationResult:
        del kwargs
        return DistillationResult(
            status=DistillationStatus.COMPLETED,
            candidates=(self.candidate,),
            model_calls=1,
        )


class CloudAdapter:
    def __init__(self) -> None:
        self.hashes: set[str] = set()
        self.add_calls = 0

    async def has_fact_hash(self, fact_hash: str, **kwargs: object) -> bool:
        del kwargs
        return fact_hash in self.hashes

    async def add(
        self,
        candidate: MemoryCandidate,
        identity: Mem0Identity,
    ) -> CloudWriteOutcome:
        del identity
        self.add_calls += 1
        self.hashes.add(candidate.fact_hash)
        return CloudWriteOutcome(accepted=True, memory_id="remote-1")


@pytest.mark.asyncio
async def test_replay_after_remote_write_uses_fact_hash_deduplication() -> None:
    candidate = MemoryCandidate(
        scope=MemoryScope.USER,
        content="User prefers concise answers.",
        fact_hash="a" * 64,
    )
    adapter = CloudAdapter()
    finalizer = CloudPostAnswerMemory(
        distiller=cast(Any, Distiller(candidate)),
        adapter=cast(Any, adapter),
        identity=Mem0Identity(
            user_id="user_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            workspace_key="ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ),
    )
    selected_model = SelectedModel(
        provider="deepseek",
        model="deepseek/deepseek-v4-flash",
    )
    first = await finalizer.finalize(
        user_text="remember concise answers",
        final_answer="done",
        selected_model=selected_model,
        remaining_model_calls=10,
        remaining_provider_retries=6,
        workspace_key="ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )
    second = await finalizer.finalize(
        user_text="remember concise answers",
        final_answer="done",
        selected_model=selected_model,
        remaining_model_calls=10,
        remaining_provider_retries=6,
        workspace_key="ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )

    assert first.status == "completed"
    assert second.status == "completed"
    assert adapter.add_calls == 1
