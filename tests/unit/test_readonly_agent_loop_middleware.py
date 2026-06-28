from __future__ import annotations

from awesome_agent.modeling import (
    AssistantMessage,
    ModelTurn,
    ModelUsage,
    StopReason,
    ToolCall,
)
from awesome_agent.runtime.agent_loop.read_only_middleware import (
    ReadOnlyEvidenceMiddleware,
    ReadOnlyProgressMiddleware,
)


def test_readonly_evidence_middleware_routes_tool_calls_to_tools() -> None:
    middleware = ReadOnlyEvidenceMiddleware()
    turn = ModelTurn(
        assistant=AssistantMessage(
            content="",
            tool_calls=[
                ToolCall(
                    call_id="call-1",
                    name="repo.read",
                    arguments_json='{"path":"README.md"}',
                )
            ],
        ),
        stop_reason=StopReason.TOOL_CALLS,
        usage=ModelUsage(),
        provider="fake",
        model="fake",
    )

    assert (
        middleware.route_turn(
            turn=turn,
            force_final=False,
            successful_inspections=0,
        )
        == "tools"
    )
    assert (
        middleware.route_turn(
            turn=turn,
            force_final=True,
            successful_inspections=0,
        )
        == "feedback"
    )


def test_readonly_evidence_middleware_requires_successful_inspection() -> None:
    middleware = ReadOnlyEvidenceMiddleware()
    turn = ModelTurn(
        assistant=AssistantMessage(content="Answer with evidence."),
        stop_reason=StopReason.COMPLETED,
        usage=ModelUsage(),
        provider="fake",
        model="fake",
    )

    assert (
        middleware.route_turn(
            turn=turn,
            force_final=False,
            successful_inspections=0,
        )
        == "feedback"
    )
    assert (
        middleware.route_turn(
            turn=turn,
            force_final=False,
            successful_inspections=1,
        )
        == "finalize"
    )


def test_readonly_progress_middleware_emits_convergence_reminders() -> None:
    middleware = ReadOnlyProgressMiddleware()

    assert middleware.budget_reminder(next_count=41, max_model_turns=60) is None
    assert "Start converging" in (
        middleware.budget_reminder(next_count=42, max_model_turns=60) or ""
    )
    assert "Stop broad exploration" in (
        middleware.budget_reminder(next_count=54, max_model_turns=60) or ""
    )
