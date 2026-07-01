from __future__ import annotations

from collections import Counter
from typing import Protocol

from pydantic import BaseModel, Field

from awesome_agent.domain.enums import RunStatus
from awesome_agent.domain.models import Run
from awesome_agent.extensions.models import ExtensionCatalog
from awesome_agent.persistence.tool_invocations import ToolInvocationRepository
from awesome_agent.runtime.repository import RuntimeRepository

_TERMINAL_RUN_STATUSES = {
    RunStatus.COMPLETED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
    RunStatus.RECOVERY_REQUIRED,
}
_EXTENSION_TOOL_PREFIXES = ("mcp.", "community.", "extension.")


class ExtensionToolDenial(BaseModel):
    tool: str
    reason: str
    source_id: str | None = None


class ExtensionInvocationEvidence(BaseModel):
    run_id: str
    tool: str
    source_id: str | None = None
    reason: str | None = None
    error: str | None = None


class ExtensionWarning(BaseModel):
    kind: str
    message: str
    run_id: str | None = None
    pinned_catalog_version: str | None = None
    active_catalog_version: str | None = None


class ExtensionCatalogStats(BaseModel):
    sources: int
    tools: int
    skills: int


class ExtensionDiagnosticsSummary(BaseModel):
    active_catalog_version: str
    catalog: ExtensionCatalogStats
    source_health: list[dict[str, str | None]] = Field(default_factory=list)
    unhealthy_sources: list[dict[str, str | None]] = Field(default_factory=list)
    denials: list[ExtensionToolDenial] = Field(default_factory=list)
    invocation_denials: list[ExtensionInvocationEvidence] = Field(default_factory=list)
    execution_errors: list[ExtensionInvocationEvidence] = Field(default_factory=list)
    metrics: dict[str, int] = Field(default_factory=dict)
    warnings: list[ExtensionWarning] = Field(default_factory=list)


class ExtensionCatalogDiff(BaseModel):
    from_version: str
    to_version: str
    added_tools: list[str] = Field(default_factory=list)
    removed_tools: list[str] = Field(default_factory=list)
    changed_tools: list[str] = Field(default_factory=list)
    added_sources: list[str] = Field(default_factory=list)
    removed_sources: list[str] = Field(default_factory=list)
    changed_sources: list[str] = Field(default_factory=list)


class ToolInvocationLister(Protocol):
    async def list_for_run(self, run_id: object) -> list[object]:
        """Load durable tool invocations for one run."""


class ExtensionDiagnosticsService:
    def __init__(
        self,
        *,
        active_catalog: ExtensionCatalog,
        runtime_repository: RuntimeRepository,
        tool_invocation_repository: ToolInvocationRepository | None = None,
    ) -> None:
        self._active_catalog = active_catalog
        self._runtime = runtime_repository
        self._tools = tool_invocation_repository

    async def summarize(self) -> ExtensionDiagnosticsSummary:
        runs = await self._runtime.list_runs()
        invocation_denials: list[ExtensionInvocationEvidence] = []
        execution_errors: list[ExtensionInvocationEvidence] = []
        if self._tools is not None:
            for run in runs:
                for invocation in await self._tools.list_for_run(run.id):
                    if not _is_extension_tool(invocation.tool_name):
                        continue
                    evidence = ExtensionInvocationEvidence(
                        run_id=str(run.id),
                        tool=invocation.tool_name,
                        source_id=_source_for_tool(
                            invocation.tool_name,
                            self._active_catalog,
                        ),
                        reason=invocation.error,
                        error=invocation.error,
                    )
                    if invocation.status == "denied":
                        invocation_denials.append(evidence)
                    elif invocation.status == "failed":
                        execution_errors.append(evidence)
        warnings = [
            _stale_catalog_warning(run, self._active_catalog.version)
            for run in runs
            if _has_stale_catalog(run, self._active_catalog.version)
        ]
        metrics = _metrics(
            self._active_catalog,
            invocation_denials=invocation_denials,
            execution_errors=execution_errors,
            warnings=warnings,
        )
        return ExtensionDiagnosticsSummary(
            active_catalog_version=self._active_catalog.version,
            catalog=ExtensionCatalogStats(
                sources=len(self._active_catalog.sources),
                tools=len(self._active_catalog.tools),
                skills=len(self._active_catalog.skills),
            ),
            source_health=[
                {
                    "source_id": source.id,
                    "status": source.health.status.value,
                    "detail": source.health.detail,
                    "catalog_version": self._active_catalog.version,
                }
                for source in self._active_catalog.sources
            ],
            unhealthy_sources=[
                {
                    "source_id": source.id,
                    "status": source.health.status.value,
                    "detail": source.health.detail,
                }
                for source in self._active_catalog.sources
                if source.health.status.value != "healthy"
            ],
            denials=[
                ExtensionToolDenial(
                    tool=tool.name,
                    reason="not_assigned",
                    source_id=tool.source_id,
                )
                for tool in self._active_catalog.tools
            ],
            invocation_denials=invocation_denials,
            execution_errors=execution_errors,
            metrics=metrics,
            warnings=warnings,
        )


def diff_extension_catalogs(
    from_catalog: ExtensionCatalog,
    to_catalog: ExtensionCatalog,
) -> ExtensionCatalogDiff:
    from_tools = {tool.name: tool for tool in from_catalog.tools}
    to_tools = {tool.name: tool for tool in to_catalog.tools}
    from_sources = {source.id: source for source in from_catalog.sources}
    to_sources = {source.id: source for source in to_catalog.sources}
    return ExtensionCatalogDiff(
        from_version=from_catalog.version,
        to_version=to_catalog.version,
        added_tools=sorted(set(to_tools) - set(from_tools)),
        removed_tools=sorted(set(from_tools) - set(to_tools)),
        changed_tools=sorted(
            name
            for name in set(from_tools) & set(to_tools)
            if from_tools[name] != to_tools[name]
        ),
        added_sources=sorted(set(to_sources) - set(from_sources)),
        removed_sources=sorted(set(from_sources) - set(to_sources)),
        changed_sources=sorted(
            source_id
            for source_id in set(from_sources) & set(to_sources)
            if from_sources[source_id] != to_sources[source_id]
        ),
    )


def _has_stale_catalog(run: Run, active_catalog_version: str) -> bool:
    return (
        run.status not in _TERMINAL_RUN_STATUSES
        and run.extension_catalog_version is not None
        and run.extension_catalog_version != active_catalog_version
    )


def _stale_catalog_warning(run: Run, active_catalog_version: str) -> ExtensionWarning:
    return ExtensionWarning(
        kind="stale_extension_catalog",
        message="Run uses an older pinned extension catalog than the active catalog.",
        run_id=str(run.id),
        pinned_catalog_version=run.extension_catalog_version,
        active_catalog_version=active_catalog_version,
    )


def _source_for_tool(tool_name: str, catalog: ExtensionCatalog) -> str | None:
    for tool in catalog.tools:
        if tool.name == tool_name:
            return tool.source_id
    return None


def _is_extension_tool(tool_name: str) -> bool:
    return tool_name.startswith(_EXTENSION_TOOL_PREFIXES)


def _metrics(
    catalog: ExtensionCatalog,
    *,
    invocation_denials: list[ExtensionInvocationEvidence],
    execution_errors: list[ExtensionInvocationEvidence],
    warnings: list[ExtensionWarning],
) -> dict[str, int]:
    counts = Counter[str]()
    counts["sources"] = len(catalog.sources)
    counts["tools"] = len(catalog.tools)
    counts["unhealthy_sources"] = sum(
        1 for source in catalog.sources if source.health.status.value != "healthy"
    )
    counts["exposure_denials"] = len(catalog.tools)
    counts["invocation_denials"] = len(invocation_denials)
    counts["execution_errors"] = len(execution_errors)
    counts["stale_catalog_warnings"] = len(warnings)
    return dict(counts)
