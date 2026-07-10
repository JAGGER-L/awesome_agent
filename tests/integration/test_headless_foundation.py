import asyncio
import sys
from pathlib import Path

import pytest

from awesome_agent.application import (
    InteractionDecision,
    LocalApplication,
    StartupStatus,
)
from awesome_agent.application.commands import (
    CommandIntent,
    CommandName,
    CommandStatus,
)
from awesome_agent.core.events import CollectingEventSink, EventType
from awesome_agent.core.tools import ToolRequest, ToolStatus


@pytest.mark.asyncio
async def test_unknown_workspace_requires_trust_before_tools(tmp_path: Path) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret_instruction = "PROJECT_CONTENT_MUST_NOT_LOAD"
    (workspace / "AGENTS.md").write_text(secret_instruction, encoding="utf-8")
    sink = CollectingEventSink()
    application = LocalApplication.create(
        home=home,
        workspace=workspace,
        event_sink=sink,
    )

    startup = await application.start()
    result = await application.execute_tool(
        ToolRequest(
            call_id="call_read",
            tool_name="read_file",
            arguments={"path": "AGENTS.md"},
        )
    )

    assert startup.status is StartupStatus.TRUST_REQUIRED
    assert startup.interaction_id is not None
    assert [event.event_type for event in sink.events] == [
        EventType.INTERACTION_REQUIRED
    ]
    assert result.status is ToolStatus.ERROR
    assert result.error is not None
    assert result.error.code.value == "workspace_not_trusted"
    assert secret_instruction not in result.content
    await application.close()


@pytest.mark.asyncio
async def test_denied_trust_is_not_persisted(tmp_path: Path) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first = LocalApplication.create(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
    )
    startup = await first.start()
    assert startup.interaction_id is not None

    denied = await first.respond(startup.interaction_id, InteractionDecision.DENY)
    await first.close()
    reopened = LocalApplication.create(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
    )

    assert denied is None
    assert (await reopened.start()).status is StartupStatus.TRUST_REQUIRED
    await reopened.close()


@pytest.mark.asyncio
async def test_trusted_workspace_reopens_ready(tmp_path: Path) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first = LocalApplication.create(
        home=home,
        workspace=workspace,
        event_sink=CollectingEventSink(),
    )
    startup = await first.start()
    assert startup.interaction_id is not None

    trusted = await first.respond(startup.interaction_id, InteractionDecision.TRUST)
    assert trusted is not None
    assert trusted.status is StartupStatus.READY
    await first.close()

    sink = CollectingEventSink()
    reopened = LocalApplication.create(
        home=home,
        workspace=workspace,
        event_sink=sink,
    )
    reopened_startup = await reopened.start()

    assert reopened_startup.status is StartupStatus.READY
    assert reopened_startup.interaction_id is None
    assert sink.events == []
    await reopened.close()


async def trusted_application(
    tmp_path: Path,
) -> tuple[LocalApplication, CollectingEventSink, Path]:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sink = CollectingEventSink()
    application = LocalApplication.create(
        home=home,
        workspace=workspace,
        event_sink=sink,
    )
    startup = await application.start()
    assert startup.interaction_id is not None
    ready = await application.respond(
        startup.interaction_id,
        InteractionDecision.TRUST,
    )
    assert ready is not None
    sink.events.clear()
    return application, sink, workspace


@pytest.mark.asyncio
async def test_modifying_turn_commands_and_event_sequence(tmp_path: Path) -> None:
    application, sink, workspace = await trusted_application(tmp_path)
    (workspace / "remove.txt").write_text("restore me", encoding="utf-8")
    turn_id = "turn_1"

    write = await application.execute_tool(
        ToolRequest(
            call_id="call_write",
            tool_name="write_file",
            arguments={"path": "notes.txt", "content": "first"},
        ),
        turn_id=turn_id,
    )
    edit = await application.execute_tool(
        ToolRequest(
            call_id="call_edit",
            tool_name="edit_file",
            arguments={
                "path": "notes.txt",
                "old_string": "first",
                "new_string": "second",
            },
        ),
        turn_id=turn_id,
    )
    delete = await application.execute_tool(
        ToolRequest(
            call_id="call_delete",
            tool_name="delete",
            arguments={"path": "remove.txt"},
        ),
        turn_id=turn_id,
    )
    execute = await application.execute_tool(
        ToolRequest(
            call_id="call_execute",
            tool_name="execute",
            arguments={"command": "echo executed"},
        ),
        turn_id=turn_id,
    )
    tools = await application.dispatch(CommandIntent(name=CommandName.TOOLS))
    diff = await application.dispatch(CommandIntent(name=CommandName.DIFF))
    undo = await application.dispatch(CommandIntent(name=CommandName.UNDO))
    change_set_id = undo.data["change_set_id"]
    assert isinstance(change_set_id, str)

    assert all(
        result.status is ToolStatus.SUCCESS for result in (write, edit, delete, execute)
    )
    assert tools.status is CommandStatus.SUCCESS
    tool_data = tools.data["tools"]
    assert isinstance(tool_data, list)
    assert len(tool_data) == 8
    assert diff.status is CommandStatus.SUCCESS
    assert "notes.txt" in diff.content
    assert undo.status is CommandStatus.SUCCESS
    assert undo.data["warning"] is not None
    assert not (workspace / "notes.txt").exists()
    assert (workspace / "remove.txt").read_text(encoding="utf-8") == "restore me"

    redo = await application.dispatch(
        CommandIntent(name=CommandName.REDO, arguments=(change_set_id,))
    )

    assert redo.status is CommandStatus.SUCCESS
    assert (workspace / "notes.txt").read_text(encoding="utf-8") == "second"
    assert not (workspace / "remove.txt").exists()
    sequences = [event.sequence for event in sink.events]
    assert sequences == list(range(sequences[0], sequences[0] + len(sequences)))
    await application.close()


async def wait_for_interaction(sink: CollectingEventSink) -> str:
    for _ in range(100):
        interactions = [
            event
            for event in sink.events
            if event.event_type is EventType.INTERACTION_REQUIRED
        ]
        if interactions:
            payload = interactions[-1].payload
            interaction_id = getattr(payload, "interaction_id", None)
            assert isinstance(interaction_id, str)
            return interaction_id
        await asyncio.sleep(0)
    raise AssertionError("Interaction event was not emitted.")


@pytest.mark.asyncio
async def test_execute_boundary_allow_once_is_not_reused(tmp_path: Path) -> None:
    application, sink, _ = await trusted_application(tmp_path)
    outside = tmp_path / "outside.txt"
    command = f"echo {outside}"

    denied_task = asyncio.create_task(application.execute_direct(command))
    denied_id = await wait_for_interaction(sink)
    await application.respond(denied_id, InteractionDecision.DENY)
    denied = await denied_task
    assert denied.status is ToolStatus.ERROR

    sink.events.clear()
    allowed_task = asyncio.create_task(application.execute_direct(command))
    allowed_id = await wait_for_interaction(sink)
    await application.respond(allowed_id, InteractionDecision.ALLOW_ONCE)
    allowed = await allowed_task
    assert allowed.status is ToolStatus.SUCCESS

    sink.events.clear()
    repeated_task = asyncio.create_task(application.execute_direct(command))
    repeated_id = await wait_for_interaction(sink)
    assert repeated_id != allowed_id
    await application.respond(repeated_id, InteractionDecision.DENY)
    await repeated_task
    await application.close()


@pytest.mark.asyncio
async def test_complete_fresh_state_headless_vertical_slice(tmp_path: Path) -> None:
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "source.txt").write_text("source", encoding="utf-8")
    (workspace / "remove.txt").write_text("restore", encoding="utf-8")
    sink = CollectingEventSink()
    application = LocalApplication.create(
        home=home,
        workspace=workspace,
        event_sink=sink,
    )
    startup = await application.start()
    assert startup.status is StartupStatus.TRUST_REQUIRED
    assert startup.interaction_id is not None
    ready = await application.respond(
        startup.interaction_id,
        InteractionDecision.TRUST,
    )
    assert ready is not None

    listed = await application.execute_tool(
        ToolRequest(call_id="call_ls", tool_name="ls", arguments={"path": "."})
    )
    read = await application.execute_tool(
        ToolRequest(
            call_id="call_read",
            tool_name="read_file",
            arguments={"path": "source.txt"},
        )
    )
    turn_id = "turn_vertical"
    for request in (
        ToolRequest(
            call_id="call_write",
            tool_name="write_file",
            arguments={"path": "notes.txt", "content": "first"},
        ),
        ToolRequest(
            call_id="call_edit",
            tool_name="edit_file",
            arguments={
                "path": "notes.txt",
                "old_string": "first",
                "new_string": "second",
            },
        ),
        ToolRequest(
            call_id="call_delete",
            tool_name="delete",
            arguments={"path": "remove.txt"},
        ),
        ToolRequest(
            call_id="call_execute",
            tool_name="execute",
            arguments={"command": "echo mixed-turn"},
        ),
    ):
        assert (
            await application.execute_tool(request, turn_id=turn_id)
        ).status is ToolStatus.SUCCESS

    diff = await application.dispatch(CommandIntent(name=CommandName.DIFF))
    change_set_id = diff.data["change_set_id"]
    assert isinstance(change_set_id, str)
    undo = await application.dispatch(
        CommandIntent(name=CommandName.UNDO, arguments=(change_set_id,))
    )
    assert undo.data["warning"] is not None
    redo = await application.dispatch(
        CommandIntent(name=CommandName.REDO, arguments=(change_set_id,))
    )
    assert redo.status is CommandStatus.SUCCESS

    long_command = f'"{sys.executable}" -c "import time; time.sleep(30)"'
    long_task = asyncio.create_task(application.execute_direct(long_command))
    operation_id = None
    for _ in range(200):
        status = await application.dispatch(CommandIntent(name=CommandName.STATUS))
        candidate = status.data["active_operation_id"]
        if isinstance(candidate, str):
            operation_id = candidate
            break
        await asyncio.sleep(0)
    assert operation_id is not None
    assert await application.cancel(operation_id) is True
    with pytest.raises(asyncio.CancelledError):
        await long_task

    (workspace / "notes.txt").write_text("later user edit", encoding="utf-8")
    conflict = await application.dispatch(
        CommandIntent(name=CommandName.UNDO, arguments=(change_set_id,))
    )
    assert conflict.status is CommandStatus.ERROR
    assert conflict.data["error_code"] == "change_conflict"
    assert (workspace / "notes.txt").read_text(encoding="utf-8") == "later user edit"

    assert listed.status is ToolStatus.SUCCESS
    assert read.status is ToolStatus.SUCCESS
    assert "source" in read.content
    assert diff.status is CommandStatus.SUCCESS
    assert sink.events
    sequences = [event.sequence for event in sink.events]
    assert sequences == list(range(1, len(sequences) + 1))
    assert (
        sum(event.event_type is EventType.OPERATION_CANCELLED for event in sink.events)
        == 1
    )
    first_session = startup.session_id
    await application.close()

    reopened_sink = CollectingEventSink()
    reopened = LocalApplication.create(
        home=home,
        workspace=workspace,
        event_sink=reopened_sink,
    )
    reopened_startup = await reopened.start()
    reconstructed = await reopened.dispatch(
        CommandIntent(name=CommandName.DIFF, arguments=(change_set_id,))
    )

    assert reopened_startup.status is StartupStatus.READY
    assert reopened_startup.session_id != first_session
    assert reopened_sink.events == []
    assert reconstructed.status is CommandStatus.SUCCESS
    assert "notes.txt" in reconstructed.content
    await reopened.close()
