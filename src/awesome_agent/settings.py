from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="AWESOME_AGENT_",
        extra="ignore",
    )

    log_level: str = "INFO"
    database_url: str = (
        "postgresql+asyncpg://awesome_agent:awesome_agent@localhost:54329/awesome_agent"
    )
    checkpoint_database_url: str = (
        "postgresql://awesome_agent:awesome_agent@localhost:54329/awesome_agent"
    )
    artifact_root: Path = Field(
        default_factory=lambda: Path.home() / ".awesome-agent" / "artifacts"
    )
    builtin_memory_enabled: bool = False
    mem0_enabled: bool = False
    max_teammates: int = Field(default=6, ge=1)
    max_subagents_per_teammate: int = Field(default=3, ge=0)
    max_model_concurrency: int = Field(default=8, ge=1)
    max_tool_concurrency: int = Field(default=12, ge=1)
    max_sandbox_concurrency: int = Field(default=6, ge=1)
