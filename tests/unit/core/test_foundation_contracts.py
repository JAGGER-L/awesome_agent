import json

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
        read_only=True,
    )
    assert spec.input_schema["type"] == "object"


def test_tool_request_defaults_to_empty_arguments() -> None:
    request = ToolRequest(call_id="call_1", tool_name="ls")

    assert request.arguments == {}
