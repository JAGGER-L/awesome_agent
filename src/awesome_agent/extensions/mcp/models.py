from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class McpSource(StrEnum):
    USER = "user"
    WORKSPACE = "workspace"


class McpServerConfig(BaseModel):
    """Secret-free declaration for one stdio MCP server."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    command: str = Field(min_length=1, max_length=2_000)
    args: tuple[str, ...] = ()
    env_names: tuple[str, ...] = ()
    source: McpSource
    enabled: bool = False

    @field_validator("env_names")
    @classmethod
    def unique_environment_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not name or "=" in name for name in value):
            raise ValueError("environment names must be non-empty names")
        if len(set(value)) != len(value):
            raise ValueError("environment names must be unique")
        return value
