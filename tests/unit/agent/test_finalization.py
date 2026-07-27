from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any, cast

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import StateSnapshot
from pydantic import ValidationError

from awesome_agent.agent import (
    AgentRuntimeContext,
    AgentState,
    PostAnswerDiagnostic,
    PostAnswerFinalizationRequest,
    PostAnswerFinalizationResult,
    PostAnswerFinalizer,
    PreparedAgentContext,
    TurnBudget,
    compile_agent_graph,
    new_agent_state,
    validate_agent_state,
)
from awesome_agent.agent.finalization import collect_tool_citations
from awesome_agent.context import estimate_messages
from awesome_agent.core.citations import Citation
from awesome_agent.core.tools import (
    ToolError,
    ToolErrorCode,
    ToolExecutionContext,
    ToolResult,
    ToolStatus,
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
        self.warnings: list[tuple[str, str]] = []

    async def project_gateway(self, event: GatewayEvent) -> None:
        del event

    async def project_tool(self, result: ToolResult) -> None:
        del result

    async def project_context(self, **kwargs: object) -> None:
        del kwargs

    async def project_warning(self, *, code: str, message: str) -> None:
        self.warnings.append((code, message))


class Finalizer:
    def __init__(
        self,
        *,
        fail: bool = False,
        use_model: bool = True,
    ) -> None:
        self.fail = fail
        self.use_model = use_model
        self.calls: list[PostAnswerFinalizationRequest] = []

    async def finalize(
        self,
        request: PostAnswerFinalizationRequest,
    ) -> PostAnswerFinalizationResult:
        self.calls.append(request)
        if self.fail:
            raise RuntimeError("private finalizer failure")
        return PostAnswerFinalizationResult(
            final_answer=f"{request.final_answer} [finalized]",
            model_calls=1 if self.use_model else 0,
            usage=(
                ModelUsage(input_tokens=9, output_tokens=3, provider_retries=1)
                if self.use_model
                else ModelUsage()
            ),
        )


async def _run(
    finalizer: PostAnswerFinalizer,
    *,
    budget: TurnBudget | None = None,
    tool_results: tuple[ToolResult, ...] = (),
    monotonic: Callable[[], float] | None = None,
    projector: Projector | None = None,
) -> tuple[AgentState, StateSnapshot, Projector]:
    saver = InMemorySaver()
    graph = compile_agent_graph(saver)
    event_projector = projector or Projector()

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
        event_projector=event_projector,
        context_builder=context_builder,
        budget=budget or TurnBudget(),
        monotonic=monotonic or (lambda: 1.0),
        context_token_estimator=estimate_messages,
        current_user_text="current user text",
        post_answer_finalizer=finalizer,
    )
    state = new_agent_state(
        thread_id="thread_1",
        turn_id="turn_1",
        workspace_key="ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        provider="deepseek",
        model="deepseek/deepseek-v4-flash",
        thinking_enabled=False,
    )
    state["tool_results"] = [item.model_dump(mode="json") for item in tool_results]
    config: RunnableConfig = {"configurable": {"thread_id": "turn_1"}}
    result = validate_agent_state(
        await graph.ainvoke(
            state,
            config=config,
            context=runtime,
        )
    )
    snapshot = await graph.aget_state(config)
    return result, snapshot, event_projector


@pytest.mark.asyncio
async def test_answer_finalization_is_checkpointed_and_retry_usage_is_charged() -> None:
    finalizer = Finalizer()

    result, snapshot, projector = await _run(finalizer)

    [request] = finalizer.calls
    assert request.user_text == "current user text"
    assert request.final_answer == "final answer"
    assert request.remaining_model_calls == 31
    assert request.remaining_provider_retries == 2
    assert result["final_answer"] == "final answer [finalized]"
    assert result["termination_reason"] == "completed"
    assert result["model_calls"] == 3
    assert result["provider_retries"] == 1
    assert result["usage"]["input_tokens"] == 9
    assert snapshot.values["final_answer"] == "final answer [finalized]"
    assert snapshot.values["model_calls"] == 3
    assert projector.warnings == []


@pytest.mark.asyncio
async def test_finalizer_runs_without_model_budget_when_it_uses_no_model() -> None:
    finalizer = Finalizer(use_model=False)

    result, _, projector = await _run(
        finalizer,
        budget=TurnBudget(model_calls=1),
    )

    [request] = finalizer.calls
    assert request.remaining_model_calls == 0
    assert result["final_answer"] == "final answer [finalized]"
    assert result["model_calls"] == 1
    assert projector.warnings == []


@pytest.mark.asyncio
async def test_active_time_exhaustion_allows_only_no_model_finalization() -> None:
    finalizer = Finalizer(use_model=False)
    timestamps = iter((0.0, 1.0))

    result, _, projector = await _run(
        finalizer,
        budget=TurnBudget(active_execution_seconds=0.5),
        monotonic=lambda: next(timestamps),
    )

    [request] = finalizer.calls
    assert request.remaining_model_calls == 0
    assert result["final_answer"] == "final answer [finalized]"
    assert result["model_calls"] == 1
    assert projector.warnings == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "budget",
    [
        TurnBudget(model_calls=2, provider_retries=2),
        TurnBudget(model_calls=4, provider_retries=0),
    ],
)
async def test_finalizer_budget_overrun_fails_closed_without_charging(
    budget: TurnBudget,
) -> None:
    finalizer = Finalizer()

    result, snapshot, projector = await _run(finalizer, budget=budget)

    assert result["final_answer"] == "final answer"
    assert result["model_calls"] == 1
    assert result["provider_retries"] == 0
    assert result["usage"] == ModelUsage().model_dump(mode="python")
    assert snapshot.values["final_answer"] == "final answer"
    assert snapshot.values["model_calls"] == 1
    assert projector.warnings == [
        ("answer_finalization_failed", "Optional answer finalization failed.")
    ]


@pytest.mark.asyncio
async def test_optional_finalizer_failure_preserves_completed_answer() -> None:
    finalizer = Finalizer(fail=True)
    result, snapshot, projector = await _run(finalizer)

    assert result["final_answer"] == "final answer"
    assert result["termination_reason"] == "completed"
    assert snapshot.values["final_answer"] == "final answer"
    assert projector.warnings == [
        (
            "answer_finalization_failed",
            "Optional answer finalization failed.",
        )
    ]


@pytest.mark.asyncio
async def test_caller_cancellation_during_finalization_propagates_promptly() -> None:
    class BlockingFinalizer:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.block = asyncio.Event()

        async def finalize(
            self,
            request: PostAnswerFinalizationRequest,
        ) -> PostAnswerFinalizationResult:
            del request
            self.started.set()
            await self.block.wait()
            raise AssertionError("blocking finalizer unexpectedly resumed")

    finalizer = BlockingFinalizer()
    projector = Projector()
    task = asyncio.create_task(_run(finalizer, projector=projector))
    await asyncio.wait_for(finalizer.started.wait(), timeout=0.5)
    task.cancel("caller cancellation")

    with pytest.raises(asyncio.CancelledError, match="caller cancellation"):
        await asyncio.wait_for(task, timeout=0.5)

    assert projector.warnings == []


@pytest.mark.asyncio
async def test_finalizer_receives_ordered_deduplicated_tool_citations() -> None:
    first = Citation(id="S1", title="First", url="https://example.com/first")
    second = Citation(id="S2", title="Second", url="https://example.com/second")
    finalizer = Finalizer(use_model=False)

    await _run(
        finalizer,
        tool_results=(
            _tool_result("call_1", first),
            _tool_result("call_2", first, second),
        ),
    )

    assert finalizer.calls[0].citations == (first, second)


@pytest.mark.asyncio
async def test_uncited_web_sources_are_appended_and_checkpointed() -> None:
    citation = Citation(
        id="S1",
        title="Web source",
        url="https://example.com/source",
    )

    result, snapshot, projector = await _run(
        Finalizer(use_model=False),
        tool_results=(_tool_result("call_1", citation),),
    )

    final_answer = result["final_answer"]
    assert final_answer is not None
    assert final_answer.endswith(
        "Sources:\n- [[S1]] Web source — https://example.com/source"
    )
    assert result["citations"] == [citation.model_dump(mode="json")]
    assert snapshot.values["citations"] == [citation.model_dump(mode="json")]
    assert projector.warnings == [
        (
            "citation_sources_appended",
            "Web sources were appended because the answer cited none.",
        )
    ]


@pytest.mark.asyncio
async def test_invalid_citation_id_stays_text_and_emits_warning() -> None:
    citation = Citation(
        id="S1",
        title="Web source",
        url="https://example.com/source",
    )

    class CitationFinalizer:
        async def finalize(
            self,
            request: PostAnswerFinalizationRequest,
        ) -> PostAnswerFinalizationResult:
            return PostAnswerFinalizationResult(
                final_answer=f"{request.final_answer} [[S1]] [[S999]]"
            )

    result, _, projector = await _run(
        CitationFinalizer(),
        tool_results=(_tool_result("call_1", citation),),
    )

    final_answer = result["final_answer"]
    assert final_answer is not None
    assert final_answer.endswith("[[S1]] [[S999]]")
    assert projector.warnings == [
        (
            "citation_invalid_id",
            "The answer contains a citation ID with no matching source.",
        )
    ]


def test_conflicting_citation_ids_are_an_agent_invariant() -> None:
    first = Citation(id="S1", title="First", url="https://example.com/first")
    conflict = Citation(id="S1", title="Changed", url="https://example.com/other")

    with pytest.raises(RuntimeError, match="Citation S1 has conflicting values"):
        collect_tool_citations(
            [
                _tool_result("call_1", first).model_dump(mode="json"),
                _tool_result("call_2", conflict).model_dump(mode="json"),
            ]
        )


def test_turn_citation_limit_is_an_agent_invariant() -> None:
    citations = tuple(
        Citation(
            id=f"S{index}",
            title=f"Source {index}",
            url=f"https://example.com/{index}",
        )
        for index in range(1, 130)
    )

    with pytest.raises(RuntimeError, match="128-source limit"):
        collect_tool_citations(
            [
                _tool_result("call_1", *citations[:128]).model_dump(mode="json"),
                _tool_result("call_2", citations[128]).model_dump(mode="json"),
            ]
        )


def test_error_tool_results_do_not_contribute_citations() -> None:
    error_result = ToolResult(
        call_id="call_1",
        tool_name="read_file",
        status=ToolStatus.ERROR,
        content="not executed",
        error=ToolError(
            code=ToolErrorCode.EXECUTION_FAILED,
            message="not executed",
        ),
    )

    assert collect_tool_citations([error_result.model_dump(mode="json")]) == ()


def test_finalizer_replacement_answer_must_be_nonblank_without_normalizing() -> None:
    with pytest.raises(ValidationError, match="non-whitespace"):
        PostAnswerFinalizationResult(final_answer=" \t ")

    result = PostAnswerFinalizationResult(final_answer="  preserved  ")
    assert result.final_answer == "  preserved  "


@pytest.mark.asyncio
async def test_constructed_malformed_finalizer_result_fails_closed() -> None:
    class MalformedFinalizer:
        async def finalize(
            self,
            request: PostAnswerFinalizationRequest,
        ) -> PostAnswerFinalizationResult:
            del request
            return PostAnswerFinalizationResult.model_construct(
                final_answer="untrusted replacement",
                usage={"input_tokens": -1},
                model_calls=1,
                diagnostics=(),
            )

    result, _, projector = await _run(MalformedFinalizer())

    assert result["final_answer"] == "final answer"
    assert result["model_calls"] == 1
    assert projector.warnings == [
        ("answer_finalization_failed", "Optional answer finalization failed.")
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "diagnostics",
    [
        [],
        "diagnostic",
        [PostAnswerDiagnostic(code="safe_warning", message="safe")],
        ({"code": "safe_warning", "message": "safe"},),
        (type("FakeDiagnostic", (), {"code": "safe_warning", "message": "safe"})(),),
        (
            PostAnswerDiagnostic.model_construct(
                code="INVALID-CODE",
                message="safe",
            ),
        ),
    ],
)
async def test_constructed_invalid_diagnostic_shape_fails_closed(
    diagnostics: object,
) -> None:
    class MalformedDiagnosticFinalizer:
        async def finalize(
            self,
            request: PostAnswerFinalizationRequest,
        ) -> PostAnswerFinalizationResult:
            del request
            return PostAnswerFinalizationResult.model_construct(
                final_answer="untrusted replacement",
                usage=ModelUsage(),
                model_calls=0,
                diagnostics=diagnostics,
            )

    result, _, projector = await _run(MalformedDiagnosticFinalizer())

    assert result["final_answer"] == "final answer"
    assert projector.warnings == [
        ("answer_finalization_failed", "Optional answer finalization failed.")
    ]


@pytest.mark.asyncio
async def test_constructed_nested_invalid_usage_is_revalidated() -> None:
    class MalformedUsageFinalizer:
        async def finalize(
            self,
            request: PostAnswerFinalizationRequest,
        ) -> PostAnswerFinalizationResult:
            del request
            return PostAnswerFinalizationResult.model_construct(
                final_answer="untrusted replacement",
                usage=ModelUsage.model_construct(input_tokens=-1),
                model_calls=1,
                diagnostics=(),
            )

    result, _, projector = await _run(MalformedUsageFinalizer())

    assert result["final_answer"] == "final answer"
    assert result["model_calls"] == 1
    assert projector.warnings == [
        ("answer_finalization_failed", "Optional answer finalization failed.")
    ]


def _tool_result(call_id: str, *citations: Citation) -> ToolResult:
    return ToolResult(
        call_id=call_id,
        tool_name="read_file",
        status=ToolStatus.SUCCESS,
        content="bounded",
        citations=citations,
    )
