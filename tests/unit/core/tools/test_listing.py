from pathlib import Path
from unittest.mock import Mock

import pytest

from awesome_agent.core.events import CollectingEventSink, EventEmitter
from awesome_agent.core.tools.builtins.listing import LsArguments, list_directory
from awesome_agent.core.tools.context import ToolExecutionContext
from awesome_agent.core.tools.contracts import ToolExecutionOrigin
from awesome_agent.core.workspace import resolve_workspace


@pytest.mark.asyncio
async def test_list_presentation_reports_exact_bounded_detail_count(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for name in ("a.py", "b.py", "c.py", "d.py", "e.py"):
        (workspace / name).write_text("", encoding="utf-8")
    identity = resolve_workspace(workspace)
    context = ToolExecutionContext(
        workspace=identity,
        thread_id="thread_1",
        operation_id="operation_1",
        turn_id="turn_1",
        origin=ToolExecutionOrigin.AGENT,
        emitter=EventEmitter(
            session_id="session_1",
            workspace_key=identity.key,
            sink=CollectingEventSink(),
        ),
        activity_writer=Mock(),
        monotonic=lambda: 0.0,
    )

    output = await list_directory(LsArguments(path=".", max_entries=2), context)

    assert output.presentation is not None
    assert output.presentation.summary == "2 entries"
    assert output.presentation.detail is not None
    assert len(output.presentation.detail.splitlines()) == 2
    assert output.presentation.detail_truncated_count == 3
