from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from awesome_agent.conversation.models import ThreadMessage
from awesome_agent.domain.enums import EventType
from awesome_agent.domain.models import RuntimeEvent
from awesome_agent.modeling.messages import AssistantMessage
from awesome_agent.modeling.tools import ToolCall

CONTINUATION_PAYLOAD_VERSION = 1


@dataclass(frozen=True, slots=True)
class ApprovalContinuation:
    approval_id: UUID
    tool_call_id: str
    tool_name: str
    tool_version: str
    arguments_json: str
    arguments_hash: str
    workspace_path: str
    workspace_fingerprint: str
    capabilities: tuple[str, ...]

    def to_payload(self) -> dict[str, object]:
        return {
            "version": CONTINUATION_PAYLOAD_VERSION,
            "approval_id": str(self.approval_id),
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
            "arguments_json": self.arguments_json,
            "arguments_hash": self.arguments_hash,
            "workspace_path": self.workspace_path,
            "workspace_fingerprint": self.workspace_fingerprint,
            "capabilities": list(self.capabilities),
        }

    def to_tool_call(self) -> ToolCall:
        return ToolCall(
            call_id=self.tool_call_id,
            name=self.tool_name,
            arguments_json=self.arguments_json,
        )

    def to_assistant_message(self) -> AssistantMessage:
        return AssistantMessage(tool_calls=[self.to_tool_call()])


def continuation_from_payload(
    payload: dict[str, object],
) -> ApprovalContinuation | None:
    raw = payload.get("approval_continuation")
    if not isinstance(raw, dict):
        return None
    if raw.get("version") != CONTINUATION_PAYLOAD_VERSION:
        return None
    capabilities = raw.get("capabilities")
    if not isinstance(capabilities, list) or not all(
        isinstance(item, str) for item in capabilities
    ):
        return None
    return ApprovalContinuation(
        approval_id=UUID(str(raw["approval_id"])),
        tool_call_id=str(raw["tool_call_id"]),
        tool_name=str(raw["tool_name"]),
        tool_version=str(raw["tool_version"]),
        arguments_json=str(raw["arguments_json"]),
        arguments_hash=str(raw["arguments_hash"]),
        workspace_path=str(raw["workspace_path"]),
        workspace_fingerprint=str(raw["workspace_fingerprint"]),
        capabilities=tuple(capabilities),
    )


def latest_open_approval_continuation(
    events: list[RuntimeEvent],
    messages: list[ThreadMessage],
) -> ApprovalContinuation | None:
    completed_call_ids = {
        message.metadata.get("tool_call_id")
        for message in messages
        if message.metadata.get("kind") == "tool_result"
    }
    for event in reversed(events):
        if event.event_type is not EventType.APPROVAL_REQUESTED:
            continue
        continuation = continuation_from_payload(event.payload)
        if continuation is None:
            continue
        if continuation.tool_call_id not in completed_call_ids:
            return continuation
    return None
