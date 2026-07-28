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
    UnavailableToolCommandItem,
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

type StatusReader = Callable[[], Awaitable[StatusSnapshot | None]]
type UsageReader = Callable[[], Awaitable[UsageSummary | None]]
type ProviderDoctor = Callable[[], Awaitable[dict[str, str]]]
type ReadinessReader = Callable[[], Awaitable[bool | None]]
type CurrentThreadIdReader = Callable[[], str | None]
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
        current_thread_id: CurrentThreadIdReader,
        unavailable_tools: tuple[UnavailableToolCommandItem, ...],
        status_reader: StatusReader,
        usage_reader: UsageReader,
        credential_statuses: Callable[[], ProviderCredentialStatuses],
        provider_doctor: ProviderDoctor,
        configuration_ready: ReadinessReader,
        sqlite_ready: ReadinessReader,
        checkpoints_ready: ReadinessReader,
        workspace_instruction_diagnostic: WorkspaceInstructionDiagnosticReader,
    ) -> None:
        self._workspace_path = workspace_path
        self._registry = registry
        self._permission_session = permission_session
        self._current_thread_id = current_thread_id
        self._unavailable_tools = unavailable_tools
        self._status_reader = status_reader
        self._usage_reader = usage_reader
        self._credential_statuses = credential_statuses
        self._provider_doctor = provider_doctor
        self._configuration_ready = configuration_ready
        self._sqlite_ready = sqlite_ready
        self._checkpoints_ready = checkpoints_ready
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
        thread_id = self._current_thread_id()
        granted_thread_capabilities = (
            self._permission_session.thread_granted_capabilities
        )
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
                                    thread_id=thread_id,
                                    granted_thread_capabilities=(
                                        granted_thread_capabilities
                                    ),
                                )
                            ).action
                            is PolicyAction.ASK
                        ),
                    )
                    for spec in self._registry.specifications()
                ),
                unavailable_tools=self._unavailable_tools,
            )
        )

    async def status(self, intent: CommandIntent) -> CommandOutcome:
        if intent.arguments:
            return error("invalid_arguments", "Usage: /status")
        snapshot = await self._status_reader()
        if snapshot is None:
            return error("thread_not_found", "Select a Thread first.")
        return result(StatusCommandPayload(snapshot=snapshot))

    async def usage(self, intent: CommandIntent) -> CommandOutcome:
        if intent.arguments:
            return error("invalid_arguments", "Usage: /usage")
        usage = await self._usage_reader()
        if usage is None:
            return error("thread_not_found", "Select a Thread first.")
        return result(UsageCommandPayload(usage=usage))

    async def doctor(self, intent: CommandIntent) -> CommandOutcome:
        if intent.arguments:
            return error("invalid_arguments", "Usage: /doctor")
        providers = await self._provider_doctor()
        checks = [
            await self._readiness_check("Configuration", self._configuration_ready),
            await self._readiness_check("SQLite", self._sqlite_ready),
            await self._readiness_check("Checkpoints", self._checkpoints_ready),
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

    @staticmethod
    async def _readiness_check(name: str, reader: ReadinessReader) -> DoctorCheck:
        try:
            ready = await reader()
        except Exception:
            ready = None
        if ready is True:
            return DoctorCheck(name=name, status="ok")
        if ready is False:
            return DoctorCheck(
                name=name,
                status="error",
                detail=f"{name} is not ready.",
            )
        return DoctorCheck(
            name=name,
            status="unverified",
            detail=f"{name} readiness could not be verified.",
        )

    async def config(self, intent: CommandIntent) -> CommandOutcome:
        if intent.arguments:
            return error("invalid_arguments", "Usage: /config")
        return result(
            ConfigCommandPayload(
                sources=("defaults", "user", "workspace", "environment"),
                credentials=self._credential_statuses(),
            )
        )
