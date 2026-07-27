import time
from pathlib import Path
from types import MappingProxyType

import pytest
from pydantic import ValidationError

from awesome_agent.core.events import CollectingEventSink, EventEmitter
from awesome_agent.core.tools.context import (
    CapabilityQuotaLedger,
    ToolExecutionContext,
    ToolResourceGrant,
)
from awesome_agent.core.tools.contracts import (
    ToolActivityDraft,
    ToolErrorCode,
    ToolExecutionOrigin,
)
from awesome_agent.core.tools.errors import ExpectedToolFailure
from awesome_agent.core.workspace import resolve_workspace


class _ActivityWriter:
    async def finalize(self, activity: ToolActivityDraft) -> None:
        del activity


def test_capability_quota_consumption_exposes_stable_usage_snapshot() -> None:
    ledger = CapabilityQuotaLedger(
        {"network.read": 3},
        used_counts={"network.read": 1},
    )

    assert ledger.remaining("network.read") == 2
    assert ledger.consume("network.read") == 2
    snapshot = ledger.used_counts

    assert isinstance(snapshot, MappingProxyType)
    assert snapshot == {"network.read": 2}
    assert ledger.remaining("network.read") == 1

    ledger.consume("network.read")

    assert snapshot == {"network.read": 2}
    assert ledger.used_counts == {"network.read": 3}


def test_capability_quota_fails_closed_without_limit_and_never_overdraws() -> None:
    missing = CapabilityQuotaLedger()

    with pytest.raises(ExpectedToolFailure) as missing_error:
        missing.require_remaining("network.read")

    assert missing_error.value.code is ToolErrorCode.WEB_REQUEST_BUDGET_EXHAUSTED
    assert missing.used_counts == {}

    ledger = CapabilityQuotaLedger({"network.read": 1})
    ledger.consume("network.read")

    with pytest.raises(ExpectedToolFailure) as exhausted_error:
        ledger.consume("network.read")

    assert exhausted_error.value.code is ToolErrorCode.WEB_REQUEST_BUDGET_EXHAUSTED
    assert exhausted_error.value.metadata == {"capability": "network.read"}
    assert ledger.used_counts == {"network.read": 1}


@pytest.mark.parametrize(
    ("limits", "used_counts"),
    [
        ({"network.read": -1}, None),
        ({"network.read": 1}, {"network.read": 2}),
        ({}, {"network.read": 0}),
    ],
)
def test_capability_quota_rejects_invalid_initial_state(
    limits: dict[str, int],
    used_counts: dict[str, int] | None,
) -> None:
    with pytest.raises(ValueError):
        CapabilityQuotaLedger(limits, used_counts=used_counts)


def _context_with_grants(
    tmp_path: Path,
    *grants: ToolResourceGrant,
    skill_mode: str = "auto",
) -> ToolExecutionContext:
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir(exist_ok=True)
    workspace = resolve_workspace(workspace_path)
    return ToolExecutionContext(
        workspace=workspace,
        thread_id="thread_context",
        operation_id="operation_context",
        turn_id="turn_context",
        origin=ToolExecutionOrigin.AGENT,
        emitter=EventEmitter(
            session_id="session_context",
            workspace_key=workspace.key,
            sink=CollectingEventSink(),
        ),
        activity_writer=_ActivityWriter(),
        monotonic=time.monotonic,
        skill_mode=skill_mode,
        resource_grants=grants,
    )


def test_resource_grants_are_strict_frozen_and_operation_scoped(
    tmp_path: Path,
) -> None:
    grant = ToolResourceGrant(
        capability="context.read",
        resource_type="skill",
        resource_id="review",
        identity=f"skill-v1-sha256:{'a' * 64}",
        operations=("load", "read"),
    )
    context = _context_with_grants(tmp_path, grant)

    assert (
        context.resource_grant(
            capability="context.read",
            resource_type="skill",
            resource_id="review",
            operation="load",
        )
        is grant
    )
    assert (
        context.resource_grant(
            capability="context.read",
            resource_type="skill",
            resource_id="review",
            operation="write",
        )
        is None
    )
    with pytest.raises(ValidationError):
        grant.resource_id = "changed"
    with pytest.raises(ValidationError):
        ToolResourceGrant.model_validate(
            {
                "capability": "context.read",
                "resource_type": "skill",
                "resource_id": "review",
                "identity": f"skill-v1-sha256:{'a' * 64}",
                "operations": ["read"],
                "unexpected": True,
            }
        )


def test_execution_context_rejects_invalid_mode_and_duplicate_resource_scope(
    tmp_path: Path,
) -> None:
    grant = ToolResourceGrant(
        capability="context.read",
        resource_type="skill",
        resource_id="review",
        identity=f"skill-v1-sha256:{'a' * 64}",
        operations=("read",),
    )

    with pytest.raises(ValueError, match="skill_mode"):
        _context_with_grants(tmp_path, skill_mode="../review")
    with pytest.raises(ValueError, match="unique resource scopes"):
        _context_with_grants(tmp_path, grant, grant)

    context = _context_with_grants(tmp_path)
    with pytest.raises(ValueError, match="immutable tuple"):
        ToolExecutionContext(
            workspace=context.workspace,
            thread_id=context.thread_id,
            operation_id=context.operation_id,
            turn_id=context.turn_id,
            origin=context.origin,
            emitter=context.emitter,
            activity_writer=context.activity_writer,
            monotonic=context.monotonic,
            skill_mode="auto",
            resource_grants=[grant],  # type: ignore[arg-type]
        )
