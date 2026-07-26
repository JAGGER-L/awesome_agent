from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from awesome_agent.core.safe_files import FileFingerprint, FileIdentity


class SkillSource(StrEnum):
    BUNDLED = "bundled"
    USER = "user"
    WORKSPACE = "workspace"


class SkillDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    description: str = Field(min_length=1, max_length=500)
    source: SkillSource
    root: Path
    license: str | None = Field(default=None, max_length=500)
    compatibility: str | None = Field(default=None, max_length=500)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    allowed_tools: tuple[str, ...] = ()


class SkillDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str = Field(min_length=1, max_length=128)
    source: SkillSource
    path: str = Field(max_length=2_000)
    name: str | None = Field(default=None, max_length=64)
    message: str = Field(min_length=1, max_length=1_000)


class SkillNotFound(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class _WorkspaceSkillBoundary:
    workspace_anchor: Path
    workspace_anchor_identity: FileIdentity
    skills_root: Path
    skills_root_identity: FileIdentity
    package_root: Path
    package_root_identity: FileIdentity
    package_component_identities: tuple[FileIdentity, ...]
    skill_file_fingerprint: FileFingerprint
    skill_file_content_hash: str


class SkillCatalog:
    def __init__(
        self,
        descriptors: tuple[SkillDescriptor, ...],
        diagnostics: tuple[SkillDiagnostic, ...],
        workspace_boundaries: dict[str, _WorkspaceSkillBoundary] | None = None,
    ) -> None:
        self._descriptors = descriptors
        self._diagnostics = diagnostics
        self._workspace_boundaries = dict(workspace_boundaries or {})

    def descriptors(self) -> tuple[SkillDescriptor, ...]:
        return self._descriptors

    def diagnostics(self) -> tuple[SkillDiagnostic, ...]:
        return self._diagnostics

    def resolve(self, name: str) -> SkillDescriptor:
        descriptor = next(
            (item for item in self._descriptors if item.name == name),
            None,
        )
        if descriptor is None:
            raise SkillNotFound(name)
        return descriptor

    def _workspace_boundary(self, name: str) -> _WorkspaceSkillBoundary | None:
        return self._workspace_boundaries.get(name)
