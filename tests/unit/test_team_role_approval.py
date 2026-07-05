from __future__ import annotations

from uuid import uuid4

from awesome_agent.domain.enums import EventType
from awesome_agent.domain.models import RuntimeEvent
from awesome_agent.runtime.team_role_approval import (
    TEAM_ROLE_APPROVAL_CONTINUATION_PAYLOAD_KEY,
    TeamRoleApprovalContinuation,
    continuation_from_payload,
    latest_open_team_role_approval_continuation,
)


def test_team_role_approval_continuation_round_trips_payload() -> None:
    continuation = TeamRoleApprovalContinuation(
        approval_id=uuid4(),
        tool_invocation_id=uuid4(),
        tool_call_id="shell-1",
        tool_name="shell.execute",
        tool_version="1",
        arguments_json='{"command":"pytest -q"}',
        arguments_hash="hash",
        workspace_path="E:/repo/.worktrees/run",
        workspace_fingerprint="fingerprint",
        capabilities=("shell:execute",),
        message_payloads=({"role": "user", "content": "run tests"},),
        model_turn_count=2,
        tool_call_count=3,
        successful_inspections=1,
        successful_writes=0,
        diff_after_last_write=False,
    )

    parsed = continuation_from_payload(continuation.to_payload())

    assert parsed == continuation
    assert parsed is not None
    assert parsed.to_tool_call().name == "shell.execute"


def test_team_role_approval_continuation_rejects_wrong_version() -> None:
    continuation = TeamRoleApprovalContinuation(
        approval_id=uuid4(),
        tool_invocation_id=uuid4(),
        tool_call_id="shell-1",
        tool_name="shell.execute",
        tool_version="1",
        arguments_json="{}",
        arguments_hash="hash",
        workspace_path="E:/repo/.worktrees/run",
        workspace_fingerprint="fingerprint",
        capabilities=("shell:execute",),
        message_payloads=(),
        model_turn_count=0,
        tool_call_count=0,
        successful_inspections=0,
        successful_writes=0,
        diff_after_last_write=False,
    )
    payload = continuation.to_payload()
    payload["version"] = 999

    assert continuation_from_payload(payload) is None


def test_latest_open_team_role_approval_continuation_uses_wrapper_payload() -> None:
    run_id = uuid4()
    continuation = TeamRoleApprovalContinuation(
        approval_id=uuid4(),
        tool_invocation_id=uuid4(),
        tool_call_id="shell-1",
        tool_name="shell.execute",
        tool_version="1",
        arguments_json="{}",
        arguments_hash="hash",
        workspace_path="E:/repo/.worktrees/run",
        workspace_fingerprint="fingerprint",
        capabilities=("shell:execute",),
        message_payloads=(),
        model_turn_count=0,
        tool_call_count=0,
        successful_inspections=0,
        successful_writes=0,
        diff_after_last_write=False,
    )
    events = [
        RuntimeEvent(
            run_id=run_id,
            sequence=1,
            event_type=EventType.APPROVAL_REQUESTED,
            payload={
                TEAM_ROLE_APPROVAL_CONTINUATION_PAYLOAD_KEY: (
                    continuation.to_payload()
                )
            },
        )
    ]

    assert latest_open_team_role_approval_continuation(
        events,
        completed_invocation_ids=set(),
    ) == continuation
    assert latest_open_team_role_approval_continuation(
        events,
        completed_invocation_ids={continuation.tool_invocation_id},
    ) is None
