from __future__ import annotations

from uuid import UUID

from awesome_agent.conversation.events import (
    ConversationStreamEvent,
    ConversationStreamEventKind,
)
from awesome_agent.domain.enums import EventType
from awesome_agent.domain.models import RuntimeEvent

_PRIVATE_PAYLOAD_KEYS = {"prompt", "message", "secret", "api_key"}
_USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
)
_TEAM_EVENTS = {
    EventType.TEAM_CHILD_RUN_CREATED,
    EventType.TEAM_CHILD_RUN_COMPLETED,
    EventType.TEAM_ASSIGNMENT_CREATED,
    EventType.TEAM_PLAN_CREATED,
    EventType.TEAM_PLAN_REJECTED,
    EventType.TEAM_PLAN_REPAIR_CREATED,
    EventType.TEAM_PLAN_REPAIR_REJECTED,
    EventType.TEAM_PLAN_REPAIR_APPLIED,
    EventType.TEAM_PLAN_REPAIR_EXHAUSTED,
    EventType.TEAM_SUBAGENT_REQUESTED,
    EventType.TEAM_REWORK_REQUESTED,
    EventType.TEAM_REWORK_EXHAUSTED,
    EventType.TEAM_ASSIGNMENT_RETIRED,
    EventType.TEAM_MAILBOX_MESSAGE_CREATED,
    EventType.TEAM_MAILBOX_MESSAGE_READ,
    EventType.TEAM_MAILBOX_MESSAGE_RESPONDED,
    EventType.TEAM_PATCH_AGGREGATED,
}


def project_runtime_event(
    *,
    thread_id: UUID,
    turn_id: UUID,
    event: RuntimeEvent,
) -> list[ConversationStreamEvent]:
    if event.event_type is EventType.RUN_CREATED:
        return [
            _conversation_event(
                ConversationStreamEventKind.TURN_STARTED,
                thread_id=thread_id,
                turn_id=turn_id,
                event=event,
                payload={
                    **event.payload,
                    "run_id": str(event.run_id),
                },
            )
        ]
    if event.event_type is EventType.MODEL_CALL_CREATED:
        route_attempt = event.payload.get("route_attempt")
        if isinstance(route_attempt, dict):
            return [
                _conversation_event(
                    ConversationStreamEventKind.MODEL_ATTEMPT,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    event=event,
                    payload=_public_payload(route_attempt),
                )
            ]
        if event.payload.get("reasoning_started") is True:
            return [
                _conversation_event(
                    ConversationStreamEventKind.REASONING_STARTED,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    event=event,
                    payload={},
                )
            ]
        reasoning_delta = event.payload.get("reasoning_delta")
        if isinstance(reasoning_delta, str) and reasoning_delta:
            return [
                _conversation_event(
                    ConversationStreamEventKind.REASONING_DELTA,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    event=event,
                    payload={"text": reasoning_delta},
                )
            ]
        if "reasoning_completed" in event.payload:
            return [
                _conversation_event(
                    ConversationStreamEventKind.REASONING_COMPLETED,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    event=event,
                    payload={
                        "failed": bool(event.payload.get("reasoning_failed", False))
                    },
                )
            ]
        usage = {key: event.payload[key] for key in _USAGE_KEYS if key in event.payload}
        if usage:
            return [
                _conversation_event(
                    ConversationStreamEventKind.USAGE_UPDATED,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    event=event,
                    payload=usage,
                )
            ]
        text = event.payload.get("text_delta")
        if isinstance(text, str):
            return [
                _conversation_event(
                    ConversationStreamEventKind.MESSAGE_DELTA,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    event=event,
                    payload={"text": text},
                )
            ]
    if event.event_type is EventType.TOOL_CALL_CREATED:
        status = str(event.payload.get("status") or "")
        if status == "started":
            kind = ConversationStreamEventKind.TOOL_STARTED
        elif status in {"completed", "failed"}:
            kind = ConversationStreamEventKind.TOOL_COMPLETED
        else:
            kind = ConversationStreamEventKind.TOOL_PROGRESS
        return [
            _conversation_event(
                kind,
                thread_id=thread_id,
                turn_id=turn_id,
                event=event,
                payload=_public_payload(event.payload),
            )
        ]
    if event.event_type is EventType.TOOL_PROGRESS:
        return [
            _conversation_event(
                ConversationStreamEventKind.TOOL_PROGRESS,
                thread_id=thread_id,
                turn_id=turn_id,
                event=event,
                payload=_public_payload(event.payload),
            )
        ]
    if event.event_type is EventType.APPROVAL_REQUESTED:
        tool = str(event.payload.get("tool") or "tool")
        args_summary = str(event.payload.get("args_summary") or "")
        approval_type = "command" if tool == "shell.execute" else "edit"
        payload: dict[str, object] = {
            "code": "approval_required",
            "message": f"Approval required for {tool}.",
            "approval_required": True,
            "run_id": str(event.run_id),
            "approval_id": str(event.payload.get("approval_id") or ""),
            "approval_type": approval_type,
            "tool": tool,
            "risk": str(event.payload.get("risk") or ""),
            "expires_at": str(event.payload.get("expires_at") or ""),
        }
        if approval_type == "command":
            payload["command"] = args_summary
        else:
            payload["path"] = args_summary
        return [
            _conversation_event(
                ConversationStreamEventKind.APPROVAL_REQUIRED,
                thread_id=thread_id,
                turn_id=turn_id,
                event=event,
                payload=payload,
            )
        ]
    if event.event_type in _TEAM_EVENTS:
        return [
            _conversation_event(
                ConversationStreamEventKind.TEAM_EVENT,
                thread_id=thread_id,
                turn_id=turn_id,
                event=event,
                payload=_public_payload(event.payload),
            )
        ]
    if event.event_type is EventType.VERIFICATION_CREATED:
        return [
            _conversation_event(
                ConversationStreamEventKind.VALIDATION_EVENT,
                thread_id=thread_id,
                turn_id=turn_id,
                event=event,
                payload=_public_payload(event.payload),
            )
        ]
    if event.event_type is EventType.RUN_STATUS_CHANGED:
        status = str(event.payload.get("status") or "")
        if status == "completed":
            return [
                _conversation_event(
                    ConversationStreamEventKind.TURN_COMPLETED,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    event=event,
                    payload={"status": status},
                )
            ]
        if status == "cancelled":
            return [
                _conversation_event(
                    ConversationStreamEventKind.TURN_COMPLETED,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    event=event,
                    payload={"status": status},
                )
            ]
        if status in {"failed", "recovery_required"}:
            return [
                _conversation_event(
                    ConversationStreamEventKind.ERROR,
                    thread_id=thread_id,
                    turn_id=turn_id,
                    event=event,
                    payload={
                        "code": "runtime_error",
                        "message": str(event.payload.get("error") or status),
                        "retryable": status == "recovery_required",
                        "provider": "runtime",
                    },
                )
            ]
    return []


def _conversation_event(
    kind: ConversationStreamEventKind,
    *,
    thread_id: UUID,
    turn_id: UUID,
    event: RuntimeEvent,
    payload: dict[str, object],
) -> ConversationStreamEvent:
    return ConversationStreamEvent(
        event=kind,
        thread_id=thread_id,
        turn_id=turn_id,
        sequence=event.sequence,
        created_at=event.created_at,
        trace_id=event.trace_id or event.run_id.hex,
        run_id=event.run_id,
        runtime_sequence=event.sequence,
        payload=payload,
    )


def _public_payload(payload: dict[str, object]) -> dict[str, object]:
    return {
        key: value for key, value in payload.items() if key not in _PRIVATE_PAYLOAD_KEYS
    }
