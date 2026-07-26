from __future__ import annotations

from pathlib import Path

import pytest

from awesome_agent.application.command_results import DoctorCommandPayload
from awesome_agent.application.commands import CommandIntent, CommandName
from awesome_agent.application.diagnostic_commands import DiagnosticCommandService
from awesome_agent.config import missing_provider_credential_statuses
from awesome_agent.core.tools.permissions import PermissionSession
from awesome_agent.core.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_doctor_reports_runtime_readiness_without_assuming_success(
    tmp_path: Path,
) -> None:
    async def provider_doctor() -> dict[str, str]:
        return {"deepseek": "missing", "kimi": "missing"}

    def unavailable() -> bool | None:
        raise RuntimeError("private readiness failure")

    service = DiagnosticCommandService(
        workspace_path=tmp_path,
        registry=ToolRegistry(),
        permission_session=PermissionSession(),
        status_reader=lambda: None,
        usage_reader=lambda: None,
        credential_statuses=missing_provider_credential_statuses,
        provider_doctor=provider_doctor,
        configuration_ready=lambda: True,
        sqlite_ready=lambda: None,
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
