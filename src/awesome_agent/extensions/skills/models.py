from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, JsonValue


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


class SkillCatalog:
    def __init__(
        self,
        descriptors: tuple[SkillDescriptor, ...],
        diagnostics: tuple[SkillDiagnostic, ...],
    ) -> None:
        self._descriptors = descriptors
        self._diagnostics = diagnostics

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
