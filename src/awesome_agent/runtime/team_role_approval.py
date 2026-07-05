from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from awesome_agent.modeling import ToolCall

TEAM_ROLE_APPROVAL_CONTINUATION_VERSION = 1


@dataclass(frozen=True, slots=True)
class TeamRoleApprovalContinuation:
    approval_id: UUID
    tool_invocation_id: UUID
    tool_call_id: str
    tool_name: str
    tool_version: str
    arguments_json: str
    arguments_hash: str
    workspace_path: str
    workspace_fingerprint: str
    capabilities: tuple[str, ...]
    message_payloads: tuple[dict[str, Any], ...]
    model_turn_count: int
    tool_call_count: int
    successful_inspections: int
    successful_writes: int
    diff_after_last_write: bool

    def to_payload(self) -> dict[str, object]:
        return {
            "version": TEAM_ROLE_APPROVAL_CONTINUATION_VERSION,
            "approval_id": str(self.approval_id),
            "tool_invocation_id": str(self.tool_invocation_id),
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "tool_version": self.tool_version,
            "arguments_json": self.arguments_json,
            "arguments_hash": self.arguments_hash,
            "workspace_path": self.workspace_path,
            "workspace_fingerprint": self.workspace_fingerprint,
            "capabilities": list(self.capabilities),
            "message_payloads": list(self.message_payloads),
            "model_turn_count": self.model_turn_count,
            "tool_call_count": self.tool_call_count,
            "successful_inspections": self.successful_inspections,
            "successful_writes": self.successful_writes,
            "diff_after_last_write": self.diff_after_last_write,
        }

    def to_tool_call(self) -> ToolCall:
        return ToolCall(
            call_id=self.tool_call_id,
            name=self.tool_name,
            arguments_json=self.arguments_json,
        )


def continuation_from_payload(
    payload: dict[str, object],
) -> TeamRoleApprovalContinuation | None:
    if payload.get("version") != TEAM_ROLE_APPROVAL_CONTINUATION_VERSION:
        return None
    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, list) or not all(
        isinstance(item, str) for item in capabilities
    ):
        return None
    message_payloads = payload.get("message_payloads")
    if not isinstance(message_payloads, list) or not all(
        isinstance(item, dict) for item in message_payloads
    ):
        return None
    return TeamRoleApprovalContinuation(
        approval_id=UUID(str(payload["approval_id"])),
        tool_invocation_id=UUID(str(payload["tool_invocation_id"])),
        tool_call_id=str(payload["tool_call_id"]),
        tool_name=str(payload["tool_name"]),
        tool_version=str(payload["tool_version"]),
        arguments_json=str(payload["arguments_json"]),
        arguments_hash=str(payload["arguments_hash"]),
        workspace_path=str(payload["workspace_path"]),
        workspace_fingerprint=str(payload["workspace_fingerprint"]),
        capabilities=tuple(capabilities),
        message_payloads=tuple(
            dict(item) for item in message_payloads if isinstance(item, dict)
        ),
        model_turn_count=int(str(payload["model_turn_count"])),
        tool_call_count=int(str(payload["tool_call_count"])),
        successful_inspections=int(str(payload["successful_inspections"])),
        successful_writes=int(str(payload["successful_writes"])),
        diff_after_last_write=bool(payload["diff_after_last_write"]),
    )
