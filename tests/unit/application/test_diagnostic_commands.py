from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from awesome_agent.application.command_results import (
    DoctorCommandPayload,
    ToolCatalogCommandPayload,
    UnavailableToolCommandItem,
)
from awesome_agent.application.commands import CommandIntent, CommandName
from awesome_agent.application.diagnostic_commands import DiagnosticCommandService
from awesome_agent.config import missing_provider_credential_statuses
from awesome_agent.core.tools import (
    ToolArguments,
    ToolExecutionContext,
    ToolOutput,
    ToolSpec,
)
from awesome_agent.core.tools.permissions import PermissionSession
from awesome_agent.core.tools.registry import ToolRegistry


class _NoArguments(ToolArguments):
    pass


async def _unused_handler(
    arguments: BaseModel,
    context: ToolExecutionContext,
) -> ToolOutput:
    del arguments, context
    return ToolOutput(content="unused")


@pytest.mark.asyncio
async def test_doctor_reports_runtime_readiness_without_assuming_success(
    tmp_path: Path,
) -> None:
    async def provider_doctor() -> dict[str, str]:
        return {"deepseek": "missing", "kimi": "missing"}

    async def unavailable() -> bool | None:
        raise RuntimeError("private readiness failure")

    async def absent() -> None:
        return None

    async def ready() -> bool:
        return True

    service = DiagnosticCommandService(
        workspace_path=tmp_path,
        registry=ToolRegistry(),
        permission_session=PermissionSession(),
        current_thread_id=lambda: None,
        unavailable_tools=(),
        status_reader=absent,
        usage_reader=absent,
        credential_statuses=missing_provider_credential_statuses,
        provider_doctor=provider_doctor,
        configuration_ready=ready,
        sqlite_ready=absent,
        checkpoints_ready=unavailable,
        workspace_instruction_diagnostic=lambda: None,
    )

    outcome = await service.doctor(CommandIntent(name=CommandName.DOCTOR))

    assert outcome.kind == "result"
    assert isinstance(outcome.payload, DoctorCommandPayload)
    checks = {check.name: check for check in outcome.payload.checks}
    assert checks["Configuration"].status == "ok"
    assert checks["SQLite"].status == "unverified"
    assert checks["Checkpoints"].status == "unverified"
    assert "private readiness failure" not in outcome.model_dump_json()


@pytest.mark.asyncio
async def test_tools_reports_the_active_thread_network_grant(tmp_path: Path) -> None:
    async def provider_doctor() -> dict[str, str]:
        return {}

    async def absent() -> None:
        return None

    async def ready() -> bool:
        return True

    registry = ToolRegistry()
    registry.register(
        spec=ToolSpec(
            name="network_probe",
            description="Read from the network",
            input_schema=_NoArguments.model_json_schema(),
            capability="network.read",
            read_only=True,
        ),
        input_model=_NoArguments,
        handler=_unused_handler,
    )
    permissions = PermissionSession()
    selected = {"thread_id": "thread_current"}
    unavailable = UnavailableToolCommandItem(
        name="web_search",
        description="Search the public web",
        read_only=True,
        reason_code="web_disabled",
        reason="Web access is turned off.",
        hint="Run /web on.",
    )
    service = DiagnosticCommandService(
        workspace_path=tmp_path,
        registry=registry,
        permission_session=permissions,
        current_thread_id=lambda: selected["thread_id"],
        unavailable_tools=(unavailable,),
        status_reader=absent,
        usage_reader=absent,
        credential_statuses=missing_provider_credential_statuses,
        provider_doctor=provider_doctor,
        configuration_ready=ready,
        sqlite_ready=ready,
        checkpoints_ready=ready,
        workspace_instruction_diagnostic=lambda: None,
    )

    async def approval_required() -> bool:
        outcome = await service.tools(CommandIntent(name=CommandName.TOOLS))
        assert outcome.kind == "result"
        assert isinstance(outcome.payload, ToolCatalogCommandPayload)
        assert len(outcome.payload.tools) == 1
        assert outcome.payload.unavailable_tools == (unavailable,)
        return outcome.payload.tools[0].approval_required

    assert await approval_required() is True

    permissions.grant_thread_network("thread_other")
    assert await approval_required() is True

    selected["thread_id"] = "thread_other"
    assert await approval_required() is False

    selected["thread_id"] = "thread_current"
    permissions.grant_thread_network("thread_current")
    assert await approval_required() is False

    permissions.revoke_thread_network("thread_current")
    assert await approval_required() is True
