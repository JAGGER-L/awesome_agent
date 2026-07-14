from pathlib import Path

from pydantic import BaseModel

from awesome_agent.application.change_scope import ChangeScope
from awesome_agent.core.changes import ChangeJournal
from awesome_agent.core.tools import ToolExecutionContext, ToolOutput, ToolSpec
from awesome_agent.core.tools.registry import ToolRegistry
from awesome_agent.core.workspace import resolve_workspace
from awesome_agent.storage.changes import FileChangeBlobStore, SQLiteChangeSetStore


class _Arguments(BaseModel):
    pass


async def _handler(
    arguments: BaseModel,
    context: ToolExecutionContext,
) -> ToolOutput:
    del arguments, context
    return ToolOutput(content="ok")


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    for name, read_only in (
        ("read_file", True),
        ("write_file", False),
        ("edit_file", False),
    ):
        registry.register(
            spec=ToolSpec(
                name=name,
                description=f"{name} test tool",
                input_schema=_Arguments.model_json_schema(),
                capability="workspace.read" if read_only else "workspace.write",
                read_only=read_only,
            ),
            input_model=_Arguments,
            handler=_handler,
        )
    return registry


def _scope(
    tmp_path: Path,
) -> tuple[ChangeScope, SQLiteChangeSetStore, str]:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    workspace = resolve_workspace(workspace_path)
    store = SQLiteChangeSetStore(tmp_path / "application.db")
    journal = ChangeJournal(
        store,
        FileChangeBlobStore(tmp_path / "change-journal"),
        workspace,
    )
    return (
        ChangeScope(
            journal=journal,
            store=store,
            registry=_registry(),
            session_id="session_1",
            workspace=workspace,
        ),
        store,
        workspace.key,
    )


def test_read_only_tool_does_not_allocate_change_set(tmp_path: Path) -> None:
    scope, store, workspace_key = _scope(tmp_path)

    assert (
        scope.change_set_for_tool(
            tool_name="read_file",
            owner="turn_1",
            turn_id="turn_1",
        )
        is None
    )
    assert store.latest(workspace_key) is None


def test_unknown_tool_does_not_allocate_change_set(tmp_path: Path) -> None:
    scope, store, workspace_key = _scope(tmp_path)

    assert (
        scope.change_set_for_tool(
            tool_name="unknown_tool",
            owner="turn_1",
            turn_id="turn_1",
        )
        is None
    )
    assert store.latest(workspace_key) is None


def test_write_tools_reuse_one_owner_change_set(tmp_path: Path) -> None:
    scope, store, workspace_key = _scope(tmp_path)

    first = scope.change_set_for_tool(
        tool_name="write_file",
        owner="turn_1",
        turn_id="turn_1",
    )
    second = scope.change_set_for_tool(
        tool_name="edit_file",
        owner="turn_1",
        turn_id="turn_1",
    )

    assert first is not None
    assert second == first
    assert store.latest(workspace_key) is not None


def test_sealing_without_a_mutating_tool_is_a_noop(tmp_path: Path) -> None:
    scope, store, workspace_key = _scope(tmp_path)

    scope.seal("turn_1")

    assert store.latest(workspace_key) is None
