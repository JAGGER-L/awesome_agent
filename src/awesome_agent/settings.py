from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
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
    deepseek_api_key: SecretStr | None = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_pro_model: str = "deepseek-v4-pro"
    deepseek_flash_model: str = "deepseek-v4-flash"
    deepseek_thinking_enabled: bool = True
    deepseek_reasoning_effort: Literal["high", "max"] = "high"
    leader_model: str = "deepseek-v4-pro"
    teammate_model: str = "deepseek-v4-flash"
    verifier_model: str = "deepseek-v4-flash"
    subagent_model: str = "deepseek-v4-flash"
    role_model_overrides: dict[str, str] = Field(default_factory=dict)
    mem0_api_key: SecretStr | None = None
    artifact_root: Path = Field(
        default_factory=lambda: Path.home() / ".awesome-agent" / "artifacts"
    )
    local_config_path: Path = Field(
        default_factory=lambda: Path.home() / ".awesome-agent" / "config.toml"
    )
    workspace_root: Path | None = None
    lease_duration_seconds: int = Field(default=60, ge=15, le=600)
    heartbeat_interval_seconds: int = Field(default=15, ge=1)
    max_claim_attempts: int = Field(default=3, ge=1, le=100)
    worker_poll_interval_seconds: float = Field(default=1.0, gt=0, le=60)
    worker_recovery_interval_seconds: float = Field(default=15.0, gt=0, le=600)
    worker_shutdown_grace_seconds: float = Field(default=30.0, ge=0, le=600)
    worker_retry_delay_seconds: float = Field(default=5.0, ge=0, le=3600)
    approval_default_expiry_seconds: int = Field(default=3600, ge=60, le=86400)
    event_poll_interval_seconds: float = Field(default=0.5, gt=0, le=60)
    max_model_turns: int = Field(default=60, ge=2, le=500)
    max_tool_calls_per_run: int = Field(default=120, ge=1, le=2000)
    max_parallel_read_tools: int = Field(default=4, ge=1, le=32)
    agent_graph_recursion_limit: int = Field(default=256, ge=16, le=4096)
    no_progress_turns: int = Field(default=8, ge=2, le=100)
    builtin_memory_enabled: bool = False
    mem0_enabled: bool = False
    max_teammates: int = Field(default=6, ge=1)
    max_subagents_per_teammate: int = Field(default=3, ge=0)
    max_model_concurrency: int = Field(default=8, ge=1)
    max_tool_concurrency: int = Field(default=12, ge=1)
    max_sandbox_concurrency: int = Field(default=6, ge=1)

    @model_validator(mode="after")
    def validate_heartbeat_interval(self) -> "Settings":
        if self.heartbeat_interval_seconds >= self.lease_duration_seconds:
            raise ValueError("Heartbeat interval must be shorter than lease duration.")
        return self
