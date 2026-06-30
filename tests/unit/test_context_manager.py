from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from awesome_agent.artifacts.repository import InMemoryArtifactMetadataRepository
from awesome_agent.artifacts.store import LocalArtifactStore
from awesome_agent.modeling import (
    AssistantMessage,
    SystemMessage,
    ToolCall,
    ToolResultMessage,
    UserMessage,
)
from awesome_agent.runtime.context import (
    ContextManager,
    ContextPolicy,
    DeterministicSummaryProvider,
)
from awesome_agent.runtime.token_accounting import ModelTokenProfile, TokenAccountant


class OneTokenTokenizer:
    def count_text(self, text: str) -> int:
        return 1 if text else 0


def _one_token_accountant() -> TokenAccountant:
    return TokenAccountant(
        profiles=[
            ModelTokenProfile(
                provider="unknown",
                model_pattern="*",
                estimator_name="one-token",
                tokenizer=OneTokenTokenizer(),
                message_overhead_tokens=0,
                request_overhead_tokens=0,
                tool_overhead_tokens=0,
                error_margin_ratio=0,
            )
        ]
    )


@pytest.mark.asyncio
async def test_context_manager_keeps_system_goal_and_recent_tool_cycle(
    tmp_path: Path,
) -> None:
    artifact_repository = InMemoryArtifactMetadataRepository()
    manager = ContextManager(
        summary_provider=DeterministicSummaryProvider(),
        artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
        artifact_repository=artifact_repository,
    )
    run_id = uuid4()
    agent_id = uuid4()
    tool_call = ToolCall(
        call_id="call-read",
        name="repo.read_file",
        arguments_json='{"path":"src/app.py"}',
    )
    messages = [
        SystemMessage(content="system").model_dump(mode="json"),
        UserMessage(content="goal").model_dump(mode="json"),
        UserMessage(content="old observation " * 1000).model_dump(mode="json"),
        AssistantMessage(content="", tool_calls=[tool_call]).model_dump(mode="json"),
        ToolResultMessage(
            call_id=tool_call.call_id,
            content="recent result",
        ).model_dump(mode="json"),
    ]

    prepared = await manager.prepare_request(
        run_id=run_id,
        agent_id=agent_id,
        runtime_route="solo-readonly",
        messages=messages,
        rolling_summary="",
        policy=ContextPolicy(
            soft_context_tokens=100,
            hard_context_tokens=200,
            recent_context_tokens=80,
        ),
    )

    assert prepared.compacted
    assert prepared.request_messages[0].content == "system"
    assert prepared.request_messages[1].content.startswith("Prior context summary:")
    assert prepared.request_messages[2].content == "goal"
    assert prepared.request_messages[-2].role == "assistant"
    assert prepared.request_messages[-1].role == "tool"
    assert prepared.artifact_refs
    assert await artifact_repository.list_for_run(run_id)


@pytest.mark.asyncio
async def test_deterministic_summary_mentions_paths_tools_and_artifacts() -> None:
    provider = DeterministicSummaryProvider()

    summary = await provider.summarize(
        prior_summary="",
        removed_messages=[
            UserMessage(content="Read src/app.py").model_dump(mode="json"),
            ToolResultMessage(
                call_id="call-read",
                content="src/app.py imports FastAPI",
            ).model_dump(mode="json"),
        ],
        artifact_refs=["artifact-id"],
    )

    assert "src/app.py" in summary
    assert "call-read" in summary
    assert "artifact-id" in summary


@pytest.mark.asyncio
async def test_long_tool_result_is_replaced_with_artifact_ref(
    tmp_path: Path,
) -> None:
    manager = ContextManager(
        summary_provider=DeterministicSummaryProvider(),
        artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
        artifact_repository=InMemoryArtifactMetadataRepository(),
    )
    run_id = uuid4()
    tool_call = ToolCall(
        call_id="call-large",
        name="repo.search",
        arguments_json='{"query":"needle"}',
    )

    prepared = await manager.prepare_request(
        run_id=run_id,
        agent_id=uuid4(),
        runtime_route="solo-readonly",
        messages=[
            SystemMessage(content="system").model_dump(mode="json"),
            UserMessage(content="goal").model_dump(mode="json"),
            AssistantMessage(tool_calls=[tool_call]).model_dump(mode="json"),
            ToolResultMessage(
                call_id=tool_call.call_id,
                content="large-result " * 200,
            ).model_dump(mode="json"),
        ],
        rolling_summary="",
        policy=ContextPolicy(
            soft_context_tokens=100,
            hard_context_tokens=500,
            recent_context_tokens=20,
        ),
    )

    tool_result = prepared.request_messages[-1]
    assert isinstance(tool_result, ToolResultMessage)
    assert "offloaded to artifact" in tool_result.content
    assert tool_result.artifact_refs
    assert len(tool_result.content) < 200


@pytest.mark.asyncio
async def test_context_manager_uses_injected_token_accountant(
    tmp_path: Path,
) -> None:
    manager = ContextManager(
        summary_provider=DeterministicSummaryProvider(),
        artifact_store=LocalArtifactStore(tmp_path / "artifacts"),
        artifact_repository=InMemoryArtifactMetadataRepository(),
        token_accountant=_one_token_accountant(),
    )

    prepared = await manager.prepare_request(
        run_id=uuid4(),
        agent_id=uuid4(),
        runtime_route="solo-readonly",
        messages=[
            SystemMessage(content="system").model_dump(mode="json"),
            UserMessage(content="goal").model_dump(mode="json"),
            UserMessage(content="large " * 1000).model_dump(mode="json"),
        ],
        rolling_summary="",
        policy=ContextPolicy(
            soft_context_tokens=10,
            hard_context_tokens=20,
            recent_context_tokens=10,
        ),
    )

    assert not prepared.compacted
    assert prepared.before_estimated_tokens == 3
