import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import pytest

from awesome_agent.agent import AgentRuntimeContext, AgentState, new_agent_state
from awesome_agent.application.command_results import (
    CommandError,
    CommandResult,
    ContextCommandPayload,
)
from awesome_agent.application.commands import CommandIntent, CommandName
from awesome_agent.application.context import ApplicationContextService
from awesome_agent.application.operations import OperationController
from awesome_agent.application.turns import (
    TurnCoordinator,
    TurnInputInvalid,
)
from awesome_agent.config import BudgetConfig, TurnConfig
from awesome_agent.context import (
    ContextBuilder,
    ContextSource,
    ContextSourceKind,
    Mem0ContextResult,
    ThreadCompressor,
    estimate_messages,
)
from awesome_agent.conversation import ConversationService, UsageSummary
from awesome_agent.core.events import CollectingEventSink, EventEmitter
from awesome_agent.core.tools import ToolResult, ToolStatus
from awesome_agent.core.workspace import resolve_workspace
from awesome_agent.modeling import (
    AssistantMessage,
    GatewayEvent,
    ModelIdentitySnapshot,
    ModelMessage,
    ModelTurn,
    StopReason,
    ToolCall,
    ToolResultMessage,
    TurnCompleted,
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

    async def stream(
        self,
        selected: object,
        request: object,
    ) -> AsyncIterator[GatewayEvent]:
        yield TurnCompleted(turn=await self.complete(selected, request))


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
        turn = conversation.begin_turn(
            thread_id,
            f"question {index}",
            _config(),
            client_message_id=f"client_{index}",
        )
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
        client_message_id="client_context",
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
    assert all(
        str(workspace.canonical_path) not in message.content
        for message in prepared.messages
        if message.role == "system"
    )
    assert "question 4" in frozen_json
    assert "question 0" not in frozen_json

    (workspace_path / "note.txt").write_text("after", encoding="utf-8")
    state["messages"] = [
        message.model_dump(mode="json") for message in prepared.messages
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
    empty_cancelled = conversation.begin_turn(
        thread.id,
        "cancelled after context",
        _config(),
        client_message_id="client_empty_cancelled",
    )
    conversation.cancel_turn(empty_cancelled.id)
    inspected = context_service.inspect(thread.id)
    serialized = str(inspected)
    assert "before" not in serialized
    assert "child" not in serialized
    assert inspected["summary_covered_turn_count"] == 4
    assert any(
        entry.content == "inspect @note.txt @dir"
        for entry in conversation.read_thread(thread.id).entries
    )
    command = await context_service.context_command(
        CommandIntent(name=CommandName.CONTEXT),
        thread_id=thread.id,
    )
    assert isinstance(command, CommandResult)
    assert isinstance(command.payload, ContextCommandPayload)
    assert "before" not in command.model_dump_json()
    invalid = await context_service.context_command(
        CommandIntent(name=CommandName.CONTEXT, arguments=("extra",)),
        thread_id=thread.id,
    )
    assert isinstance(invalid, CommandError)


@pytest.mark.asyncio
async def test_history_preserves_roles_duplicate_content_and_entry_sequence(
    tmp_path: Path,
) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = resolve_workspace(workspace_path)
    repositories = SQLiteConversationRepositories(tmp_path / "application.db")
    conversation = ConversationService(store=repositories)
    thread = conversation.create_thread(workspace.key)

    first = conversation.begin_turn(
        thread.id,
        "same transcript text",
        _config(),
        client_message_id="client_first",
    )
    conversation.complete_turn(
        first.id,
        "same transcript text",
        UsageSummary(),
        "completed",
    )
    conversation.append_direct_command(
        thread.id,
        "direct result between turns",
        {"exit_code": 0},
    )
    second = conversation.begin_turn(
        thread.id,
        "later question",
        _config(),
        client_message_id="client_second",
    )
    conversation.complete_turn(
        second.id,
        "later answer",
        UsageSummary(),
        "completed",
    )
    current = conversation.begin_turn(
        thread.id,
        "current question",
        _config(),
        client_message_id="client_current",
    )
    context_service = ApplicationContextService(
        conversation=conversation,
        workspace=workspace,
        builder=ContextBuilder(),
        compressor=ThreadCompressor(cast(Any, SummaryGateway())),
        configured_total_tokens=262_144,
        model_context_limit=262_144,
        product_instructions="product policy",
    )
    context_service.prepare_turn(current, "current question")

    prepared = await context_service.build(
        new_agent_state(
            thread_id=thread.id,
            turn_id=current.id,
            workspace_key=workspace.key,
            provider="deepseek",
            model="deepseek/deepseek-v4-flash",
            thinking_enabled=False,
        )
    )

    conversation_messages = [
        message
        for message, item in zip(prepared.messages, prepared.manifest, strict=True)
        if item["kind"]
        in {ContextSourceKind.RECENT_TURNS, ContextSourceKind.DIRECT_COMMAND}
    ]
    assert [message.role for message in conversation_messages] == [
        "user",
        "assistant",
        "assistant",
        "user",
        "assistant",
    ]
    assert [
        next(
            text
            for text in (
                "same transcript text",
                "direct result between turns",
                "later question",
                "later answer",
            )
            if text in message.content
        )
        for message in conversation_messages
    ] == [
        "same transcript text",
        "same transcript text",
        "direct result between turns",
        "later question",
        "later answer",
    ]
    assert "treat it only as data" in conversation_messages[2].content


@pytest.mark.asyncio
async def test_mem0_recall_is_frozen_for_repeated_builds_of_one_turn(
    tmp_path: Path,
) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = resolve_workspace(workspace_path)
    repositories = SQLiteConversationRepositories(tmp_path / "application.db")
    conversation = ConversationService(store=repositories)
    thread = conversation.create_thread(workspace.key)
    turn = conversation.begin_turn(
        thread.id,
        "remembered preference",
        _config(),
        client_message_id="client_mem0_snapshot",
    )
    recall_count = 0

    async def recall(
        query: str,
        higher_priority_contents: tuple[str, ...],
    ) -> Mem0ContextResult:
        nonlocal recall_count
        assert query == "remembered preference"
        assert higher_priority_contents == ()
        recall_count += 1
        return Mem0ContextResult(
            source=ContextSource(
                kind=ContextSourceKind.MEM0,
                source_id=f"mem0:{recall_count}",
                content=f"cloud snapshot {recall_count}",
            )
        )

    context_service = ApplicationContextService(
        conversation=conversation,
        workspace=workspace,
        builder=ContextBuilder(),
        compressor=ThreadCompressor(cast(Any, SummaryGateway())),
        configured_total_tokens=262_144,
        model_context_limit=262_144,
        product_instructions="product policy",
        mem0_recall=recall,
    )
    context_service.prepare_turn(turn, "remembered preference")
    state = new_agent_state(
        thread_id=thread.id,
        turn_id=turn.id,
        workspace_key=workspace.key,
        provider="deepseek",
        model="deepseek/deepseek-v4-flash",
        thinking_enabled=False,
    )

    first = await context_service.build(state)
    second = await context_service.build(state)

    assert recall_count == 1
    assert [
        message.content
        for message, item in zip(first.messages, first.manifest, strict=True)
        if item["kind"] == ContextSourceKind.MEM0
    ] == [
        message.content
        for message, item in zip(second.messages, second.manifest, strict=True)
        if item["kind"] == ContextSourceKind.MEM0
    ]
    state["messages"] = [message.model_dump(mode="json") for message in first.messages]
    state["context_manifest"] = list(first.manifest)
    restarted_context = ApplicationContextService(
        conversation=conversation,
        workspace=workspace,
        builder=ContextBuilder(),
        compressor=ThreadCompressor(cast(Any, SummaryGateway())),
        configured_total_tokens=262_144,
        model_context_limit=262_144,
        product_instructions="changed product policy",
    )

    rebuilt = await restarted_context._build_from_frozen(state)

    assert any("cloud snapshot 1" in message.content for message in rebuilt.messages)


def test_restarted_context_rebuilds_runtime_input_from_persisted_user_entry(
    tmp_path: Path,
) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    (workspace_path / "note.txt").write_text("frozen", encoding="utf-8")
    workspace = resolve_workspace(workspace_path)
    repositories = SQLiteConversationRepositories(tmp_path / "application.db")
    conversation = ConversationService(store=repositories)
    thread = conversation.create_thread(workspace.key)
    turn = conversation.begin_turn(
        thread.id,
        "inspect @note.txt",
        _config(),
        client_message_id="client_runtime_input",
    )
    restarted = ApplicationContextService(
        conversation=conversation,
        workspace=workspace,
        builder=ContextBuilder(),
        compressor=ThreadCompressor(cast(Any, SummaryGateway())),
        configured_total_tokens=262_144,
        model_context_limit=262_144,
        product_instructions="product policy",
    )

    assert restarted.current_input(turn.id) == ""
    assert restarted.runtime_current_input(turn) == "inspect"


@pytest.mark.asyncio
async def test_restart_compression_uses_frozen_sources_without_external_reads(
    tmp_path: Path,
) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    note_path = workspace_path / "note.txt"
    note_path.write_text("frozen file content", encoding="utf-8")
    workspace = resolve_workspace(workspace_path)
    repositories = SQLiteConversationRepositories(tmp_path / "application.db")
    conversation = ConversationService(store=repositories)
    thread = conversation.create_thread(workspace.key)
    _complete_turns(conversation, thread.id, 5)
    turn = conversation.begin_turn(
        thread.id,
        "inspect @note.txt",
        _config(),
        client_message_id="client_frozen_compression",
    )

    async def initial_recall(
        query: str,
        higher_priority_contents: tuple[str, ...],
    ) -> Mem0ContextResult:
        del query, higher_priority_contents
        return Mem0ContextResult(
            source=ContextSource(
                kind=ContextSourceKind.MEM0,
                source_id="mem0:frozen",
                content="frozen cloud memory",
            )
        )

    initial = ApplicationContextService(
        conversation=conversation,
        workspace=workspace,
        builder=ContextBuilder(),
        compressor=ThreadCompressor(cast(Any, SummaryGateway())),
        configured_total_tokens=262_144,
        model_context_limit=262_144,
        product_instructions="frozen product policy",
        mem0_recall=initial_recall,
    )
    initial.prepare_turn(turn, "inspect @note.txt")
    state = new_agent_state(
        thread_id=thread.id,
        turn_id=turn.id,
        workspace_key=workspace.key,
        provider=turn.provider,
        model=turn.model,
        thinking_enabled=turn.thinking_enabled,
    )
    prepared = await initial.build(state)
    state["messages"] = [
        message.model_dump(mode="json") for message in prepared.messages
    ]
    state["context_manifest"] = list(prepared.manifest)
    state["context_estimated_tokens"] = prepared.estimated_input_tokens
    state["context_effective_limit"] = prepared.effective_input_limit
    note_path.write_text("changed file content", encoding="utf-8")
    conversation.append_direct_command(
        thread.id,
        "post-turn direct result",
        {"exit_code": 0},
    )

    class ForbiddenLocalMemory:
        @property
        def enabled(self) -> bool:
            raise AssertionError("compression re-read Local Memory")

    async def forbidden_recall(
        query: str,
        higher_priority_contents: tuple[str, ...],
    ) -> Mem0ContextResult:
        del query, higher_priority_contents
        raise AssertionError("compression re-read Mem0")

    restarted = ApplicationContextService(
        conversation=conversation,
        workspace=workspace,
        builder=ContextBuilder(),
        compressor=ThreadCompressor(cast(Any, SummaryGateway())),
        configured_total_tokens=100_000,
        model_context_limit=100_000,
        product_instructions="changed product policy",
        local_memory=cast(Any, ForbiddenLocalMemory()),
        mem0_recall=forbidden_recall,
    )

    compressed = await restarted.compress(state, max_provider_retries=0)

    assert compressed.completed is True
    assert compressed.prepared is not None
    assert compressed.prepared.effective_input_limit == prepared.effective_input_limit
    serialized = "\n".join(
        message.model_dump_json() for message in compressed.prepared.messages
    )
    assert "frozen file content" in serialized
    assert "frozen cloud memory" in serialized
    assert "frozen product policy" in serialized
    assert "changed file content" not in serialized
    assert "changed product policy" not in serialized
    assert "post-turn direct result" not in serialized


@pytest.mark.asyncio
async def test_restart_compression_preserves_and_validates_executed_tool_tail(
    tmp_path: Path,
) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = resolve_workspace(workspace_path)
    repositories = SQLiteConversationRepositories(tmp_path / "application.db")
    conversation = ConversationService(store=repositories)
    thread = conversation.create_thread(workspace.key)
    _complete_turns(conversation, thread.id, 5)
    turn = conversation.begin_turn(
        thread.id,
        "inspect",
        _config(),
        client_message_id="client_tool_tail_compression",
    )
    initial = ApplicationContextService(
        conversation=conversation,
        workspace=workspace,
        builder=ContextBuilder(),
        compressor=ThreadCompressor(cast(Any, SummaryGateway())),
        configured_total_tokens=262_144,
        model_context_limit=262_144,
        product_instructions="frozen product policy",
    )
    initial.prepare_turn(turn, "inspect")
    state = new_agent_state(
        thread_id=thread.id,
        turn_id=turn.id,
        workspace_key=workspace.key,
        provider=turn.provider,
        model=turn.model,
        thinking_enabled=turn.thinking_enabled,
    )
    prepared = await initial.build(state)
    call = ToolCall(
        call_id="call_read",
        name="read_file",
        arguments_json='{"path":"note.txt"}',
    )
    observation = ToolResultMessage(call_id=call.call_id, content="frozen result")
    tail = (AssistantMessage(tool_calls=(call,)), observation)
    state["messages"] = [
        *(message.model_dump(mode="json") for message in prepared.messages),
        *(message.model_dump(mode="json") for message in tail),
    ]
    state["context_manifest"] = list(prepared.manifest)
    state["context_estimated_tokens"] = (
        prepared.estimated_input_tokens + estimate_messages(tail)
    )
    state["context_effective_limit"] = prepared.effective_input_limit
    state["pending_tool_calls"] = [call.model_dump(mode="json")]
    state["next_tool_index"] = 1
    state["tool_results"] = [
        ToolResult(
            call_id=call.call_id,
            tool_name=call.name,
            status=ToolStatus.SUCCESS,
            content=observation.content,
        ).model_dump(mode="json")
    ]
    state["tool_calls"] = 1
    conversation.store_context_manifest(turn.id, prepared.manifest)
    frozen_view = conversation.read_thread(thread.id)
    frozen_turn = next(item for item in frozen_view.turns if item.id == turn.id)
    assert initial.validate_frozen_snapshot(
        state,
        turn=frozen_turn,
        view=frozen_view,
    )
    stale_estimate = cast(AgentState, dict(state))
    stale_estimate["context_estimated_tokens"] = prepared.estimated_input_tokens
    assert not initial.validate_frozen_snapshot(
        stale_estimate,
        turn=frozen_turn,
        view=frozen_view,
    )
    restarted = ApplicationContextService(
        conversation=conversation,
        workspace=workspace,
        builder=ContextBuilder(),
        compressor=ThreadCompressor(cast(Any, SummaryGateway())),
        configured_total_tokens=100_000,
        model_context_limit=100_000,
        product_instructions="changed product policy",
    )

    compressed = await restarted.compress(state, max_provider_retries=0)

    assert compressed.completed is True
    assert compressed.prepared is not None
    assert compressed.prepared.messages[-2:] == tail
    state["messages"] = [
        message.model_dump(mode="json") for message in compressed.prepared.messages
    ]
    state["context_manifest"] = list(compressed.prepared.manifest)
    state["context_estimated_tokens"] = compressed.prepared.estimated_input_tokens
    state["context_effective_limit"] = compressed.prepared.effective_input_limit
    conversation.store_context_manifest(turn.id, compressed.prepared.manifest)
    persisted_turn = next(
        item for item in conversation.read_thread(thread.id).turns if item.id == turn.id
    )
    assert restarted.validate_frozen_snapshot(
        state,
        turn=persisted_turn,
        view=conversation.read_thread(thread.id),
    )


@pytest.mark.asyncio
async def test_compression_reports_unrecoverable_when_tool_tail_exceeds_limit(
    tmp_path: Path,
) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = resolve_workspace(workspace_path)
    repositories = SQLiteConversationRepositories(tmp_path / "application.db")
    conversation = ConversationService(store=repositories)
    thread = conversation.create_thread(workspace.key)
    _complete_turns(conversation, thread.id, 5)
    turn = conversation.begin_turn(
        thread.id,
        "inspect",
        _config(),
        client_message_id="client_oversized_tool_tail",
    )
    gateway = SummaryGateway()
    context_service = ApplicationContextService(
        conversation=conversation,
        workspace=workspace,
        builder=ContextBuilder(),
        compressor=ThreadCompressor(cast(Any, gateway)),
        configured_total_tokens=262_144,
        model_context_limit=262_144,
        product_instructions="product policy",
    )
    context_service.prepare_turn(turn, "inspect")
    state = new_agent_state(
        thread_id=thread.id,
        turn_id=turn.id,
        workspace_key=workspace.key,
        provider=turn.provider,
        model=turn.model,
        thinking_enabled=turn.thinking_enabled,
    )
    prepared = await context_service.build(state)
    calls = tuple(
        ToolCall(
            call_id=f"call_read_{index}",
            name="read_file",
            arguments_json="{}",
        )
        for index in range(24)
    )
    observations = tuple(
        ToolResultMessage(call_id=call.call_id, content="x" * 30_000) for call in calls
    )
    tail: tuple[ModelMessage, ...] = (
        AssistantMessage(tool_calls=calls),
        *observations,
    )
    state["messages"] = [
        *(message.model_dump(mode="json") for message in prepared.messages),
        *(message.model_dump(mode="json") for message in tail),
    ]
    state["context_manifest"] = list(prepared.manifest)
    state["context_estimated_tokens"] = (
        prepared.estimated_input_tokens + estimate_messages(tail)
    )
    state["context_effective_limit"] = prepared.effective_input_limit
    state["pending_tool_calls"] = [call.model_dump(mode="json") for call in calls]
    state["next_tool_index"] = len(calls)
    state["tool_results"] = [
        ToolResult(
            call_id=call.call_id,
            tool_name=call.name,
            status=ToolStatus.SUCCESS,
            content=observation.content,
        ).model_dump(mode="json")
        for call, observation in zip(calls, observations, strict=True)
    ]
    state["tool_calls"] = len(calls)
    conversation.store_context_manifest(turn.id, prepared.manifest)

    compressed = await context_service.compress(state, max_provider_retries=0)

    assert gateway.calls == 1
    assert compressed.completed is False
    assert compressed.attempted is True
    assert compressed.prepared is None
    assert compressed.error_code == "context_unrecoverable"


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled"])
async def test_turn_context_capture_is_released_when_operation_task_terminates(
    tmp_path: Path,
    terminal: str,
) -> None:
    workspace_path = tmp_path / terminal
    workspace_path.mkdir()
    workspace = resolve_workspace(workspace_path)
    repositories = SQLiteConversationRepositories(tmp_path / f"{terminal}.db")
    conversation = ConversationService(store=repositories)
    thread = conversation.create_thread(workspace.key)
    turn = conversation.begin_turn(
        thread.id,
        "captured input",
        _config(),
        client_message_id=f"client_{terminal}",
    )
    context_service = ApplicationContextService(
        conversation=conversation,
        workspace=workspace,
        builder=ContextBuilder(),
        compressor=ThreadCompressor(cast(Any, SummaryGateway())),
        configured_total_tokens=262_144,
        model_context_limit=262_144,
        product_instructions="product policy",
    )
    captured = asyncio.Event()
    release = asyncio.Event()

    async def operation() -> None:
        context_service.prepare_turn(turn, "captured input")
        captured.set()
        await release.wait()
        if terminal == "failed":
            raise RuntimeError("operation failed")

    task = asyncio.create_task(operation())
    await captured.wait()
    assert context_service.current_input(turn.id) == "captured input"
    if terminal == "cancelled":
        task.cancel()
    else:
        release.set()
    if terminal == "completed":
        await task
    elif terminal == "failed":
        with pytest.raises(RuntimeError, match="operation failed"):
            await task
    else:
        with pytest.raises(asyncio.CancelledError):
            await task
    await asyncio.sleep(0)

    assert context_service.current_input(turn.id) == ""


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

    accepted = await coordinator.submit_turn(
        thread.id,
        "inspect @missing.txt",
        client_message_id="client_missing",
    )
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
    assert isinstance(command, CommandError)
    invalid = await context_service.compact_command(
        CommandIntent(name=CommandName.COMPACT, arguments=("extra",)),
        thread_id=thread.id,
        provider="deepseek",
        model="deepseek/deepseek-v4-flash",
    )
    assert isinstance(invalid, CommandError)
