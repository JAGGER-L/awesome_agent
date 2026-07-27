import asyncio
import copy
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
from awesome_agent.application.context import (
    ApplicationContextService,
    frozen_context_manifests_share_lineage,
    frozen_context_snapshot_is_valid,
)
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
    estimate_text,
)
from awesome_agent.conversation import ConversationService, UsageSummary
from awesome_agent.core.events import CollectingEventSink, EventEmitter
from awesome_agent.core.tools import ToolResult, ToolStatus
from awesome_agent.core.workspace import resolve_workspace
from awesome_agent.extensions.skills import SkillLoader, discover_skills
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
from awesome_agent.storage.application_sqlite import ApplicationSQLite
from awesome_agent.storage.conversations import SQLiteConversationRepositories


@pytest.fixture
async def application_database(tmp_path: Path) -> AsyncIterator[ApplicationSQLite]:
    database = ApplicationSQLite(tmp_path / "application.db")
    await database.initialize()
    try:
        yield database
    finally:
        await database.aclose()


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


def _config(*, skill_mode: str = "auto") -> TurnConfig:
    return TurnConfig(
        provider="deepseek",
        model="deepseek/deepseek-v4-flash",
        budgets=BudgetConfig(),
        skill_mode=skill_mode,
    )


async def _complete_turns(
    conversation: ConversationService,
    thread_id: str,
    count: int,
) -> None:
    for index in range(count):
        turn = await conversation.begin_turn(
            thread_id,
            f"question {index}",
            _config(),
            client_message_id=f"client_{index}",
        )
        await conversation.complete_turn(
            turn.id,
            f"answer {index}",
            UsageSummary(),
            "completed",
        )


@pytest.mark.asyncio
async def test_multi_turn_summary_direct_command_and_paths_are_bounded_and_frozen(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    (workspace_path / "note.txt").write_text("before", encoding="utf-8")
    directory = workspace_path / "dir"
    directory.mkdir()
    (directory / "child.txt").write_text("child", encoding="utf-8")
    workspace = resolve_workspace(workspace_path)
    repositories = SQLiteConversationRepositories(application_database)
    conversation = ConversationService(store=repositories)
    thread = await conversation.create_thread(workspace.key)
    await _complete_turns(conversation, thread.id, 8)
    await conversation.append_direct_command(
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

    turn = await conversation.begin_turn(
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

    await conversation.complete_turn(
        turn.id,
        "done",
        UsageSummary(),
        "completed",
        tuple(prepared.manifest),
    )
    empty_cancelled = await conversation.begin_turn(
        thread.id,
        "cancelled after context",
        _config(),
        client_message_id="client_empty_cancelled",
    )
    await conversation.cancel_turn(empty_cancelled.id)
    inspected = await context_service.inspect(thread.id)
    serialized = str(inspected)
    assert "before" not in serialized
    assert "child" not in serialized
    assert inspected["summary_covered_turn_count"] == 4
    assert any(
        entry.content == "inspect @note.txt @dir"
        for entry in (await conversation.read_thread(thread.id)).entries
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
    application_database: ApplicationSQLite,
) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = resolve_workspace(workspace_path)
    repositories = SQLiteConversationRepositories(application_database)
    conversation = ConversationService(store=repositories)
    thread = await conversation.create_thread(workspace.key)

    first = await conversation.begin_turn(
        thread.id,
        "same transcript text",
        _config(),
        client_message_id="client_first",
    )
    await conversation.complete_turn(
        first.id,
        "same transcript text",
        UsageSummary(),
        "completed",
    )
    await conversation.append_direct_command(
        thread.id,
        "direct result between turns",
        {"exit_code": 0},
    )
    second = await conversation.begin_turn(
        thread.id,
        "later question",
        _config(),
        client_message_id="client_second",
    )
    await conversation.complete_turn(
        second.id,
        "later answer",
        UsageSummary(),
        "completed",
    )
    current = await conversation.begin_turn(
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
    application_database: ApplicationSQLite,
) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = resolve_workspace(workspace_path)
    repositories = SQLiteConversationRepositories(application_database)
    conversation = ConversationService(store=repositories)
    thread = await conversation.create_thread(workspace.key)
    turn = await conversation.begin_turn(
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


@pytest.mark.asyncio
async def test_restarted_context_rebuilds_runtime_input_from_persisted_user_entry(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    (workspace_path / "note.txt").write_text("frozen", encoding="utf-8")
    workspace = resolve_workspace(workspace_path)
    repositories = SQLiteConversationRepositories(application_database)
    conversation = ConversationService(store=repositories)
    thread = await conversation.create_thread(workspace.key)
    turn = await conversation.begin_turn(
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
    assert await restarted.runtime_current_input(turn) == "inspect"


@pytest.mark.asyncio
async def test_restart_compression_uses_frozen_sources_without_external_reads(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    note_path = workspace_path / "note.txt"
    note_path.write_text("frozen file content", encoding="utf-8")
    workspace = resolve_workspace(workspace_path)
    repositories = SQLiteConversationRepositories(application_database)
    conversation = ConversationService(store=repositories)
    thread = await conversation.create_thread(workspace.key)
    await _complete_turns(conversation, thread.id, 5)
    turn = await conversation.begin_turn(
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
    await conversation.append_direct_command(
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
    application_database: ApplicationSQLite,
) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = resolve_workspace(workspace_path)
    repositories = SQLiteConversationRepositories(application_database)
    conversation = ConversationService(store=repositories)
    thread = await conversation.create_thread(workspace.key)
    await _complete_turns(conversation, thread.id, 5)
    turn = await conversation.begin_turn(
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
    await conversation.store_context_manifest(turn.id, prepared.manifest)
    frozen_view = await conversation.read_thread(thread.id)
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
    await conversation.store_context_manifest(turn.id, compressed.prepared.manifest)
    persisted_view = await conversation.read_thread(thread.id)
    persisted_turn = next(item for item in persisted_view.turns if item.id == turn.id)
    assert restarted.validate_frozen_snapshot(
        state,
        turn=persisted_turn,
        view=persisted_view,
    )


@pytest.mark.asyncio
async def test_compression_reports_unrecoverable_when_tool_tail_exceeds_limit(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = resolve_workspace(workspace_path)
    repositories = SQLiteConversationRepositories(application_database)
    conversation = ConversationService(store=repositories)
    thread = await conversation.create_thread(workspace.key)
    await _complete_turns(conversation, thread.id, 5)
    turn = await conversation.begin_turn(
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
    await conversation.store_context_manifest(turn.id, prepared.manifest)

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
    application_database: ApplicationSQLite,
) -> None:
    workspace_path = tmp_path / terminal
    workspace_path.mkdir()
    workspace = resolve_workspace(workspace_path)
    repositories = SQLiteConversationRepositories(application_database)
    conversation = ConversationService(store=repositories)
    thread = await conversation.create_thread(workspace.key)
    turn = await conversation.begin_turn(
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
    application_database: ApplicationSQLite,
) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = resolve_workspace(workspace_path)
    repositories = SQLiteConversationRepositories(application_database)
    conversation = ConversationService(store=repositories)
    thread = await conversation.create_thread(workspace.key)
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

    async def runtime_context_factory(
        turn: object,
        operation: str,
        projector: object,
    ) -> AgentRuntimeContext:
        del turn, operation, projector
        return cast(AgentRuntimeContext, object())

    async def seal_changes(turn_id: str) -> None:
        del turn_id

    coordinator = TurnCoordinator(
        workspace_key=workspace.key,
        conversation=conversation,
        config_resolver=lambda current: _config(),
        graph=cast(Any, graph),
        runtime_context_factory=runtime_context_factory,
        operations=OperationController(emitter),
        emitter=emitter,
        checkpoints=Checkpoints(),
        seal_changes=seal_changes,
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
    view = await conversation.read_thread(thread.id)
    assert view.entries[0].content == "inspect @missing.txt"
    assert view.turns[0].error_code == "invalid_explicit_path"


@pytest.mark.asyncio
async def test_compact_failure_keeps_history_and_summary_unchanged(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = resolve_workspace(workspace_path)
    repositories = SQLiteConversationRepositories(application_database)
    conversation = ConversationService(store=repositories)
    thread = await conversation.create_thread(workspace.key)
    await _complete_turns(conversation, thread.id, 5)
    before = await conversation.read_thread(thread.id)
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

    after = await conversation.read_thread(thread.id)
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


@pytest.mark.asyncio
async def test_auto_skill_catalog_is_mandatory_bounded_and_deterministic(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    workspace_path = tmp_path / "workspace-auto-skills"
    workspace_path.mkdir()
    user_skills = tmp_path / "user-skills"
    oversized = user_skills / "a-oversized"
    oversized.mkdir(parents=True)
    oversized_tools = ", ".join(
        f"tool.{index:03d}.{'x' * 180}" for index in range(128)
    )
    (oversized / "SKILL.md").write_text(
        "---\n"
        "name: a-oversized\n"
        "description: Individually valid metadata must not starve later Skills\n"
        f"allowed-tools: [{oversized_tools}]\n"
        "---\n"
        "oversized catalog item\n",
        encoding="utf-8",
    )
    for index in range(70):
        name = f"skill-{index:02d}"
        package = user_skills / name
        package.mkdir(parents=True)
        (package / "SKILL.md").write_text(
            "---\n"
            f"name: {name}\n"
            f"description: Deterministic Skill {index:02d}\n"
            "allowed-tools: [read_file]\n"
            "---\n"
            f"body {index}\n",
            encoding="utf-8",
        )
    loader = SkillLoader(
        discover_skills(
            bundled_root=None,
            user_root=user_skills,
            workspace_root=None,
            workspace_trusted=False,
        )
    )
    workspace = resolve_workspace(workspace_path)
    conversation = ConversationService(
        store=SQLiteConversationRepositories(application_database)
    )
    thread = await conversation.create_thread(workspace.key)
    turn = await conversation.begin_turn(
        thread.id,
        "choose a skill",
        _config(skill_mode="auto"),
        client_message_id="client_auto_skill_catalog",
    )
    context_service = ApplicationContextService(
        conversation=conversation,
        workspace=workspace,
        builder=ContextBuilder(),
        compressor=ThreadCompressor(cast(Any, SummaryGateway())),
        configured_total_tokens=262_144,
        model_context_limit=262_144,
        product_instructions="product policy",
        skill_loader=loader,
    )

    context_service.prepare_turn(turn, "choose a skill")
    state = new_agent_state(
        thread_id=thread.id,
        turn_id=turn.id,
        workspace_key=workspace.key,
        provider=turn.provider,
        model=turn.model,
        thinking_enabled=turn.thinking_enabled,
    )
    prepared = await context_service.build(state)
    context_service.prepare_turn(turn, "choose a skill")
    repeated = await context_service.build(state)

    catalog_indexes = [
        index
        for index, item in enumerate(prepared.manifest)
        if item["kind"] == ContextSourceKind.SKILL_CATALOG
    ]
    assert len(catalog_indexes) == 1
    catalog_index = catalog_indexes[0]
    catalog = prepared.manifest[catalog_index]
    identities = cast(list[dict[str, object]], catalog["skill_identities"])
    catalog_content = prepared.messages[catalog_index].content.split("\n", 1)[1]
    assert len(identities) == 64
    identity_names = [str(item["name"]) for item in identities]
    assert identity_names == sorted(identity_names)
    assert "a-oversized" not in identity_names
    assert "skill-00" in identity_names
    assert len(catalog_content.encode("utf-8")) <= 32 * 1024
    assert estimate_text(catalog_content) <= 4_096
    assert '"catalog_complete":false' in catalog_content
    assert prepared.messages[catalog_index].role == "system"
    assert repeated.manifest == prepared.manifest
    assert repeated.messages == prepared.messages

    state["messages"] = [
        message.model_dump(mode="json") for message in prepared.messages
    ]
    state["context_manifest"] = list(prepared.manifest)
    state["context_estimated_tokens"] = prepared.estimated_input_tokens
    state["context_effective_limit"] = prepared.effective_input_limit
    await conversation.store_context_manifest(turn.id, prepared.manifest)
    view = await conversation.read_thread(thread.id)
    persisted_turn = next(item for item in view.turns if item.id == turn.id)
    assert context_service.validate_frozen_snapshot(
        state,
        turn=persisted_turn,
        view=view,
    )
    assert not context_service.validate_frozen_snapshot(
        state,
        turn=persisted_turn.model_copy(update={"skill_mode": "off"}),
        view=view,
    )


@pytest.mark.asyncio
async def test_named_and_off_skill_modes_freeze_distinct_context_shapes(
    tmp_path: Path,
    application_database: ApplicationSQLite,
) -> None:
    workspace_path = tmp_path / "workspace-skill-modes"
    workspace_path.mkdir()
    user_skills = tmp_path / "named-user-skills"
    package = user_skills / "review"
    package.mkdir(parents=True)
    skill_file = package / "SKILL.md"
    skill_file.write_text(
        "---\n"
        "name: review\n"
        "description: Review code\n"
        "allowed-tools: [read_file]\n"
        "---\n"
        "frozen review instructions\n",
        encoding="utf-8",
    )
    loader = SkillLoader(
        discover_skills(
            bundled_root=None,
            user_root=user_skills,
            workspace_root=None,
            workspace_trusted=False,
        )
    )
    workspace = resolve_workspace(workspace_path)
    conversation = ConversationService(
        store=SQLiteConversationRepositories(application_database)
    )
    context_service = ApplicationContextService(
        conversation=conversation,
        workspace=workspace,
        builder=ContextBuilder(),
        compressor=ThreadCompressor(cast(Any, SummaryGateway())),
        configured_total_tokens=262_144,
        model_context_limit=262_144,
        product_instructions="product policy",
        skill_loader=loader,
    )

    named_thread = await conversation.create_thread(workspace.key)
    named_turn = await conversation.begin_turn(
        named_thread.id,
        "review this",
        _config(skill_mode="review"),
        client_message_id="client_named_skill",
    )
    context_service.prepare_turn(named_turn, "review this")
    skill_file.write_text(
        "---\nname: review\ndescription: Changed\n---\nchanged instructions\n",
        encoding="utf-8",
    )
    named_state = new_agent_state(
        thread_id=named_thread.id,
        turn_id=named_turn.id,
        workspace_key=workspace.key,
        provider=named_turn.provider,
        model=named_turn.model,
        thinking_enabled=named_turn.thinking_enabled,
    )
    named = await context_service.build(named_state)
    named_items = [
        item for item in named.manifest if item["kind"] == ContextSourceKind.SKILL
    ]
    assert len(named_items) == 1
    frozen_identities = cast(
        list[dict[str, object]], named_items[0]["skill_identities"]
    )
    assert len(frozen_identities) == 1
    assert frozen_identities[0]["name"] == "review"
    assert any("frozen review instructions" in item.content for item in named.messages)
    assert all("changed instructions" not in item.content for item in named.messages)

    named_state["messages"] = [
        message.model_dump(mode="json") for message in named.messages
    ]
    named_state["context_manifest"] = list(named.manifest)
    named_state["context_estimated_tokens"] = named.estimated_input_tokens
    named_state["context_effective_limit"] = named.effective_input_limit
    await conversation.store_context_manifest(named_turn.id, named.manifest)
    named_view = await conversation.read_thread(named_thread.id)
    persisted_named = next(
        item for item in named_view.turns if item.id == named_turn.id
    )
    assert context_service.validate_frozen_snapshot(
        named_state,
        turn=persisted_named,
        view=named_view,
    )
    legacy_named_manifest = copy.deepcopy(list(named.manifest))
    legacy_named_item = next(
        item
        for item in legacy_named_manifest
        if item["kind"] == ContextSourceKind.SKILL
    )
    legacy_named_item["skill_identities"] = []
    legacy_named_state = copy.deepcopy(named_state)
    legacy_named_state["context_manifest"] = legacy_named_manifest
    legacy_named_turn = persisted_named.model_copy(
        update={"context_manifest": tuple(legacy_named_manifest)}
    )

    assert not frozen_context_snapshot_is_valid(
        legacy_named_state,
        turn=legacy_named_turn,
        view=named_view,
    )
    assert frozen_context_snapshot_is_valid(
        legacy_named_state,
        turn=legacy_named_turn,
        view=named_view,
        allow_legacy_skill_snapshot=True,
    )
    assert context_service.validate_frozen_snapshot(
        legacy_named_state,
        turn=legacy_named_turn,
        view=named_view,
    )
    rebuilt_legacy_named = await context_service._build_from_frozen(
        legacy_named_state
    )
    assert any(
        "frozen review instructions" in item.content
        for item in rebuilt_legacy_named.messages
    )
    rebuilt_legacy_item = next(
        item
        for item in rebuilt_legacy_named.manifest
        if item["kind"] == ContextSourceKind.SKILL
    )
    assert rebuilt_legacy_item["skill_identities"] == []

    restarted = ApplicationContextService(
        conversation=conversation,
        workspace=workspace,
        builder=ContextBuilder(),
        compressor=ThreadCompressor(cast(Any, SummaryGateway())),
        configured_total_tokens=262_144,
        model_context_limit=262_144,
        product_instructions="changed policy",
    )
    rebuilt = await restarted._build_from_frozen(named_state)
    assert rebuilt.manifest == named.manifest
    changed_manifest = copy.deepcopy(list(named.manifest))
    changed_skill = cast(
        list[dict[str, object]],
        next(
            item["skill_identities"]
            for item in changed_manifest
            if item["kind"] == ContextSourceKind.SKILL
        ),
    )
    changed_skill[0]["identity"] = f"skill-v1-sha256:{'f' * 64}"
    assert not frozen_context_manifests_share_lineage(
        tuple(changed_manifest),
        named.manifest,
    )

    off_thread = await conversation.create_thread(workspace.key)
    off_turn = await conversation.begin_turn(
        off_thread.id,
        "no skills",
        _config(skill_mode="off"),
        client_message_id="client_skill_off",
    )
    context_service.prepare_turn(off_turn, "no skills")
    off_state = new_agent_state(
        thread_id=off_thread.id,
        turn_id=off_turn.id,
        workspace_key=workspace.key,
        provider=off_turn.provider,
        model=off_turn.model,
        thinking_enabled=off_turn.thinking_enabled,
    )
    off = await context_service.build(off_state)
    assert not any(
        item["kind"] in {ContextSourceKind.SKILL, ContextSourceKind.SKILL_CATALOG}
        for item in off.manifest
    )
    off_state["messages"] = [
        message.model_dump(mode="json") for message in off.messages
    ]
    off_state["context_manifest"] = list(off.manifest)
    off_state["context_estimated_tokens"] = off.estimated_input_tokens
    off_state["context_effective_limit"] = off.effective_input_limit
    await conversation.store_context_manifest(off_turn.id, off.manifest)
    off_view = await conversation.read_thread(off_thread.id)
    persisted_off = next(item for item in off_view.turns if item.id == off_turn.id)
    legacy_auto = persisted_off.model_copy(update={"skill_mode": "auto"})

    assert not frozen_context_snapshot_is_valid(
        off_state,
        turn=legacy_auto,
        view=off_view,
    )
    assert frozen_context_snapshot_is_valid(
        off_state,
        turn=legacy_auto,
        view=off_view,
        allow_legacy_skill_snapshot=True,
    )
    assert context_service.validate_frozen_snapshot(
        off_state,
        turn=legacy_auto,
        view=off_view,
    )
