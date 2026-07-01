from __future__ import annotations

from dataclasses import dataclass
from typing import Any

TERMINAL_STATUSES = {"completed", "failed", "cancelled", "recovery_required"}
ATTENTION_STATUSES = {"waiting", "paused", "recovery_required", "failed"}


@dataclass(frozen=True, slots=True)
class RunRow:
    id: str
    goal: str
    status: str
    mode: str
    runtime_route: str
    dispatch_status: str
    updated_at: str | None

    @property
    def attention(self) -> bool:
        return self.status in ATTENTION_STATUSES

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


@dataclass(frozen=True, slots=True)
class ApprovalRow:
    id: str
    approved: bool | None
    event_type: str
    sequence: int

    @property
    def pending(self) -> bool:
        return self.event_type == "approval.requested" and self.approved is None


def run_row(payload: dict[str, Any]) -> RunRow:
    return RunRow(
        id=str(payload["id"]),
        goal=str(payload.get("goal", "")),
        status=str(payload.get("status", "unknown")),
        mode=str(payload.get("mode", "unknown")),
        runtime_route=str(payload.get("runtime_route", "unknown")),
        dispatch_status=str(payload.get("dispatch_status", "unknown")),
        updated_at=(
            str(payload["updated_at"])
            if payload.get("updated_at") is not None
            else None
        ),
    )


def approval_row(event: dict[str, Any]) -> ApprovalRow:
    payload = event.get("payload", {})
    if not isinstance(payload, dict):
        payload = {}
    approved = payload.get("approved") if "approved" in payload else None
    return ApprovalRow(
        id=str(payload.get("approval_id", "")),
        approved=approved if isinstance(approved, bool) else None,
        event_type=str(event.get("event_type", "")),
        sequence=int(event.get("sequence", 0)),
    )
