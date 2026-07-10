from pathlib import Path

import pytest

from awesome_agent.application import (
    InteractionDecision,
    LocalApplication,
    StartupStatus,
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
