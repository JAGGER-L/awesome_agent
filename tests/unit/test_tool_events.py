from __future__ import annotations

import json
from pathlib import Path

from awesome_agent.modeling import ToolCall, ToolResultMessage
from awesome_agent.runtime.tool_events import tool_event_payload


def test_tool_event_payload_marks_bash_exit_failure_as_operation_failure(
    tmp_path: Path,
) -> None:
    call = ToolCall(
        call_id="call-bash",
        name="Bash",
        arguments_json='{"command":"pytest -q"}',
    )
    result = ToolResultMessage(
        call_id="call-bash",
        content=json.dumps(
            {
                "command": "pytest -q",
                "status": "failed",
                "exit_code": 1,
                "stderr": "failed\n",
            }
        ),
    )

    payload = tool_event_payload(
        tool_name="Bash",
        call=call,
        result=result,
        workspace=tmp_path,
        duration_ms=12,
    )

    assert payload["tool"] == "Bash"
    assert payload["call_id"] == "call-bash"
    assert payload["status"] == "failed"
    assert payload["invocation_status"] == "completed"
    assert payload["operation_status"] == "failed"
    assert payload["exit_code"] == 1
    assert payload["duration_ms"] == 12
    assert "requested_path" not in payload


def test_tool_event_payload_does_not_mark_glob_paths_as_changed(
    tmp_path: Path,
) -> None:
    result = ToolResultMessage(
        call_id="glob-cube",
        content=json.dumps({"paths": ["cube.py", "snake.html"]}),
        is_error=False,
    )

    payload = tool_event_payload(
        tool_name="Glob",
        call=None,
        result=result,
        workspace=tmp_path,
    )

    assert payload["changed_files"] == []
    assert payload["paths"] == ["cube.py", "snake.html"]


def test_tool_event_payload_keeps_writefile_changed_files(tmp_path: Path) -> None:
    result = ToolResultMessage(
        call_id="write-cube",
        content=json.dumps(
            {
                "status": "updated",
                "paths": ["cube.py"],
                "preimage_hashes": {"cube.py": "old"},
                "postimage_hashes": {"cube.py": "new"},
            }
        ),
        is_error=False,
    )

    payload = tool_event_payload(
        tool_name="WriteFile",
        call=None,
        result=result,
        workspace=tmp_path,
    )

    assert payload["changed_files"] == [{"path": "cube.py", "status": "updated"}]
