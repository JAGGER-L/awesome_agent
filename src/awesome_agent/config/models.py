from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from awesome_agent.modeling.catalog import MODEL_CATALOG, KimiRegion
from awesome_agent.modeling.turns import ProviderId

_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ENV_NAME_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")


class CredentialSource(StrEnum):
    ENVIRONMENT = "environment"
    AWESOME = "awesome"


class CredentialSelectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    deepseek: CredentialSource | None = Field(default=None, strict=False)
    kimi: CredentialSource | None = Field(default=None, strict=False)
    mem0: CredentialSource | None = Field(default=None, strict=False)
    tavily: CredentialSource = Field(
        default=CredentialSource.ENVIRONMENT,
        strict=False,
    )
    web_proxy: CredentialSource | None = Field(default=None, strict=False)

    @field_validator(
        "deepseek",
        "kimi",
        "mem0",
        "tavily",
        "web_proxy",
        mode="before",
    )
    @classmethod
    def validate_credential_source_type(
        cls,
        value: object,
    ) -> object:
        if value is not None and not isinstance(value, str):
            raise ValueError("credential source must be a string or null")
        return value

    @field_validator("tavily")
    @classmethod
    def validate_tavily_source(cls, value: CredentialSource) -> CredentialSource:
        if value is not CredentialSource.ENVIRONMENT:
            raise ValueError("tavily credentials must use the environment source")
        return value


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    default_model: str | None = None
    kimi_region: KimiRegion = Field(default=KimiRegion.CN, strict=False)

    @field_validator("kimi_region", mode="before")
    @classmethod
    def validate_kimi_region_type(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("kimi_region must be a string")
        return value

    @field_validator("default_model")
    @classmethod
    def validate_default_model(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                MODEL_CATALOG.profile(value)
            except ValueError as error:
                raise ValueError(
                    "default_model must be a curated Provider/model id"
                ) from error
        return value


class BudgetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    model_calls: int = Field(default=32, ge=1, le=256)
    tool_calls: int = Field(default=64, ge=1, le=512)
    provider_retries: int = Field(default=2, ge=0, le=6)
    compressions: int = Field(default=2, ge=0, le=10)
    active_execution_seconds: int = Field(default=1_800, ge=1, le=21_600)
    total_context_tokens: int = Field(default=262_144, ge=1)
    web_requests: int = Field(default=8, ge=0, le=8)


class UserBudgetConfig(BudgetConfig):
    pass


class ProjectBudgetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

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
    web_requests: int | None = Field(default=None, ge=0, le=8)


class WebConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    enabled: bool = False
    provider: Literal["tavily"] = "tavily"
    blocked_domains: tuple[str, ...] = Field(default=(), strict=False, max_length=128)

    @field_validator("blocked_domains")
    @classmethod
    def validate_blocked_domains(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_blocked_domains(value)


class ProjectWebConfig(BaseModel):
    """Workspace-owned Web restrictions that cannot enable network access."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    blocked_domains: tuple[str, ...] = Field(default=(), strict=False, max_length=128)

    @field_validator("blocked_domains")
    @classmethod
    def validate_blocked_domains(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _validate_blocked_domains(value)


class MemoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

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
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    disabled: tuple[str, ...] = Field(default=(), strict=False)

    @field_validator("disabled")
    @classmethod
    def validate_disabled(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("disabled skill names must be unique")
        if any(_NAME_PATTERN.fullmatch(name) is None for name in value):
            raise ValueError("disabled skill name is invalid")
        return value


class SkillSourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    name: str
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if _NAME_PATTERN.fullmatch(value) is None:
            raise ValueError("skill name is invalid")
        return value


class McpServerDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    id: str
    command: str = Field(min_length=1, max_length=2_000)
    args: tuple[str, ...] = Field(default=(), strict=False)
    env: tuple[str, ...] = Field(default=(), strict=False)

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
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: Literal[2] = 2
    providers: ProviderConfig = Field(default_factory=ProviderConfig)
    credentials: CredentialSelectionConfig = Field(
        default_factory=CredentialSelectionConfig
    )
    budgets: UserBudgetConfig = Field(default_factory=UserBudgetConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    skills: SkillConfig = Field(default_factory=SkillConfig)
    mcp_servers: tuple[UserMcpServerConfig, ...] = Field(default=(), strict=False)

    @field_validator("version", mode="before")
    @classmethod
    def validate_version_type(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("version must be an integer")
        return value

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
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    version: Literal[1] = 1
    budgets: ProjectBudgetConfig = Field(default_factory=ProjectBudgetConfig)
    web: ProjectWebConfig = Field(default_factory=ProjectWebConfig)
    skills: SkillConfig = Field(default_factory=SkillConfig)
    mcp_servers: tuple[McpServerDeclaration, ...] = Field(default=(), strict=False)

    @field_validator("version", mode="before")
    @classmethod
    def validate_version_type(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("version must be an integer")
        return value

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
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    deepseek_api_key: bool = False
    moonshot_api_key: bool = False
    mem0_api_key: bool = False


class StartupOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    model: str | None = None
    thinking_enabled: bool | None = None
    skill_mode: str | None = None


class ThreadConfigState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    model: str | None = None
    thinking_enabled: bool | None = None
    skill_mode: str | None = None


class ApplicationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    providers: ProviderConfig
    budgets: BudgetConfig
    web: WebConfig = Field(default_factory=WebConfig)
    memory: MemoryConfig
    user_skills: tuple[SkillSourceConfig, ...] = Field(default=(), strict=False)
    workspace_skills: tuple[SkillSourceConfig, ...] = Field(default=(), strict=False)
    user_mcp_servers: tuple[UserMcpServerConfig, ...] = Field(default=(), strict=False)
    workspace_mcp_servers: tuple[McpServerDeclaration, ...] = Field(
        default=(), strict=False
    )
    secret_status: SecretStatus


class TurnConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    provider: ProviderId
    model: str
    thinking_enabled: bool = True
    skill_mode: str = "auto"
    budgets: BudgetConfig


def _valid_domain(value: str) -> bool:
    if not value or len(value) > 253 or value.startswith(".") or value.endswith("."):
        return False
    labels = value.split(".")
    return all(
        label
        and len(label) <= 63
        and label[0].isalnum()
        and label[-1].isalnum()
        and all(
            character.isascii() and (character.isalnum() or character == "-")
            for character in label
        )
        for label in labels
    )


def _validate_blocked_domains(value: tuple[str, ...]) -> tuple[str, ...]:
    if len(value) != len(set(value)):
        raise ValueError("blocked domains must be unique")
    for domain in value:
        if domain != domain.strip().lower() or not _valid_domain(domain):
            raise ValueError("blocked domain must be a normalized ASCII hostname")
    return value
