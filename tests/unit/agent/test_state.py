from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from awesome_agent.agent import AgentState, new_agent_state, validate_agent_state


def _state() -> AgentState:
    state = new_agent_state(
        thread_id="thread_1",
        turn_id="turn_1",
        workspace_key="workspace_1",
        provider="deepseek",
        model="deepseek/deepseek-v4-flash",
        thinking_enabled=False,
    )
    state["context_manifest"] = [{"kind": "temporary_thread_history", "count": 2}]
    state["messages"] = [
        {"role": "user", "content": "inspect"},
        {"role": "assistant", "content": "working"},
    ]
    state["continuation"] = {"provider": "deepseek", "kind": "response"}
    state["pending_tool_calls"] = [
        {"call_id": "call_1", "name": "read_file", "arguments_json": "{}"}
    ]
    state["next_tool_index"] = 0
    state["tool_results"] = [{"call_id": "call_0", "content": "done"}]
    state["model_calls"] = 2
    state["tool_calls"] = 1
    state["provider_retries"] = 1
    state["compressions"] = 0
    state["active_execution_seconds"] = 1.5
    state["usage"] = {"input_tokens": 10, "output_tokens": 4}
    state["recovery_issue"] = None
    state["final_answer"] = "working"
    state["termination_reason"] = None
    return state


def test_complete_state_round_trips_through_json() -> None:
    state = _state()

    restored = validate_agent_state(json.loads(json.dumps(state)))

    assert restored == state


def test_state_contains_only_checkpoint_safe_json_values() -> None:
    state = _state()

    def assert_safe(value: Any) -> None:
        assert not isinstance(value, Path)
        assert not callable(value)
        assert type(value) in {dict, list, str, int, float, bool, type(None)}
        if isinstance(value, dict):
            for key, item in value.items():
                assert isinstance(key, str)
                assert_safe(item)
        elif isinstance(value, list):
            for item in value:
                assert_safe(item)

    assert_safe(state)


def test_final_answer_is_bounded_in_checkpoint_state() -> None:
    payload = dict(_state())
    payload["final_answer"] = "x" * 200_001

    with pytest.raises(ValidationError):
        validate_agent_state(payload)


@pytest.mark.parametrize(
    "forbidden",
    [
        "database_connection",
        "sdk_client",
        "tool_executor",
        "event_sink",
        "path_handle",
        "callable",
        "asyncio_task",
        "node_name",
        "run_status",
    ],
)
def test_state_rejects_runtime_objects_and_duplicate_state_machine_fields(
    forbidden: str,
) -> None:
    payload = dict(_state())
    payload[forbidden] = object()

    with pytest.raises(ValidationError):
        validate_agent_state(payload)
