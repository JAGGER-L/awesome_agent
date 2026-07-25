from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal, cast

from awesome_agent.application.command_results import (
    CommandOutcome,
    ConfigCommandPayload,
    DoctorCheck,
    DoctorCommandPayload,
    StatusCommandPayload,
    ToolCatalogCommandPayload,
    ToolCommandItem,
    UsageCommandPayload,
    WorkspaceCommandPayload,
    error,
    result,
)
from awesome_agent.application.commands import CommandIntent
from awesome_agent.application.contracts import StatusSnapshot
from awesome_agent.config.credentials import ProviderCredentialStatuses
from awesome_agent.context import WorkspaceInstructionDiagnostic
from awesome_agent.conversation.models import UsageSummary
from awesome_agent.core.tools.permissions import (
    PermissionPolicy,
    PermissionSession,
    PolicyAction,
    PolicyRequest,
)
from awesome_agent.core.tools.registry import ToolRegistry

type StatusReader = Callable[[], StatusSnapshot | None]
type UsageReader = Callable[[], UsageSummary | None]
type ProviderDoctor = Callable[[], Awaitable[dict[str, str]]]
type WorkspaceInstructionDiagnosticReader = Callable[
    [], WorkspaceInstructionDiagnostic | None
]


class DiagnosticCommandService:
    """Own read-only workspace and application diagnostic commands."""

    def __init__(
        self,
        *,
        workspace_path: Path,
        registry: ToolRegistry,
        permission_session: PermissionSession,
        status_reader: StatusReader,
        usage_reader: UsageReader,
        credential_statuses: Callable[[], ProviderCredentialStatuses],
        provider_doctor: ProviderDoctor,
        workspace_instruction_diagnostic: WorkspaceInstructionDiagnosticReader,
    ) -> None:
        self._workspace_path = workspace_path
        self._registry = registry
        self._permission_session = permission_session
        self._status_reader = status_reader
        self._usage_reader = usage_reader
        self._credential_statuses = credential_statuses
        self._provider_doctor = provider_doctor
        self._workspace_instruction_diagnostic = workspace_instruction_diagnostic
        self._policy = PermissionPolicy()

    async def workspace(self, intent: CommandIntent) -> CommandOutcome:
        if intent.arguments:
            return error("invalid_arguments", "Usage: /workspace")
        return result(WorkspaceCommandPayload(path=str(self._workspace_path)))

    async def tools(self, intent: CommandIntent) -> CommandOutcome:
        if intent.arguments:
            return error("invalid_arguments", "Usage: /tools")
        mode = self._permission_session.mode
        granted = frozenset(self._permission_session.granted_capabilities)
        return result(
            ToolCatalogCommandPayload(
                permission_mode=mode,
                tools=tuple(
                    ToolCommandItem(
                        name=spec.name,
                        description=spec.description,
                        read_only=spec.read_only,
                        approval_required=(
                            self._policy.evaluate(
                                PolicyRequest(
                                    capability=spec.capability,
                                    mode=mode,
                                    granted_capabilities=granted,
                                )
                            ).action
                            is PolicyAction.ASK
                        ),
                    )
                    for spec in self._registry.specifications()
                ),
            )
        )

    async def status(self, intent: CommandIntent) -> CommandOutcome:
        if intent.arguments:
            return error("invalid_arguments", "Usage: /status")
        snapshot = self._status_reader()
        if snapshot is None:
            return error("thread_not_found", "Select a Thread first.")
        return result(StatusCommandPayload(snapshot=snapshot))

    async def usage(self, intent: CommandIntent) -> CommandOutcome:
        if intent.arguments:
            return error("invalid_arguments", "Usage: /usage")
        usage = self._usage_reader()
        if usage is None:
            return error("thread_not_found", "Select a Thread first.")
        return result(UsageCommandPayload(usage=usage))

    async def doctor(self, intent: CommandIntent) -> CommandOutcome:
        if intent.arguments:
            return error("invalid_arguments", "Usage: /doctor")
        providers = await self._provider_doctor()
        checks = [
            DoctorCheck(name="Configuration", status="ok"),
            DoctorCheck(name="SQLite", status="ok"),
            DoctorCheck(name="Checkpoints", status="ok"),
        ]
        workspace_diagnostic = self._workspace_instruction_diagnostic()
        if workspace_diagnostic is not None:
            checks.append(
                DoctorCheck(
                    name="Workspace instructions",
                    status="error",
                    detail=workspace_diagnostic.message,
                )
            )
        for provider in ("deepseek", "kimi"):
            raw_status = providers.get(provider, "unverified")
            status = (
                raw_status
                if raw_status
                in {
                    "ok",
                    "missing",
                    "valid",
                    "invalid",
                    "unverified",
                    "off",
                    "error",
                }
                else "unverified"
            )
            checks.append(
                DoctorCheck(
                    name=provider.title(),
                    status=cast(
                        Literal[
                            "ok",
                            "missing",
                            "valid",
                            "invalid",
                            "unverified",
                            "off",
                            "error",
                        ],
                        status,
                    ),
                )
            )
        return result(DoctorCommandPayload(checks=tuple(checks)))

    async def config(self, intent: CommandIntent) -> CommandOutcome:
        if intent.arguments:
            return error("invalid_arguments", "Usage: /config")
        return result(
            ConfigCommandPayload(
                sources=("defaults", "user", "workspace", "environment"),
                credentials=self._credential_statuses(),
            )
        )
