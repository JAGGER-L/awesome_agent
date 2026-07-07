from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from awesome_agent.conversation.models import ThreadMessage, ThreadMessageRole
from awesome_agent.domain.enums import EventType
from awesome_agent.domain.models import RuntimeEvent
from awesome_agent.modeling.messages import AssistantMessage, ToolResultMessage
from awesome_agent.modeling.tools import ToolCall
from awesome_agent.modeling.turns import ContinuationState
from awesome_agent.runtime.approval_continuation import (
    ApprovalContinuation,
    continuation_from_payload,
    latest_open_approval_continuation,
)


def _continuation(tool_call_id: str = "call-1") -> ApprovalContinuation:
    return ApprovalContinuation(
        approval_id=uuid4(),
        tool_call_id=tool_call_id,
        tool_name="shell.execute",
        tool_version="1",
        arguments_json='{"argv":["python","square.py"]}',
        arguments_hash="hash",
        workspace_path="/workspace",
        workspace_fingerprint="fingerprint",
        capabilities=("shell:execute",),
    )


def _provider_continuation() -> ContinuationState:
    return ContinuationState(
        provider="deepseek",
        kind="chat.reasoning_content",
        data={"reasoning_content": "private chain"},
    )


def _event(continuation: ApprovalContinuation) -> RuntimeEvent:
    now = datetime.now(UTC)
    return RuntimeEvent(
        id=uuid4(),
        run_id=uuid4(),
        agent_id=None,
        event_type=EventType.APPROVAL_REQUESTED,
        sequence=1,
        payload={"approval_continuation": continuation.to_payload()},
        created_at=now,
        trace_id="trace",
        span_id=None,
        parent_span_id=None,
    )


def test_approval_continuation_payload_round_trips() -> None:
    continuation = _continuation()

    restored = continuation_from_payload(
        {"approval_continuation": continuation.to_payload()}
    )

    assert restored == continuation
    assert restored is not None
    assert restored.to_tool_call().call_id == "call-1"
    assert restored.to_tool_call().name == "shell.execute"
    assert restored.to_tool_call().arguments_json == ('{"argv":["python","square.py"]}')
    assistant = restored.to_assistant_message()
    assert assistant.tool_calls == [restored.to_tool_call()]


def test_approval_continuation_payload_round_trips_provider_continuation() -> None:
    continuation = replace(
        _continuation(),
        provider_continuation=_provider_continuation(),
    )

    restored = continuation_from_payload(
        {"approval_continuation": continuation.to_payload()}
    )

    assert restored is not None
    assert restored.provider_continuation == _provider_continuation()


def test_approval_continuation_payload_accepts_missing_provider_continuation() -> None:
    continuation = _continuation()
    payload = continuation.to_payload()
    payload.pop("provider_continuation", None)

    restored = continuation_from_payload({"approval_continuation": payload})

    assert restored is not None
    assert restored.provider_continuation is None


def test_approval_continuation_payload_round_trips_message_payloads() -> None:
    assistant = AssistantMessage(
        tool_calls=[
            ToolCall(
                call_id="call-read",
                name="ReadFile",
                arguments_json='{"path":"fixture.txt"}',
            ),
            ToolCall(
                call_id="call-bash",
                name="Bash",
                arguments_json='{"command":"python square.py"}',
            ),
        ]
    )
    read_result = ToolResultMessage(
        call_id="call-read",
        content="fixture\n",
    )
    continuation = replace(
        _continuation("call-bash"),
        message_payloads=(
            assistant.model_dump(mode="json"),
            read_result.model_dump(mode="json"),
        ),
    )

    restored = continuation_from_payload(
        {"approval_continuation": continuation.to_payload()}
    )

    assert restored is not None
    messages = restored.to_messages()
    assert messages == [assistant, read_result]


def test_latest_open_approval_continuation_skips_completed_tool_result() -> None:
    closed = _continuation("call-closed")
    open_continuation = _continuation("call-open")
    messages = [
        ThreadMessage(
            id=uuid4(),
            thread_id=uuid4(),
            role=ThreadMessageRole.TOOL,
            content="done",
            metadata={"kind": "tool_result", "tool_call_id": "call-closed"},
            created_at=datetime.now(UTC),
        )
    ]

    found = latest_open_approval_continuation(
        [_event(closed), _event(open_continuation)],
        messages,
    )

    assert found == open_continuation
