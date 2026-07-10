import json
from collections.abc import AsyncIterator

import pytest

from awesome_agent.memory.distiller import DistillationStatus, MemoryDistiller
from awesome_agent.memory.models import MemoryScope
from awesome_agent.modeling import (
    AssistantMessage,
    GatewayEvent,
    ModelTurn,
    ModelUsage,
    SelectedModel,
    StopReason,
    TurnCompleted,
)

SELECTED = SelectedModel(
    provider="deepseek",
    model="deepseek/deepseek-v4-flash",
)


class FakeGateway:
    def __init__(self, content: str, *, fail: bool = False) -> None:
        self.content = content
        self.fail = fail
        self.calls: list[tuple[SelectedModel, object]] = []

    async def stream(
        self,
        selected: SelectedModel,
        request: object,
    ) -> AsyncIterator[GatewayEvent]:
        self.calls.append((selected, request))
        if self.fail:
            raise RuntimeError("provider request secret")
        yield TurnCompleted(
            turn=ModelTurn(
                provider=selected.provider,
                model=selected.model,
                assistant=AssistantMessage(content=self.content),
                stop_reason=StopReason.COMPLETED,
                usage=ModelUsage(
                    input_tokens=100,
                    output_tokens=20,
                    provider_retries=2,
                ),
            )
        )


@pytest.mark.asyncio
async def test_distiller_receives_only_current_user_text_and_final_answer() -> None:
    gateway = FakeGateway(
        json.dumps(
            {
                "candidates": [
                    {"scope": "user", "content": "User prefers concise answers."},
                    {"scope": "workspace", "content": "Project uses pytest."},
                ]
            }
        )
    )
    distiller = MemoryDistiller(gateway)

    result = await distiller.distill(
        user_text="Remember concise answers. token=secret-value",
        final_answer="Done. The preference was applied.",
        selected_model=SELECTED,
        remaining_model_calls=1,
        workspace_key="ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )

    assert result.status is DistillationStatus.COMPLETED
    assert [item.scope for item in result.candidates] == [
        MemoryScope.USER,
        MemoryScope.WORKSPACE,
    ]
    [(selected, request)] = gateway.calls
    assert selected == SELECTED
    request_json = request.model_dump_json()
    assert "token=secret-value" not in request_json
    assert "[REDACTED:token]" in request_json
    for excluded in (
        "path snapshot",
        "source code",
        "diff --git",
        "tool output",
        "reasoning",
        "thread summary",
        "local memory",
        "prior history",
    ):
        assert excluded not in request_json
    assert request.tools == ()
    assert request.thinking_enabled is False
    assert result.model_calls == 1
    assert result.usage.provider_retries == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        "not json",
        json.dumps({"candidates": [{"scope": "user", "content": "x", "extra": 1}]}),
        json.dumps(
            {
                "candidates": [
                    {"scope": "user", "content": str(index)} for index in range(6)
                ]
            }
        ),
        json.dumps({"candidates": [{"scope": "user", "content": "x" * 501}]}),
        json.dumps({"candidates": [{"scope": "global", "content": "fact"}]}),
    ],
)
async def test_invalid_structured_output_warns_and_writes_nothing(content: str) -> None:
    gateway = FakeGateway(content)

    result = await MemoryDistiller(gateway).distill(
        user_text="Remember a stable preference.",
        final_answer="Done.",
        selected_model=SELECTED,
        remaining_model_calls=1,
        workspace_key="ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )

    assert result.status is DistillationStatus.WARNING
    assert result.candidates == ()
    assert result.model_calls == 1
    assert result.diagnostic is not None
    assert "not json" not in result.diagnostic.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("remaining", "user_text", "answer"),
    [
        (0, "Remember this.", "Done."),
        (1, "", "Done."),
        (1, "Remember this.", ""),
    ],
)
async def test_distillation_skips_without_budget_or_eligible_input(
    remaining: int,
    user_text: str,
    answer: str,
) -> None:
    gateway = FakeGateway('{"candidates": []}')

    result = await MemoryDistiller(gateway).distill(
        user_text=user_text,
        final_answer=answer,
        selected_model=SELECTED,
        remaining_model_calls=remaining,
        workspace_key="ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )

    assert result.status is DistillationStatus.SKIPPED
    assert result.model_calls == 0
    assert gateway.calls == []


@pytest.mark.asyncio
async def test_provider_failure_is_warning_and_preserves_call_charge() -> None:
    gateway = FakeGateway("", fail=True)

    result = await MemoryDistiller(gateway).distill(
        user_text="Remember a stable preference.",
        final_answer="Done.",
        selected_model=SELECTED,
        remaining_model_calls=1,
        workspace_key="ws_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    )

    assert result.status is DistillationStatus.WARNING
    assert result.model_calls == 1
    assert result.candidates == ()
    assert "secret" not in result.model_dump_json()
