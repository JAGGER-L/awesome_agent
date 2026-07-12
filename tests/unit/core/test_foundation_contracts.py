import json
from datetime import UTC, datetime

from awesome_agent.core.changes import (
    ChangeLifecycle,
    ChangeReversibility,
    ChangeSet,
    ExecuteObservation,
    FileChange,
    FileChangeKind,
    FileNodeType,
)
from awesome_agent.core.contracts import new_identifier
from awesome_agent.core.tools import (
    ToolError,
    ToolErrorCode,
    ToolRequest,
    ToolResult,
    ToolSpec,
    ToolStatus,
)


def test_identifiers_are_prefixed_and_unique() -> None:
    first = new_identifier("call")
    second = new_identifier("call")
    assert first.startswith("call_")
    assert first != second


def test_tool_result_round_trips_json() -> None:
    result = ToolResult(
        call_id="call_1",
        tool_name="read_file",
        status=ToolStatus.ERROR,
        content="Path was not found.",
        metadata={"path": "missing.txt"},
        error=ToolError(
            code=ToolErrorCode.NOT_FOUND,
            message="Path was not found.",
            retryable=True,
        ),
    )
    restored = ToolResult.model_validate_json(result.model_dump_json())
    assert restored == result
    assert json.loads(result.model_dump_json())["status"] == "error"


def test_tool_spec_exports_provider_neutral_schema() -> None:
    spec = ToolSpec(
        name="read_file",
        description="Read file contents.",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        capability="workspace.read",
        read_only=True,
    )
    assert spec.input_schema["type"] == "object"


def test_tool_request_defaults_to_empty_arguments() -> None:
    request = ToolRequest(call_id="call_1", tool_name="ls")

    assert request.arguments == {}


def test_change_set_expresses_controlled_and_unmanaged_changes() -> None:
    change_set = ChangeSet(
        id="change_1",
        session_id="session_1",
        turn_id=None,
        workspace_key="ws_1",
        lifecycle=ChangeLifecycle.APPLIED,
        reversibility=ChangeReversibility.PARTIAL,
        files=[
            FileChange(
                path="src/app.py",
                kind=FileChangeKind.UPDATED,
                node_type=FileNodeType.FILE,
                before_hash="a" * 64,
                after_hash="b" * 64,
                before_blob="a" * 64,
                after_blob="b" * 64,
                before_mode=0o644,
                after_mode=0o644,
            )
        ],
        execute=[ExecuteObservation(command="pytest", observed_paths=[])],
        created_at=datetime.now(UTC),
        sealed_at=datetime.now(UTC),
    )
    assert ChangeSet.model_validate_json(change_set.model_dump_json()) == change_set
    assert change_set.reversibility is ChangeReversibility.PARTIAL
