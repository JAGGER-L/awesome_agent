from pathlib import Path

import pytest

from awesome_agent.application.change_scope import ChangeScope
from awesome_agent.core.changes import ChangeJournal
from awesome_agent.core.tools.builtins import (
    register_modifying_tools,
    register_read_tools,
)
from awesome_agent.core.tools.process import ProcessRunner
from awesome_agent.core.tools.registry import ToolRegistry
from awesome_agent.core.workspace import resolve_workspace
from awesome_agent.storage import ApplicationSQLite
from awesome_agent.storage.changes import FileChangeBlobStore, SQLiteChangeSetStore


@pytest.mark.asyncio
async def test_builtin_tool_metadata_controls_change_scope(tmp_path: Path) -> None:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = resolve_workspace(workspace_path)
    database = ApplicationSQLite(tmp_path / "application.db")
    await database.initialize()
    store = SQLiteChangeSetStore(database)
    journal = ChangeJournal(
        store,
        FileChangeBlobStore(tmp_path / "change-journal"),
        workspace,
    )
    registry = ToolRegistry()
    register_read_tools(registry)
    register_modifying_tools(registry, journal, ProcessRunner())
    scope = ChangeScope(
        journal=journal,
        store=store,
        registry=registry,
        session_id="session_1",
        workspace=workspace,
    )

    for tool_name in ("ls", "read_file", "glob", "grep"):
        assert (
            await scope.change_set_for_tool(
                tool_name=tool_name,
                owner="turn_read",
                turn_id="turn_read",
            )
            is None
        )
    assert await store.latest(workspace.key) is None

    execute_change_set = await scope.change_set_for_tool(
        tool_name="execute",
        owner="operation_1",
        turn_id=None,
    )
    assert execute_change_set is not None
    assert await store.get(execute_change_set) is not None
    await database.aclose()
