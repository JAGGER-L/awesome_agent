from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SUPPORTED_MODEL_IDS = frozenset(
    {
        "deepseek/deepseek-v4-flash",
        "deepseek/deepseek-v4-pro",
        "kimi/kimi-k2.6",
        "kimi/kimi-k2.5",
    }
)

_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ENV_NAME_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")


class KimiRegion(StrEnum):
    CN = "cn"
    GLOBAL = "global"


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    default_model: str | None = None
    kimi_region: KimiRegion = KimiRegion.CN

    @field_validator("default_model")
    @classmethod
    def validate_default_model(cls, value: str | None) -> str | None:
        if value is not None and value not in SUPPORTED_MODEL_IDS:
            raise ValueError("default_model must be a curated Provider/model id")
        return value


class BudgetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_calls: int = Field(default=32, ge=1, le=256)
    tool_calls: int = Field(default=64, ge=1, le=512)
    provider_retries: int = Field(default=2, ge=0, le=6)
    compressions: int = Field(default=2, ge=0, le=10)
    active_execution_seconds: int = Field(default=1_800, ge=1, le=21_600)
    total_context_tokens: int = Field(default=262_144, ge=1)


class ProjectBudgetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model_calls: int | None = Field(default=None, ge=1, le=256)
    tool_calls: int | None = Field(default=None, ge=1, le=512)
    provider_retries: int | None = Field(default=None, ge=0, le=6)
    compressions: int | None = Field(default=None, ge=0, le=10)
    active_execution_seconds: int | None = Field(
        default=None,
        ge=1,
        le=21_600,
    )
    total_context_tokens: int | None = Field(default=None, ge=1)


class MemoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    local_file_memory: bool = False
    mem0_cloud: bool = False
    mem0_user_id: str | None = Field(default=None, min_length=8, max_length=128)

    @field_validator("mem0_user_id")
    @classmethod
    def validate_opaque_user_id(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(r"user_[a-f0-9]{32}", value):
            raise ValueError("mem0_user_id must be an opaque user identifier")
        return value


class SkillConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    disabled: tuple[str, ...] = ()

    @field_validator("disabled")
    @classmethod
    def validate_disabled(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("disabled skill names must be unique")
        if any(_NAME_PATTERN.fullmatch(name) is None for name in value):
            raise ValueError("disabled skill name is invalid")
        return value


class SkillSourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if _NAME_PATTERN.fullmatch(value) is None:
            raise ValueError("skill name is invalid")
        return value


class McpServerDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    command: str = Field(min_length=1, max_length=2_000)
    args: tuple[str, ...] = ()
    env: tuple[str, ...] = ()

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if _NAME_PATTERN.fullmatch(value) is None:
            raise ValueError("MCP server id is invalid")
        return value

    @field_validator("env")
    @classmethod
    def validate_env_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("MCP environment names must be unique")
        if any(_ENV_NAME_PATTERN.fullmatch(name) is None for name in value):
            raise ValueError("MCP environment name is invalid")
        return value


class UserMcpServerConfig(McpServerDeclaration):
    enabled: bool = False


class UserConfigDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    providers: ProviderConfig = Field(default_factory=ProviderConfig)
    budgets: BudgetConfig = Field(default_factory=BudgetConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    skills: SkillConfig = Field(default_factory=SkillConfig)
    mcp_servers: tuple[UserMcpServerConfig, ...] = ()

    @field_validator("mcp_servers")
    @classmethod
    def validate_unique_mcp_ids(
        cls,
        value: tuple[UserMcpServerConfig, ...],
    ) -> tuple[UserMcpServerConfig, ...]:
        ids = [server.id for server in value]
        if len(ids) != len(set(ids)):
            raise ValueError("MCP server ids must be unique")
        return value


class WorkspaceConfigDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    budgets: ProjectBudgetConfig = Field(default_factory=ProjectBudgetConfig)
    skills: SkillConfig = Field(default_factory=SkillConfig)
    mcp_servers: tuple[McpServerDeclaration, ...] = ()

    @field_validator("mcp_servers")
    @classmethod
    def validate_unique_mcp_ids(
        cls,
        value: tuple[McpServerDeclaration, ...],
    ) -> tuple[McpServerDeclaration, ...]:
        ids = [server.id for server in value]
        if len(ids) != len(set(ids)):
            raise ValueError("MCP server ids must be unique")
        return value


class SecretStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    deepseek_api_key: bool = False
    moonshot_api_key: bool = False
    mem0_api_key: bool = False


class StartupOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str | None = None
    thinking_enabled: bool | None = None
    skill_mode: str | None = None


class ThreadConfigState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str | None = None
    thinking_enabled: bool | None = None
    skill_mode: str | None = None


class ApplicationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    providers: ProviderConfig
    budgets: BudgetConfig
    memory: MemoryConfig
    user_skills: tuple[SkillSourceConfig, ...] = ()
    workspace_skills: tuple[SkillSourceConfig, ...] = ()
    user_mcp_servers: tuple[UserMcpServerConfig, ...] = ()
    workspace_mcp_servers: tuple[McpServerDeclaration, ...] = ()
    secret_status: SecretStatus


class TurnConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["deepseek", "kimi"]
    model: str
    thinking_enabled: bool = False
    skill_mode: str = "auto"
    budgets: BudgetConfig
