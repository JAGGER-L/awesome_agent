from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from awesome_agent.domain.enums import RiskLevel


def utc_now() -> datetime:
    return datetime.now(UTC)


class ExtensionConfigError(ValueError):
    """Raised when extension configuration selects an unsupported capability."""


class ExtensionSourceType(StrEnum):
    STATIC = "static"
    SKILL_DIRECTORY = "skill_directory"


class ExtensionTrustLevel(StrEnum):
    PROJECT = "project"
    USER = "user"
    SYSTEM = "system"


class ExtensionHealthStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class ExtensionHealthSnapshot(BaseModel):
    status: ExtensionHealthStatus = ExtensionHealthStatus.UNKNOWN
    detail: str | None = None
    checked_at: datetime = Field(default_factory=utc_now)


class ExtensionStaticToolConfig(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=4096)
    risk_level: RiskLevel = RiskLevel.LOW
    required_capabilities: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)


class ExtensionStaticSkillConfig(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    version: str = Field(default="1", min_length=1, max_length=64)
    requested_tools: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)


class ExtensionSourceConfig(BaseModel):
    id: str = Field(min_length=1, max_length=128)
    type: ExtensionSourceType
    trust: ExtensionTrustLevel = ExtensionTrustLevel.PROJECT
    path: Path | None = None
    tools: list[ExtensionStaticToolConfig] = Field(default_factory=list)
    skills: list[ExtensionStaticSkillConfig] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        if any(character.isspace() for character in value):
            raise ValueError("Extension source id cannot contain whitespace.")
        return value


class ExtensionSourceSnapshot(BaseModel):
    id: str
    type: ExtensionSourceType
    trust: ExtensionTrustLevel
    health: ExtensionHealthSnapshot = Field(default_factory=ExtensionHealthSnapshot)


class ExtensionToolInventoryItem(BaseModel):
    name: str
    source_id: str
    description: str = ""
    risk_level: RiskLevel
    required_capabilities: set[str] = Field(default_factory=set)
    input_schema: dict[str, Any] = Field(default_factory=dict)


class ExtensionSkillInventoryItem(BaseModel):
    id: str
    source_id: str
    version: str
    instructions: str = ""
    context_refs: list[str] = Field(default_factory=list)
    requested_tools: list[str] = Field(default_factory=list)
    required_capabilities: set[str] = Field(default_factory=set)
    compatible_actor_kinds: set[str] = Field(default_factory=set)
    compatible_routes: set[str] = Field(default_factory=set)
    risk_level: RiskLevel = RiskLevel.LOW


class ExtensionDiscoverySnapshot(BaseModel):
    source: ExtensionSourceSnapshot
    tools: list[ExtensionToolInventoryItem] = Field(default_factory=list)
    skills: list[ExtensionSkillInventoryItem] = Field(default_factory=list)


class ExtensionCatalog(BaseModel):
    version: str
    published_at: datetime = Field(default_factory=utc_now)
    sources: list[ExtensionSourceSnapshot] = Field(default_factory=list)
    tools: list[ExtensionToolInventoryItem] = Field(default_factory=list)
    skills: list[ExtensionSkillInventoryItem] = Field(default_factory=list)


ExtensionSourceConfigInput = ExtensionSourceConfig | Mapping[str, object]
