import asyncio
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import pytest

from awesome_agent.agent import (
    AgentRuntimeContext,
    PreparedAgentContext,
    TurnBudget,
    compile_agent_graph,
)
from awesome_agent.application.events import ApplicationEventProjector
from awesome_agent.application.operations import OperationController
from awesome_agent.application.turns import TurnCoordinator
from awesome_agent.config import BudgetConfig, TurnConfig
from awesome_agent.conversation import ConversationService, Turn, TurnStatus
from awesome_agent.core.changes import ChangeJournal, ChangeLifecycle
from awesome_agent.core.events import CollectingEventSink, EventEmitter
from awesome_agent.core.tools import (
    ToolExecutionContext,
    ToolExecutionOrigin,
    ToolExecutor,
)
from awesome_agent.core.tools.builtins import (
    register_modifying_tools,
    register_read_tools,
)
from awesome_agent.core.tools.permissions import PermissionMode, PermissionSession
from awesome_agent.core.tools.process import ProcessResult
from awesome_agent.core.tools.registry import ToolRegistry
from awesome_agent.core.workspace import resolve_workspace
from awesome_agent.modeling import (
    AssistantMessage,
    GatewayEvent,
    ModelRequest,
    ModelTurn,
    StopReason,
    ToolCall,
    TurnCompleted,
    UserMessage,
)
from awesome_agent.storage.changes import FileChangeBlobStore, SQLiteChangeSetStore
from awesome_agent.storage.checkpoints import (
    LangGraphCheckpointStore,
    sqlite_checkpoint_saver,
)
from awesome_agent.storage.conversations import SQLiteConversationRepositories


class ScriptedGateway:
    def __init__(self, scripts: tuple[tuple[GatewayEvent, ...], ...]) -> None:
        self.scripts = list(scripts)
        self.requests: list[ModelRequest] = []

    async def stream(
        self,
        selected: object,
        request: ModelRequest,
    ) -> AsyncIterator[GatewayEvent]:
        del selected
        self.requests.append(request)
        for event in self.scripts.pop(0):
            yield event


class BlockingGateway:
    async def stream(
        self,
        selected: object,
        request: ModelRequest,
    ) -> AsyncIterator[GatewayEvent]:
        del selected, request
        await asyncio.Event().wait()
        if False:
            yield _completed("unreachable")


class SuccessfulProcessRunner:
    async def run(
        self,
        *,
        argv: list[str],
        cwd: Path,
        environment: dict[str, str],
        timeout_seconds: float,
        max_output_chars: int,
    ) -> ProcessResult:
        del argv, cwd, environment, timeout_seconds, max_output_chars
        return ProcessResult(
            exit_code=0,
            stdout="1 passed",
            stderr="",
            timed_out=False,
            stdout_truncated=False,
            stderr_truncated=False,
            duration_ms=1.0,
        )


def _completed(
    content: str,
    tool_calls: tuple[ToolCall, ...] = (),
) -> TurnCompleted:
    return TurnCompleted(
        turn=ModelTurn(
            provider="deepseek",
            model="deepseek/deepseek-v4-flash",
            assistant=AssistantMessage(content=content, tool_calls=tool_calls),
            stop_reason=(StopReason.TOOL_CALLS if tool_calls else StopReason.COMPLETED),
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments", "expected_status"),
    [
        (None, {}, None),
        ("read_file", {"path": "note.txt"}, "success"),
        (
            "edit_file",
            {"path": "note.txt", "old_string": "before", "new_string": "after"},
            "success",
        ),
        (
            "write_file",
            {
                "path": "circle_area.py",
                "content": "def area(radius):\n    return 3.14 * radius**2\n",
            },
            "success",
        ),
        (
            "execute",
            {"command": "python -m pytest tests/test_area.py"},
            "success",
        ),
        ("read_file", {"path": "missing.txt"}, "error"),
    ],
)
async def test_real_graph_tool_turn_commits_history_and_removes_checkpoint(
    tmp_path: Path,
    tool_name: str | None,
    arguments: dict[str, str],
    expected_status: str | None,
) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    (workspace_path / "note.txt").write_text("before", encoding="utf-8")
    workspace = resolve_workspace(workspace_path)
    repositories = SQLiteConversationRepositories(tmp_path / "application.db")
    conversation = ConversationService(store=repositories)
    thread = conversation.create_thread(workspace.key)
    change_store = SQLiteChangeSetStore(tmp_path / "application.db")
    journal = ChangeJournal(
        change_store,
        FileChangeBlobStore(tmp_path / "change-journal"),
        workspace,
    )
    registry = ToolRegistry()
    register_read_tools(registry)
    register_modifying_tools(registry, journal, SuccessfulProcessRunner())
    executor = ToolExecutor(registry)
    scripts = (
        ((_completed("done"),),)
        if tool_name is None
        else (
            (
                _completed(
                    "",
                    (
                        ToolCall(
                            call_id="call_1",
                            name=tool_name,
                            arguments_json=json.dumps(arguments),
                        ),
                    ),
                ),
            ),
            (_completed("done"),),
        )
    )
    gateway = ScriptedGateway(scripts)
    sink = CollectingEventSink()
    emitter = EventEmitter(
        session_id="session_1",
        workspace_key=workspace.key,
        sink=sink,
    )
    change_sets: dict[str, str] = {}

    async with sqlite_checkpoint_saver(tmp_path / "checkpoints.db") as saver:
        checkpoint_store = LangGraphCheckpointStore(saver)
        graph = compile_agent_graph(saver)

        def runtime_factory(
            turn: Turn,
            operation_id: str,
            projector: ApplicationEventProjector,
        ) -> AgentRuntimeContext:
            change_set = journal.begin(
                session_id="session_1",
                turn_id=turn.id,
                workspace=workspace,
            )
            change_sets[turn.id] = change_set.id

            async def context_builder(state: object) -> PreparedAgentContext:
                del state
                return PreparedAgentContext(
                    messages=(
                        UserMessage(
                            content=(
                                "validate with tests"
                                if tool_name == "execute"
                                else "inspect"
                            )
                        ),
                    ),
                    manifest=({"kind": "temporary_thread_history", "count": 1},),
                )

            return AgentRuntimeContext(
                gateway=cast(Any, gateway),
                executor=executor,
                tool_catalog=registry.specifications,
                tool_context_factory=lambda state: ToolExecutionContext(
                    workspace=workspace,
                    thread_id=turn.thread_id,
                    operation_id=operation_id,
                    turn_id=turn.id,
                    origin=ToolExecutionOrigin.AGENT,
                    emitter=emitter,
                    activity_writer=repositories.tool_activities,
                    monotonic=time.monotonic,
                    change_set_id=change_set.id,
                    permission_session=PermissionSession(
                        mode=PermissionMode.FULL_ACCESS
                    ),
                ),
                event_projector=projector,
                context_builder=context_builder,
                budget=TurnBudget(),
                monotonic=time.monotonic,
            )

        def seal_changes(turn_id: str) -> None:
            journal.seal(change_sets[turn_id])

        coordinator = TurnCoordinator(
            workspace_key=workspace.key,
            conversation=conversation,
            config_resolver=lambda current: TurnConfig(
                provider="deepseek",
                model="deepseek/deepseek-v4-flash",
                budgets=BudgetConfig(),
            ),
            graph=cast(Any, graph),
            runtime_context_factory=runtime_factory,
            operations=OperationController(emitter),
            emitter=emitter,
            checkpoints=checkpoint_store,
            seal_changes=seal_changes,
        )

        accepted = await coordinator.submit_turn(
            thread.id,
            "validate with tests" if tool_name == "execute" else "inspect",
            client_message_id="client_inspect",
        )
        await coordinator.wait(accepted.operation_id)

        assert accepted.turn_id is not None
        assert await checkpoint_store.exists(accepted.turn_id) is False

    view = conversation.read_thread(thread.id)
    assert view.turns[0].status is TurnStatus.COMPLETED
    assert view.entries[-1].content == "done"
    if expected_status is None:
        assert view.tool_activities == ()
    else:
        assert view.tool_activities[0].outcome.value == expected_status
    stored_change = change_store.get(change_sets[view.turns[0].id])
    assert stored_change is not None
    assert stored_change.lifecycle is ChangeLifecycle.APPLIED
    if tool_name == "edit_file":
        assert (workspace_path / "note.txt").read_text(encoding="utf-8") == "after"
    if tool_name == "write_file":
        assert (workspace_path / "circle_area.py").is_file()
        assert [activity.tool_name for activity in view.tool_activities] == [
            "write_file"
        ]
        assert len(gateway.requests) == 2
    if tool_name == "execute":
        assert [activity.tool_name for activity in view.tool_activities] == ["execute"]
        assert gateway.requests[0].messages[-1].content == "validate with tests"
    if expected_status == "error":
        observation = gateway.requests[1].messages[-1]
        assert observation.role == "tool"
        assert observation.is_error is True
        assert [activity.tool_name for activity in view.tool_activities] == [
            "read_file"
        ]


@pytest.mark.asyncio
async def test_real_graph_cancellation_finalizes_turn_and_checkpoint(
    tmp_path: Path,
) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = resolve_workspace(workspace_path)
    repositories = SQLiteConversationRepositories(tmp_path / "application.db")
    conversation = ConversationService(store=repositories)
    thread = conversation.create_thread(workspace.key)
    registry = ToolRegistry()
    executor = ToolExecutor(registry)
    emitter = EventEmitter(
        session_id="session_1",
        workspace_key=workspace.key,
        sink=CollectingEventSink(),
    )

    async with sqlite_checkpoint_saver(tmp_path / "checkpoints.db") as saver:
        checkpoint_store = LangGraphCheckpointStore(saver)
        graph = compile_agent_graph(saver)

        def runtime_factory(
            turn: Turn,
            operation_id: str,
            projector: ApplicationEventProjector,
        ) -> AgentRuntimeContext:
            async def context_builder(state: object) -> PreparedAgentContext:
                del state
                return PreparedAgentContext(
                    messages=(UserMessage(content="wait"),),
                    manifest=({"kind": "temporary_thread_history"},),
                )

            return AgentRuntimeContext(
                gateway=cast(Any, BlockingGateway()),
                executor=executor,
                tool_catalog=registry.specifications,
                tool_context_factory=lambda state: cast(Any, None),
                event_projector=projector,
                context_builder=context_builder,
                budget=TurnBudget(),
                monotonic=time.monotonic,
            )

        coordinator = TurnCoordinator(
            workspace_key=workspace.key,
            conversation=conversation,
            config_resolver=lambda current: TurnConfig(
                provider="deepseek",
                model="deepseek/deepseek-v4-flash",
                budgets=BudgetConfig(),
            ),
            graph=cast(Any, graph),
            runtime_context_factory=runtime_factory,
            operations=OperationController(emitter),
            emitter=emitter,
            checkpoints=checkpoint_store,
            seal_changes=lambda turn_id: None,
        )

        accepted = await coordinator.submit_turn(
            thread.id, "wait", client_message_id="client_wait"
        )
        assert await coordinator.cancel_operation(accepted.operation_id) is True
        with pytest.raises(asyncio.CancelledError):
            await coordinator.wait(accepted.operation_id)
        assert accepted.turn_id is not None
        assert await checkpoint_store.exists(accepted.turn_id) is False

    assert conversation.read_thread(thread.id).turns[0].status is TurnStatus.CANCELLED
