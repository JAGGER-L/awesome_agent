from pathlib import Path
from typing import Any, cast

import pytest

from awesome_agent.agent import AgentRuntimeContext, AgentState, new_agent_state
from awesome_agent.application.commands import (
    CommandIntent,
    CommandName,
    CommandStatus,
)
from awesome_agent.application.context import ApplicationContextService
from awesome_agent.application.operations import OperationController
from awesome_agent.application.turns import (
    TurnCoordinator,
    TurnInputInvalid,
)
from awesome_agent.config import BudgetConfig, TurnConfig
from awesome_agent.context import ContextBuilder, ThreadCompressor
from awesome_agent.conversation import ConversationService, UsageSummary
from awesome_agent.core.events import CollectingEventSink, EventEmitter
from awesome_agent.core.workspace import resolve_workspace
from awesome_agent.modeling import (
    AssistantMessage,
    ModelIdentitySnapshot,
    ModelTurn,
    StopReason,
)
from awesome_agent.storage.conversations import SQLiteConversationRepositories


class SummaryGateway:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    async def complete(self, selected: object, request: object) -> ModelTurn:
        del selected, request
        self.calls += 1
        if self.fail:
            raise RuntimeError("summary failed")
        return ModelTurn(
            provider="deepseek",
            model="deepseek/deepseek-v4-flash",
            assistant=AssistantMessage(content="rolling summary"),
            stop_reason=StopReason.COMPLETED,
        )


def _config() -> TurnConfig:
    return TurnConfig(
        provider="deepseek",
        model="deepseek/deepseek-v4-flash",
        budgets=BudgetConfig(),
    )


def _complete_turns(
    conversation: ConversationService,
    thread_id: str,
    count: int,
) -> None:
    for index in range(count):
        turn = conversation.begin_turn(thread_id, f"question {index}", _config())
        conversation.complete_turn(
            turn.id,
            f"answer {index}",
            UsageSummary(),
            "completed",
        )


@pytest.mark.asyncio
async def test_multi_turn_summary_direct_command_and_paths_are_bounded_and_frozen(
    tmp_path: Path,
) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    (workspace_path / "note.txt").write_text("before", encoding="utf-8")
    directory = workspace_path / "dir"
    directory.mkdir()
    (directory / "child.txt").write_text("child", encoding="utf-8")
    workspace = resolve_workspace(workspace_path)
    repositories = SQLiteConversationRepositories(tmp_path / "application.db")
    conversation = ConversationService(store=repositories)
    thread = conversation.create_thread(workspace.key)
    _complete_turns(conversation, thread.id, 8)
    conversation.append_direct_command(
        thread.id,
        "pytest: passed",
        {"exit_code": 0},
    )
    context_service = ApplicationContextService(
        conversation=conversation,
        workspace=workspace,
        builder=ContextBuilder(),
        compressor=ThreadCompressor(cast(Any, SummaryGateway())),
        configured_total_tokens=262_144,
        model_context_limit=262_144,
        product_instructions="product policy",
        model_identity=lambda _turn: ModelIdentitySnapshot.from_models(
            configured_model="deepseek/deepseek-v4-flash",
            effective_model="deepseek/deepseek-v4-flash",
        ),
    )

    compacted = await context_service.compact_thread(
        thread.id,
        provider="deepseek",
        model="deepseek/deepseek-v4-flash",
    )
    assert compacted.summary is not None
    assert compacted.summary.covered_turn_count == 4
    assert compacted.summary.covered_entry_sequence == 8

    turn = conversation.begin_turn(
        thread.id,
        "inspect @note.txt @dir",
        _config(),
    )
    context_service.prepare_turn(turn, "inspect @note.txt @dir")
    state = new_agent_state(
        thread_id=thread.id,
        turn_id=turn.id,
        workspace_key=workspace.key,
        provider="deepseek",
        model="deepseek/deepseek-v4-flash",
        thinking_enabled=False,
    )
    prepared = await context_service.build(state)
    frozen_json = "\n".join(message.model_dump_json() for message in prepared.messages)
    assert "before" in frozen_json
    assert "child.txt" in frozen_json
    assert "pytest: passed" in frozen_json
    assert "Awesome Agent" in frozen_json
    assert "deepseek/deepseek-v4-flash" in frozen_json
    assert "question 4" in frozen_json
    assert "question 0" not in frozen_json

    (workspace_path / "note.txt").write_text("after", encoding="utf-8")
    state["messages"] = [
        cast(dict[str, Any], message.model_dump(mode="json"))
        for message in prepared.messages
    ]
    state["context_manifest"] = list(prepared.manifest)
    assert "after" not in "\n".join(str(message) for message in state["messages"])
    restarted_context = ApplicationContextService(
        conversation=conversation,
        workspace=workspace,
        builder=ContextBuilder(),
        compressor=ThreadCompressor(cast(Any, SummaryGateway())),
        configured_total_tokens=262_144,
        model_context_limit=262_144,
        product_instructions="changed process instructions",
    )
    rebuilt = await restarted_context._build_from_frozen(state)
    rebuilt_json = "\n".join(message.model_dump_json() for message in rebuilt.messages)
    assert "before" in rebuilt_json
    assert "after" not in rebuilt_json
    assert "changed process instructions" not in rebuilt_json

    conversation.complete_turn(
        turn.id,
        "done",
        UsageSummary(),
        "completed",
        tuple(prepared.manifest),
    )
    inspected = context_service.inspect(thread.id)
    serialized = str(inspected)
    assert "before" not in serialized
    assert "child" not in serialized
    assert inspected["summary_covered_turn_count"] == 4
    assert conversation.read_thread(thread.id).entries[-2].content == (
        "inspect @note.txt @dir"
    )
    command = await context_service.context_command(
        CommandIntent(name=CommandName.CONTEXT),
        thread_id=thread.id,
    )
    assert command.status is CommandStatus.SUCCESS
    assert "before" not in command.model_dump_json()
    invalid = await context_service.context_command(
        CommandIntent(name=CommandName.CONTEXT, arguments=("extra",)),
        thread_id=thread.id,
    )
    assert invalid.status is CommandStatus.ERROR


class NeverGraph:
    def __init__(self) -> None:
        self.called = False

    async def ainvoke(self, *args: object, **kwargs: object) -> AgentState:
        self.called = True
        raise AssertionError("invalid @path reached the graph")


class Checkpoints:
    async def exists(self, turn_id: str) -> bool:
        return False

    async def latest_state(self, turn_id: str) -> AgentState | None:
        return None

    async def delete(self, turn_id: str) -> None:
        return None


@pytest.mark.asyncio
async def test_invalid_explicit_path_fails_turn_before_graph_or_model(
    tmp_path: Path,
) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = resolve_workspace(workspace_path)
    repositories = SQLiteConversationRepositories(tmp_path / "application.db")
    conversation = ConversationService(store=repositories)
    thread = conversation.create_thread(workspace.key)
    context_service = ApplicationContextService(
        conversation=conversation,
        workspace=workspace,
        builder=ContextBuilder(),
        compressor=ThreadCompressor(cast(Any, SummaryGateway())),
        configured_total_tokens=262_144,
        model_context_limit=262_144,
        product_instructions="policy",
    )
    graph = NeverGraph()
    emitter = EventEmitter(
        session_id="session_1",
        workspace_key=workspace.key,
        sink=CollectingEventSink(),
    )
    coordinator = TurnCoordinator(
        workspace_key=workspace.key,
        conversation=conversation,
        config_resolver=lambda current: _config(),
        graph=cast(Any, graph),
        runtime_context_factory=lambda turn, operation, projector: cast(
            AgentRuntimeContext, object()
        ),
        operations=OperationController(emitter),
        emitter=emitter,
        checkpoints=Checkpoints(),
        seal_changes=lambda turn_id: None,
        turn_input_preparer=context_service.prepare_turn,
    )

    accepted = await coordinator.submit_turn(thread.id, "inspect @missing.txt")
    with pytest.raises(TurnInputInvalid):
        await coordinator.wait(accepted.operation_id)

    assert graph.called is False
    view = conversation.read_thread(thread.id)
    assert view.entries[0].content == "inspect @missing.txt"
    assert view.turns[0].error_code == "invalid_explicit_path"


@pytest.mark.asyncio
async def test_compact_failure_keeps_history_and_summary_unchanged(
    tmp_path: Path,
) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = resolve_workspace(workspace_path)
    repositories = SQLiteConversationRepositories(tmp_path / "application.db")
    conversation = ConversationService(store=repositories)
    thread = conversation.create_thread(workspace.key)
    _complete_turns(conversation, thread.id, 5)
    before = conversation.read_thread(thread.id)
    context_service = ApplicationContextService(
        conversation=conversation,
        workspace=workspace,
        builder=ContextBuilder(),
        compressor=ThreadCompressor(cast(Any, SummaryGateway(fail=True))),
        configured_total_tokens=262_144,
        model_context_limit=262_144,
        product_instructions="policy",
    )

    result = await context_service.compact_thread(
        thread.id,
        provider="deepseek",
        model="deepseek/deepseek-v4-flash",
    )

    after = conversation.read_thread(thread.id)
    assert result.error_code == "compression_failed"
    assert after.entries == before.entries
    assert after.summary is None
    command = await context_service.compact_command(
        CommandIntent(name=CommandName.COMPACT),
        thread_id=thread.id,
        provider="deepseek",
        model="deepseek/deepseek-v4-flash",
    )
    assert command.status is CommandStatus.ERROR
    invalid = await context_service.compact_command(
        CommandIntent(name=CommandName.COMPACT, arguments=("extra",)),
        thread_id=thread.id,
        provider="deepseek",
        model="deepseek/deepseek-v4-flash",
    )
    assert invalid.status is CommandStatus.ERROR
